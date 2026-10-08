"""Validate and normalize Codex notifications at the provider boundary.

Private Runtime adapter only. These are not platform authorization events or
public Studio ports. No raw Codex command, argument, path, tool output or
provider-auth data is ever forwarded by this module.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


_ALLOWED_ITEM_KINDS = frozenset({
    "agentMessage", "userMessage", "reasoning", "commandExecution", "fileChange",
    "mcpToolCall", "webSearch", "imageView", "plan", "collabToolCall",
})
_TERMINAL_STATES = frozenset({"completed", "failed", "interrupted"})
_MAX_DELTA_CHARS = 65536
_MAX_ID_CHARS = 256


def _safe_id(value: Any) -> str | None:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > _MAX_ID_CHARS
        or any(ord(char) < 32 or ord(char) == 127 for char in value)
    ):
        return None
    return value


def normalize_codex_notification(packet: Mapping[str, Any]) -> dict[str, str] | None:
    """Return bounded, presentation-safe *metadata* for selected events.

    The authorized host must additionally bind thread_id to project/account and
    ensure current grant/sandbox policy before presenting the event. Nothing
    here grants access or persists a separate thread/history database.

    Unknown events are ignored rather than implicitly treated as model text.
    Every allowed event requires a concrete thread ID for isolation.
    """
    if not isinstance(packet, Mapping) or "id" in packet:
        return None
    method = packet.get("method")
    params = packet.get("params")
    if not isinstance(method, str) or not isinstance(params, Mapping):
        return None
    thread_id = _safe_id(params.get("threadId"))
    if thread_id is None:
        return None

    if method == "item/agentMessage/delta":
        turn_id = _safe_id(params.get("turnId"))
        delta = params.get("delta")
        if turn_id is None or not isinstance(delta, str) or not delta or len(delta) > _MAX_DELTA_CHARS:
            return None
        return {"kind": "message_delta", "thread_id": thread_id, "turn_id": turn_id, "delta": delta}

    if method in ("turn/started", "turn/completed"):
        turn = params.get("turn")
        if not isinstance(turn, Mapping):
            return None
        turn_id = _safe_id(turn.get("id"))
        if turn_id is None:
            return None
        if method == "turn/started":
            return {"kind": "turn_started", "thread_id": thread_id, "turn_id": turn_id}
        status = turn.get("status")
        if not isinstance(status, str) or status not in _TERMINAL_STATES:
            return None
        return {
            "kind": "turn_completed", "thread_id": thread_id,
            "turn_id": turn_id, "status": status,
        }

    if method in ("item/started", "item/completed"):
        item = params.get("item")
        if not isinstance(item, Mapping):
            return None
        item_id = _safe_id(item.get("id"))
        item_type = item.get("type")
        if item_id is None or not isinstance(item_type, str) or item_type not in _ALLOWED_ITEM_KINDS:
            return None
        # No command, cwd, patch, tool input/output, or provider metadata.
        turn_id = _safe_id(params.get("turnId"))
        if turn_id is None:
            return None
        return {
            "kind": "item_started" if method == "item/started" else "item_completed",
            "thread_id": thread_id, "turn_id": turn_id,
            "item_id": item_id, "item_type": item_type,
        }

    return None
