from __future__ import annotations

import json
import sys
import traceback
from typing import Any

from ordax_dev_agent.actions import ActionRegistry
from ordax_dev_agent.config import AgentConfig

from .product_web_desktop import StudioProductApi


_ALLOWED_METHODS = frozenset(
    {
        "projects_catalog",
        "startup_project",
        "bootstrap",
        "select_project",
        "inventory",
        "read_file",
        "save_file",
        "preview_status",
        "preview_start",
        "preview_stop",
        "preview_capture",
        "preview_logs",
        "preview_image",
        "execution_status",
        "task_add",
        "checkpoint",
        "product_status",
        "computer_access_settings",
        "save_computer_access_settings",
        "health",
        "briefing",
        "search",
        "git_diff",
        "memory_context",
        "ai_sessions_status",
        "assistant_catalog",
        "assistant_state",
        "assistant_create_chat",
        "assistant_select_chat",
        "assistant_update_chat",
        "assistant_close_chat",
        "connect_product_account",
        "remote_computer_grants",
        "authorize_remote_computer_grant",
        "revoke_remote_computer_grant",
        "remote_app_intelligence_grants",
        "authorize_remote_app_intelligence_grant",
        "revoke_remote_app_intelligence_grant",
        "blender_prepare",
        "blender_install_bridge",
        "blender_instances",
        "blender_adopt",
        "blender_start",
    }
)


def _response(request_id: Any, *, result: Any = None, error: str | None = None) -> str:
    payload: dict[str, Any] = {"id": request_id}
    if error is None:
        payload["result"] = result
    else:
        payload["error"] = error
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _invoke(api: StudioProductApi, method: str, args: Any) -> Any:
    if method not in _ALLOWED_METHODS:
        raise ValueError(f"workbench method is not allowed: {method}")
    target = getattr(api, method, None)
    if target is None or not callable(target):
        raise ValueError(f"workbench method is unavailable: {method}")
    if args is None:
        return target()
    if isinstance(args, list):
        return target(*args)
    if isinstance(args, dict):
        return target(**args)
    raise ValueError("workbench args must be a JSON array or object")


def main() -> int:
    api = StudioProductApi(ActionRegistry(AgentConfig.from_env()))
    for raw in sys.stdin:
        line = raw.strip()
        if not line:
            continue
        request_id: Any = None
        try:
            request = json.loads(line)
            if not isinstance(request, dict):
                raise ValueError("workbench request must be a JSON object")
            request_id = request.get("id")
            method = str(request.get("method") or "")
            result = _invoke(api, method, request.get("args", []))
            print(_response(request_id, result=result), flush=True)
        except Exception as error:
            detail = f"{type(error).__name__}: {error}"
            print(_response(request_id, error=detail), flush=True)
            traceback.print_exc(file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())