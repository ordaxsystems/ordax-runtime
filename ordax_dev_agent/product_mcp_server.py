from __future__ import annotations

from functools import wraps
from typing import Any, Callable

from mcp.server.fastmcp import FastMCP

from . import product_mcp_handlers as handlers
from .product_action_scope import DEVICE_SCOPED_ACTIONS
from .product_mcp import PRODUCT_MCP_TOOLS


# The executable Product MCP is composed here. The large handler module remains
# transport-neutral implementation during migration; this file owns the MCP
# contract and therefore owns the device-vs-project scope visible to clients.
mcp = FastMCP("ordax-studio-remote")

# Kept patchable for focused unit tests and local embedding. Delegated handlers
# synchronize this reference immediately before use.
ProductRemoteClient = handlers.ProductRemoteClient


def _sync_remote_client() -> None:
    handlers.ProductRemoteClient = ProductRemoteClient


def _delegate(function: Callable[..., Any]) -> Callable[..., Any]:
    @wraps(function)
    def wrapped(*args: Any, **kwargs: Any) -> Any:
        _sync_remote_client()
        return function(*args, **kwargs)

    return wrapped


def _invoke(
    *,
    device_id: str,
    action: str,
    space_id: str | None = None,
    arguments: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if action not in DEVICE_SCOPED_ACTIONS:
        raise RuntimeError(f"device-scoped MCP attempted non-device action: {action}")
    _sync_remote_client()
    return handlers._invoke(
        device_id=device_id,
        action=action,
        project=None,
        space_id=space_id,
        arguments=arguments,
    )


def _register_delegate(name: str) -> None:
    function = _delegate(getattr(handlers, name))
    globals()[name] = function
    mcp.tool()(function)


# Product/account discovery remains unchanged.
_register_delegate("product_session")
_register_delegate("product_targets")

# All non-overridden tools reuse the existing typed handlers. Computer actions
# that were already device-scoped (filesystem/process/access status) are also
# reused. Only the legacy desktop wrappers below are replaced because they used
# to carry a synthetic project argument.
_DEVICE_DESKTOP_TOOL_NAMES = {
    "computer_windows",
    "computer_active_window",
    "computer_screenshot",
    "computer_screen_info",
    "computer_clipboard_read",
    "computer_focus_window",
    "computer_click",
    "computer_mouse_move",
    "computer_drag",
    "computer_clipboard_write",
    "computer_launch_app",
    "computer_scroll",
    "computer_type",
    "computer_hotkey",
}

for _tool in PRODUCT_MCP_TOOLS:
    if _tool.name not in _DEVICE_DESKTOP_TOOL_NAMES:
        _register_delegate(_tool.name)


@mcp.tool()
def computer_windows(
    device_id: str,
    max_items: int = 100,
    space_id: str | None = None,
) -> dict[str, Any]:
    return _invoke(
        device_id=device_id,
        action="computer.windows",
        space_id=space_id,
        arguments={"max_items": max_items},
    )


@mcp.tool()
def computer_active_window(
    device_id: str,
    space_id: str | None = None,
) -> dict[str, Any]:
    return _invoke(
        device_id=device_id,
        action="computer.active_window",
        space_id=space_id,
        arguments={},
    )


@mcp.tool()
def computer_screenshot(
    device_id: str,
    mode: str = "desktop",
    space_id: str | None = None,
) -> dict[str, Any]:
    return _invoke(
        device_id=device_id,
        action="computer.screenshot",
        space_id=space_id,
        arguments={"mode": mode},
    )


@mcp.tool()
def computer_screen_info(
    device_id: str,
    space_id: str | None = None,
) -> dict[str, Any]:
    return _invoke(
        device_id=device_id,
        action="computer.screen_info",
        space_id=space_id,
        arguments={},
    )


@mcp.tool()
def computer_clipboard_read(
    device_id: str,
    max_bytes: int = 65536,
    space_id: str | None = None,
) -> dict[str, Any]:
    return _invoke(
        device_id=device_id,
        action="computer.clipboard_read",
        space_id=space_id,
        arguments={"max_bytes": max_bytes},
    )


@mcp.tool()
def computer_focus_window(
    device_id: str,
    handle: str,
    space_id: str | None = None,
) -> dict[str, Any]:
    return _invoke(
        device_id=device_id,
        action="computer.focus_window",
        space_id=space_id,
        arguments={"handle": handle},
    )


@mcp.tool()
def computer_click(
    device_id: str,
    x: int,
    y: int,
    button: str = "left",
    clicks: int = 1,
    space_id: str | None = None,
) -> dict[str, Any]:
    return _invoke(
        device_id=device_id,
        action="computer.click",
        space_id=space_id,
        arguments={"x": x, "y": y, "button": button, "clicks": clicks},
    )


@mcp.tool()
def computer_mouse_move(
    device_id: str,
    x: int,
    y: int,
    duration_ms: int = 0,
    space_id: str | None = None,
) -> dict[str, Any]:
    return _invoke(
        device_id=device_id,
        action="computer.mouse_move",
        space_id=space_id,
        arguments={"x": x, "y": y, "duration_ms": duration_ms},
    )


@mcp.tool()
def computer_drag(
    device_id: str,
    from_x: int,
    from_y: int,
    to_x: int,
    to_y: int,
    button: str = "left",
    duration_ms: int = 500,
    space_id: str | None = None,
) -> dict[str, Any]:
    return _invoke(
        device_id=device_id,
        action="computer.drag",
        space_id=space_id,
        arguments={
            "from_x": from_x,
            "from_y": from_y,
            "to_x": to_x,
            "to_y": to_y,
            "button": button,
            "duration_ms": duration_ms,
        },
    )


@mcp.tool()
def computer_clipboard_write(
    device_id: str,
    text: str,
    space_id: str | None = None,
) -> dict[str, Any]:
    return _invoke(
        device_id=device_id,
        action="computer.clipboard_write",
        space_id=space_id,
        arguments={"text": text},
    )


@mcp.tool()
def computer_launch_app(
    device_id: str,
    application: str,
    args: list[str] | None = None,
    space_id: str | None = None,
) -> dict[str, Any]:
    return _invoke(
        device_id=device_id,
        action="computer.launch_app",
        space_id=space_id,
        arguments={"application": application, "args": args or []},
    )


@mcp.tool()
def computer_scroll(
    device_id: str,
    amount: int,
    horizontal: bool = False,
    space_id: str | None = None,
) -> dict[str, Any]:
    return _invoke(
        device_id=device_id,
        action="computer.scroll",
        space_id=space_id,
        arguments={"amount": amount, "horizontal": horizontal},
    )


@mcp.tool()
def computer_type(
    device_id: str,
    text: str,
    space_id: str | None = None,
) -> dict[str, Any]:
    return _invoke(
        device_id=device_id,
        action="computer.type",
        space_id=space_id,
        arguments={"text": text},
    )


@mcp.tool()
def computer_hotkey(
    device_id: str,
    keys: list[str],
    space_id: str | None = None,
) -> dict[str, Any]:
    return _invoke(
        device_id=device_id,
        action="computer.hotkey",
        space_id=space_id,
        arguments={"keys": keys},
    )


# Preserve the historical direct-Python call surface while callers migrate.
# These aliases are assigned only *after* FastMCP captured the clean device-
# scoped functions above, so they never reintroduce project into the MCP schema.
# They can be removed with the historical handler module once internal callers
# have migrated.
_MCP_DEVICE_TOOLS = {name: globals()[name] for name in _DEVICE_DESKTOP_TOOL_NAMES}
for _name in _DEVICE_DESKTOP_TOOL_NAMES:
    globals()[_name] = _delegate(getattr(handlers, _name))


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
