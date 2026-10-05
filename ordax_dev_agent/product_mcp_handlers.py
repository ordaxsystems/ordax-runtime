from __future__ import annotations

import os
from typing import Any

from mcp.server.fastmcp import FastMCP

from .product_remote_client import ProductRemoteClient


mcp = FastMCP("ordax-studio-remote")


def _access_token() -> str:
    token = os.environ.get("ORDAX_PRODUCT_ACCESS_TOKEN", "").strip()
    if not token:
        raise RuntimeError("ORDAX_PRODUCT_ACCESS_TOKEN is required")
    return token


def _base_url() -> str:
    value = os.environ.get(
        "ORDAX_PRODUCT_CONTROL_PLANE_URL",
        "https://ordax-control-plane-v3.ordax-ac1ca1b50d09.workers.dev",
    ).strip()
    if not value:
        raise RuntimeError("ORDAX_PRODUCT_CONTROL_PLANE_URL is required")
    return value


def _invoke(
    *,
    device_id: str,
    action: str,
    project: str | None = None,
    space_id: str | None = None,
    arguments: dict[str, Any] | None = None,
) -> dict[str, Any]:
    token = _access_token()
    with ProductRemoteClient(_base_url()) as client:
        request_id = client.submit_action(
            token,
            device_id=device_id,
            action=action,
            project=project,
            space_id=space_id,
            arguments=arguments,
        )
        result = client.wait_action(token, request_id)
    return {
        "request_id": request_id,
        "status": result.get("status"),
        "result": result.get("result"),
        "error_code": result.get("error_code"),
        "created_at": result.get("created_at"),
        "started_at": result.get("started_at"),
        "finished_at": result.get("finished_at"),
    }


@mcp.tool()
def product_session() -> dict[str, Any]:
    """Return the authenticated OrdaX Product subject for this MCP host."""
    token = _access_token()
    with ProductRemoteClient(_base_url()) as client:
        return client.session(token)


@mcp.tool()
def product_targets(space_id: str | None = None) -> list[dict[str, Any]]:
    """List only devices and grants authorized for the authenticated Product subject."""
    token = _access_token()
    with ProductRemoteClient(_base_url()) as client:
        return client.targets(token, space_id=space_id)


@mcp.tool()
def projects_list(
    device_id: str,
    space_id: str | None = None,
) -> dict[str, Any]:
    """List granted projects; use first when a resume/continue request does not identify the project."""
    return _invoke(
        device_id=device_id,
        action="projects.list",
        space_id=space_id,
        arguments={},
    )


@mcp.tool()
def project_create(
    device_id: str,
    slug: str,
    name: str = "",
    apps: list[str] | None = None,
    set_default: bool = True,
    git_init: bool = True,
    readme: bool = True,
    space_id: str | None = None,
) -> dict[str, Any]:
    """Create and register a project inside the device's configured ORDAX workspace."""
    return _invoke(
        device_id=device_id,
        action="workspace.project_create",
        space_id=space_id,
        arguments={
            "slug": slug,
            "name": name or slug,
            "apps": list(apps or []),
            "set_default": set_default,
            "git_init": git_init,
            "readme": readme,
        },
    )


@mcp.tool()
def project_import(
    device_id: str,
    slug: str,
    relative_path: str,
    apps: list[str] | None = None,
    set_default: bool = False,
    blender_scripts_dir: str = "automation/blender",
    blend_file: str = "",
    space_id: str | None = None,
) -> dict[str, Any]:
    """Register an existing project inside the configured ORDAX workspace."""
    arguments: dict[str, Any] = {
        "slug": slug,
        "relative_path": relative_path,
        "apps": list(apps or []),
        "set_default": set_default,
        "blender_scripts_dir": blender_scripts_dir,
    }
    if blend_file:
        arguments["blend_file"] = blend_file
    return _invoke(
        device_id=device_id,
        action="workspace.bind_project",
        space_id=space_id,
        arguments=arguments,
    )


@mcp.tool()
def repository_catalog(
    device_id: str,
    space_id: str | None = None,
) -> dict[str, Any]:
    """List canonical ORDAX repositories to disambiguate the project before resuming work."""
    return _invoke(
        device_id=device_id,
        action="workspace.repository_catalog",
        space_id=space_id,
        arguments={},
    )


