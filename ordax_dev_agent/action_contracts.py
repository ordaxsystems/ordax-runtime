from __future__ import annotations

from dataclasses import dataclass
from typing import Any


DEVICE_ACTION_PREFIXES = (
    "blender.",
    "unity.",
    "git.",
    "project.",
    "projects.",
    "workspace.",
    "terminal.",
    "process.",
    "browser.",
    "computer.",
    "artifact.",
    "observation.",
    "game_assets.",
    "geo.",
    "visual.",
    "agent.",
    "handoff.",
    "continuity.",
)

ADAPTER_INVOKE_CAPABILITY = "ordax.dev.adapter.invoke"


@dataclass(frozen=True)
class DispatchedAction:
    action: str
    payload: dict[str, Any]
    project: str | None


def is_device_owned_capability(capability: str) -> bool:
    """Return whether a capability belongs to the Device Agent action surface."""
    return capability.startswith(DEVICE_ACTION_PREFIXES)


def dispatch_device_capability(
    capability: str,
    payload: dict[str, Any],
) -> DispatchedAction:
    """Map a remote capability envelope to one typed local Device Agent action.

    This module is intentionally transport-neutral. It owns only the contract
    between a remote capability name and the local allow-listed action surface.
    It does not grant permissions and it never accepts arbitrary shell commands.
    """
    if capability == ADAPTER_INVOKE_CAPABILITY:
        allowed = {"adapter", "action", "payload", "project"}
        if set(payload) - allowed:
            raise RuntimeError("adapter invocation contains unsupported fields")

        adapter = payload.get("adapter")
        action = payload.get("action")
        action_payload = payload.get("payload", {})
        project = payload.get("project")

        if not isinstance(action_payload, dict):
            raise RuntimeError("adapter invocation payload must be an object")
        if project is not None and not isinstance(project, str):
            raise RuntimeError("adapter invocation project must be a string")

        if adapter == "blender":
            if not isinstance(action, str) or not action.startswith("blender."):
                raise RuntimeError("adapter invocation action is not a Blender action")
            return DispatchedAction(action, action_payload, project)

        if adapter == "workspace":
            if action != "workspace.bind_project":
                raise RuntimeError("adapter invocation workspace action is not allowed")
            if project is not None:
                raise RuntimeError(
                    "workspace binding must not target an existing project"
                )
            return DispatchedAction(action, action_payload, None)

        raise RuntimeError("adapter invocation is not an allowed capability")

    if is_device_owned_capability(capability):
        project = payload.get("project")
        return DispatchedAction(
            capability,
            payload,
            project if isinstance(project, str) else None,
        )

    raise RuntimeError(f"remote capability is not owned by Device Agent: {capability}")
