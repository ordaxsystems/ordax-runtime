"""Bounded, strict Product JSON decoding shared by Owner and Device clients.

This is payload validation only; Product authority, grants, retry policy and
HTTP ingress remain with their existing owners.
"""
from __future__ import annotations

import json
import math
import re
from typing import Any


MAX_PRODUCT_HTTP_RESPONSE_BYTES = 4 * 1024 * 1024
_PRODUCT_ERROR_CODE = re.compile(r"[a-z][a-z0-9_]{0,127}")


def canonical_product_error_code(value: object) -> str | None:
    """Never expose arbitrary server-supplied text in Product diagnostics."""
    if isinstance(value, str) and _PRODUCT_ERROR_CODE.fullmatch(value):
        return value
    return None


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Product response contains duplicate JSON object keys")
        result[key] = value
    return result


def _finite_json_float(value: str) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("Product response contains a non-finite JSON number")
    return number


def _reject_nonfinite(_value: str) -> None:
    raise ValueError("Non-finite JSON number")


def parse_product_json(raw: bytes) -> Any:
    """Decode one canonical JSON response, rejecting ambiguity and non-finite data.

    Callers enforce their endpoint-specific status and object-shape semantics.
    The maximum size is shared with the existing Owner streaming transport.
    """
    if not isinstance(raw, bytes) or len(raw) > MAX_PRODUCT_HTTP_RESPONSE_BYTES:
        raise ValueError("Product response exceeds the configured response limit")
    return json.loads(
        raw.decode("utf-8"),
        parse_constant=_reject_nonfinite,
        parse_float=_finite_json_float,
        object_pairs_hook=_reject_duplicate_keys,
    )