@mcp.tool()
def handoff_get(
    device_id: str,
    project: str,
    handoff_id: str,
    space_id: str | None = None,
) -> dict[str, Any]:
    """Load one expiring ORDAX continuation handoff for a granted project."""
    return _invoke(
        device_id=device_id,
        action="handoff.get",
        project=project,
        space_id=space_id,
        arguments={"project": project, "handoff_id": handoff_id},
    )


@mcp.tool()
def handoff_create(
    device_id: str,
    project: str,
    summary: str,
    next_action: str = "",
    completed: list[str] | None = None,
    blockers: list[str] | None = None,
    changed_paths: list[str] | None = None,
    ttl_hours: int = 24,
    space_id: str | None = None,
) -> dict[str, Any]:
    """Create a bounded expiring handoff for continuing work in a fresh chat."""
    return _invoke(
        device_id=device_id,
        action="handoff.create",
        project=project,
        space_id=space_id,
        arguments={
            "project": project,
            "summary": summary,
            "next_action": next_action,
            "completed": list(completed or []),
            "blockers": list(blockers or []),
            "changed_paths": list(changed_paths or []),
            "ttl_hours": ttl_hours,
        },
    )


@mcp.tool()
def project_inventory(
    device_id: str,
    project: str,
    max_depth: int = 4,
    max_entries: int = 500,
    space_id: str | None = None,
) -> dict[str, Any]:
    """Inspect a bounded read-only inventory of one granted project."""
    return _invoke(
        device_id=device_id,
        action="project.inventory",
        project=project,
        space_id=space_id,
        arguments={
            "project": project,
            "max_depth": max_depth,
            "max_entries": max_entries,
        },
    )


@mcp.tool()
def project_text_read(
    device_id: str,
    project: str,
    path: str,
    space_id: str | None = None,
) -> dict[str, Any]:
    """Read one granted project-relative text file."""
    return _invoke(
        device_id=device_id,
        action="project.text_read",
        project=project,
        space_id=space_id,
        arguments={"project": project, "path": path},
    )


@mcp.tool()
def project_briefing(
    device_id: str,
    project: str,
    query: str = "",
    recall_limit: int = 20,
    space_id: str | None = None,
) -> dict[str, Any]:
    """Load durable project state and relevant context for fresh-chat continuation; pass the user intent as query when useful."""
    return _invoke(
        device_id=device_id,
        action="agent.project_briefing",
        project=project,
        space_id=space_id,
        arguments={"project": project, "query": query, "recall_limit": recall_limit},
    )


@mcp.tool()
def continuity_state(
    device_id: str,
    project: str,
    space_id: str | None = None,
) -> dict[str, Any]:
    """Read the non-expiring continuation state for a granted project."""
    return _invoke(
        device_id=device_id,
        action="continuity.get",
        project=project,
        space_id=space_id,
        arguments={"project": project},
    )


@mcp.tool()
def continuity_update(
    device_id: str,
    project: str,
    summary: str,
    next_action: str = "",
    completed: list[str] | None = None,
    blockers: list[str] | None = None,
    changed_paths: list[str] | None = None,
    space_id: str | None = None,
) -> dict[str, Any]:
    """Persist non-expiring project progress for future conversations."""
    return _invoke(
        device_id=device_id,
        action="continuity.update",
        project=project,
        space_id=space_id,
        arguments={
            "project": project,
            "summary": summary,
            "next_action": next_action,
            "completed": list(completed or []),
            "blockers": list(blockers or []),
            "changed_paths": list(changed_paths or []),
        },
    )


@mcp.tool()
def project_health(
    device_id: str,
    project: str,
    space_id: str | None = None,
) -> dict[str, Any]:
    """Read sanitized ORDAX Studio health for one granted project."""
    return _invoke(
        device_id=device_id,
        action="agent.project_health",
        project=project,
        space_id=space_id,
        arguments={"project": project},
    )


@mcp.tool()
def project_search(
    device_id: str,
    project: str,
    query: str,
    max_results: int = 40,
    space_id: str | None = None,
) -> dict[str, Any]:
    """Search bounded approved text sources in one granted project."""
    return _invoke(
        device_id=device_id,
        action="project.search_text",
        project=project,
        space_id=space_id,
        arguments={"project": project, "query": query, "max_results": max_results},
    )


@mcp.tool()
def project_read_batch(
    device_id: str,
    project: str,
    paths: list[str],
    max_total_bytes: int = 393216,
    space_id: str | None = None,
) -> dict[str, Any]:
    """Read several approved text files from one granted project."""
    return _invoke(
        device_id=device_id,
        action="project.text_read_batch",
        project=project,
        space_id=space_id,
        arguments={"project": project, "paths": paths, "max_total_bytes": max_total_bytes},
    )


