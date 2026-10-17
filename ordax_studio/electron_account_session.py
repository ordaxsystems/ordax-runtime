"""Browser OAuth + owner-bound device enrollment for the canonical installed Studio.

No ORDAX password crosses the Studio renderer or Electron IPC. The private Runtime
child owns PKCE, loopback callback, token exchange, Product subject validation and
owner-bound device enrollment. MCP grants remain a separate explicit authorization
boundary. The access token leaves this child only through its private stdio pipe to
the Electron main process and is never persisted by this bridge.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import secrets
import sys
import time
import uuid
import webbrowser
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, Callable
from urllib.parse import parse_qs, urlencode, urlparse

from ordax_dev_agent.config import AgentConfig, DEFAULT_PRODUCT_MCP_URL
from ordax_dev_agent.product_remote_client import ProductRemoteClient, ProductRemoteError
from .product_auth import (
    ProductAccountError,
    ProductAuthSession,
    connect_existing_device,
    exchange_authorization_code,
)
from .public_auth_metadata import (
    PRODUCT_AUTH_ISSUER,
    STUDIO_OAUTH_CLIENT_ID,
    STUDIO_OAUTH_REDIRECT_URI,
    STUDIO_OAUTH_SCOPES,
)

_SCHEMA = "ordax.studio-product-account-session/1"
_MAX_INPUT = 1000
_DEFAULT_CONTROL_PLANE = "https://mcp.ordax.com.br"
_PRODUCT_MCP_TRANSPORT_FALLBACK = "https://ordax-mcp.ordaxsystems.workers.dev"
_CALLBACK_HOST = "127.0.0.1"
_CALLBACK_PORT = 8766
_CALLBACK_PATH = "/oauth/callback"
_OAUTH_TIMEOUT_SECONDS = 300
_SAFE_ERROR_CODE = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,127}\Z")


def _pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).rstrip(b"=").decode("ascii")
    return verifier, challenge


def _safe_error_code(value: Any, *, fallback: str = "account_auth_failed") -> str:
    code = value if isinstance(value, str) else ""
    return code if _SAFE_ERROR_CODE.fullmatch(code) is not None else fallback


def _write_failure(stdout: Any, code: str) -> None:
    stdout.write(json.dumps(
        {"schema": _SCHEMA, "ok": False, "error": _safe_error_code(code)},
        separators=(",", ":"), ensure_ascii=True,
    ) + "\n")
    stdout.flush()


def _normalize_product_base(value: str | None) -> str:
    raw = str(value or DEFAULT_PRODUCT_MCP_URL).strip().rstrip("/")
    parsed = urlparse(raw)
    loopback = parsed.hostname in {"127.0.0.1", "localhost", "::1"}
    if not parsed.netloc or parsed.scheme not in ({"http", "https"} if loopback else {"https"}):
        raise ProductAccountError(
            "product_mcp_url_invalid",
            "O endereço Product configurado neste computador é inválido.",
        )
    return raw


def _validate_product_subject(
    token: str,
    primary: str,
    remote_factory: Callable[[str], Any],
) -> tuple[dict[str, Any], str]:
    primary = _normalize_product_base(primary)
    branded = DEFAULT_PRODUCT_MCP_URL.rstrip("/")
    candidates = [primary]
    if primary == branded and _PRODUCT_MCP_TRANSPORT_FALLBACK != primary:
        candidates.append(_PRODUCT_MCP_TRANSPORT_FALLBACK)

    for index, base in enumerate(candidates):
        try:
            with remote_factory(base) as remote:
                subject = remote.session(token)
            if not isinstance(subject, dict):
                raise ValueError("invalid_product_subject")
            return subject, base
        except ProductRemoteError as error:
            # The fallback is only a second network path to the same Product
            # authority. HTTP/auth/business failures are authoritative and must
            # never be bypassed by trying another host.
            can_retry = (
                error.error_code == "product_remote_transport_error"
                and index + 1 < len(candidates)
            )
            if not can_retry:
                raise
    raise ProductRemoteError(
        "product_remote_transport_error", 0,
        "Product transport was unavailable on all canonical endpoints",
    )


def browser_oauth_session(
    *,
    open_browser: Callable[..., bool] = webbrowser.open,
    exchange: Callable[[str, str], ProductAuthSession] = exchange_authorization_code,
    server_factory: Callable[..., HTTPServer] = HTTPServer,
    timeout_seconds: float = _OAUTH_TIMEOUT_SECONDS,
) -> ProductAuthSession:
    redirect = urlparse(STUDIO_OAUTH_REDIRECT_URI)
    if (
        redirect.scheme != "http"
        or redirect.hostname != _CALLBACK_HOST
        or redirect.port != _CALLBACK_PORT
        or redirect.path != _CALLBACK_PATH
        or redirect.query
        or redirect.fragment
    ):
        raise ProductAccountError("product_oauth_redirect_invalid", "O callback OAuth do Studio é inválido.")

    verifier, challenge = _pkce()
    state = secrets.token_urlsafe(32)
    outcome: dict[str, str] = {}

    class CallbackHandler(BaseHTTPRequestHandler):
        def log_message(self, _format: str, *_args: Any) -> None:
            return

        def do_GET(self) -> None:  # noqa: N802 - stdlib callback name
            parsed = urlparse(self.path)
            host = self.headers.get("Host", "")
            if parsed.path != _CALLBACK_PATH or host != f"{_CALLBACK_HOST}:{_CALLBACK_PORT}":
                self.send_response(404)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            if outcome:
                self.send_response(409)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            query = parse_qs(parsed.query, keep_blank_values=True, strict_parsing=False)
            returned_state = query.get("state", [])
            if len(returned_state) != 1 or not secrets.compare_digest(returned_state[0], state):
                outcome["error"] = "state_mismatch"
                ok = False
            elif "error" in query:
                outcome["error"] = "authorization_denied"
                ok = False
            else:
                codes = query.get("code", [])
                if len(codes) != 1 or not codes[0] or len(codes[0]) > 4096:
                    outcome["error"] = "invalid_code"
                    ok = False
                else:
                    outcome["code"] = codes[0]
                    ok = True
            body = (
                "<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width'>"
                "<title>ORDAX Studio</title><style>body{font:16px system-ui;background:#07111d;color:#eaf2ff;"
                "display:grid;place-items:center;min-height:90vh}main{max-width:520px;padding:32px;"
                "border:1px solid #24364d;border-radius:18px;background:#0d1a2a}</style><main><h1>"
                + ("Autorização recebida" if ok else "Autorização não concluída")
                + "</h1><p>" + (
                    "Volte ao ORDAX Studio para concluir a validação deste computador."
                    if ok else "Volte ao ORDAX Studio e tente novamente."
                ) + "</p></main>"
            ).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; frame-ancestors 'none'")
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    try:
        server = server_factory((_CALLBACK_HOST, _CALLBACK_PORT), CallbackHandler)
    except OSError as error:
        raise ProductAccountError(
            "product_oauth_callback_unavailable",
            "O Studio não conseguiu reservar o retorno seguro do navegador.",
        ) from error
    try:
        server.timeout = 0.5
        authorize_url = PRODUCT_AUTH_ISSUER + "/oauth/authorize?" + urlencode({
            "response_type": "code",
            "client_id": STUDIO_OAUTH_CLIENT_ID,
            "redirect_uri": STUDIO_OAUTH_REDIRECT_URI,
            "scope": STUDIO_OAUTH_SCOPES,
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        })
        if open_browser(authorize_url, new=1, autoraise=True) is False:
            raise ProductAccountError(
                "product_oauth_browser_unavailable",
                "Não foi possível abrir o navegador padrão.",
            )
        deadline = time.monotonic() + max(1.0, min(float(timeout_seconds), _OAUTH_TIMEOUT_SECONDS))
        while not outcome and time.monotonic() < deadline:
            server.handle_request()
        if not outcome:
            raise ProductAccountError(
                "product_oauth_timeout",
                "A autorização no navegador expirou.",
            )
        if "code" not in outcome:
            raise ProductAccountError(
                "product_oauth_rejected",
                "A conexão foi cancelada ou não pôde ser confirmada.",
            )
        return exchange(outcome["code"], verifier)
    finally:
        server.server_close()


def authorize_account(
    data: Any,
    *,
    authenticate: Callable[[], ProductAuthSession] = browser_oauth_session,
    remote_factory: Callable[[str], Any] = ProductRemoteClient,
    config_factory: Callable[[], AgentConfig] = AgentConfig.from_env,
    connect_device: Callable[..., dict[str, Any]] = connect_existing_device,
    product_mcp_url: str | None = None,
) -> dict[str, Any]:
    if not isinstance(data, dict) or set(data) != {"schema", "operation"}:
        raise ValueError("invalid_request")
    if data["schema"] != _SCHEMA or data["operation"] != "browser-sign-in":
        raise ValueError("unsupported_operation")

    session = authenticate()
    if not isinstance(session, ProductAuthSession) or not isinstance(session.access_token, str):
        raise ValueError("invalid_session")
    token = session.access_token
    if not token or len(token) > 16000 or "\r" in token or "\n" in token:
        raise ValueError("invalid_session")

    config = config_factory()
    if not isinstance(config, AgentConfig):
        raise ValueError("invalid_runtime_config")
    primary_product = product_mcp_url or config.product_mcp_url
    subject, selected_product = _validate_product_subject(
        token,
        _normalize_product_base(primary_product),
        remote_factory,
    )
    if not isinstance(subject.get("subject_id"), str):
        raise ValueError("invalid_product_subject")
    try:
        owner_id = str(uuid.UUID(subject["subject_id"]))
        if subject["subject_id"].lower() != owner_id:
            raise ValueError("noncanonical_subject")
    except (ValueError, AttributeError):
        raise ValueError("invalid_product_subject") from None

    device = connect_device(
        replace(config, product_mcp_url=selected_product),
        session=session,
        product_mcp_url=selected_product,
    )
    if not isinstance(device, dict) or not isinstance(device.get("device_id"), str):
        raise ValueError("invalid_device_enrollment")
    try:
        device_id = str(uuid.UUID(device["device_id"]))
        if device["device_id"].lower() != device_id:
            raise ValueError("noncanonical_device")
    except (ValueError, AttributeError):
        raise ValueError("invalid_device_enrollment") from None
    enrolled_now = device.get("enrolled_now")
    if not isinstance(enrolled_now, bool):
        raise ValueError("invalid_device_enrollment")

    return {
        "schema": _SCHEMA,
        "ok": True,
        "access_token": token,
        "email": session.email or "",
        "subject_id": owner_id,
        "device_id": device_id,
        "product_mcp_url": selected_product,
        "enrolled_now": enrolled_now,
    }


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_field")
        result[key] = value
    return result


def run(stdin: Any = sys.stdin.buffer, stdout: Any = sys.stdout) -> int:
    try:
        raw = stdin.read(_MAX_INPUT + 1)
        if len(raw) > _MAX_INPUT or not raw:
            raise ValueError("invalid_size")
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object)
        result = authorize_account(value)
        stdout.write(json.dumps(result, separators=(",", ":"), ensure_ascii=True) + "\n")
        stdout.flush()
        return 0
    except ProductAccountError as error:
        _write_failure(stdout, _safe_error_code(error.code))
        return 1
    except ProductRemoteError as error:
        _write_failure(stdout, _safe_error_code(error.error_code))
        return 1
    except (ValueError, UnicodeError, OSError):
        _write_failure(stdout, "account_auth_failed")
        return 1
    except Exception:
        _write_failure(stdout, "account_auth_unavailable")
        return 1


if __name__ == "__main__":
    raise SystemExit(run())
