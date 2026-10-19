"""OAuth PKCE for first-party OrdaX Owner clients, without a Studio process.

The registered public OAuth client and loopback redirect are shared Product
metadata. Authorization remains an explicit human action; no token is stored.
"""
from __future__ import annotations

import base64
import hashlib
import secrets
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlencode, urlparse
from typing import Callable

import httpx

from ordax_core.product_auth_metadata import (
    PRODUCT_AUTH_ISSUER, STUDIO_OAUTH_CLIENT_ID, STUDIO_OAUTH_REDIRECT_URI,
    STUDIO_OAUTH_SCOPES, SUPABASE_ORIGIN,
)


class OwnerOAuthError(RuntimeError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def browser_authorization_code(
    *, open_browser: Callable[..., bool] = webbrowser.open,
    server_factory: Callable[..., HTTPServer] = HTTPServer,
    timeout_seconds: float = 300.0,
    studio_callback: bool = False,
) -> tuple[str, str]:
    redirect = urlparse(STUDIO_OAUTH_REDIRECT_URI)
    if (redirect.scheme, redirect.hostname, redirect.port, redirect.path) != (
        "http", "127.0.0.1", 8766, "/oauth/callback"
    ) or redirect.query or redirect.fragment:
        raise OwnerOAuthError("owner_oauth_redirect_invalid")
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode("ascii")).digest()
    ).rstrip(b"=").decode("ascii")
    state = secrets.token_urlsafe(32)
    outcome: dict[str, str] = {}

    class Callback(BaseHTTPRequestHandler):
        def log_message(self, *_args: object) -> None:
            return

        def do_GET(self) -> None:  # noqa: N802
            path = urlparse(self.path)
            if path.path != redirect.path or self.headers.get("Host") != "127.0.0.1:8766":
                self.send_response(404)
                self.end_headers()
                return
            if outcome:
                self.send_response(409)
                self.end_headers()
                return
            q = parse_qs(path.query, keep_blank_values=True)
            states = q.get("state", [])
            if len(states) != 1 or not secrets.compare_digest(states[0], state):
                outcome["error"] = "owner_oauth_state_invalid"
            elif "error" in q:
                outcome["error"] = "owner_oauth_rejected"
            else:
                codes = q.get("code", [])
                if len(codes) != 1 or not codes[0] or len(codes[0]) > 4096:
                    outcome["error"] = "owner_oauth_rejected"
                else:
                    outcome["code"] = codes[0]
            accepted = "code" in outcome
            if studio_callback:
                message = ("Volte ao ORDAX Studio para concluir a validação deste computador."
                           if accepted else "Volte ao ORDAX Studio e tente novamente.")
            else:
                message = ("Volte ao terminal OrdaX Runtime para concluir a autorização."
                           if accepted else "A autorização OrdaX não foi concluída.")
            body = (
                "<!doctype html><meta charset=utf-8><title>ORDAX</title>"
                "<main><h1>" + ("Autorização recebida" if accepted else "Autorização não concluída")
                + "</h1><p>" + message + "</p></main>"
            ).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Security-Policy", "default-src 'none'; base-uri 'none'; frame-ancestors 'none'")
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    try:
        server = server_factory(("127.0.0.1", 8766), Callback)
    except OSError as exc:
        raise OwnerOAuthError("owner_oauth_callback_unavailable") from exc
    try:
        server.timeout = 0.5
        url = PRODUCT_AUTH_ISSUER + "/oauth/authorize?" + urlencode({
            "response_type": "code", "client_id": STUDIO_OAUTH_CLIENT_ID,
            "redirect_uri": STUDIO_OAUTH_REDIRECT_URI, "scope": STUDIO_OAUTH_SCOPES,
            "state": state, "code_challenge": challenge, "code_challenge_method": "S256",
        })
        if open_browser(url, new=1, autoraise=True) is False:
            raise OwnerOAuthError("owner_oauth_browser_unavailable")
        deadline = time.monotonic() + max(1, min(float(timeout_seconds), 300))
        while not outcome and time.monotonic() < deadline:
            server.handle_request()
        if not outcome:
            raise OwnerOAuthError("owner_oauth_timeout")
        if "code" not in outcome:
            raise OwnerOAuthError(outcome.get("error", "owner_oauth_rejected"))
        return outcome["code"], verifier
    finally:
        server.server_close()


def exchange_owner_code(code: str, verifier: str, *, http: httpx.Client | None = None) -> str:
    if not code or len(code) > 4096 or "\r" in code or "\n" in code:
        raise OwnerOAuthError("owner_oauth_code_invalid")
    if not 43 <= len(verifier) <= 128 or not all(c.isalnum() or c in "-._~" for c in verifier):
        raise OwnerOAuthError("owner_oauth_pkce_invalid")
    owns = http is None
    client = http or httpx.Client(timeout=20, follow_redirects=False)
    try:
        r = client.post(SUPABASE_ORIGIN + "/auth/v1/oauth/token",
                        headers={"accept": "application/json", "content-type": "application/x-www-form-urlencoded"},
                        data={"grant_type": "authorization_code", "client_id": STUDIO_OAUTH_CLIENT_ID,
                              "redirect_uri": STUDIO_OAUTH_REDIRECT_URI, "code": code, "code_verifier": verifier})
        if r.status_code >= 400:
            raise OwnerOAuthError("owner_oauth_exchange_rejected")
        token = r.json().get("access_token")
        if not isinstance(token, str) or not 16 <= len(token) <= 16000 or "\r" in token or "\n" in token:
            raise OwnerOAuthError("owner_oauth_token_invalid")
        return token
    except (httpx.HTTPError, ValueError, AttributeError) as exc:
        raise OwnerOAuthError("owner_oauth_exchange_unavailable") from exc
    finally:
        if owns:
            client.close()