@mcp.tool()
def project_preview_status(
    device_id: str,
    project: str,
    space_id: str | None = None,
) -> dict[str, Any]:
    """Read sanitized project preview/runtime state without exposing local process details."""
    return _invoke(
        device_id=device_id,
        action="project.preview_status",
        project=project,
        space_id=space_id,
        arguments={"project": project},
    )


@mcp.tool()
def git_status(
    device_id: str,
    project: str,
    space_id: str | None = None,
) -> dict[str, Any]:
    """Read Git status for one granted project."""
    return _invoke(
        device_id=device_id,
        action="git.status",
        project=project,
        space_id=space_id,
        arguments={"project": project},
    )


@mcp.tool()
def git_diff(
    device_id: str,
    project: str,
    paths: list[str] | None = None,
    space_id: str | None = None,
) -> dict[str, Any]:
    """Read a bounded Git diff for one granted project."""
    arguments: dict[str, Any] = {"project": project}
    if paths is not None:
        arguments["paths"] = paths
    return _invoke(
        device_id=device_id,
        action="git.diff",
        project=project,
        space_id=space_id,
        arguments=arguments,
    )


@mcp.tool()
def artifacts_list(
    device_id: str,
    project: str,
    max_items: int = 100,
    space_id: str | None = None,
) -> dict[str, Any]:
    """List bounded metadata for artifacts in one granted project."""
    return _invoke(
        device_id=device_id,
        action="artifacts.list",
        project=project,
        space_id=space_id,
        arguments={"project": project, "max_items": max_items},
    )


@mcp.tool()
def artifact_preview(
    device_id: str,
    project: str,
    artifact_name: str | None = None,
    project_artifact_path: str | None = None,
    thumbnail: bool = False,
    max_bytes: int = 262144,
    max_width: int = 480,
    max_height: int = 320,
    quality: int = 72,
    space_id: str | None = None,
) -> dict[str, Any]:
    """Read a bounded preview of one granted artifact."""
    arguments: dict[str, Any] = {
        "project": project,
        "thumbnail": thumbnail,
        "max_bytes": max_bytes,
        "max_width": max_width,
        "max_height": max_height,
        "quality": quality,
    }
    if artifact_name is not None:
        arguments["artifact_name"] = artifact_name
    if project_artifact_path is not None:
        arguments["project_artifact_path"] = project_artifact_path
    return _invoke(
        device_id=device_id,
        action="artifact.preview",
        project=project,
        space_id=space_id,
        arguments=arguments,
    )


@mcp.tool()
def project_text_write(device_id: str, project: str, path: str, content: str, expected_sha256: str | None = None, create: bool = False, space_id: str | None = None) -> dict[str, Any]:
    arguments: dict[str, Any] = {"project": project, "path": path, "content": content, "create": create}
    if expected_sha256 is not None:
        arguments["expected_sha256"] = expected_sha256
    return _invoke(device_id=device_id, action="project.text_write", project=project, space_id=space_id, arguments=arguments)


@mcp.tool()
def project_text_patch(device_id: str, project: str, path: str, expected_sha256: str, replacements: list[dict[str, Any]], space_id: str | None = None) -> dict[str, Any]:
    return _invoke(device_id=device_id, action="project.text_patch", project=project, space_id=space_id, arguments={"project": project, "path": path, "expected_sha256": expected_sha256, "replacements": replacements})


@mcp.tool()
def workspace_file_stat(device_id: str, project: str, path: str = ".", space_id: str | None = None) -> dict[str, Any]:
    return _invoke(device_id=device_id, action="workspace.file_stat", project=project, space_id=space_id, arguments={"project": project, "path": path})


@mcp.tool()
def workspace_directory_list(device_id: str, project: str, path: str = ".", max_depth: int = 2, max_entries: int = 500, include_hidden: bool = False, space_id: str | None = None) -> dict[str, Any]:
    return _invoke(device_id=device_id, action="workspace.directory_list", project=project, space_id=space_id, arguments={"project": project, "path": path, "max_depth": max_depth, "max_entries": max_entries, "include_hidden": include_hidden})


@mcp.tool()
def workspace_text_read(device_id: str, project: str, path: str, start_line: int = 1, end_line: int | None = None, space_id: str | None = None) -> dict[str, Any]:
    arguments: dict[str, Any] = {"project": project, "path": path, "start_line": start_line}
    if end_line is not None:
        arguments["end_line"] = end_line
    return _invoke(device_id=device_id, action="workspace.text_read", project=project, space_id=space_id, arguments=arguments)


