"""Trusted network ingress selection for the ONE Product MCP authority.

The workers.dev hostname is an existing restricted ingress to the same
Cloudflare Worker and PostgreSQL authority, NOT a backup execution system.
Only connection-stage errors may switch ingress: a request that reached the
server must never be repeated merely because its response was lost.
"""
from __future__ import annotations

import errno
import re
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
# Canonical Owner API routes already used by ProductRemoteClient. The strict
# allowlist permits the same-Worker restricted ingress ONLY after a proven
# preconnect TCP/DNS failure; it never changes the OAuth/grant authority.
_OWNER_PATHS = frozenset({
    "/v3/owner/remote-authority",
    "/v3/owner/project-scope",
    "/v3/owner/workspace-resources/approvals",
    "/v3/owner/central-session-targets",
    "/v3/owner/central-session-selections",
    "/v3/owner/remote-grant-groups",
})
_OWNER_GRANT_REVOKE = re.compile(
    r"/v3/owner/remote-grant-groups/[0-9a-f]{8}-[0-9a-f]{4}-"
    r"[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\\Z"
)
_T = TypeVar("_T")



def inspect_pinned_https_ingress(
    *, client: httpx.Client | None = None,
) -> dict[str, object]:
    """Observe HTTPS reachability only; NEVER claim MCP, session or grants.

    Explicitly invoked by the Windows first-run diagnostic. Two safe GETs,
    no Owner credential, no device registration or state mutation, and no
    automatic retries. A 401/404/405 response means that HTTPS responded,
    not that the Product service or its authentication is ready.
    """
    own_client = client is None
    if own_client:
        client = httpx.Client(
            timeout=httpx.Timeout(5.0, connect=3.0),
            follow_redirects=False,
        )
    if client is None:
        raise RuntimeError("HTTPS inspection client was not initialized")
    result: dict[str, object] = {
        "schema": "ordax.product-mcp-https-observation/1",
        "primary": {},
        "restricted": {},
        "mcpReady": False,
        "authenticated": False,
        "grantsAuthorized": False,
    }
    try:
        for key, origin in (("primary", _PRIMARY), ("restricted", _RESTRICTED)):
            try:
                response = client.get(
                    origin + "/v3/device/setup",
                    headers={"Accept": "application/json"},
                )
                result[key] = {
                    "transport": "https_responded",
                    "httpStatus": response.status_code,
                }
            except (httpx.RequestError, ValueError):
                result[key] = {
                    "transport": "unavailable",
                    "httpStatus": None,
                }
    finally:
        if own_client:
            client.close()
    return result


# Only pre-HTTP TCP/DNS failures allow a second request to the existing
# restricted hostname. httpx.ConnectError is NOT sufficient evidence on its
# own: it also wraps certificate verification and other TLS failures.
_PRECONNECT_OS_CODES = frozenset({
    errno.ECONNREFUSED, errno.ECONNRESET, errno.ENETUNREACH,
    errno.EHOSTUNREACH, errno.ETIMEDOUT,
    10051, 10054, 10060, 10061, 10065,  # Windows Winsock.
})


def _can_retry_on_restricted_ingress(
    error: httpx.ConnectError | httpx.ConnectTimeout,
) -> bool:
    """Permit only demonstrated DNS/TCP failure; reject all SSL causes.

    Trace nested httpx/httpcore exceptions without parsing exception messages:
    client- or proxy-generated ConnectError with no OS cause is ambiguous and
    must never cause a second Owner enrollment or an action delivery.
    """
    network_failure = isinstance(error, httpx.ConnectTimeout)
    pending: list[BaseException] = [error]
    seen: set[int] = set()
    while pending:
        # Exception cause and context form a graph, not necessarily a chain.
        # Unusually deep or cyclic graphs are ambiguous: fail closed.
        if len(seen) >= 32:
            return False
        current = pending.pop()
        if id(current) in seen:
            return False
        seen.add(id(current))
        if isinstance(current, ssl.SSLError):
            return False
        if current is not error and isinstance(current, OSError):
            if isinstance(current, (
                socket.gaierror, ConnectionRefusedError,
                ConnectionResetError, ConnectionAbortedError, TimeoutError,
            )):
                network_failure = True
            elif current.errno in _PRECONNECT_OS_CODES:
                network_failure = True
        cause = current.__cause__
        context = current.__context__
        if context is not None and context is not cause:
            pending.append(context)
        if cause is not None:
            pending.append(cause)
    return network_failure

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
        if path in {_SESSION_PATH, "/v3/device/setup"} or path in _OWNER_PATHS:
            return True
        if _OWNER_GRANT_REVOKE.fullmatch(path) is not None:
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
            except (httpx.ConnectError, httpx.ConnectTimeout) as error:
                if not _can_retry_on_restricted_ingress(error):
                    raise
                # Only a demonstrated network failure may probe primary.
                self._use_restricted_until = 0.0
                return send(_PRIMARY + path)

        try:
            response = send(_PRIMARY + path)
        except (httpx.ConnectError, httpx.ConnectTimeout) as error:
            if not _can_retry_on_restricted_ingress(error):
                raise
            response = send(_RESTRICTED + path)
            self._use_restricted_until = self._clock() + self._reprobe_seconds
        else:
            self._use_restricted_until = 0.0
        return response
