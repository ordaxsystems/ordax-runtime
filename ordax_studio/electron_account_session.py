"""Single-shot account sign-in bridge for the canonical Studio Electron host.

This is not an executor and does not enumerate or grant local device rights.
The only caller is the installed Studio process via a private child stdio pipe.
"""
from __future__ import annotations

import json
import os
import re
import sys
from typing import Any, Callable

from ordax_dev_agent.product_remote_client import ProductRemoteClient, ProductRemoteError
from .product_auth import ProductAccountError, ProductAuthSession, sign_in_with_password

_SCHEMA = "ordax.studio-product-account-session/1"
_MAX_INPUT = 3000
_DEFAULT_CONTROL_PLANE = "https://ordax-control-plane-v3.ordax-ac1ca1b50d09.workers.dev"
_CODE = re.compile(r"[a-z][a-z0-9_]{0,79}")


def authorize_account(
    data: Any,
    *,
    authenticate: Callable[[str, str], ProductAuthSession] = sign_in_with_password,
    remote_factory: Callable[[str], Any] = ProductRemoteClient,
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
    if not isinstance(subject, dict) or not isinstance(subject.get("subject_id"), str) or not subject["subject_id"]:
        raise ValueError("invalid_product_subject")
    # The token crosses only the private child stdout -> Electron main-process
    # pipe; never store it in a profile, HTTP response, renderer or log.
    return {
        "schema": _SCHEMA, "ok": True, "access_token": token,
        "email": session.email or email.strip(),
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
