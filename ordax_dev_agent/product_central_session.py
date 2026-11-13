"""Device side of the Product-MCP Central session attestation handshake.

The client owns no scheduler and stores no device credential. The existing
Cloudflare command heartbeat only schedules checks for short-lived pending
attestation challenges. It does not renew or keep alive the durable
conversation -> device binding owned by the canonical Platform. A challenge is
polled from the Product MCP and confirmed with the same Product device identity
already verified by the canonical Product device heartbeat.
"""
from __future__ import annotations

import re
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Callable

import httpx

from .product_ingress import TrustedProductIngress

_UUID = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\Z",
    re.IGNORECASE,
)
_TOKEN = re.compile(r"[!-~]{32,512}\Z")
_PUBLIC_CENTRAL_ERRORS = frozenset({
    "product_identity_reenrollment_required",
    "product_central_session_unavailable",
    "product_central_session_response_invalid",
    "product_central_session_response_oversized",
    "product_central_session_receipt_mismatch",
    "central_session_challenge_invalid",
    "central_session_challenge_expired",
    "central_device_grant_revoked",
    "central_session_revoked",
})


@dataclass(frozen=True, slots=True)
class ProductCentralSessionStatus:
    checked: bool
    confirmed: bool
    challenge_id: str | None
    last_error: str | None
    last_success_at: float | None

    def public_observation(self) -> dict[str, bool | str | None]:
        """A bounded local diagnosis, not proof of a current remote grant."""
        code = self.last_error
        if code is not None and code not in _PUBLIC_CENTRAL_ERRORS:
            code = "product_central_session_unavailable"
        return {
            "checked": self.checked is True,
            # confirmed is the latest tick's result and resets on an empty
            # poll. The public field explicitly describes session history.
            "last_confirmation_observed": self.last_success_at is not None,
            "last_error_code": code,
        }


