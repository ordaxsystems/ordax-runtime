"""Read-only Unity editor health actions.

Separated from the larger Unity action module so status semantics stay small,
auditable, and impossible to confuse with active recovery behavior.
"""
from __future__ import annotations

from typing import Any

from .models import ActionResult


class UnityStatusActions:
    """Observational Unity status that never nudges, focuses, or mutates Unity."""

    def unity_editor_status(self, payload: dict[str, Any]) -> ActionResult:
        unsupported = set(payload) - {"project"}
        if unsupported:
            return ActionResult(
                False,
                f"unsupported fields: {', '.join(sorted(unsupported))}",
            )
        # Deliberately no project file writes, import waits, companion nudges, or
        # editor focus. Active recovery belongs to unity.refresh_editor and the
        # dedicated recovery actions.
        editor = self._editor(payload)
        status = editor.status()
        ready = bool(status.get("presence_fresh"))
        return ActionResult(
            ready,
            "Unity Editor companion ready" if ready else "Unity Editor companion not ready",
            status,
        )
