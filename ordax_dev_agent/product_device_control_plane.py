from __future__ import annotations

import base64
import hashlib
import json
import time
import uuid
from typing import Any

import httpx

from . import __version__ as DEVICE_AGENT_VERSION
from .config import AgentConfig
from .device_credentials import resolve_token_path
from .models import ActionResult, AgentJob
from .product_central_session import ProductCentralSessionClient
from .projects import resolve_project_for_repository
from .remote_protocol import (
    DeviceAuthorizationError,
    TransientDeliveryError,
    canonical_result,
    decode_job_payload,
    dispatch_job,
    read_secret,
)
from .terminal_outbox import TerminalOutbox


_PROJECT_SCOPE_ACTIONS = frozenset({
    "agent.project_briefing",
    "continuity.get",
    "continuity.update",
    "handoff.create",
    "handoff.get",
})


class ProductDeviceControlPlane:
    """Canonical device transport over the Product MCP PostgreSQL authority.

    This transport intentionally has no WebSocket/D1/operator-token fallback.
    Device identity, presence, action queue, leases and terminal reports are
    authorized by the device credential and persisted by the Platform RPCs.
    """

    uses_long_poll = False
    runtime_kind = "desktop-agent"

    def __init__(self, config: AgentConfig):
        if not config.product_mcp_url:
            raise RuntimeError("ORDAX_PRODUCT_MCP_URL is required for the canonical device transport.")
        if not config.device_id:
            raise RuntimeError("ORDAX_DEVICE_ID is required for the canonical device transport.")
        self.config = config
        self.device_id = str(config.device_id)
        try:
            uuid.UUID(self.device_id)
        except ValueError as error:
            raise RuntimeError("ORDAX_DEVICE_ID must be a UUID.") from error
        self.device_token = read_secret(resolve_token_path(config.state_dir))
        if not self.device_token or not 32 <= len(self.device_token) <= 512:
            raise RuntimeError("Canonical Product device token is missing or invalid.")
        self.base_http_url = str(config.product_mcp_url).rstrip("/")
        self.agent_instance_id = str(uuid.uuid4())
        self.boot_id = str(uuid.uuid4())
        # Execution provenance is immutable for this process. Heartbeat already
        # publishes the same package version; carrying it with every fenced
        # action lets Platform persist the version that actually performed the
        # effect rather than attributing old actions to current presence later.
        self.agent_version = str(DEVICE_AGENT_VERSION)
        self._jobs: dict[str, AgentJob] = {}
        self._terminal_outbox = TerminalOutbox(
            config.state_dir, "product-http-v1", self.device_id,
        )
        self.http = httpx.Client(
            timeout=httpx.Timeout(30.0),
            limits=httpx.Limits(max_connections=4, max_keepalive_connections=2),
            headers={
                "X-Ordax-Device-Id": self.device_id,
                "X-Ordax-Device-Token": self.device_token,
                "user-agent": f"OrdaX-Device-Agent/{DEVICE_AGENT_VERSION}",
            },
        )
        self.product_central_session = ProductCentralSessionClient(
            self.base_http_url,
            self.device_id,
            lambda: self.device_token,
        )

    @property
    def is_paired(self) -> bool:
        return True

    def pair_if_needed(self, capabilities: list[str], *, agent_version: str,
                       metadata: dict[str, Any] | None = None) -> bool:
        del capabilities, agent_version, metadata
        return False

    def _runtime_provenance(self) -> dict[str, str]:
        return {
            "runtime_kind": self.runtime_kind,
            "agent_version": self.agent_version,
        }

    def _execution_context(self, job: AgentJob) -> dict[str, Any]:
        required = {
            "job_id": job.id,
            "effect_id": job.effect_id,
            "attempt_id": job.attempt_id,
            "lease_id": job.lease_token,
            "execution_epoch": job.execution_epoch,
            "agent_instance_id": job.agent_instance_id,
            "boot_id": job.boot_id,
            **self._runtime_provenance(),
        }
        if any(value in (None, "") for value in required.values()):
            raise RuntimeError("canonical Product job execution context is incomplete")
        return required

    def _post(self, operation: str, body: dict[str, Any], *, timeout: float = 20.0) -> dict[str, Any]:
        try:
            response = self.http.post(
                f"{self.base_http_url}/v3/device/transport/{operation}",
                json=body,
                timeout=timeout,
            )
        except httpx.HTTPError as error:
            raise TransientDeliveryError(
                f"canonical Product transport failed: {type(error).__name__}: {error}"
            ) from error
        if response.status_code in {401, 403}:
            raise DeviceAuthorizationError("DEVICE_CREDENTIAL_REJECTED")
        if response.status_code in {408, 429, 500, 502, 503, 504}:
            raise TransientDeliveryError(
                f"canonical Product transport HTTP {response.status_code}"
            )
        try:
            payload = response.json()
        except ValueError as error:
            raise RuntimeError("canonical Product transport returned invalid JSON") from error
        if not isinstance(payload, dict):
            raise RuntimeError("canonical Product transport returned a non-object response")
        if response.status_code >= 400:
            raise RuntimeError(
                f"canonical Product transport HTTP {response.status_code}: "
                f"{str(payload.get('error') or 'request_rejected')[:160]}"
            )
        if payload.get("ok") is not True:
            raise RuntimeError(
                "canonical Product transport rejected request: "
                + str(payload.get("error") or "request_rejected")[:160]
            )
        return payload

    def heartbeat(self, actions: list[str], *, agent_version: str,
                  metadata: dict[str, Any] | None = None, status: str = "online",
                  last_error: str | None = None) -> None:
        del metadata, last_error
        self._post("heartbeat", {
            "status": status,
            "agent_version": agent_version,
            "capabilities": list(actions),
        }, timeout=10.0)
        if status != "offline":
            try:
                self.product_central_session.tick()
            except Exception:
                # Session attestation never weakens device transport health.
                pass

    def _bind_project_scope(self, row: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
        capability = str(row.get("capability") or row.get("operation") or "")
        raw_scope = payload.pop("_ordax_project_scope", None)
        if capability not in _PROJECT_SCOPE_ACTIONS:
            if raw_scope is not None:
                raise RuntimeError("canonical Product project scope attestation is unexpected")
            return payload

        project_id = str(row.get("project_id") or "")
        try:
            uuid.UUID(project_id)
        except ValueError as error:
            raise RuntimeError("canonical Product project id is missing or invalid") from error
        if not isinstance(raw_scope, dict):
            raise RuntimeError("canonical Product project scope attestation is missing")

        scope_project_id = str(raw_scope.get("project_id") or "")
        repository_full_name = str(raw_scope.get("repository_full_name") or "")
        if scope_project_id.lower() != project_id.lower():
            raise RuntimeError("canonical Product project scope id mismatch")

        local_project = resolve_project_for_repository(self.config, repository_full_name)
        rebound = dict(payload)
        rebound.pop("project", None)
        rebound["project"] = local_project.slug
        return rebound

    def _job_from_wire(self, row: dict[str, Any]) -> AgentJob:
        payload = self._bind_project_scope(row, decode_job_payload(row))
        action, action_payload, project = dispatch_job(row, payload)
        job = AgentJob(
            id=str(row.get("job_id") or ""),
            action=action,
            payload=action_payload,
            project_slug=project,
            lease_token=str(row.get("lease_id") or ""),
            effect_id=str(row.get("effect_id") or ""),
            attempt_id=str(row.get("attempt_id") or ""),
            execution_epoch=int(row.get("execution_epoch") or 0),
            agent_instance_id=str(row.get("agent_instance_id") or self.agent_instance_id),
            boot_id=str(row.get("boot_id") or self.boot_id),
            payload_sha256=str(row.get("payload_sha256") or ""),
        )
        if job.agent_instance_id != self.agent_instance_id or job.boot_id != self.boot_id:
            raise RuntimeError("canonical Product job runtime identity mismatch")
        self._post("start", self._execution_context(job))
        self._jobs[job.id] = job
        return job

    def claim_next_job(self) -> AgentJob | None:
        self.recover_pending_reports()
        payload = self._post("claim", {
            "agent_instance_id": self.agent_instance_id,
            "boot_id": self.boot_id,
            "lease_seconds": 120,
            **self._runtime_provenance(),
        }, timeout=15.0)
        row = payload.get("job")
        if row is None:
            return None
        if not isinstance(row, dict):
            raise RuntimeError("canonical Product claim returned invalid job")
        return self._job_from_wire(row)

    def append_event(self, job_id: str, level: str, message: str,
                     data: dict[str, Any] | None = None) -> None:
        job = self._jobs.get(job_id)
        if job is None:
            return
        percent = data.get("progress_percent") if isinstance(data, dict) else None
        body = {
            **self._execution_context(job),
            "stage": str(level)[:96] or "info",
            "message": str(message)[:512],
            "progress_percent": percent if isinstance(percent, int) else None,
        }
        self._post("progress", body)

    def renew(self, job: AgentJob) -> dict[str, Any]:
        return self._post("lease", {
            **self._execution_context(job),
            "lease_seconds": 120,
        })

    @staticmethod
    def _canonical_bytes(body: dict[str, Any]) -> bytes:
        return json.dumps(body, sort_keys=True, separators=(",", ":"),
                          ensure_ascii=False).encode("utf-8")

    def _wire_report(self, report: dict[str, Any]) -> dict[str, Any]:
        encoded = report.get("result_canonical_b64")
        digest = str(report.get("result_sha256") or "").lower()
        if not isinstance(encoded, str):
            result = report.get("result")
            if not isinstance(result, dict):
                raise RuntimeError("terminal report has no canonical result")
            raw = self._canonical_bytes(result)
            observed = hashlib.sha256(raw).hexdigest()
            if digest and digest != observed:
                raise RuntimeError("terminal report digest does not match canonical result")
            digest = observed
            encoded = base64.b64encode(raw).decode("ascii")
        return {
            key: report[key] for key in (
                "job_id", "effect_id", "attempt_id", "lease_id", "execution_epoch",
                "agent_instance_id", "boot_id", "runtime_kind", "agent_version",
                "report_id", "status", "error_code",
            ) if key in report
        } | {
            "result_canonical_b64": encoded,
            "result_sha256": digest,
        }

    def _recover_terminal_report(self, report: dict[str, Any]) -> dict[str, Any]:
        return self._post("recover-report", self._wire_report(report), timeout=30.0)

    def recover_pending_reports(self) -> int:
        recovered = 0
        for path, report in self._terminal_outbox.pending():
            self._recover_terminal_report(report)
            self._terminal_outbox.acknowledge(path)
            self._jobs.pop(str(report.get("job_id") or ""), None)
            recovered += 1
        return recovered

    def complete(self, job: AgentJob, result: ActionResult) -> None:
        body, digest = canonical_result(result)
        raw = self._canonical_bytes(body)
        if hashlib.sha256(raw).hexdigest() != digest:
            raise RuntimeError("canonical result digest invariant failed")
        report = {
            **self._execution_context(job),
            "report_id": str(uuid.uuid4()),
            "status": "succeeded" if result.ok else "failed",
            "result_canonical_b64": base64.b64encode(raw).decode("ascii"),
            "result_sha256": digest,
            "error_code": None if result.ok else "device_agent_action_failed",
        }
        outbox_path = self._terminal_outbox.persist(report)
        for attempt in range(3):
            try:
                self._post("report", report, timeout=30.0)
                break
            except TransientDeliveryError:
                if attempt == 2:
                    raise
                time.sleep(2 ** attempt)
        self._terminal_outbox.acknowledge(outbox_path)
        self._jobs.pop(job.id, None)

    def upload_artifact(self, *args, **kwargs):
        del args, kwargs
        raise RuntimeError(
            "Canonical Product artifact transport is not enabled; refusing legacy D1 artifact fallback."
        )

    def record_product_audit(self, event) -> None:
        del event
        raise RuntimeError(
            "Canonical Product audit transport is not enabled; refusing legacy operator/D1 audit fallback."
        )
