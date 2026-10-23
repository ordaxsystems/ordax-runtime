"""Trusted network ingress selection for the ONE Product MCP authority.

The workers.dev hostname is an existing restricted ingress to the same
Cloudflare Worker and PostgreSQL authority, NOT a backup execution system.
Only connection-stage errors may switch ingress: a request that reached the
server must never be repeated merely because its response was lost.
"""
from __future__ import annotations

import errno
import socket
import ssl
import time
from typing import Callable, TypeVar

import httpx

_PRIMARY = "https://mcp.ordax.com.br"
_RESTRICTED = "https://ordax-mcp.ordaxsystems.workers.dev"
_TRANSPORT_OPS = frozenset({
    "heartbeat", "claim", "start", "lease", "progress",
    "report", "recover-report", "wake",
})
_SESSION_PATH = "/v3/product/device/central-session"
_T = TypeVar("_T")


class TrustedProductIngress:
    """Use only two pinned TLS origins, retaining the same request/fencing ID."""

    def __init__(
        self,
        origin: str,
        *,
        monotonic: Callable[[], float] = time.monotonic,
        reprobe_seconds: float = 120.0,
    ) -> None:
        self.origin = str(origin).rstrip("/")
        self._clock = monotonic
        if reprobe_seconds <= 0:
            raise ValueError("Product ingress reprobe interval must be positive")
        self._reprobe_seconds = reprobe_seconds
        self._use_restricted_until = 0.0

    @staticmethod
    def _allowed(path: str) -> bool:
        if path == _SESSION_PATH:
            return True
        prefix = "/v3/device/transport/"
        return path.startswith(prefix) and path[len(prefix):] in _TRANSPORT_OPS

    def websocket(self, path: str, connect: Callable[[str], _T]) -> _T:
        """Use the SAME canonical ingress selector for wake-only WSS.

        Only a TCP/DNS/connect timeout before a successful handshake may use
        the existing restricted hostname. TLS certificate errors and HTTP
        401/403/invalid WebSocket handshakes are not connectivity failures and
        must not initiate failover. The restricted host is the same Worker,
        OAuth and PostgreSQL authority; this does not introduce a fallback
        execution system, token, job queue or grant decision.
        """
        if path != "/v3/device/transport/wake":
            raise ValueError("Only the Product wake endpoint supports WebSocket ingress")

        def establish(https_url: str) -> _T:
            if not https_url.startswith("https://"):
                raise ValueError("Product WebSocket requires the pinned TLS origin")
            wss_url = "wss://" + https_url[len("https://"):]
            try:
                return connect(wss_url)
            except OSError as error:
                # Whitelist TCP/DNS connection-stage OS errors, including
                # network-unreachable on Linux and Windows Winsock. Never
                # fail over certificate failures, protocol/auth responses,
                # local permission errors or an ambiguous OS failure.
                network_codes = frozenset({
                    errno.ECONNREFUSED, errno.ECONNRESET, errno.ENETUNREACH,
                    errno.EHOSTUNREACH, errno.ETIMEDOUT,
                    10051, 10054, 10060, 10061, 10065,
                })
                if (isinstance(error, ssl.SSLError) or not (
                    isinstance(error, (ConnectionError, TimeoutError, socket.gaierror))
                    or error.errno in network_codes
                )):
                    raise
                raise httpx.ConnectError("Product wake TCP connection failed") from error

        return self.request(path, establish)

    def request(self, path: str, send: Callable[[str], _T]) -> _T:
        """send(url) must use TLS verification and reject HTTP redirects.

        A connect exception occurs before a request is delivered, unlike a
        ReadTimeout or HTTP 5xx. Never fail over the latter, including report,
        start or claim: its side effect may have happened already.
        """
        if self.origin != _PRIMARY or not self._allowed(path):
            return send(self.origin + path)

        if self._clock() < self._use_restricted_until:
            try:
                return send(_RESTRICTED + path)
            except (httpx.ConnectError, httpx.ConnectTimeout):
                # This fallback is also inaccessible. Probe the primary now.
                self._use_restricted_until = 0.0
                return send(_PRIMARY + path)

        try:
            response = send(_PRIMARY + path)
        except (httpx.ConnectError, httpx.ConnectTimeout):
            response = send(_RESTRICTED + path)
            self._use_restricted_until = self._clock() + self._reprobe_seconds
        else:
            self._use_restricted_until = 0.0
        return response
