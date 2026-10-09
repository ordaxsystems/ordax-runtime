"""Single-shot canonical Product device-presence transport.

This is a *consumer* of the Platform PostgreSQL authority, not an identity
provider or heartbeat scheduler. Its call site must obtain an explicitly
verified PostgreSQL device UUID and device-scoped credential; the legacy
Cloudflare-v3 WebSocket token is NEVER guessed or imported here.
"""
from __future__ import annotations

import json
import math
import re
import time
import uuid
from dataclasses import dataclass
from urllib.parse import urlsplit

import httpx

PRESENCE_PATH = "/v3/product/device/presence"
_MAX_RESPONSE_BYTES = 8192
_RUNTIME_KINDS = frozenset({"ordax-os", "desktop-agent", "mobile-client", "other"})
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")


class ProductPresenceError(RuntimeError):
    """Sanitized device-presence failure; never contains tokens/backend bodies."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class ProductPresenceSnapshot:
    online: bool
    runtime_kind: str | None
    agent_version: str | None
    capability_digest: str | None

    def as_wire(self) -> dict[str, object]:
        if type(self.online) is not bool:
            raise ValueError("online must be a boolean")
        if self.runtime_kind is not None and (
            type(self.runtime_kind) is not str or self.runtime_kind not in _RUNTIME_KINDS
        ):
            raise ValueError("runtime_kind not allowed")
        if self.agent_version is not None and (
            type(self.agent_version) is not str
            or not 1 <= len(self.agent_version.encode("utf-16-le", errors="surrogatepass")) // 2 <= 80
            or self.agent_version.strip() != self.agent_version
            or any(ord(ch) < 32 or ord(ch) == 127 for ch in self.agent_version)
        ):
            raise ValueError("agent_version not allowed")
        if self.capability_digest is not None and (
            type(self.capability_digest) is not str
            or _DIGEST.fullmatch(self.capability_digest) is None
        ):
            raise ValueError("capability_digest must be lowercase SHA-256")
        return {
            "online": self.online,
            "runtime_kind": self.runtime_kind,
            "agent_version": self.agent_version,
            "capability_digest": self.capability_digest,
        }


@dataclass(frozen=True)
class ProductPresenceReceipt:
    device_id: str
    changed: bool


def _uuid(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("Canonical Product device UUID is required")
    try:
        parsed = uuid.UUID(value)
    except (ValueError, AttributeError) as exc:
        raise ValueError("Canonical Product device UUID is invalid") from exc
    if str(parsed) != value:
        raise ValueError("Canonical Product device UUID must be normalized")
    return value


def _token(value: str) -> str:
    if not isinstance(value, str) or not 32 <= len(value) <= 512:
        raise ValueError("Canonical Product device credential is required")
    if any(not 33 <= ord(ch) <= 126 for ch in value):
        raise ValueError("Canonical Product device credential is invalid")
    return value


def _object_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON keys are forbidden")
        result[key] = value
    return result


def _reject_constant(_value: str) -> None:
    raise ValueError("Non-finite JSON number")


class ProductDevicePresenceClient:
    """One POST, no automatic retries, no legacy WebSocket and no stored secret."""

    def __init__(
        self, base_url: str, *, http: httpx.Client | None = None,
        timeout_seconds: float = 10.0,
    ):
        if not isinstance(base_url, str):
            raise ValueError("Product presence origin must be HTTPS")
        parsed = urlsplit(base_url)
        if (parsed.scheme != "https" or not parsed.hostname or
            parsed.username is not None or parsed.password is not None or
            parsed.path not in {"", "/"} or parsed.query or parsed.fragment or
            not parsed.netloc or any(ch.isspace() for ch in base_url)):
            raise ValueError("Product presence origin must be an HTTPS origin")
        try:
            _ = parsed.port
        except ValueError as exc:
            raise ValueError("Product presence origin port is invalid") from exc
        if not isinstance(timeout_seconds, (int, float)) or not math.isfinite(timeout_seconds) or not 0 < timeout_seconds <= 30:
            raise ValueError("timeout_seconds must be between 0 and 30")
        self.base_url = base_url.rstrip("/")
        self.http = http if http is not None else httpx.Client(timeout=timeout_seconds)
        self._owns_http = http is None
        self.timeout_seconds = float(timeout_seconds)

    def close(self) -> None:
        if self._owns_http:
            self.http.close()

    def __enter__(self) -> "ProductDevicePresenceClient":
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def report(
        self, *, device_id: str, device_credential: str,
        snapshot: ProductPresenceSnapshot,
    ) -> ProductPresenceReceipt:
        canonical_id = _uuid(device_id)
        token = _token(device_credential)
        if not isinstance(snapshot, ProductPresenceSnapshot):
            raise ValueError("Canonical Product presence snapshot is required")
        data = snapshot.as_wire()
        # An injected transport must not silently attach account credentials to
        # a device-authenticated observation. Fail before contacting the server.
        if "authorization" in self.http.headers or self.http.auth is not None:
            raise ValueError("Product presence transport must not inherit account authentication")
        deadline = time.monotonic() + self.timeout_seconds
        try:
            # No bearer account credential. No log/query string, redirects or
            # retry: failure after delivery is an uncertain receipt.
            with self.http.stream(
                "POST", self.base_url + PRESENCE_PATH,
                headers={
                    "X-Ordax-Device-Id": canonical_id,
                    "X-Ordax-Device-Token": token,
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                },
                json=data,
                timeout=self.timeout_seconds,
                follow_redirects=False,
            ) as response:
                if response.status_code in {401, 403}:
                    raise ProductPresenceError("product_device_unauthorized")
                if response.status_code != 200:
                    raise ProductPresenceError("product_presence_unavailable")
                if response.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
                    raise ProductPresenceError("product_presence_invalid_response")
                chunks: list[bytes] = []
                size = 0
                for chunk in response.iter_bytes():
                    if time.monotonic() >= deadline:
                        raise ProductPresenceError("product_presence_uncertain")
                    size += len(chunk)
                    if size > _MAX_RESPONSE_BYTES:
                        raise ProductPresenceError("product_presence_invalid_response")
                    chunks.append(chunk)
                if time.monotonic() >= deadline:
                    raise ProductPresenceError("product_presence_uncertain")
            data_obj = json.loads(
                b"".join(chunks).decode("utf-8", "strict"),
                object_pairs_hook=_object_pairs,
                parse_constant=_reject_constant,
            )
        except ProductPresenceError:
            raise
        except (httpx.HTTPError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
            raise ProductPresenceError("product_presence_uncertain") from None
        if (not isinstance(data_obj, dict)
            or set(data_obj) != {"ok", "device_id", "changed"}
            or data_obj.get("ok") is not True
            or data_obj.get("device_id") != canonical_id
            or type(data_obj.get("changed")) is not bool):
            raise ProductPresenceError("product_presence_invalid_response")
        return ProductPresenceReceipt(device_id=canonical_id, changed=data_obj["changed"])
