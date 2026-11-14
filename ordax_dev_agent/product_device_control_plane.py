from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import time
import uuid
from typing import Any, Callable

import httpx

from ordax_connector.capabilities import canonical_capabilities, capability_digest

from . import __version__ as DEVICE_AGENT_VERSION
from .config import AgentConfig
from .device_credentials import resolve_token_path
from .models import ActionResult, AgentJob
from .product_central_session import ProductCentralSessionClient
from .projects import canonical_github_repository_full_name, resolve_project_for_repository
from .remote_protocol import (
    DeviceAuthorizationError,
    TransientDeliveryError,
    canonical_result,
    postgres_safe_result,
    decode_job_payload,
    dispatch_job,
    read_secret,
)
from .terminal_outbox import TerminalOutbox
from .product_ingress import TrustedProductIngress


_PROJECT_SCOPE_ACTIONS = frozenset({
    "agent.project_briefing",
    "continuity.get",
    "continuity.update",
    "handoff.create",
    "handoff.get",
    # Project terminal/process actions are never authorized by a model-supplied
    # local slug. The canonical repository binding must be verified first.
    "terminal.exec",
    "process.start",
    "process.status",
    "process.list",
    "process.logs",
    "process.write_stdin",
    "process.stop",
})

_PROJECT_BINDING_CONFIRM_ERRORS = frozenset({
    "project_device_binding_confirmation_invalid",
    "project_device_binding_intent_not_active",
    "project_device_binding_authority_changed",
    "github_repository_changed",
    "project_device_binding_conflict",
})
_LOCAL_PROJECT_REF = re.compile(r"^project-[1-9][0-9]*$")


def _canonical_uuid(value: object) -> bool:
    if not isinstance(value, str):
        return False
    try:
        return str(uuid.UUID(value)) == value
    except (AttributeError, TypeError, ValueError):
        return False


def _canonical_project_binding_links(value: object) -> list[dict[str, str]]:
    """Validate only the OS-owned explicit Project Catalog link projection."""
    if not isinstance(value, dict) or set(value) != {
        "schema", "host", "persistence", "links",
    }:
        raise RuntimeError("Native Project link snapshot is invalid")
    if (
        value.get("schema") != "ordax.desktop-project-links/1"
        or value.get("host") != "ordax-desktop"
        or value.get("persistence") != "device"
        or not isinstance(value.get("links"), list)
        or len(value["links"]) > 32
    ):
        raise RuntimeError("Native Project link snapshot is invalid")
    local_seen: set[str] = set()
    cloud_seen: set[str] = set()
    normalized: list[dict[str, str]] = []
    for raw in value["links"]:
        if not isinstance(raw, dict) or set(raw) != {
            "localProjectId", "cloudProjectId", "spaceId",
        }:
            raise RuntimeError("Native Project link is invalid")
        local_id = raw.get("localProjectId")
        cloud_id = raw.get("cloudProjectId")
        space_id = raw.get("spaceId")
        if (
            not isinstance(local_id, str)
            or _LOCAL_PROJECT_REF.fullmatch(local_id) is None
            or not _canonical_uuid(cloud_id)
            or not _canonical_uuid(space_id)
            or local_id in local_seen
            or cloud_id in cloud_seen
        ):
            raise RuntimeError("Native Project link is invalid")
        local_seen.add(local_id)
        cloud_seen.add(cloud_id)
        normalized.append({
            "localProjectId": local_id,
            "cloudProjectId": cloud_id,
            "spaceId": space_id,
        })
    return normalized