class ProductCentralSessionClient:
    """Poll and confirm one server-selected Central session challenge.

    The model never supplies the device id or the challenge to this client.
    Both arrive from the Product authority after the device authenticates with
    its existing credential. Raw credentials are requested just-in-time and are
    never retained on the instance.
    """

    def __init__(
        self,
        base_url: str,
        device_id: str,
        credential_provider: Callable[[], str],
        *,
        http: httpx.Client | None = None,
        min_poll_seconds: float = 5.0,
    ):
        base = str(base_url or "").strip().rstrip("/")
        parsed = httpx.URL(base)
        if parsed.scheme != "https" or not parsed.host or parsed.query or parsed.fragment:
            raise ValueError("Canonical Product MCP URL must be HTTPS origin")
        try:
            canonical_id = str(uuid.UUID(str(device_id)))
        except (ValueError, AttributeError) as exc:
            raise ValueError("Canonical Product device UUID is invalid") from exc
        if canonical_id != device_id or _UUID.fullmatch(device_id) is None:
            raise ValueError("Canonical Product device UUID must be normalized")
        if not callable(credential_provider):
            raise ValueError("Product credential provider must be callable")
        if min_poll_seconds < 5 or min_poll_seconds > 300:
            raise ValueError("Product Central session poll interval is invalid")

        self.base_url = base
        self.endpoint = base + "/v3/product/device/central-session"
        self._trusted_ingress = TrustedProductIngress(base)
        self.device_id = canonical_id
        self._credential_provider = credential_provider
        self._http = http or httpx.Client(timeout=8.0, follow_redirects=False)
        self._owns_http = http is None
        self._min_poll_seconds = float(min_poll_seconds)
        self._next_poll_at = 0.0
        self._status = ProductCentralSessionStatus(False, False, None, None, None)
        self._tick_lock = threading.Lock()
        self._tick_thread: threading.Thread | None = None
        self._closing = threading.Event()

    @property
    def status(self) -> ProductCentralSessionStatus:
        return self._status

    def schedule_tick(self) -> bool:
        """Run the existing attestation check off the heartbeat/claim path.

        The heartbeat is the only trigger for checking pending attestation:
        no extra timer, daemon process, credential store, or concurrent session
        polls. This tick is not a session-renewal mechanism; confirmed
        conversation -> device routing is persistent in Platform until an
        explicit authority event invalidates it. A stalled optional attestation
        cannot hold the authoritative Product device heartbeat.
        """
        with self._tick_lock:
            if (self._closing.is_set()
                    or time.monotonic() < self._next_poll_at
                    or (self._tick_thread is not None and self._tick_thread.is_alive())):
                return False
            self._tick_thread = threading.Thread(
                target=self.tick,
                name="ordax-product-central-session",
                daemon=True,
            )
            self._tick_thread.start()
            return True

    def close(self) -> None:
        with self._tick_lock:
            self._closing.set()
            active = self._tick_thread
        if active is not None and active.is_alive():
            active.join(timeout=0.25)
        if self._owns_http:
            self._http.close()

    @staticmethod
    def _credential(value: str) -> str:
        if not isinstance(value, str) or _TOKEN.fullmatch(value) is None:
            raise ValueError("Canonical Product device credential is invalid")
        return value

    def _headers(self, credential: str) -> dict[str, str]:
        return {
            "X-Ordax-Device-Id": self.device_id,
            "X-Ordax-Device-Token": credential,
        }

    @staticmethod
    def _json(response: httpx.Response) -> dict:
        if len(response.content) > 16 * 1024:
            raise RuntimeError("product_central_session_response_oversized")
        try:
            value = response.json()
        except ValueError as exc:
            raise RuntimeError("product_central_session_response_invalid") from exc
        if not isinstance(value, dict):
            raise RuntimeError("product_central_session_response_invalid")
        return value

    def tick(self, *, now: float | None = None) -> ProductCentralSessionStatus:
        if self._closing.is_set():
            return self._status
        current = time.monotonic() if now is None else float(now)
        if current < self._next_poll_at:
            return self._status
        self._next_poll_at = current + self._min_poll_seconds

        try:
            credential = self._credential(self._credential_provider())
            polled = self._trusted_ingress.request(
                "/v3/product/device/central-session",
                lambda url: self._http.get(
                    url, headers=self._headers(credential), follow_redirects=False,
                ),
            )
            if polled.status_code == 401:
                raise RuntimeError("product_identity_reenrollment_required")
            payload = self._json(polled)
            if polled.status_code != 200 or payload.get("ok") is not True:
                raise RuntimeError("product_central_session_unavailable")
            if payload.get("pending") is False:
                self._status = ProductCentralSessionStatus(
                    True, False, None, None, self._status.last_success_at
                )
                return self._status
            challenge_id = payload.get("challenge_id")
            if (
                payload.get("pending") is not True
                or not isinstance(challenge_id, str)
                or _UUID.fullmatch(challenge_id) is None
            ):
                raise RuntimeError("product_central_session_response_invalid")

            # Do not confirm an old challenge while shutdown/revocation is in
            # progress. The next accepted heartbeat can schedule a fresh poll.
            if self._closing.is_set():
                return self._status
            credential = self._credential(self._credential_provider())
            confirmed = self._trusted_ingress.request(
                "/v3/product/device/central-session",
                lambda url: self._http.post(
                    url,
                    headers={
                        **self._headers(credential),
                        "Content-Type": "application/json",
                    },
                    json={"challenge_id": challenge_id},
                    follow_redirects=False,
                ),
            )
            if confirmed.status_code == 401:
                raise RuntimeError("product_identity_reenrollment_required")
            receipt = self._json(confirmed)
            if confirmed.status_code == 403:
                code = receipt.get("error")
                if isinstance(code, str) and code in {"central_device_grant_revoked", "central_session_revoked"}:
                    raise RuntimeError(code)
                raise RuntimeError("product_central_session_unavailable")
            if confirmed.status_code not in {200, 202} or receipt.get("ok") is not True:
                code = receipt.get("error")
                if code in {
                    "central_session_challenge_invalid",
                    "central_session_challenge_expired",
                }:
                    raise RuntimeError(str(code))
                raise RuntimeError("product_central_session_unavailable")
            if receipt.get("status") != "bound" or receipt.get("device_id") != self.device_id:
                raise RuntimeError("product_central_session_receipt_mismatch")

            self._status = ProductCentralSessionStatus(
                True, True, challenge_id, None, time.time()
            )
        except (httpx.HTTPError, RuntimeError, ValueError) as exc:
            code = str(exc)
            if code not in _PUBLIC_CENTRAL_ERRORS:
                code = "product_central_session_unavailable"
            self._status = ProductCentralSessionStatus(
                True,
                False,
                None,
                code,
                self._status.last_success_at,
            )
        return self._status
