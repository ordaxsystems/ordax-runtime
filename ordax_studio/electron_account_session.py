"""Single-shot account + device-link bridge for the canonical Studio host.

The bridge authenticates the owner, enrolls/pairs this installed Runtime through
its canonical Product owner, and returns only bounded identity metadata. It does
not mint remote capability grants or execute device actions. The only caller is
the installed Studio process via a private child stdio pipe.
"""
from __future__ import annotations

import json
import os
import re
import sys
import uuid
from dataclasses import replace
from typing import Any, Callable

from ordax_dev_agent.config import AgentConfig
from ordax_dev_agent.product_remote_client import ProductRemoteClient, ProductRemoteError
from .product_auth import (
    ProductAccountError,
    ProductAuthSession,
    connect_existing_device,
    sign_in_with_password,
)

_SCHEMA = "ordax.studio-product-account-session/1"
_MAX_INPUT = 3000
_DEFAULT_CONTROL_PLANE = "https://mcp.ordax.com.br"
_CODE = re.compile(r"[a-z][a-z0-9_]{0,79}")


def authorize_account(
    data: Any,
    *,
    authenticate: Callable[[str, str], ProductAuthSession] = sign_in_with_password,
    remote_factory: Callable[[str], Any] = ProductRemoteClient,
    config_factory: Callable[[], AgentConfig] = AgentConfig.from_env,
    connect_device: Callable[..., dict[str, Any]] = connect_existing_device,
    control_plane: str | None = None,
) -> dict[str, Any]:
    if not isinstance(data, dict) or set(data) != {"schema", "operation", "email", "password"}:
        raise ValueError("invalid_request")
    if data["schema"] != _SCHEMA or data["operation"] != "sign-in":
        raise ValueError("unsupported_operation")
    email, password = data["email"], data["password"]
    if not isinstance(email, str) or not isinstance(password, str):
        raise ValueError("invalid_credentials")
    # Reject control characters before passing the values to the shared owner.
    if ("\x00" in email or "\x00" in password or
        any(ord(ch) < 32 for ch in email) or len(email) > 320 or
        len(password) > 2048 or not password):
        raise ValueError("invalid_credentials")
    session = authenticate(email, password)
    if not isinstance(session, ProductAuthSession) or not isinstance(session.access_token, str):
        raise ValueError("invalid_session")
    token = session.access_token
    if not token or len(token) > 16000 or "\r" in token or "\n" in token:
        raise ValueError("invalid_session")
    base = control_plane or os.environ.get("ORDAX_PRODUCT_CONTROL_PLANE_URL") or _DEFAULT_CONTROL_PLANE
    with remote_factory(base) as remote:
        subject = remote.session(token)
    if not isinstance(subject, dict) or not isinstance(subject.get("subject_id"), str):
        raise ValueError("invalid_product_subject")
    try:
        owner_id = str(uuid.UUID(subject["subject_id"]))
        if subject["subject_id"].lower() != owner_id:
            raise ValueError("noncanonical_subject")
    except (ValueError, AttributeError):
        raise ValueError("invalid_product_subject") from None

    config = config_factory()
    if not isinstance(config, AgentConfig):
        raise ValueError("invalid_runtime_config")
    if control_plane:
        config = replace(config, control_plane_url=base)
    device = connect_device(
        config,
        email,
        password,
        session=session,
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

    # The token crosses only the private child stdout -> Electron main-process
    # pipe; never store it in a profile, HTTP response, renderer or log.
    # Device metadata is non-secret and proves that the canonical Runtime owner
    # completed enrollment/pairing; no capability grant is minted here.
    return {
        "schema": _SCHEMA, "ok": True, "access_token": token,
        "email": session.email or email.strip(),
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
    except (ValueError, UnicodeError, ProductAccountError, ProductRemoteError, OSError):
        # Do not echo passwords, JWTs, provider bodies or tracebacks.
        stdout.write('{"schema":"' + _SCHEMA + '","ok":false,"error":"account_auth_failed"}\n')
        stdout.flush()
        return 1
    except Exception:
        stdout.write('{"schema":"' + _SCHEMA + '","ok":false,"error":"account_auth_unavailable"}\n')
        stdout.flush()
        return 1


if __name__ == "__main__":
    raise SystemExit(run())