def _canonical_project_binding_intents(
    value: object, device_id: str,
) -> list[dict[str, str]]:
    if not isinstance(value, dict) or set(value) != {
        "ok", "schema", "device_id", "intents",
    }:
        raise RuntimeError("Project binding intents response is invalid")
    intents = value.get("intents")
    if (
        value.get("ok") is not True
        or value.get("schema") != "ordax.device-project-binding-intents/1"
        or value.get("device_id") != device_id
        or not isinstance(intents, list)
        or len(intents) > 16
    ):
        raise RuntimeError("Project binding intents response is invalid")
    seen: set[str] = set()
    normalized: list[dict[str, str]] = []
    for raw in intents:
        if not isinstance(raw, dict) or set(raw) != {
            "intent_id", "project_id", "space_id",
            "repository_full_name", "expires_at",
        }:
            raise RuntimeError("Project binding intent is invalid")
        intent_id = raw.get("intent_id")
        project_id = raw.get("project_id")
        space_id = raw.get("space_id")
        repository = raw.get("repository_full_name")
        expires_at = raw.get("expires_at")
        canonical_repository = (
            canonical_github_repository_full_name(repository)
            if isinstance(repository, str) else None
        )
        if (
            not _canonical_uuid(intent_id)
            or intent_id in seen
            or not _canonical_uuid(project_id)
            or not _canonical_uuid(space_id)
            or canonical_repository != repository
            or not isinstance(expires_at, str)
            or not expires_at
        ):
            raise RuntimeError("Project binding intent is invalid")
        seen.add(intent_id)
        normalized.append({
            "intent_id": intent_id,
            "project_id": project_id,
            "space_id": space_id,
            "repository_full_name": repository,
            "expires_at": expires_at,
        })
    return normalized


def _validate_project_binding_confirmation(
    value: object, *, intent: dict[str, str], device_id: str,
) -> bool:
    if not isinstance(value, dict):
        raise RuntimeError("Project binding confirmation response is invalid")
    if value.get("ok") is False:
        if set(value) == {"ok", "error"} and value.get("error") in _PROJECT_BINDING_CONFIRM_ERRORS:
            return False
        raise RuntimeError("Project binding confirmation response is invalid")
    if set(value) != {
        "ok", "schema", "intent_id", "binding_id", "project_id",
        "space_id", "device_id", "state", "next_state",
    }:
        raise RuntimeError("Project binding confirmation response is invalid")
    if (
        value.get("ok") is not True
        or value.get("schema") != "ordax.device-project-binding-confirmed/1"
        or value.get("intent_id") != intent["intent_id"]
        or not _canonical_uuid(value.get("binding_id"))
        or value.get("project_id") != intent["project_id"]
        or value.get("space_id") != intent["space_id"]
        or value.get("device_id") != device_id
        or value.get("state") != "active"
        or value.get("next_state") != "ready_without_grants"
    ):
        raise RuntimeError("Project binding confirmation response is invalid")
    return True


