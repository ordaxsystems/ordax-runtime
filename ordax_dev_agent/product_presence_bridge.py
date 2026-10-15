"""Bind canonical Product presence to the already-authenticated Device Agent.

The bridge deliberately owns no device credential and no scheduler. It verifies
that the credential already consumed by the Cloudflare command transport is the
canonical Product credential for this machine, then publishes a bounded
presence observation through ``ProductDevicePresenceClient``. The existing
control-plane heartbeat remains the single cadence owner.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
import uuid
from dataclasses import dataclass
from typing import Callable

import httpx

from .capability_snapshot import device_capability_snapshot
from .device_setup import SetupError, _resolve_control_plane, request
from .identity import machine_id
from .product_device_presence import (
    ProductDevicePresenceClient,
    ProductPresenceError,
    ProductPresenceSnapshot,
)

_DIGEST = re.compile(r"[0-9a-f]{64}\Z")


@dataclass(frozen=True, slots=True)
class ProductPresenceBridgeStatus:
    verified: bool
    online: bool | None
    last_error: str | None
    last_success_at: float | None


class ProductPresenceBridge:
    """Best-effort Product presence over one explicitly verified device identity.

    The bridge never stores the raw device credential. A credential provider is
    consulted for each report and only its SHA-256 is cached after a successful
    Product identity proof. Credential rotation therefore forces re-verification.
    """

    def __init__(
        self,
        base_url: str,
        device_id: str,
        credential_provider: Callable[[], str],
        *,
        verify_http: httpx.Client | None = None,
        binding_provider: Callable[[], str] = machine_id,
        presence_client_factory: Callable[[str], ProductDevicePresenceClient] = ProductDevicePresenceClient,
    ):
        control_plane, setup_endpoint = _resolve_control_plane(base_url)
        try:
            canonical_id = str(uuid.UUID(str(device_id)))
        except (ValueError, AttributeError) as exc:
            raise ValueError("Canonical Product device UUID is invalid") from exc
        if canonical_id != device_id:
            raise ValueError("Canonical Product device UUID must be normalized")
        if not callable(credential_provider) or not callable(binding_provider):
            raise ValueError("Product presence providers must be callable")
        if not callable(presence_client_factory):
            raise ValueError("Product presence client factory must be callable")

        self.base_url = control_plane
        self.device_id = canonical_id
        self.setup_endpoint = setup_endpoint
        self._credential_provider = credential_provider
        self._binding_provider = binding_provider
        self._presence_client_factory = presence_client_factory
        self._verify_http = verify_http or httpx.Client(timeout=10.0, follow_redirects=False)
        self._owns_verify_http = verify_http is None
        self._verified_credential_sha256: str | None = None
        self._status = ProductPresenceBridgeStatus(False, None, None, None)

    @property
    def status(self) -> ProductPresenceBridgeStatus:
        return self._status

    def close(self) -> None:
        if self._owns_verify_http:
            self._verify_http.close()

    @staticmethod
    def capability_digest(actions: list[str]) -> str:
        snapshot = device_capability_snapshot(actions)
        encoded = json.dumps(
            snapshot,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    @staticmethod
    def _credential(value: str) -> str:
        if not isinstance(value, str) or not 32 <= len(value) <= 512:
            raise ValueError("Canonical Product device credential is required")
        if any(not 33 <= ord(ch) <= 126 for ch in value):
            raise ValueError("Canonical Product device credential is invalid")
        return value

    def _verify_identity(self, credential: str) -> None:
        digest = hashlib.sha256(credential.encode("utf-8")).hexdigest()
        if digest == self._verified_credential_sha256:
            return

        binding = self._binding_provider()
        if not isinstance(binding, str) or _DIGEST.fullmatch(binding) is None:
            raise RuntimeError("product_machine_binding_invalid")
        if "authorization" in self._verify_http.headers:
            raise RuntimeError("product_identity_transport_has_account_auth")

        identity = request(
            self._verify_http,
            self.setup_endpoint,
            {"operation": "identify", "machine_binding_sha256": binding},
            {"X-Ordax-Device-Token": credential},
        )
        if identity.get("denied") in {401, 403}:
            raise RuntimeError("product_identity_reenrollment_required")
        if (
            identity.get("ok") is not True
            or identity.get("protocol") != "cloudflare-v3"
            or identity.get("device_id") != self.device_id
        ):
            raise RuntimeError("product_identity_mismatch")

        self._verified_credential_sha256 = digest

    def report(
        self,
        actions: list[str],
        *,
        agent_version: str,
        online: bool,
    ) -> ProductPresenceBridgeStatus:
        try:
            credential = self._credential(self._credential_provider())
            self._verify_identity(credential)
            snapshot = ProductPresenceSnapshot(
                online=bool(online),
                runtime_kind="desktop-agent",
                agent_version=agent_version,
                capability_digest=self.capability_digest(actions),
            )
            with self._presence_client_factory(self.base_url) as client:
                client.report(
                    device_id=self.device_id,
                    device_credential=credential,
                    snapshot=snapshot,
                )
            self._status = ProductPresenceBridgeStatus(
                True,
                bool(online),
                None,
                time.time(),
            )
        except ProductPresenceError as exc:
            if exc.code == "product_device_unauthorized":
                self._verified_credential_sha256 = None
                code = "product_identity_reenrollment_required"
            else:
                code = exc.code
            self._status = ProductPresenceBridgeStatus(
                self._verified_credential_sha256 is not None,
                None,
                code,
                self._status.last_success_at,
            )
        except SetupError as exc:
            self._status = ProductPresenceBridgeStatus(
                self._verified_credential_sha256 is not None,
                None,
                str(exc),
                self._status.last_success_at,
            )
        except (RuntimeError, ValueError, httpx.HTTPError) as exc:
            code = str(exc)
            if not code.startswith("product_"):
                code = "product_presence_bridge_unavailable"
            self._status = ProductPresenceBridgeStatus(
                self._verified_credential_sha256 is not None,
                None,
                code,
                self._status.last_success_at,
            )
        return self._status
