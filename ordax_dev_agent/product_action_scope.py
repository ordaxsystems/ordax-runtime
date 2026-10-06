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
        "computer.screen_info",
        "computer.screenshot",
        "computer.scroll",
        "computer.search",
        "computer.terminate_process",
        "computer.text_patch",
        "computer.text_read",
        "computer.text_write",
        "computer.type",
        "computer.windows",
    }
)

DEVICE_SCOPED_ACTIONS = frozenset({
    *COMPUTER_DEVICE_ACTIONS,
    *APP_INTELLIGENCE_DEVICE_ACTIONS,
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
