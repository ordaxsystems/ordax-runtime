from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
from typing import Any

from .action_contracts import dispatch_device_capability
from .product_remote import PRODUCT_REMOTE_CAPABILITY
from .models import ActionResult


class DeviceAuthorizationError(RuntimeError):
    """The supervisor must reauthenticate this device before restarting."""


class TransientDeliveryError(RuntimeError):
    """Transport failure eligible for an identical terminal-report retry."""


def read_secret(path: Path) -> str | None:
    if not path.is_file():
        return None
    value = path.read_text(encoding="utf-8").strip()
    return value or None


def decode_job_payload(row: dict[str, Any]) -> dict[str, Any]:
    encoded = row.get("payload_canonical_b64")
    expected = str(row.get("payload_sha256") or "").lower()
    if not isinstance(encoded, str) or len(expected) != 64:
        raise RuntimeError("remote job payload envelope is incomplete")
    try:
        raw = base64.b64decode(encoded, validate=True)
    except Exception as error:
        raise RuntimeError("remote job payload is not valid base64") from error

    observed = hashlib.sha256(raw).hexdigest()
    if observed != expected:
        raise RuntimeError("remote job payload digest mismatch")

    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RuntimeError("remote job payload is not valid UTF-8 JSON") from error
    if not isinstance(payload, dict):
        raise RuntimeError("remote job payload must be an object")
    return payload


def dispatch_job(
    row: dict[str, Any],
    payload: dict[str, Any],
) -> tuple[str, dict[str, Any], str | None]:
    capability = str(row.get("capability") or row.get("operation") or "")
    if capability == PRODUCT_REMOTE_CAPABILITY:
        return capability, payload, None
    dispatched = dispatch_device_capability(capability, payload)
    return dispatched.action, dispatched.payload, dispatched.project


def postgres_safe_result(value: Any) -> Any:
    """Escape NUL inside report text before JSONB signing and persistence.

    PostgreSQL jsonb rejects U+0000 even when JSON encodes it as \\u0000.
    Represent NUL visibly as the six printable characters \\u0000. Normal
    text (including literal backslash sequences) remains unchanged. This is
    deterministic for persisted report replay and does not alter arbitrary
    files or raw process streams.
    """
    if isinstance(value, str):
        return value.replace(chr(0), r"\u0000")
    if isinstance(value, dict):
        return {postgres_safe_result(key): postgres_safe_result(item)
                for key, item in value.items()}
    if isinstance(value, list):
        return [postgres_safe_result(item) for item in value]
    if isinstance(value, tuple):
        return [postgres_safe_result(item) for item in value]
    return value


# The local terminal outbox allows at most 1 MiB for its whole JSON envelope.
# Leave ample space for base64 expansion, execution context, and recovery
# metadata; never try to persist a multi-megabyte action result.
_MAX_CANONICAL_RESULT_BYTES = 256 * 1024


class _CanonicalResultOversized(ValueError):
    pass


def _bounded_result_bytes(body: dict[str, Any]) -> bytes:
    # iterencode lets us stop before constructing the entire JSON document.
    # JSON must reject NaN/Infinity rather than emit invalid protocol data.
    encoder = json.JSONEncoder(
        sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        allow_nan=False,
    )
    chunks: list[bytes] = []
    byte_count = 0
    for fragment in encoder.iterencode(body):
        part = fragment.encode("utf-8")
        byte_count += len(part)
        if byte_count > _MAX_CANONICAL_RESULT_BYTES:
            raise _CanonicalResultOversized("terminal action result too large")
        chunks.append(part)
    return b"".join(chunks)


def _unavailable_result(reason: str) -> dict[str, Any]:
    # The side effect might already have happened. Do not invent an action
    # failure, claim the output was delivered, or echo unknown sensitive data.
    return {
        "ok": False,
        "summary": "Action result unavailable; execution outcome must not be inferred.",
        "data": {"reason": reason, "execution_effect": "unknown"},
    }


def canonical_result(result: ActionResult) -> tuple[dict[str, Any], str]:
    try:
        body = {
            "ok": bool(result.ok),
            "summary": str(result.summary),
            "data": result.data if isinstance(result.data, dict) else {},
        }
        body = postgres_safe_result(body)
        encoded = _bounded_result_bytes(body)
    except _CanonicalResultOversized:
        body = _unavailable_result("result_too_large")
        encoded = _bounded_result_bytes(body)
    except (TypeError, ValueError, OverflowError, RecursionError):
        # Non-JSON types, circular objects and invalid JSON floats must not
        # strand a completed job without its terminal report. Never serialize
        # or log any part of the offending result in this failure receipt.
        body = _unavailable_result("result_invalid")
        encoded = _bounded_result_bytes(body)
    return body, hashlib.sha256(encoded).hexdigest()
