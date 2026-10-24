"""Advisory Product wake receiver. PostgreSQL remains the only action authority.

This websocket never accepts commands, grants, leases or job payloads. The
server sends only an opaque "wake" signal after committing work in PostgreSQL.
The existing authenticated claim RPC is still required for every action.
Lost notifications are repaired by periodic bounded claims in main.py.
"""
from __future__ import annotations

import threading
import time
from typing import Callable
from urllib.parse import urlsplit

from websockets.sync.client import connect


WAKE_PATH = "/v3/device/transport/wake"
WAKE_MESSAGE = "wake"
_INITIAL_RETRY_SECONDS = 30.0
_MAX_RETRY_SECONDS = 300.0
_STABLE_CONNECTION_RESET_SECONDS = 60.0
TRUSTED_WAKE_HOSTS = frozenset({
    "mcp.ordax.com.br",
    "ordax-mcp.ordaxsystems.workers.dev",
})


def trusted_wake_url(origin: str) -> str:
    parsed = urlsplit(origin.rstrip("/"))
    if (parsed.scheme != "https" or parsed.hostname not in TRUSTED_WAKE_HOSTS
            or parsed.port not in (None, 443) or parsed.username
            or parsed.password or parsed.path not in {"", "/"}
            or parsed.query or parsed.fragment):
        raise ValueError("Product wake requires an HTTPS Product origin without userinfo")
    return f"wss://{parsed.netloc}{WAKE_PATH}"


class ProductWakeListener:
    """Optional, one-per-Runtime receiver; not a second scheduler or queue."""

    def __init__(
        self, control: object, stop_requested: threading.Event,
        local_access_enabled: Callable[[], bool] | None = None,
        *,
        connect_websocket: Callable = connect,
    ) -> None:
        self.url = trusted_wake_url(str(getattr(control, "base_http_url")))
        # Reuse the SAME selector used by heartbeat/claim on this agent.
        # No second ingress state machine or credentials may be created.
        self._trusted_ingress = getattr(control, "_trusted_ingress")
        self.headers = {
            "x-ordax-device-id": str(getattr(control, "device_id")),
            "x-ordax-device-token": str(getattr(control, "device_token")),
        }
        self.wake = threading.Event()
        self.connected = threading.Event()
        self._stop = stop_requested
        self._local_access_enabled = local_access_enabled
        self._connect = connect_websocket
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None:
            raise RuntimeError("Product wake listener already started")
        self._thread = threading.Thread(
            target=self._listen, name="ordax-product-wake", daemon=True,
        )
        self._thread.start()

    def _local_allowed(self) -> bool:
        if self._local_access_enabled is None:
            return True
        try:
            return self._local_access_enabled() is True
        except Exception:
            return False

    def _listen(self) -> None:
        retry_seconds = _INITIAL_RETRY_SECONDS
        while not self._stop.is_set():
            if not self._local_allowed():
                self.connected.clear()
                self.wake.set()
                return
            connection_started: float | None = None
            try:
                with self._trusted_ingress.websocket(
                    WAKE_PATH,
                    lambda url: self._connect(
                        url,
                        additional_headers=self.headers,
                        open_timeout=10,
                        close_timeout=3,
                        ping_interval=20,
                        ping_timeout=10,
                        max_size=32,
                    ),
                ) as socket:
                    connection_started = time.monotonic()
                    self.connected.set()
                    self.wake.set()  # reconcile a job accepted during reconnect
                    while not self._stop.is_set() and self._local_allowed():
                        try:
                            incoming = socket.recv(timeout=1)
                        except TimeoutError:
                            continue
                        if incoming != WAKE_MESSAGE:
                            # No remote instruction may be accepted over signals.
                            socket.close(code=1008, reason="invalid_product_wake")
                            break
                        self.wake.set()
            except Exception:
                # No token, URL, exception payload or untrusted websocket
                # response may be written to logs.
                pass
            finally:
                self.connected.clear()
                self.wake.set()  # revert immediately to budgeted SQL reconciliation
                if (connection_started is not None
                        and time.monotonic() - connection_started
                        >= _STABLE_CONNECTION_RESET_SECONDS):
                    retry_seconds = _INITIAL_RETRY_SECONDS
            if self._stop.wait(retry_seconds):
                break
            retry_seconds = min(_MAX_RETRY_SECONDS, retry_seconds * 2.0)

    def close(self) -> None:
        # No other thread owns the Runtime transport or its credentials.
        self.connected.clear()
        if self._thread is not None:
            self._thread.join(timeout=4.0)
