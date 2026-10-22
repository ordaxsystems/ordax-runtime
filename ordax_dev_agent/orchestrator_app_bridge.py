"""Private host-to-App read channel; not an MCP server or grant authority.

The launch owner supplies an inherited child pipe and an independently pinned
App/build/profile identity. Product OAuth/grants MUST be resolved before every
call by ``authorize``. This module never grants, starts a process, opens a port,
discovers an App visually, reads its SQLite, or retries an uncertain exchange.
It is not registered in the production action catalog until the Platform
capability and managed App launcher are implemented and proven.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import hmac
from importlib.resources import files
import json
import secrets
import queue
import subprocess
import threading
import time
import uuid
from typing import Any, BinaryIO, Callable

SCHEMA = "ordax.orchestrator-host/1"
READ_CAPABILITY = "orchestrator.app_read"
APP_ID = "br.com.ordax.orchestrator.lab"
LEASE_RENEWAL = "hmac-sha256-key-rotation/1"
_CONTRACT_BYTES = files("ordax_dev_agent.assets").joinpath("orchestrator_app_contract.json").read_bytes()
_CONTRACT = json.loads(_CONTRACT_BYTES)
READ_OPERATIONS = frozenset(tool["operation"] for tool in _CONTRACT["tools"] if tool["readOnly"])
CONTRACT_SHA256 = hashlib.sha256(_CONTRACT_BYTES).hexdigest()


class OrchestratorBridgeUnavailable(RuntimeError):
    """No fallback or resend is implied by an unavailable/uncertain exchange."""


def _canonical(value: Any) -> bytes:
    def validate(item: Any) -> None:
        if item is None or type(item) in (str, bool):
            return
        if type(item) is int and abs(item) <= 9007199254740991:
            return
        if type(item) is list:
            for child in item:
                validate(child)
            return
        if type(item) is dict and all(isinstance(key, str) for key in item):
            for child in item.values():
                validate(child)
            return
        raise OrchestratorBridgeUnavailable("host_input_invalid")
    validate(value)
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _uuid(value: Any) -> bool:
    try:
        return isinstance(value, str) and str(uuid.UUID(value)) == value.lower()
    except (ValueError, AttributeError):
        return False


def _hex(value: Any, length: int) -> bool:
    return isinstance(value, str) and len(value) == length and all(c in "0123456789abcdef" for c in value)


@dataclass(frozen=True, slots=True)
class VerifiedOrchestratorReadAuthorization:
    """Trusted host result of Product authorization, never parsed from tool input.

    Dedicated, device+app-scoped read authority only. A generic computer grant,
    project label, Lab persona or app manifest cannot supply this value.
    """
    subject_id: str
    client_id: str
    device_id: str
    grant_id: str
    expires_at_ms: int
    capability: str = READ_CAPABILITY
    app_id: str = APP_ID


@dataclass(frozen=True, slots=True)
class ExpectedOrchestratorIdentity:
    source_commit: str
    content_digest: str
    profile_fingerprint: str


def require_orchestrator_read_authorization(authorize, clock) -> VerifiedOrchestratorReadAuthorization:
    try:
        value = authorize()
    except Exception:
        raise OrchestratorBridgeUnavailable("host_authorization_required") from None
    if not isinstance(value, VerifiedOrchestratorReadAuthorization) or value.capability != READ_CAPABILITY or value.app_id != APP_ID or not all(_uuid(v) for v in (value.subject_id, value.device_id, value.grant_id)) or not isinstance(value.client_id, str) or not 8 <= len(value.client_id) <= 160 or not value.client_id[0].isascii() or not value.client_id[0].isalnum() or any(not(c.isascii() and (c.isalnum() or c in "._:-")) for c in value.client_id) or type(value.expires_at_ms) is not int or value.expires_at_ms <= clock():
        raise OrchestratorBridgeUnavailable("host_authorization_required")
    return value


class InheritedOrchestratorPipe:
    """Exchange on a child created and byte-pinned by the owning launcher.

    No executable, endpoint, process ID or path is accepted from a tool. This
    object holds the dedicated data-pipe ends supplied by that launcher and the
    actual Popen child, not a discovered process. The packaged App reads fd 3
    and writes fd 4; its stdin/stdout/stderr are never protocol transports. On timeout it is permanently poisoned; the host owns child disposal.
    """
    def __init__(self, child: subprocess.Popen, *, request_pipe: BinaryIO,
                 response_pipe: BinaryIO, timeout_seconds: float = 30.0) -> None:
        if not isinstance(child, subprocess.Popen) or not callable(getattr(request_pipe, "write", None)) or not callable(getattr(request_pipe, "flush", None)) or not callable(getattr(response_pipe, "readline", None)) or child.poll() is not None or type(timeout_seconds) not in (int, float) or not 0 < timeout_seconds <= 30:
            raise OrchestratorBridgeUnavailable("host_private_pipe_required")
        self._child = child
        self._request_pipe, self._response_pipe = request_pipe, response_pipe
        self._timeout = timeout_seconds
        self._closed = False
        self._lock = threading.Lock()

    def close(self) -> None:
        # Never kill a human App, reopen handles or replay from this adapter.
        self._closed = True

    def __call__(self, message: dict[str, Any]) -> dict[str, Any]:
        if self._closed or not self._lock.acquire(blocking=False):
            raise OrchestratorBridgeUnavailable("host_private_pipe_unavailable")
        try:
            encoded = _canonical(message) + b"\n"
            if len(encoded) > 65536 or self._child.poll() is not None:
                raise OrchestratorBridgeUnavailable("host_private_pipe_unavailable")
            result: queue.Queue = queue.Queue(maxsize=1)
            def exchange() -> None:
                try:
                    self._request_pipe.write(encoded)
                    self._request_pipe.flush()
                    raw = self._response_pipe.readline(524289)
                    if not raw.endswith(b"\n") or len(raw) > 524288:
                        raise ValueError("invalid frame")
                    decoded = json.loads(raw.decode("utf-8", errors="strict"))
                    if type(decoded) is not dict:
                        raise ValueError("invalid frame")
                    result.put((True, decoded))
                except Exception:
                    result.put((False, None))
            threading.Thread(target=exchange, daemon=True, name="ordax-orchestrator-private-read").start()
            ok, reply = result.get(timeout=self._timeout)
            if not ok:
                raise OrchestratorBridgeUnavailable("host_private_pipe_unavailable")
            return reply
        except Exception:
            self.close()
            raise OrchestratorBridgeUnavailable("host_private_pipe_unavailable") from None
        finally:
            self._lock.release()


class OrchestratorAppReadClient:
    """One bounded private-pipe session, with fresh authorization for each read.

    ``exchange`` is a launcher-owned inherited pipe, not an arbitrary URL from
    an action. The launcher must pin executable/ASAR bytes BEFORE starting the
    child; the returned App proof is an additional identity check, not signing.
    Each exchange must have a host-enforced deadline and correlate one reply.
    """
    def __init__(self, *, exchange: Callable[[dict[str, Any]], dict[str, Any]],
                 authorize: Callable[[], VerifiedOrchestratorReadAuthorization | None],
                 expected: ExpectedOrchestratorIdentity,
                 clock: Callable[[], int] = lambda: int(time.time() * 1000)) -> None:
        if not callable(exchange) or not callable(authorize) or not callable(clock) or not isinstance(expected, ExpectedOrchestratorIdentity):
            raise OrchestratorBridgeUnavailable("host_ports_required")
        if not _hex(expected.source_commit, 40) or not _hex(expected.content_digest, 64) or not _hex(expected.profile_fingerprint, 64):
            raise OrchestratorBridgeUnavailable("host_expected_identity_required")
        self._exchange, self._authorize, self._clock = exchange, authorize, clock
        self._expected = expected
        self._lock = threading.Lock()
        self._closed = False
        self._key = bytearray(secrets.token_bytes(32))
        self._session_id = str(uuid.uuid4())
        try:
            self._authorization = self._require_authorization()
        except Exception:
            self.close()
            raise OrchestratorBridgeUnavailable("host_authorization_required") from None
        self._expires_at = min(self._authorization.expires_at_ms, clock() + 300000)
        import base64
        bootstrap = {
            "schema": SCHEMA, "type": "bootstrap", "sessionId": self._session_id,
            "key": base64.b64encode(self._key).decode("ascii"),
            "ownerUserId": self._authorization.subject_id, "clientId": self._authorization.client_id,
            "deviceId": self._authorization.device_id, "grantId": self._authorization.grant_id,
            "capability": READ_CAPABILITY, "expiresAt": self._expires_at,
            "expectedCommit": expected.source_commit, "expectedDigest": expected.content_digest,
            "expectedProfile": expected.profile_fingerprint,
        }
        try:
            reply = exchange(bootstrap)
            if not isinstance(reply, dict) or reply.get("schema") != SCHEMA or reply.get("type") != "ready" or reply.get("ok") is not True or reply.get("sessionId") != self._session_id or reply.get("sourceCommit") != expected.source_commit or reply.get("contentDigest") != expected.content_digest or reply.get("profileFingerprint") != expected.profile_fingerprint or reply.get("capability") != READ_CAPABILITY or not isinstance(reply.get("operations"), list) or frozenset(reply["operations"]) != READ_OPERATIONS:
                raise OrchestratorBridgeUnavailable("host_app_identity_mismatch")
            self._require_same_authorization()
            self._can_renew = reply.get("leaseRenewal") == LEASE_RENEWAL
        except Exception:
            self.close()
            raise OrchestratorBridgeUnavailable("host_bootstrap_unconfirmed") from None

    def _require_authorization(self) -> VerifiedOrchestratorReadAuthorization:
        return require_orchestrator_read_authorization(self._authorize, self._clock)

    def _require_same_authorization(self) -> VerifiedOrchestratorReadAuthorization:
        if self._closed:
            raise OrchestratorBridgeUnavailable("host_session_closed")
        current = self._require_authorization()
        if self._closed:
            raise OrchestratorBridgeUnavailable("host_session_closed")
        previous = self._authorization
        if (current.subject_id, current.client_id, current.device_id, current.grant_id) != (previous.subject_id, previous.client_id, previous.device_id, previous.grant_id):
            raise OrchestratorBridgeUnavailable("host_authority_changed")
        if self._clock() >= self._expires_at:
            raise OrchestratorBridgeUnavailable("host_session_expired")
        return current

    def close(self) -> None:
        self._closed = True
        for index in range(len(self._key)):
            self._key[index] = 0

    def renew(self) -> int:
        """Extend an unexpired lease using fresh same-caller Product authority.

        Explicit host operation, never a tool or automatic retry. A missing or
        uncertain acknowledgement permanently closes this client. Key rotation
        lets the App bound replay memory across arbitrarily many leases.
        """
        if self._closed:
            raise OrchestratorBridgeUnavailable("host_session_closed")
        if not self._lock.acquire(blocking=False):
            raise OrchestratorBridgeUnavailable("host_exchange_busy")
        next_key = bytearray()
        try:
            authorization = self._require_same_authorization()
            if not self._can_renew:
                raise OrchestratorBridgeUnavailable("host_renewal_unsupported")
            now = self._clock()
            expires_at = min(authorization.expires_at_ms, now + 300000)
            if expires_at <= self._expires_at:
                raise OrchestratorBridgeUnavailable("host_lease_extension_required")
            next_key = bytearray(secrets.token_bytes(32))
            import base64
            request = {"schema": SCHEMA, "sessionId": self._session_id,
                       "id": str(uuid.uuid4()), "operation": "$lease_renew",
                       "input": {"key": base64.b64encode(next_key).decode("ascii"),
                                 "expiresAt": expires_at},
                       "issuedAt": now,
                       "expiresAt": min(now + 30000, self._expires_at, authorization.expires_at_ms)}
            request["signature"] = hmac.new(self._key, _canonical(request), hashlib.sha256).hexdigest()
            reply = self._exchange(request)
            current = self._require_same_authorization()
            if self._clock() >= request["expiresAt"] or current.expires_at_ms < expires_at:
                raise OrchestratorBridgeUnavailable("host_renewal_expired")
            if (type(reply) is not dict or reply.get("schema") != SCHEMA
                    or reply.get("id") != request["id"] or reply.get("ok") is not True
                    or type(reply.get("result")) is not dict
                    or reply["result"] != {"leaseRenewal": LEASE_RENEWAL, "expiresAt": expires_at}):
                raise OrchestratorBridgeUnavailable("host_renewal_unconfirmed")
            self._key[:] = next_key
            self._expires_at = expires_at
            return expires_at
        except Exception:
            self.close()
            raise OrchestratorBridgeUnavailable("host_renewal_unavailable") from None
        finally:
            for index in range(len(next_key)):
                next_key[index] = 0
            self._lock.release()

    def read(self, operation: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        if self._closed:
            raise OrchestratorBridgeUnavailable("host_session_closed")
        if operation not in READ_OPERATIONS or (arguments is not None and type(arguments) is not dict):
            raise OrchestratorBridgeUnavailable("host_read_only")
        if not self._lock.acquire(blocking=False):
            raise OrchestratorBridgeUnavailable("host_exchange_busy")
        try:
            authorization = self._require_same_authorization()
            now = self._clock()
            request = {"schema": SCHEMA, "sessionId": self._session_id, "id": str(uuid.uuid4()),
                       "operation": operation, "input": arguments if arguments is not None else {},
                       "issuedAt": now, "expiresAt": min(now + 30000, self._expires_at, authorization.expires_at_ms)}
            encoded = _canonical(request)
            if len(encoded) > 32768:
                raise OrchestratorBridgeUnavailable("host_request_too_large")
            request["signature"] = hmac.new(self._key, encoded, hashlib.sha256).hexdigest()
            reply = self._exchange(request)
            self._require_same_authorization()
            if self._clock() >= request["expiresAt"]:
                raise OrchestratorBridgeUnavailable("host_request_expired")
            if type(reply) is not dict or len(_canonical(reply)) > 524288 or reply.get("schema") != SCHEMA or reply.get("id") != request["id"] or reply.get("ok") is not True or type(reply.get("result")) is not dict:
                raise OrchestratorBridgeUnavailable("host_read_unconfirmed")
            return reply["result"]
        except Exception:
            # No uncertain read is retried, and stale authority cannot return
            # a late result. A new session requires the owning host to rebind.
            self.close()
            raise OrchestratorBridgeUnavailable("host_read_unavailable") from None
        finally:
            self._lock.release()