class ProductDeviceControlPlane:
    """Canonical device transport over the Product MCP PostgreSQL authority.

    This transport intentionally has no WebSocket/D1/operator-token fallback.
    Device identity, presence, action queue, leases and terminal reports are
    authorized by the device credential and persisted by the Platform RPCs.
    """

    uses_long_poll = False
    runtime_kind = "desktop-agent"

    def __init__(
        self,
        config: AgentConfig,
        *,
        device_credential_provider: Callable[[], dict[str, str] | None] | None = None,
        project_binding_provider: Callable[[], dict[str, Any] | None] | None = None,
    ):
        if not config.product_mcp_url:
            raise RuntimeError("ORDAX_PRODUCT_MCP_URL is required for the canonical device transport.")
        if not config.device_id:
            raise RuntimeError("ORDAX_DEVICE_ID is required for the canonical device transport.")
        self.config = config
        if project_binding_provider is not None and not callable(project_binding_provider):
            raise TypeError("Project binding provider must be callable")
        self._project_binding_provider = project_binding_provider
        # A timed-out confirmation is never replayed blindly. The next
        # reconciliation must first re-read the authoritative pending intents.
        self._uncertain_project_binding_confirmations: set[str] = set()
        self.device_id = str(config.device_id)
        try:
            uuid.UUID(self.device_id)
        except ValueError as error:
            raise RuntimeError("ORDAX_DEVICE_ID must be a UUID.") from error
        # The native callback is owned by the existing OS identity store. It
        # must be rechecked before transport: an enrolled credential can be
        # rotated or revoked while this Runtime process is still alive.
        self._native_credential_provider = device_credential_provider
        if device_credential_provider is None:
            # Windows product Runtime retains the existing canonical token file.
            self.device_token = read_secret(resolve_token_path(config.state_dir))
        else:
            # The Native OS already owns this same identity; do not write a
            # second token file, and never fall back if the provider denies.
            if not callable(device_credential_provider):
                raise TypeError("Device credential provider must be callable")
            credential = device_credential_provider()
            if (
                not isinstance(credential, dict)
                or set(credential) != {"device_id", "device_token"}
                or credential.get("device_id") != self.device_id
            ):
                raise RuntimeError("Native Product device credential identity mismatch.")
            self.device_token = credential["device_token"]
            if (
                str(uuid.UUID(self.device_id)) != self.device_id
                or not isinstance(self.device_token, str)
                or re.fullmatch(r"[0-9a-f]{64}", self.device_token) is None
            ):
                raise RuntimeError("Native Product device credential is not canonical.")
        if not isinstance(self.device_token, str) or not 32 <= len(self.device_token) <= 512:
            raise RuntimeError("Canonical Product device token is missing or invalid.")
        # Platform provenance is tied to the trusted credential source, never
        # to a job payload, MCP client or Surface preference.
        self.runtime_kind = "ordax-os" if device_credential_provider is not None else "desktop-agent"
        self.base_http_url = str(config.product_mcp_url).rstrip("/")
        self._trusted_ingress = TrustedProductIngress(self.base_http_url)
        self.agent_instance_id = str(uuid.uuid4())
        self.boot_id = str(uuid.uuid4())
        # Execution provenance is immutable for this process. Heartbeat already
        # publishes the same package version; carrying it with every fenced
        # action lets Platform persist the version that actually performed the
        # effect rather than attributing old actions to current presence later.
        self.agent_version = str(DEVICE_AGENT_VERSION)
        self._heartbeat_capabilities: tuple[str, ...] | None = None
        self._heartbeat_capability_digest: str | None = None
        self._remote_heartbeat_recorded = False
        self._jobs: dict[str, AgentJob] = {}
        self._terminal_outbox = TerminalOutbox(
            config.state_dir, "product-http-v1", self.device_id,
        )
        self.http = httpx.Client(
            timeout=httpx.Timeout(30.0),
            follow_redirects=False,
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
            self._current_session_device_token,
        )

    @property
    def is_paired(self) -> bool:
        # A loaded token never implies accepted remote presence.
        return self._remote_heartbeat_recorded

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

    def _assert_native_credential_current(self) -> None:
        """Refuse stale native identity before the canonical Product transport.

        A changed/revoked OS credential must terminate this Runtime instance,
        not silently keep the old token or read a Windows fallback token file.
        The existing Supervisor can restart it and reacquire the new identity.
        """
        provider = self._native_credential_provider
        if provider is None:
            return  # Windows continues to use its existing managed credential.
        try:
            credential = provider()
            if (
                not isinstance(credential, dict)
                or set(credential) != {"device_id", "device_token"}
                or credential.get("device_id") != self.device_id
                or not isinstance(credential.get("device_token"), str)
                or re.fullmatch(r"[0-9a-f]{64}", credential["device_token"]) is None
                or not hmac.compare_digest(credential["device_token"], self.device_token)
            ):
                raise DeviceAuthorizationError("NATIVE_DEVICE_CREDENTIAL_CHANGED")
        except DeviceAuthorizationError:
            raise
        except Exception:
            # Never leak credentials, file paths, or identity-store internals.
            raise DeviceAuthorizationError("NATIVE_DEVICE_CREDENTIAL_UNAVAILABLE") from None

    def _current_session_device_token(self) -> str:
        # Central-session attestation must not bypass the same OS identity
        # revalidation as the Product action/heartbeat transport.
        self._assert_native_credential_current()
        return self.device_token

    def _post(
        self,
        operation: str,
        body: dict[str, Any],
        *,
        timeout: float = 20.0,
        allow_not_found: bool = False,
        accepted_errors: frozenset[str] = frozenset(),
    ) -> dict[str, Any] | None:
        self._assert_native_credential_current()
        try:
            response = self._trusted_ingress.request(
                f"/v3/device/transport/{operation}",
                lambda url: self.http.post(url, json=body, timeout=timeout),
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
        error_code = str(payload.get("error") or "")
        if (
            response.status_code == 404
            and allow_not_found
            and payload.get("ok") is False
            and error_code == "not_found"
        ):
            return None
        if response.status_code >= 400:
            if payload.get("ok") is False and error_code in accepted_errors:
                return payload
            raise RuntimeError(
                f"canonical Product transport HTTP {response.status_code}: "
                f"{error_code or 'request_rejected'}"
            )
        if payload.get("ok") is not True:
            raise RuntimeError(
                "canonical Product transport rejected request: "
                + str(payload.get("error") or "request_rejected")[:160]
            )
        return payload

    def _capabilities_digest(self, actions: list[str]) -> str:
        canonical = canonical_capabilities(actions)
        if canonical != self._heartbeat_capabilities:
            self._heartbeat_capabilities = canonical
            self._heartbeat_capability_digest = capability_digest(canonical)
        if self._heartbeat_capability_digest is None:
            raise RuntimeError("connector capability digest is unavailable")
        return self._heartbeat_capability_digest

    def heartbeat(self, actions: list[str], *, agent_version: str,
                  metadata: dict[str, Any] | None = None, status: str = "online",
                  last_error: str | None = None) -> None:
        del metadata, last_error
        payload = {
            "status": status,
            "agent_version": agent_version,
            "capabilities_digest": self._capabilities_digest(actions),
        }
        if self.runtime_kind == "ordax-os":
            # Existing Windows and legacy heartbeat payloads remain unchanged.
            payload["runtime_kind"] = self.runtime_kind
        self._post("heartbeat", payload, timeout=10.0)
        self._remote_heartbeat_recorded = status != "offline"
        if status != "offline":
            try:
                self.product_central_session.schedule_tick()
            except Exception:
                # Session attestation never weakens device transport health.
                pass

    def reconcile_project_bindings(self) -> int:
        """Confirm only OS Project Catalog links for current Platform intents.

        The repository carried by the intent is validated but is deliberately
        not used to derive a local identity. The only accepted local ref is an
        explicit durable OS link matching both cloud Project and Space.
        """
        provider = self._project_binding_provider
        if provider is None:
            return 0
        raw = self._post(
            "project-binding-intents",
            {},
            timeout=10.0,
            allow_not_found=True,
            accepted_errors=frozenset({"device_not_active"}),
        )
        if raw is None:
            return 0
        if raw.get("ok") is False:
            if raw == {"ok": False, "error": "device_not_active"}:
                return 0
            raise RuntimeError("Project binding intents response is invalid")
        intents = _canonical_project_binding_intents(raw, self.device_id)
        pending_ids = {item["intent_id"] for item in intents}
        # Absence after an uncertain response is authoritative evidence that
        # the previous request consumed/invalidated the intent. Presence proves
        # it is safe to retry only after this fresh read.
        self._uncertain_project_binding_confirmations.intersection_update(pending_ids)
        if not intents:
            return 0

        snapshot = provider()
        if snapshot is None:
            return 0
        links = _canonical_project_binding_links(snapshot)
        by_scope = {
            (link["cloudProjectId"], link["spaceId"]): link["localProjectId"]
            for link in links
        }
        confirmed = 0
        for intent in intents:
            local_ref = by_scope.get((intent["project_id"], intent["space_id"]))
            if local_ref is None:
                continue
            try:
                result = self._post(
                    "project-binding-confirm",
                    {
                        "intent_id": intent["intent_id"],
                        "local_project_ref": local_ref,
                        "confirm": "bind_local_project",
                    },
                    timeout=10.0,
                    accepted_errors=_PROJECT_BINDING_CONFIRM_ERRORS,
                )
            except TransientDeliveryError:
                self._uncertain_project_binding_confirmations.add(intent["intent_id"])
                continue
            if result is None:
                raise RuntimeError("Project binding confirmation response is invalid")
            self._uncertain_project_binding_confirmations.discard(intent["intent_id"])
            if _validate_project_binding_confirmation(
                result, intent=intent, device_id=self.device_id,
            ):
                confirmed += 1
        return confirmed

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

    def orchestrator_read_authorizer(self, job: AgentJob):
        """Live Product authority for one claimed, exactly fenced App-read job.

        This port neither registers an action nor launches an App. Installation
        provenance remains an independent prerequisite of the managed launcher.
        The model cannot supply subject/client/grant or a serialized receipt.
        """
        from .orchestrator_app_bridge import (
            OrchestratorBridgeUnavailable, VerifiedOrchestratorReadAuthorization,
            require_orchestrator_read_authorization,
        )
        fields = frozenset({"job_id", "effect_id", "attempt_id", "lease_id",
                            "execution_epoch", "agent_instance_id", "boot_id"})
        def fence():
            if (not isinstance(job, AgentJob) or self._jobs.get(job.id) is not job
                    or job.action != "forge.app_read" or job.project_slug is not None):
                raise OrchestratorBridgeUnavailable("host_product_action_required")
            return {key: value for key, value in self._execution_context(job).items() if key in fields}
        expected_fence = fence()
        expected_device = self.device_id
        def authorize():
            if self.device_id != expected_device or fence() != expected_fence:
                raise OrchestratorBridgeUnavailable("host_product_action_changed")
            reply = self._post("orchestrator-read-authorization", dict(expected_fence))
            if self.device_id != expected_device or fence() != expected_fence:
                raise OrchestratorBridgeUnavailable("host_product_action_changed")
            value = reply.get("authorization")
            expected_keys = {"schema", "subject_id", "client_id", "device_id", "grant_id",
                             "app_id", "capability", "expires_at_ms"}
            now = int(time.time() * 1000)
            if (reply.get("ok") is not True or type(value) is not dict
                    or set(value) != expected_keys
                    or value.get("schema") != "ordax.forge-read-authorization/1"
                    or value.get("device_id") != expected_device
                    or type(value.get("expires_at_ms")) is not int
                    or value["expires_at_ms"] > now + 300000):
                raise OrchestratorBridgeUnavailable("host_product_authorization_invalid")
            authority = VerifiedOrchestratorReadAuthorization(**{key: item for key, item in value.items() if key != "schema"})
            return require_orchestrator_read_authorization(lambda: authority, lambda: now)
        return authorize

    @staticmethod
    def _canonical_bytes(body: dict[str, Any]) -> bytes:
        return json.dumps(body, sort_keys=True, separators=(",", ":"),
                          ensure_ascii=False).encode("utf-8")

    def _wire_report(self, report: dict[str, Any]) -> dict[str, Any]:
        encoded = report.get("result_canonical_b64")
        digest = str(report.get("result_sha256") or "").lower()
        if isinstance(encoded, str):
            # Verify the saved bytes *before* any legacy repair. A malformed or
            # tampered outbox must never be silently re-signed.
            try:
                raw = base64.b64decode(encoded, validate=True)
                if hashlib.sha256(raw).hexdigest() != digest:
                    raise ValueError("digest mismatch")
                result = json.loads(raw.decode("utf-8"))
            except (ValueError, UnicodeDecodeError) as error:
                raise RuntimeError("terminal report canonical integrity failed") from error
            if not isinstance(result, dict):
                raise RuntimeError("terminal report canonical result is not an object")
        else:
            result = report.get("result")
            if not isinstance(result, dict):
                raise RuntimeError("terminal report has no canonical result")
            raw = self._canonical_bytes(result)
            observed = hashlib.sha256(raw).hexdigest()
            if digest and digest != observed:
                raise RuntimeError("terminal report digest does not match canonical result")
            digest = observed
        # Previously persisted output may contain JSON-escaped U+0000. PostgreSQL
        # JSONB cannot store it, which otherwise leaves recover-report retrying
        # forever as HTTP 500. Only rewrite reports whose decoded data needs it.
        safe_result = postgres_safe_result(result)
        if safe_result != result:
            raw = self._canonical_bytes(safe_result)
            digest = hashlib.sha256(raw).hexdigest()
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
            # The bounded canonicalizer can downgrade an undeliverable result
            # to a non-success receipt, even if the action originally returned
            # ok=True. Never report a success while its output is unavailable.
            "status": "succeeded" if body["ok"] else "failed",
            "result_canonical_b64": base64.b64encode(raw).decode("ascii"),
            "result_sha256": digest,
            "error_code": None if body["ok"] else "device_agent_action_failed",
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
