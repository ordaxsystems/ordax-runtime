from __future__ import annotations

from typing import Literal


ProductActionScope = Literal["device", "project"]

# Keep these lists explicit and byte-for-byte aligned with the Cloudflare
# product_action_scope catalog. Device-scoped App Intelligence is deliberately
# separate from Computer Control and must never be inherited from a prefix.
APP_INTELLIGENCE_DEVICE_ACTIONS = frozenset({
    "intelligence.app_catalog",
    "intelligence.app_detail",
})

NATIVE_SURFACE_DEVICE_ACTIONS = frozenset({
    "ordax.surface.status",
    "ordax.surface.open",
})

COMPUTER_DEVICE_ACTIONS = frozenset(
    {
        "computer.access_status",
        "computer.active_window",
        "computer.click",
        "computer.clipboard_read",
        "computer.clipboard_write",
        "computer.directory_create",
        "computer.directory_list",
        "computer.drag",
        "computer.file_stat",
        "computer.focus_window",
        "computer.hotkey",
        "computer.launch_app",
        "computer.mouse_move",
        "computer.path_move",
        "computer.path_remove",
        "computer.processes",
        "computer.process_start",
        "computer.process_status",
        "computer.process_list",
        "computer.process_logs",
        "computer.process_write_stdin",
        "computer.process_stop",
        "computer.screen_info",
        "computer.screenshot",
        "computer.scroll",
        "computer.search",
        "computer.terminate_process",
        "computer.terminal_exec",
        "computer.text_patch",
        "computer.text_read",
        "computer.text_write",
        "computer.type",
        "computer.windows",
    }
)

DEVICE_BLENDER_OPERATIONS = ('status', 'scene_snapshot', 'object_inspect', 'modeling_schema', 'start', 'object_transform', 'create_primitive', 'rig_create', 'material_apply', 'save')
DEVICE_BLENDER_ACTIONS = frozenset("computer.blender_" + name for name in DEVICE_BLENDER_OPERATIONS)

# Shared Product project-action scope: one Runtime authority for Windows and OrdaX Desktop.
PROJECT_MCP_ACTIONS = frozenset({
    "agent.project_briefing",
    "continuity.get",
    "continuity.update",
    "handoff.create",
    "handoff.get",
    # Project terminal/process actions are never authorized by a model-supplied
    # local slug. The canonical repository binding must be verified first.
    "terminal.exec",
    "process.start",
    "process.status",
    "process.list",
    "process.logs",
    "process.write_stdin",
    "process.stop",
    # Blender actions share the attested native Project Catalog binding.
    "blender.live_status",
    "blender.live_scene_snapshot",
    "blender.live_object_inspect",
    "blender.live_modeling_schema",
    "blender.live_start",
    "blender.live_object_transform",
    "blender.live_create_primitive",
    "blender.live_rig_create",
    "blender.live_material_apply",
    "blender.live_save",
    # Match the Platform's explicit project-scoped Browser/Artifact catalog.
    # Previously these actions failed at this runtime boundary even after
    # the correct Owner binding and grant were installed.
    "browser.click",
    "browser.list",
    "browser.navigate",
    "browser.screenshot",
    "browser.snapshot",
    "browser.start",
    "browser.tab_open",
    "browser.tabs",
    "browser.status",
    "browser.stop",
    "browser.type",
    "artifacts.list",
    "artifact.preview",
})

DEVICE_SCOPED_ACTIONS = frozenset({
    *DEVICE_BLENDER_ACTIONS,
    "forge.app_read",
    *COMPUTER_DEVICE_ACTIONS,
    *APP_INTELLIGENCE_DEVICE_ACTIONS,
    *NATIVE_SURFACE_DEVICE_ACTIONS,
})


def is_device_scoped_action(action: str) -> bool:
    return action in DEVICE_SCOPED_ACTIONS


def project_binding_matches_scope(
    action: str,
    project: str | None,
    *,
    project_scoped_actions: frozenset[str] | set[str],
) -> bool:
    if action in DEVICE_SCOPED_ACTIONS:
        return project is None
    if action in project_scoped_actions:
        return project is not None
    return project is None