@mcp.tool()
def workspace_text_write(device_id: str, project: str, path: str, content: str, expected_sha256: str | None = None, create: bool = False, space_id: str | None = None) -> dict[str, Any]:
    arguments: dict[str, Any] = {"project": project, "path": path, "content": content, "create": create}
    if expected_sha256 is not None:
        arguments["expected_sha256"] = expected_sha256
    return _invoke(device_id=device_id, action="workspace.text_write", project=project, space_id=space_id, arguments=arguments)


@mcp.tool()
def workspace_text_patch(device_id: str, project: str, path: str, expected_sha256: str, replacements: list[dict[str, Any]], space_id: str | None = None) -> dict[str, Any]:
    return _invoke(device_id=device_id, action="workspace.text_patch", project=project, space_id=space_id, arguments={"project": project, "path": path, "expected_sha256": expected_sha256, "replacements": replacements})


@mcp.tool()
def workspace_directory_create(device_id: str, project: str, path: str, parents: bool = True, space_id: str | None = None) -> dict[str, Any]:
    return _invoke(device_id=device_id, action="workspace.directory_create", project=project, space_id=space_id, arguments={"project": project, "path": path, "parents": parents})


@mcp.tool()
def workspace_path_remove(device_id: str, project: str, path: str, expected_sha256: str | None = None, recursive: bool = False, space_id: str | None = None) -> dict[str, Any]:
    arguments: dict[str, Any] = {"project": project, "path": path, "recursive": recursive}
    if expected_sha256 is not None:
        arguments["expected_sha256"] = expected_sha256
    return _invoke(device_id=device_id, action="workspace.path_remove", project=project, space_id=space_id, arguments=arguments)


@mcp.tool()
def workspace_path_move(device_id: str, project: str, source: str, destination: str, expected_sha256: str | None = None, overwrite: bool = False, space_id: str | None = None) -> dict[str, Any]:
    arguments: dict[str, Any] = {"project": project, "source": source, "destination": destination, "overwrite": overwrite}
    if expected_sha256 is not None:
        arguments["expected_sha256"] = expected_sha256
    return _invoke(device_id=device_id, action="workspace.path_move", project=project, space_id=space_id, arguments=arguments)


@mcp.tool()
def git_command(device_id: str, project: str, args: list[str], timeout_seconds: int = 300, space_id: str | None = None) -> dict[str, Any]:
    return _invoke(device_id=device_id, action="git.command", project=project, space_id=space_id, arguments={"project": project, "args": args, "timeout_seconds": timeout_seconds})


@mcp.tool()
def terminal_exec(
    device_id: str,
    project: str,
    argv: list[str] | None = None,
    command: str | None = None,
    shell: bool = False,
    cwd: str = ".",
    timeout_seconds: int = 900,
    env: dict[str, str] | None = None,
    space_id: str | None = None,
) -> dict[str, Any]:
    arguments: dict[str, Any] = {
        "project": project,
        "cwd": cwd,
        "shell": shell,
        "timeout_seconds": timeout_seconds,
    }
    if argv is not None:
        arguments["argv"] = argv
    if command is not None:
        arguments["command"] = command
    if env is not None:
        arguments["env"] = env
    return _invoke(device_id=device_id, action="terminal.exec", project=project, space_id=space_id, arguments=arguments)


@mcp.tool()
def process_status(device_id: str, project: str, process_id: str, space_id: str | None = None) -> dict[str, Any]:
    return _invoke(device_id=device_id, action="process.status", project=project, space_id=space_id, arguments={"project": project, "process_id": process_id})


@mcp.tool()
def process_list(device_id: str, project: str, space_id: str | None = None) -> dict[str, Any]:
    return _invoke(device_id=device_id, action="process.list", project=project, space_id=space_id, arguments={"project": project})


@mcp.tool()
def process_logs(device_id: str, project: str, process_id: str, max_bytes: int = 65536, space_id: str | None = None) -> dict[str, Any]:
    return _invoke(device_id=device_id, action="process.logs", project=project, space_id=space_id, arguments={"project": project, "process_id": process_id, "max_bytes": max_bytes})


