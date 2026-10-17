from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable

MAX_CAPABILITIES = 1024
MAX_CAPABILITY_LENGTH = 160


def canonical_capabilities(values: Iterable[str]) -> tuple[str, ...]:
    """Return the canonical capability set shared with the Platform contract."""
    items = list(values)
    if len(items) > MAX_CAPABILITIES:
        raise ValueError("too many connector capabilities")
    if any(not isinstance(item, str) or not item or len(item) > MAX_CAPABILITY_LENGTH for item in items):
        raise ValueError("invalid connector capability")
    return tuple(sorted(set(items)))


def capability_digest(values: Iterable[str]) -> str:
    normalized = canonical_capabilities(values)
    payload = json.dumps(
        normalized, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()
