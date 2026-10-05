from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlparse, urlunparse

import httpx
from websockets.sync.client import connect

from . import __version__ as DEVICE_AGENT_VERSION
from .config import AgentConfig
from .device_credentials import resolve_token_path
from .models import ActionResult, AgentJob
from .terminal_outbox import TerminalOutbox
from .remote_protocol import (
    DeviceAuthorizationError,
    TransientDeliveryError,
    canonical_result,
    decode_job_payload,
    dispatch_job,
    read_secret,
)


DIRECT_ARTIFACT_MAX_BYTES = 90 * 1024 * 1024
MULTIPART_PART_BYTES = 64 * 1024 * 1024
MULTIPART_MAX_PARTS = 10_000
ARTIFACT_MULTIPART_CAPABILITY = "artifact_multipart_v1"
CONTROL_PLANE_CAPABILITY_CACHE_SECONDS = 60.0


class CloudflareControlPlane:
    """Event-driven development control plane over one authenticated WebSocket.

    The transport keeps the Device Agent's execution contract and local allow-list.
    HTTP is reserved for artifact transfer; command delivery, presence, leases,
    progress and terminal reports use the persistent socket.
    """

    uses_long_poll = True

    def __init__(self, config: AgentConfig):
        if not config.control_plane_url:
            raise RuntimeError("ORDAX_CONTROL_PLANE_URL is required for cloudflare-v3.")
        if not config.device_id:
            raise RuntimeError("ORDAX_DEVICE_ID is required for cloudflare-v3.")

        self.config = config
        self.device_id = str(config.device_id)
        try:
            uuid.UUID(self.device_id)
        except ValueError as error:
            raise RuntimeError("ORDAX_DEVICE_ID must be a UUID.") from error

        token_path = resolve_token_path(config.state_dir)
        self.device_token = (
            os.environ.get("ORDAX_DEVICE_TOKEN")
            or read_secret(token_path)
        )
        if not self.device_token:
            raise RuntimeError(
                f"Cloudflare v3 device token is missing: {token_path}"
            )
        if not 32 <= len(self.device_token) <= 512:
            raise RuntimeError("Cloudflare v3 device token length is invalid.")

        self.base_http_url = config.control_plane_url.rstrip("/")
        self.ws_url = self._make_ws_url(self.base_http_url, self.device_id)
        self.agent_instance_id = str(uuid.uuid4())
        self.boot_id = str(uuid.uuid4())
        self._jobs: dict[str, AgentJob] = {}
        self._queued_messages: list[dict[str, Any]] = []
        self._socket = None
        self._lock = threading.RLock()
        self._capability_cache: tuple[float, frozenset[str]] | None = None
        self._terminal_outbox = TerminalOutbox(
            config.state_dir,
            "cloudflare-v3",
            self.device_id,
        )

        self.http = httpx.Client(
            timeout=httpx.Timeout(60.0),
            limits=httpx.Limits(
                max_connections=4,
                max_keepalive_connections=2,
                keepalive_expiry=30.0,
            ),
            headers={
                "X-Ordax-Device-Id": self.device_id,
                "X-Ordax-Device-Token": self.device_token,
                "user-agent": f"OrdaX-Device-Agent/{DEVICE_AGENT_VERSION}",
            },
        )

    @staticmethod
    def _make_ws_url(base_url: str, device_id: str) -> str:
        parsed = urlparse(base_url)
        if parsed.scheme not in {"http", "https", "ws", "wss"} or not parsed.netloc:
            raise RuntimeError("ORDAX_CONTROL_PLANE_URL must be an absolute HTTP(S) URL.")
        scheme = "wss" if parsed.scheme in {"https", "wss"} else "ws"
        base_path = parsed.path.rstrip("/")
        path = f"{base_path}/v3/device/ws"
        return urlunparse(
            (scheme, parsed.netloc, path, "", f"device_id={quote(device_id)}", "")
        )

    @staticmethod
    def _execution_context(job: AgentJob) -> dict[str, Any]:
        required = {
            "effect_id": job.effect_id,
            "attempt_id": job.attempt_id,
            "lease_id": job.lease_token,
            "execution_epoch": job.execution_epoch,
            "agent_instance_id": job.agent_instance_id,
            "boot_id": job.boot_id,
        }
        if any(value in (None, "") for value in required.values()):
            raise RuntimeError("cloudflare-v3 job execution context is incomplete")
        return {"job_id": job.id, **required}

    def _drop_socket(self) -> None:
        socket = self._socket
        self._socket = None
        if socket is not None:
            try:
                socket.close()
            except Exception:
                pass

    def _ensure_socket(self):
        if self._socket is not None:
            return self._socket

        try:
            socket = connect(
                self.ws_url,
                additional_headers={
                    "X-Ordax-Device-Token": self.device_token,
                    "X-Ordax-Agent-Instance": self.agent_instance_id,
                    "X-Ordax-Boot-Id": self.boot_id,
                },
                open_timeout=10,
                close_timeout=5,
                ping_interval=20,
                ping_timeout=20,
                max_size=2 * 1024 * 1024,
            )
            raw = socket.recv(timeout=10)
            hello = json.loads(raw)
            if not isinstance(hello, dict) or hello.get("type") != "hello":
                socket.close()
                raise RuntimeError("cloudflare-v3 websocket did not return hello")
            if hello.get("device_id") != self.device_id:
                socket.close()
                raise RuntimeError("cloudflare-v3 websocket device identity mismatch")
            self._socket = socket
            return socket
        except DeviceAuthorizationError:
            raise
        except Exception as error:
            status = getattr(getattr(error, "response", None), "status_code", None)
            self._drop_socket()
            if status in {401, 403}:
                raise DeviceAuthorizationError("DEVICE_CREDENTIAL_REJECTED") from error
            raise TransientDeliveryError(
                f"cloudflare-v3 websocket connect failed: {type(error).__name__}: {error}"
            ) from error

    @staticmethod
    def _decode_message(raw: Any) -> dict[str, Any]:
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        try:
            message = json.loads(raw)
        except (TypeError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise RuntimeError("cloudflare-v3 returned invalid JSON") from error
        if not isinstance(message, dict):
            raise RuntimeError("cloudflare-v3 returned a non-object message")
        return message

    def _recv(self, timeout: float) -> dict[str, Any]:
        socket = self._ensure_socket()
        try:
            return self._decode_message(socket.recv(timeout=timeout))
        except TimeoutError:
            raise
        except Exception as error:
            self._drop_socket()
            raise TransientDeliveryError(
                f"cloudflare-v3 websocket receive failed: {type(error).__name__}: {error}"
            ) from error

    def _rpc(
        self,
        operation: str,
        payload: dict[str, Any] | None = None,
        *,
        timeout: float = 20.0,
    ) -> dict[str, Any]:
        with self._lock:
            socket = self._ensure_socket()
            request_id = str(uuid.uuid4())
            body = {
                "type": operation,
                "request_id": request_id,
                **(payload or {}),
            }
            try:
                socket.send(json.dumps(body, separators=(",", ":"), ensure_ascii=False))
                while True:
                    message = self._recv(timeout)
                    if message.get("type") == "job":
                        self._queued_messages.append(message)
                        continue
                    if (
                        message.get("type") == "ack"
                        and message.get("request_id") == request_id
                    ):
                        if message.get("ok") is False:
                            error = str(message.get("error") or "request_rejected")
                            if error in {"invalid_device_token", "device_revoked"}:
                                raise DeviceAuthorizationError("DEVICE_CREDENTIAL_REJECTED")
                            raise RuntimeError(f"cloudflare-v3 error: {error}")
                        return message
            except DeviceAuthorizationError:
                raise
            except (RuntimeError, TransientDeliveryError):
                raise
            except Exception as error:
                self._drop_socket()
                raise TransientDeliveryError(
                    f"cloudflare-v3 websocket send failed: {type(error).__name__}: {error}"
                ) from error

    @property
    def is_paired(self) -> bool:
        return True

    def pair_if_needed(
        self,
        capabilities: list[str],
        *,
        agent_version: str,
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        del capabilities, agent_version, metadata
        # Device provisioning is deliberately an administrator operation. The
        # station only consumes its device-scoped token.
        return False

    def heartbeat(
        self,
        actions: list[str],
        *,
        agent_version: str,
        metadata: dict[str, Any] | None = None,
        status: str = "online",
        last_error: str | None = None,
    ) -> None:
        self._rpc(
            "heartbeat",
            {
                "boot_id": self.boot_id,
                "agent_instance_id": self.agent_instance_id,
                "runtime_mode": "developer",
                "status": status,
                "agent_version": agent_version,
                "capabilities": actions,
                "metadata": metadata or {},
                "last_error": last_error,
            },
            timeout=10.0,
        )

    def _job_from_message(self, message: dict[str, Any]) -> AgentJob:
        row = message.get("job")
        if not isinstance(row, dict):
            raise RuntimeError("cloudflare-v3 job envelope is missing")

        payload = decode_job_payload(row)
        action, action_payload, project = dispatch_job(row, payload)
        job = AgentJob(
            id=str(row["job_id"]),
            action=action,
            payload=action_payload,
            project_slug=project,
            lease_token=str(row.get("lease_id") or ""),
            effect_id=str(row.get("effect_id") or ""),
            attempt_id=str(row.get("attempt_id") or ""),
            execution_epoch=int(row.get("execution_epoch") or 0),
            agent_instance_id=self.agent_instance_id,
            boot_id=self.boot_id,
            payload_sha256=str(row.get("payload_sha256") or ""),
        )
        start_payload = self._execution_context(job)
        for attempt in range(3):
            try:
                self._rpc("start", start_payload)
                break
            except TransientDeliveryError:
                if attempt == 2:
                    raise
                time.sleep(2 ** attempt)
        self._jobs[job.id] = job
        return job

    def claim_next_job(self) -> AgentJob | None:
        # Never accept a new action while a prior executed action still has a
        # durable terminal report awaiting cloud acceptance.
        self.recover_pending_reports()
        with self._lock:
            if self._queued_messages:
                return self._job_from_message(self._queued_messages.pop(0))

            self._ensure_socket()
            try:
                while True:
                    message = self._recv(25.0)
                    if message.get("type") == "job":
                        return self._job_from_message(message)
                    if message.get("type") == "ping":
                        self._rpc("pong", {"server_time": message.get("server_time")})
            except TimeoutError:
                return None

    def append_event(
        self,
        job_id: str,
        level: str,
        message: str,
        data: dict[str, Any] | None = None,
    ) -> None:
        job = self._jobs.get(job_id)
        if job is None:
            return
        progress_percent = None
        if isinstance(data, dict) and isinstance(data.get("progress_percent"), int):
            progress_percent = max(0, min(100, int(data["progress_percent"])))
        self._rpc(
            "progress",
            {
                **self._execution_context(job),
                "progress_percent": progress_percent,
                "stage": str(level)[:96],
                "message": str(message)[:512],
            },
        )

    def renew(self, job: AgentJob) -> dict[str, Any]:
        return self._rpc("lease_heartbeat", self._execution_context(job))

    _canonical_result = staticmethod(canonical_result)

    def _recover_terminal_report(self, report: dict[str, Any]) -> dict[str, Any]:
        url = f"{self.base_http_url}/v3/device/recover-report"
        try:
            response = self.http.post(
                url,
                json=report,
                headers={
                    "X-Ordax-Recovery-Agent-Instance": self.agent_instance_id,
                    "X-Ordax-Recovery-Boot-Id": self.boot_id,
                },
            )
        except httpx.HTTPError as error:
            raise TransientDeliveryError(
                f"cloudflare-v3 terminal recovery failed: {type(error).__name__}: {error}"
            ) from error

        if response.status_code in {401, 403}:
            raise DeviceAuthorizationError("DEVICE_CREDENTIAL_REJECTED")
        if response.status_code in {408, 429, 500, 502, 503, 504}:
            raise TransientDeliveryError(
                f"cloudflare-v3 terminal recovery HTTP {response.status_code}"
            )
        if response.status_code >= 400:
            raise RuntimeError(
                f"cloudflare-v3 terminal recovery HTTP {response.status_code}: "
                f"{response.text[-2000:]}"
            )
        payload = response.json()
        if not isinstance(payload, dict) or payload.get("ok") is not True:
            raise RuntimeError("cloudflare-v3 terminal recovery returned invalid response")
        return payload

    def recover_pending_reports(self) -> int:
        recovered = 0
        for path, report in self._terminal_outbox.pending():
            self._recover_terminal_report(report)
            self._terminal_outbox.acknowledge(path)
            self._jobs.pop(str(report.get("job_id") or ""), None)
            recovered += 1
        return recovered

    def complete(self, job: AgentJob, result: ActionResult) -> None:
        body, digest = self._canonical_result(result)
        status = "succeeded" if result.ok else "failed"
        report = {
            **self._execution_context(job),
            "report_id": str(uuid.uuid4()),
            "status": status,
            "exit_code": 0 if result.ok else 1,
            "result": body,
            "result_sha256": digest,
            "error_code": None if result.ok else "device_agent_action_failed",
        }

        # Persist before the first network attempt. If the process dies after the
        # local action completed, startup recovery can deliver this exact report
        # before any new WebSocket/job claim is allowed.
        outbox_path = self._terminal_outbox.persist(report)

        # A lost ACK must only repeat delivery of the identical terminal report.
        # It must never cause the Blender/Unity/Git action to run again.
        for attempt in range(3):
            try:
                self._rpc("report", report, timeout=30.0)
                break
            except TransientDeliveryError:
                if attempt == 2:
                    raise
                time.sleep(2 ** attempt)

        self._terminal_outbox.acknowledge(outbox_path)
        self._jobs.pop(job.id, None)

    def create_product_pairing(self) -> dict[str, Any]:
        response = self.http.post(
            f"{self.base_http_url}/v3/device/product-pairings",
            json={},
            timeout=15.0,
        )
        if response.status_code in {401, 403}:
            raise DeviceAuthorizationError("DEVICE_CREDENTIAL_REJECTED")
        if response.status_code in {408, 429} or response.status_code >= 500:
            raise TransientDeliveryError(
                f"cloudflare-v3 product pairing HTTP {response.status_code}"
            )
        if response.status_code >= 400:
            raise RuntimeError(
                f"cloudflare-v3 product pairing HTTP {response.status_code}: "
                f"{response.text[-2000:]}"
            )
        payload = response.json()
        pairing = payload.get("pairing") if isinstance(payload, dict) else None
        if (
            not isinstance(pairing, dict)
            or not isinstance(pairing.get("pairing_id"), str)
            or not isinstance(pairing.get("pairing_secret"), str)
            or not isinstance(pairing.get("expires_at"), str)
        ):
            raise RuntimeError(
                "cloudflare-v3 product pairing returned invalid response"
            )
        return dict(pairing)

    def record_product_audit(self, event) -> None:
        body = {
            "request_id": event.request_id,
            "subject_id": event.subject_id,
            "grant_id": event.grant_id,
            "action": event.action,
            "project": event.project,
            "phase": event.phase,
            "decision": event.decision,
            "reason": event.reason,
            "payload_fields": list(event.payload_fields),
            "result_ok": event.result_ok,
        }
        response = self.http.post(
            f"{self.base_http_url}/v3/product/audit",
            json=body,
            timeout=15.0,
        )
        if response.status_code in {401, 403}:
            raise DeviceAuthorizationError("DEVICE_CREDENTIAL_REJECTED")
        if response.status_code in {408, 429} or response.status_code >= 500:
            raise TransientDeliveryError(
                f"cloudflare-v3 product audit HTTP {response.status_code}"
            )
        if response.status_code >= 400:
            raise RuntimeError(
                f"cloudflare-v3 product audit HTTP {response.status_code}: "
                f"{response.text[-2000:]}"
            )
        payload = response.json()
        if not isinstance(payload, dict) or payload.get("ok") is not True:
            raise RuntimeError("cloudflare-v3 product audit returned invalid response")

    def _control_plane_capabilities(self) -> frozenset[str]:
        now = time.monotonic()
        cached = self._capability_cache
        if cached is not None and now - cached[0] < CONTROL_PLANE_CAPABILITY_CACHE_SECONDS:
            return cached[1]

        try:
            response = self.http.get("/health", timeout=10.0)
        except httpx.HTTPError as error:
            raise TransientDeliveryError(
                "cloudflare-v3 capability probe failed: "
                f"{type(error).__name__}: {error}"
            ) from error

        if response.status_code in {408, 429} or response.status_code >= 500:
            raise TransientDeliveryError(
                f"cloudflare-v3 capability probe HTTP {response.status_code}"
            )
        if response.status_code >= 400:
            raise RuntimeError(
                f"cloudflare-v3 capability probe HTTP {response.status_code}: "
                f"{response.text[-2000:]}"
            )

        try:
            payload = response.json()
        except ValueError as error:
            raise RuntimeError(
                "cloudflare-v3 capability probe returned invalid JSON"
            ) from error
        if (
            not isinstance(payload, dict)
            or payload.get("ok") is not True
            or payload.get("service") != "ordax-control-plane-v3"
        ):
            raise RuntimeError(
                "cloudflare-v3 capability probe returned invalid response"
            )

        raw = payload.get("capabilities")
        capabilities = frozenset(
            item for item in raw
            if isinstance(item, str) and item
        ) if isinstance(raw, list) else frozenset()
        self._capability_cache = (now, capabilities)
        return capabilities

    @staticmethod
    def _artifact_headers(
        file_path: Path,
        *,
        kind: str,
        metadata: dict[str, Any] | None,
        digest: str,
        size: int,
        content_type: str,
    ) -> dict[str, str]:
        metadata_raw = json.dumps(
            metadata or {}, separators=(",", ":"), ensure_ascii=False
        )
        if len(metadata_raw) > 4000:
            raise ValueError("artifact metadata exceeds 4000 characters")
        return {
            "content-type": content_type,
            "X-Ordax-Artifact-Name": file_path.name[:180],
            "X-Ordax-Artifact-Kind": str(kind)[:80],
            "X-Ordax-Artifact-Sha256": digest,
            "X-Ordax-Artifact-Size": str(size),
            "X-Ordax-Artifact-Metadata": metadata_raw,
        }

    def _artifact_request(
        self,
        method: str,
        url: str,
        *,
        operation: str,
        headers: dict[str, str] | None = None,
        content: bytes | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                response = self.http.request(
                    method,
                    url,
                    headers=headers,
                    content=content,
                    json=json_body,
                )
            except httpx.HTTPError as error:
                last_error = error
                if attempt < 2:
                    time.sleep(2 ** attempt)
                    continue
                raise TransientDeliveryError(
                    f"cloudflare-v3 {operation} failed: "
                    f"{type(error).__name__}: {error}"
                ) from error

            if response.status_code in {401, 403}:
                raise DeviceAuthorizationError("DEVICE_CREDENTIAL_REJECTED")
            if response.status_code in {408, 429} or response.status_code >= 500:
                last_error = TransientDeliveryError(
                    f"cloudflare-v3 {operation} HTTP {response.status_code}"
                )
                if attempt < 2:
                    time.sleep(2 ** attempt)
                    continue
                raise last_error
            if response.status_code >= 400:
                raise RuntimeError(
                    f"cloudflare-v3 {operation} HTTP {response.status_code}: "
                    f"{response.text[-2000:]}"
                )
            try:
                result = response.json()
            except ValueError as error:
                raise RuntimeError(
                    f"cloudflare-v3 {operation} returned invalid JSON"
                ) from error
            if not isinstance(result, dict) or result.get("ok") is not True:
                raise RuntimeError(
                    f"cloudflare-v3 {operation} returned invalid response"
                )
            return result

        raise TransientDeliveryError(
            f"cloudflare-v3 {operation} failed: {last_error}"
        )

    def _upload_artifact_multipart_once(
        self,
        job: AgentJob,
        file_path: Path,
        *,
        artifact_id: str,
        kind: str,
        metadata: dict[str, Any] | None,
        digest: str,
        size: int,
        content_type: str,
    ) -> dict[str, Any]:
        url = (
            f"{self.base_http_url}/v3/artifacts/{quote(job.id)}/{quote(artifact_id)}"
        )
        headers = self._artifact_headers(
            file_path,
            kind=kind,
            metadata=metadata,
            digest=digest,
            size=size,
            content_type=content_type,
        )
        created = self._artifact_request(
            "POST",
            f"{url}?action=mpu-create",
            operation="multipart create",
            headers=headers,
        )
        if created.get("complete") is True:
            return {
                "artifact_id": artifact_id,
                "storage_path": created.get("storage_path"),
                "sha256": digest,
                "size_bytes": size,
                "signed_url": created.get("signed_url"),
                "delivery": "cloudflare-v3-multipart",
                "metadata": metadata or {},
            }

        upload_id = created.get("upload_id")
        if not isinstance(upload_id, str) or not upload_id:
            raise RuntimeError("cloudflare-v3 multipart create returned no upload id")

        uploaded_parts: list[dict[str, Any]] = []
        try:
            with file_path.open("rb") as stream:
                part_number = 0
                while True:
                    chunk = stream.read(MULTIPART_PART_BYTES)
                    if not chunk:
                        break
                    part_number += 1
                    if part_number > MULTIPART_MAX_PARTS:
                        raise RuntimeError(
                            "artifact exceeds Cloudflare multipart part-count limit"
                        )
                    part_sha256 = hashlib.sha256(chunk).hexdigest()
                    part_url = (
                        f"{url}?action=mpu-uploadpart"
                        f"&uploadId={quote(upload_id, safe='')}"
                        f"&partNumber={part_number}"
                    )
                    uploaded = self._artifact_request(
                        "PUT",
                        part_url,
                        operation=f"multipart part {part_number}",
                        headers={
                            "content-type": "application/octet-stream",
                            "content-length": str(len(chunk)),
                            "X-Ordax-Part-Sha256": part_sha256,
                        },
                        content=chunk,
                    )
                    etag = uploaded.get("etag")
                    if (
                        uploaded.get("part_number") != part_number
                        or not isinstance(etag, str)
                        or not etag
                    ):
                        raise RuntimeError(
                            "cloudflare-v3 multipart part returned invalid response"
                        )
                    uploaded_parts.append(
                        {"part_number": part_number, "etag": etag}
                    )

            completed = self._artifact_request(
                "POST",
                f"{url}?action=mpu-complete",
                operation="multipart complete",
                json_body={
                    "upload_id": upload_id,
                    "parts": uploaded_parts,
                },
            )
        except TransientDeliveryError:
            raise
        except Exception:
            try:
                self.http.delete(
                    f"{url}?action=mpu-abort"
                    f"&uploadId={quote(upload_id, safe='')}",
                    timeout=20.0,
                )
            except httpx.HTTPError:
                pass
            raise

        returned_id = completed.get("artifact_id")
        if returned_id not in {None, artifact_id}:
            raise RuntimeError("cloudflare-v3 multipart artifact id changed")
        return {
            "artifact_id": artifact_id,
            "storage_path": completed.get("storage_path"),
            "sha256": digest,
            "size_bytes": size,
            "signed_url": completed.get("signed_url"),
            "delivery": "cloudflare-v3-multipart",
            "metadata": metadata or {},
        }

    def _upload_artifact_multipart(
        self,
        job: AgentJob,
        file_path: Path,
        *,
        artifact_id: str,
        kind: str,
        metadata: dict[str, Any] | None,
        digest: str,
        size: int,
        content_type: str,
    ) -> dict[str, Any]:
        for attempt in range(2):
            try:
                return self._upload_artifact_multipart_once(
                    job,
                    file_path,
                    artifact_id=artifact_id,
                    kind=kind,
                    metadata=metadata,
                    digest=digest,
                    size=size,
                    content_type=content_type,
                )
            except TransientDeliveryError:
                if attempt == 1:
                    raise
                time.sleep(1)
        raise RuntimeError("unreachable multipart retry state")

    def upload_artifact(
        self,
        job: AgentJob,
        path: str | Path,
        *,
        kind: str,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        file_path = Path(path).expanduser().resolve()
        if not file_path.is_file():
            raise FileNotFoundError(file_path)

        size = file_path.stat().st_size
        with file_path.open("rb") as handle:
            digest = hashlib.file_digest(handle, "sha256").hexdigest()

        artifact_id = str(uuid.uuid4())
        content_type = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"

        if size > DIRECT_ARTIFACT_MAX_BYTES:
            if ARTIFACT_MULTIPART_CAPABILITY not in self._control_plane_capabilities():
                return {
                    "delivery": "local-only-v3-artifact-multipart-unavailable",
                    "kind": kind,
                    "local_name": file_path.name,
                    "sha256": digest,
                    "size_bytes": size,
                    "metadata": metadata or {},
                }
            part_count = (size + MULTIPART_PART_BYTES - 1) // MULTIPART_PART_BYTES
            if part_count > MULTIPART_MAX_PARTS:
                return {
                    "delivery": "local-only-v3-artifact-part-count-limit",
                    "kind": kind,
                    "local_name": file_path.name,
                    "sha256": digest,
                    "size_bytes": size,
                    "metadata": metadata or {},
                }
            return self._upload_artifact_multipart(
                job,
                file_path,
                artifact_id=artifact_id,
                kind=kind,
                metadata=metadata,
                digest=digest,
                size=size,
                content_type=content_type,
            )

        url = (
            f"{self.base_http_url}/v3/artifacts/{quote(job.id)}/{quote(artifact_id)}"
        )
        headers = self._artifact_headers(
            file_path,
            kind=kind,
            metadata=metadata,
            digest=digest,
            size=size,
            content_type=content_type,
        )
        try:
            with file_path.open("rb") as stream:
                response = self.http.put(url, headers=headers, content=stream)
        except httpx.HTTPError as error:
            raise TransientDeliveryError(
                f"cloudflare-v3 artifact upload failed: {type(error).__name__}: {error}"
            ) from error

        if response.status_code in {401, 403}:
            raise DeviceAuthorizationError("DEVICE_CREDENTIAL_REJECTED")
        if response.status_code >= 500:
            raise TransientDeliveryError(
                f"cloudflare-v3 artifact upload HTTP {response.status_code}"
            )
        if response.status_code >= 400:
            raise RuntimeError(
                f"cloudflare-v3 artifact upload HTTP {response.status_code}: "
                f"{response.text[-2000:]}"
            )

        result = response.json()
        if not isinstance(result, dict) or result.get("ok") is not True:
            raise RuntimeError("cloudflare-v3 artifact upload returned invalid response")
        return {
            "artifact_id": artifact_id,
            "storage_path": result.get("storage_path"),
            "sha256": digest,
            "size_bytes": size,
            "signed_url": result.get("signed_url"),
            "delivery": "cloudflare-v3-direct",
            "metadata": metadata or {},
        }