@mcp.tool()
def process_start(device_id: str, project: str, argv: list[str], cwd: str = ".", env: dict[str, str] | None = None, wait_seconds: float = 0.5, space_id: str | None = None) -> dict[str, Any]:
    arguments: dict[str, Any] = {"project": project, "argv": argv, "cwd": cwd, "wait_seconds": wait_seconds}
    if env is not None:
        arguments["env"] = env
    return _invoke(device_id=device_id, action="process.start", project=project, space_id=space_id, arguments=arguments)


@mcp.tool()
def process_write_stdin(device_id: str, project: str, process_id: str, text: str, newline: bool = True, space_id: str | None = None) -> dict[str, Any]:
    return _invoke(device_id=device_id, action="process.write_stdin", project=project, space_id=space_id, arguments={"project": project, "process_id": process_id, "text": text, "newline": newline})


@mcp.tool()
def process_stop(device_id: str, project: str, process_id: str, space_id: str | None = None) -> dict[str, Any]:
    return _invoke(device_id=device_id, action="process.stop", project=project, space_id=space_id, arguments={"project": project, "process_id": process_id})


@mcp.tool()
def browser_status(device_id: str, project: str, session_id: str, space_id: str | None = None) -> dict[str, Any]:
    return _invoke(device_id=device_id, action="browser.status", project=project, space_id=space_id, arguments={"project": project, "session_id": session_id})


@mcp.tool()
def browser_list(device_id: str, project: str, space_id: str | None = None) -> dict[str, Any]:
    return _invoke(device_id=device_id, action="browser.list", project=project, space_id=space_id, arguments={"project": project})


@mcp.tool()
def browser_snapshot(device_id: str, project: str, session_id: str, max_elements: int = 200, space_id: str | None = None) -> dict[str, Any]:
    return _invoke(device_id=device_id, action="browser.snapshot", project=project, space_id=space_id, arguments={"project": project, "session_id": session_id, "max_elements": max_elements})


@mcp.tool()
def browser_screenshot(device_id: str, project: str, session_id: str, width: int = 1440, height: int = 900, space_id: str | None = None) -> dict[str, Any]:
    return _invoke(device_id=device_id, action="browser.screenshot", project=project, space_id=space_id, arguments={"project": project, "session_id": session_id, "width": width, "height": height})


@mcp.tool()
def browser_start(device_id: str, project: str, url: str = "about:blank", headless: bool = True, wait_seconds: float = 8.0, space_id: str | None = None) -> dict[str, Any]:
    return _invoke(device_id=device_id, action="browser.start", project=project, space_id=space_id, arguments={"project": project, "url": url, "headless": headless, "wait_seconds": wait_seconds})


@mcp.tool()
def browser_navigate(device_id: str, project: str, session_id: str, url: str, wait_seconds: float = 15.0, space_id: str | None = None) -> dict[str, Any]:
    return _invoke(device_id=device_id, action="browser.navigate", project=project, space_id=space_id, arguments={"project": project, "session_id": session_id, "url": url, "wait_seconds": wait_seconds})


@mcp.tool()
def browser_click(device_id: str, project: str, session_id: str, node_id: str, space_id: str | None = None) -> dict[str, Any]:
    return _invoke(device_id=device_id, action="browser.click", project=project, space_id=space_id, arguments={"project": project, "session_id": session_id, "node_id": node_id})


@mcp.tool()
def browser_type(device_id: str, project: str, session_id: str, node_id: str, text: str, clear: bool = True, space_id: str | None = None) -> dict[str, Any]:
    return _invoke(device_id=device_id, action="browser.type", project=project, space_id=space_id, arguments={"project": project, "session_id": session_id, "node_id": node_id, "text": text, "clear": clear})


@mcp.tool()
def browser_stop(device_id: str, project: str, session_id: str, space_id: str | None = None) -> dict[str, Any]:
    return _invoke(device_id=device_id, action="browser.stop", project=project, space_id=space_id, arguments={"project": project, "session_id": session_id})


@mcp.tool()
def computer_windows(device_id: str, project: str, max_items: int = 100, space_id: str | None = None) -> dict[str, Any]:
    return _invoke(device_id=device_id, action="computer.windows", project=project, space_id=space_id, arguments={"project": project, "max_items": max_items})


@mcp.tool()
def computer_active_window(device_id: str, project: str, space_id: str | None = None) -> dict[str, Any]:
    return _invoke(device_id=device_id, action="computer.active_window", project=project, space_id=space_id, arguments={"project": project})


@mcp.tool()
def computer_screenshot(device_id: str, project: str, mode: str = "desktop", space_id: str | None = None) -> dict[str, Any]:
    return _invoke(device_id=device_id, action="computer.screenshot", project=project, space_id=space_id, arguments={"project": project, "mode": mode})


