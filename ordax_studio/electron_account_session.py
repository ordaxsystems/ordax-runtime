"""Browser OAuth + owner-bound device enrollment for the canonical installed Studio.

No ORDAX password crosses the Studio renderer or Electron IPC. The private Runtime
child owns PKCE, loopback callback, token exchange, Product subject validation and
owner-bound device enrollment. MCP grants remain a separate explicit authorization
boundary. The access token leaves this child only through its private stdio pipe to
the Electron main process and is never persisted by this bridge.
"""
from __future__ import annotations

import json
import os
import re
import sys
import uuid
import webbrowser
from dataclasses import replace
from http.server import HTTPServer
from typing import Any, Callable
from urllib.parse import urlparse

from ordax_core.owner_oauth import OwnerOAuthError, browser_authorization_code
from ordax_dev_agent.config import AgentConfig, DEFAULT_PRODUCT_MCP_URL
from ordax_dev_agent.product_remote_client import ProductRemoteClient, ProductRemoteError
from .product_auth import (
    ProductAccountError,
    ProductAuthSession,
    connect_existing_device,
    exchange_authorization_code,
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
    """Backwards-compatible Studio facade over the canonical Runtime Owner OAuth."""
    try:
        code, verifier = browser_authorization_code(
            open_browser=open_browser, server_factory=server_factory,
            timeout_seconds=timeout_seconds, studio_callback=True,
        )
    except OwnerOAuthError as error:
        descriptions = {
            "owner_oauth_redirect_invalid": "O callback OAuth do Studio é inválido.",
            "owner_oauth_callback_unavailable": "O Studio não conseguiu reservar o retorno seguro do navegador.",
            "owner_oauth_browser_unavailable": "Não foi possível abrir o navegador padrão.",
            "owner_oauth_timeout": "A autorização no navegador expirou.",
        }
        raise ProductAccountError(
            error.code, descriptions.get(error.code, "A conexão foi cancelada ou não pôde ser confirmada.")
        ) from error
    return exchange(code, verifier)

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