@mcp.tool()
def computer_screen_info(device_id: str, project: str, space_id: str | None = None) -> dict[str, Any]:
    return _invoke(device_id=device_id, action="computer.screen_info", project=project, space_id=space_id, arguments={"project": project})


@mcp.tool()
def computer_clipboard_read(device_id: str, project: str, max_bytes: int = 65536, space_id: str | None = None) -> dict[str, Any]:
    return _invoke(device_id=device_id, action="computer.clipboard_read", project=project, space_id=space_id, arguments={"project": project, "max_bytes": max_bytes})


@mcp.tool()
def computer_focus_window(device_id: str, project: str, handle: str, space_id: str | None = None) -> dict[str, Any]:
    return _invoke(device_id=device_id, action="computer.focus_window", project=project, space_id=space_id, arguments={"project": project, "handle": handle})


@mcp.tool()
def computer_click(device_id: str, project: str, x: int, y: int, button: str = "left", clicks: int = 1, space_id: str | None = None) -> dict[str, Any]:
    return _invoke(device_id=device_id, action="computer.click", project=project, space_id=space_id, arguments={"project": project, "x": x, "y": y, "button": button, "clicks": clicks})


@mcp.tool()
def computer_mouse_move(device_id: str, project: str, x: int, y: int, duration_ms: int = 0, space_id: str | None = None) -> dict[str, Any]:
    return _invoke(device_id=device_id, action="computer.mouse_move", project=project, space_id=space_id, arguments={"project": project, "x": x, "y": y, "duration_ms": duration_ms})


@mcp.tool()
def computer_drag(device_id: str, project: str, from_x: int, from_y: int, to_x: int, to_y: int, button: str = "left", duration_ms: int = 500, space_id: str | None = None) -> dict[str, Any]:
    return _invoke(device_id=device_id, action="computer.drag", project=project, space_id=space_id, arguments={"project": project, "from_x": from_x, "from_y": from_y, "to_x": to_x, "to_y": to_y, "button": button, "duration_ms": duration_ms})


@mcp.tool()
def computer_clipboard_write(device_id: str, project: str, text: str, space_id: str | None = None) -> dict[str, Any]:
    return _invoke(device_id=device_id, action="computer.clipboard_write", project=project, space_id=space_id, arguments={"project": project, "text": text})


@mcp.tool()
def computer_launch_app(device_id: str, project: str, application: str, args: list[str] | None = None, space_id: str | None = None) -> dict[str, Any]:
    return _invoke(device_id=device_id, action="computer.launch_app", project=project, space_id=space_id, arguments={"project": project, "application": application, "args": args or []})


@mcp.tool()
def computer_scroll(device_id: str, project: str, amount: int, horizontal: bool = False, space_id: str | None = None) -> dict[str, Any]:
    return _invoke(device_id=device_id, action="computer.scroll", project=project, space_id=space_id, arguments={"project": project, "amount": amount, "horizontal": horizontal})


@mcp.tool()
def computer_type(device_id: str, project: str, text: str, space_id: str | None = None) -> dict[str, Any]:
    return _invoke(device_id=device_id, action="computer.type", project=project, space_id=space_id, arguments={"project": project, "text": text})


@mcp.tool()
def computer_hotkey(device_id: str, project: str, keys: list[str], space_id: str | None = None) -> dict[str, Any]:
    return _invoke(device_id=device_id, action="computer.hotkey", project=project, space_id=space_id, arguments={"project": project, "keys": keys})


@mcp.tool()
def computer_access_status(device_id: str, space_id: str | None = None) -> dict[str, Any]:
    return _invoke(device_id=device_id, action="computer.access_status", space_id=space_id, arguments={})


@mcp.tool()
def computer_file_stat(device_id: str, path: str, space_id: str | None = None) -> dict[str, Any]:
    return _invoke(device_id=device_id, action="computer.file_stat", space_id=space_id, arguments={"path": path})


@mcp.tool()
def computer_directory_list(
    device_id: str,
    path: str,
    max_depth: int = 2,
    max_entries: int = 500,
    include_hidden: bool = False,
    space_id: str | None = None,
) -> dict[str, Any]:
    return _invoke(
        device_id=device_id,
        action="computer.directory_list",
        space_id=space_id,
        arguments={
            "path": path,
            "max_depth": max_depth,
            "max_entries": max_entries,
            "include_hidden": include_hidden,
        },
    )


@mcp.tool()
def computer_text_read(
    device_id: str,
    path: str,
    start_line: int = 1,
    end_line: int | None = None,
    space_id: str | None = None,
) -> dict[str, Any]:
    arguments: dict[str, Any] = {"path": path, "start_line": start_line}
    if end_line is not None:
        arguments["end_line"] = end_line
    return _invoke(device_id=device_id, action="computer.text_read", space_id=space_id, arguments=arguments)


@mcp.tool()
def computer_search(
    device_id: str,
    root: str,
    query: str,
    mode: str = "name",
    max_results: int = 100,
    max_depth: int = 6,
    include_hidden: bool = False,
    space_id: str | None = None,
) -> dict[str, Any]:
    return _invoke(
        device_id=device_id,
        action="computer.search",
        space_id=space_id,
        arguments={
            "root": root,
            "query": query,
            "mode": mode,
            "max_results": max_results,
            "max_depth": max_depth,
            "include_hidden": include_hidden,
        },
    )


@mcp.tool()
def computer_processes(
    device_id: str,
    query: str = "",
    max_items: int = 200,
    space_id: str | None = None,
) -> dict[str, Any]:
    return _invoke(
        device_id=device_id,
        action="computer.processes",
        space_id=space_id,
        arguments={"query": query, "max_items": max_items},
    )


@mcp.tool()
def computer_terminate_process(
    device_id: str,
    pid: int,
    expected_name: str,
    force: bool = False,
    tree: bool = True,
    space_id: str | None = None,
) -> dict[str, Any]:
    return _invoke(
        device_id=device_id,
        action="computer.terminate_process",
        space_id=space_id,
        arguments={
            "pid": pid,
            "expected_name": expected_name,
            "force": force,
            "tree": tree,
        },
    )


@mcp.tool()
def computer_text_write(
    device_id: str,
    path: str,
    content: str,
    expected_sha256: str = "",
    create: bool = False,
    space_id: str | None = None,
) -> dict[str, Any]:
    arguments: dict[str, Any] = {"path": path, "content": content, "create": create}
    if expected_sha256:
        arguments["expected_sha256"] = expected_sha256
    return _invoke(device_id=device_id, action="computer.text_write", space_id=space_id, arguments=arguments)


@mcp.tool()
def computer_text_patch(
    device_id: str,
    path: str,
    expected_sha256: str,
    replacements: list[dict[str, Any]],
    space_id: str | None = None,
) -> dict[str, Any]:
    return _invoke(
        device_id=device_id,
        action="computer.text_patch",
        space_id=space_id,
        arguments={"path": path, "expected_sha256": expected_sha256, "replacements": replacements},
    )


@mcp.tool()
def computer_directory_create(
    device_id: str,
    path: str,
    parents: bool = True,
    space_id: str | None = None,
) -> dict[str, Any]:
    return _invoke(
        device_id=device_id,
        action="computer.directory_create",
        space_id=space_id,
        arguments={"path": path, "parents": parents},
    )


@mcp.tool()
def computer_path_move(
    device_id: str,
    source: str,
    destination: str,
    overwrite: bool = False,
    space_id: str | None = None,
) -> dict[str, Any]:
    return _invoke(
        device_id=device_id,
        action="computer.path_move",
        space_id=space_id,
        arguments={"source": source, "destination": destination, "overwrite": overwrite},
    )


@mcp.tool()
def computer_path_remove(
    device_id: str,
    path: str,
    recursive: bool = False,
    space_id: str | None = None,
) -> dict[str, Any]:
    return _invoke(
        device_id=device_id,
        action="computer.path_remove",
        space_id=space_id,
        arguments={"path": path, "recursive": recursive},
    )


@mcp.tool()
def blender_status(device_id: str, project: str, space_id: str | None = None) -> dict[str, Any]:
    return _invoke(device_id=device_id, action="blender.live_status", project=project, space_id=space_id, arguments={"project": project})


@mcp.tool()
def blender_scene_snapshot(device_id: str, project: str, max_objects: int = 200, object_names: list[str] | None = None, timeout_seconds: float = 45.0, space_id: str | None = None) -> dict[str, Any]:
    arguments: dict[str, Any] = {"project": project, "max_objects": max_objects, "timeout_seconds": timeout_seconds}
    if object_names is not None:
        arguments["object_names"] = object_names
    return _invoke(device_id=device_id, action="blender.live_scene_snapshot", project=project, space_id=space_id, arguments=arguments)


@mcp.tool()
def blender_object_inspect(device_id: str, project: str, object_name: str | None = None, ordax_object_id: str | None = None, timeout_seconds: float = 30.0, space_id: str | None = None) -> dict[str, Any]:
    arguments: dict[str, Any] = {"project": project, "timeout_seconds": timeout_seconds}
    if object_name is not None:
        arguments["object_name"] = object_name
    if ordax_object_id is not None:
        arguments["ordax_object_id"] = ordax_object_id
    return _invoke(device_id=device_id, action="blender.live_object_inspect", project=project, space_id=space_id, arguments=arguments)


@mcp.tool()
def blender_modeling_schema(device_id: str, project: str, space_id: str | None = None) -> dict[str, Any]:
    return _invoke(device_id=device_id, action="blender.live_modeling_schema", project=project, space_id=space_id, arguments={"project": project})


@mcp.tool()
def blender_start(device_id: str, project: str, wait_seconds: float = 60.0, pid: int | None = None, adopt_blank: bool = False, space_id: str | None = None) -> dict[str, Any]:
    arguments: dict[str, Any] = {"project": project, "wait_seconds": wait_seconds, "adopt_blank": adopt_blank}
    if pid is not None:
        arguments["pid"] = pid
    return _invoke(device_id=device_id, action="blender.live_start", project=project, space_id=space_id, arguments=arguments)


@mcp.tool()
def blender_transform(device_id: str, project: str, object_name: str | None = None, ordax_object_id: str | None = None, location: list[float] | None = None, rotation_euler: list[float] | None = None, scale: list[float] | None = None, dimensions: list[float] | None = None, timeout_seconds: float = 30.0, space_id: str | None = None) -> dict[str, Any]:
    arguments: dict[str, Any] = {"project": project, "timeout_seconds": timeout_seconds}
    values = {"object_name": object_name, "ordax_object_id": ordax_object_id, "location": location, "rotation_euler": rotation_euler, "scale": scale, "dimensions": dimensions}
    arguments.update({key: value for key, value in values.items() if value is not None})
    return _invoke(device_id=device_id, action="blender.live_object_transform", project=project, space_id=space_id, arguments=arguments)


@mcp.tool()
def blender_create_primitive(device_id: str, project: str, name: str, primitive: str, location: list[float] | None = None, size: float | None = None, radius: float | None = None, depth: float | None = None, segments: int | None = None, timeout_seconds: float = 30.0, space_id: str | None = None) -> dict[str, Any]:
    arguments: dict[str, Any] = {"project": project, "name": name, "primitive": primitive, "timeout_seconds": timeout_seconds}
    values = {"location": location, "size": size, "radius": radius, "depth": depth, "segments": segments}
    arguments.update({key: value for key, value in values.items() if value is not None})
    return _invoke(device_id=device_id, action="blender.live_create_primitive", project=project, space_id=space_id, arguments=arguments)


@mcp.tool()
def blender_apply_material(device_id: str, project: str, material_name: str, object_name: str | None = None, ordax_object_id: str | None = None, base_color: list[float] | None = None, roughness: float = 0.4, metallic: float = 0.0, transmission: float = 0.0, alpha: float = 1.0, ior: float = 1.45, surface_render_method: str | None = None, transparency_overlap: bool = True, timeout_seconds: float = 30.0, space_id: str | None = None) -> dict[str, Any]:
    arguments: dict[str, Any] = {"project": project, "material_name": material_name, "roughness": roughness, "metallic": metallic, "transmission": transmission, "alpha": alpha, "ior": ior, "transparency_overlap": transparency_overlap, "timeout_seconds": timeout_seconds}
    values = {"object_name": object_name, "ordax_object_id": ordax_object_id, "base_color": base_color, "surface_render_method": surface_render_method}
    arguments.update({key: value for key, value in values.items() if value is not None})
    return _invoke(device_id=device_id, action="blender.live_material_apply", project=project, space_id=space_id, arguments=arguments)


@mcp.tool()
def blender_save(device_id: str, project: str, target_path: str | None = None, timeout_seconds: float = 120.0, space_id: str | None = None) -> dict[str, Any]:
    arguments: dict[str, Any] = {"project": project, "timeout_seconds": timeout_seconds}
    if target_path is not None:
        arguments["target_path"] = target_path
    return _invoke(device_id=device_id, action="blender.live_save", project=project, space_id=space_id, arguments=arguments)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
