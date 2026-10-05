from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .models import ActionResult
from .product_gateway import (
    ProductActionGateway,
    ProductGrant,
    ProductRequestContext,
)


@dataclass(frozen=True)
class ProductMcpToolSpec:
    """Transport-neutral Product MCP tool description."""

    name: str
    action: str
    description: str
    effect: str = "read"


PRODUCT_MCP_TOOLS: tuple[ProductMcpToolSpec, ...] = (
    ProductMcpToolSpec(
        name="projects_list",
        action="projects.list",
        description="List granted projects. Use this first when the user wants to continue/resume work but the target project is not yet known.",
    ),
    ProductMcpToolSpec(
        name="project_create",
        action="workspace.project_create",
        effect="write",
        description="Create and register a new project inside the device's configured ORDAX workspace.",
    ),
    ProductMcpToolSpec(
        name="project_import",
        action="workspace.bind_project",
        effect="write",
        description="Register an existing directory inside the configured ORDAX workspace and activate it without exposing arbitrary filesystem paths.",
    ),
    ProductMcpToolSpec(
        name="handoff_get",
        action="handoff.get",
        description="Load one expiring project continuation handoff by opaque id.",
    ),
    ProductMcpToolSpec(
        name="handoff_create",
        action="handoff.create",
        effect="write",
        description="Create an expiring continuation handoff for a fresh client conversation.",
    ),
    ProductMcpToolSpec(
        name="project_inventory",
        action="project.inventory",
        description="Inspect a bounded project inventory without exposing local roots.",
    ),
    ProductMcpToolSpec(
        name="project_text_read",
        action="project.text_read",
        description="Read a granted project-relative text file.",
    ),
    ProductMcpToolSpec(name="workspace_file_stat", action="workspace.file_stat", description="Inspect any path inside a granted project."),
    ProductMcpToolSpec(name="workspace_directory_list", action="workspace.directory_list", description="List project directories without the legacy source-folder restrictions."),
    ProductMcpToolSpec(name="workspace_text_read", action="workspace.text_read", description="Read UTF-8 text anywhere inside a granted project with line ranges."),
    ProductMcpToolSpec(name="workspace_text_write", action="workspace.text_write", effect="write", description="Create or replace project text with SHA-256 concurrency protection."),
    ProductMcpToolSpec(name="workspace_text_patch", action="workspace.text_patch", effect="write", description="Patch arbitrary project text with exact-match and SHA-256 guards."),
    ProductMcpToolSpec(name="workspace_directory_create", action="workspace.directory_create", effect="write", description="Create a directory inside a granted project."),
    ProductMcpToolSpec(name="workspace_path_remove", action="workspace.path_remove", effect="write", description="Remove a file or directory inside a granted project."),
    ProductMcpToolSpec(name="workspace_path_move", action="workspace.path_move", effect="write", description="Move or rename a path inside a granted project."),
    ProductMcpToolSpec(name="git_command", action="git.command", effect="execute", description="Run an approved Git subcommand against a granted project without exposing provider credentials."),
    ProductMcpToolSpec(name="terminal_exec", action="terminal.exec", effect="execute", description="Run a foreground terminal command with the local OS user's permissions."),
    ProductMcpToolSpec(name="process_status", action="process.status", description="Inspect one ORDAX-owned persistent project process."),
    ProductMcpToolSpec(name="process_list", action="process.list", description="List ORDAX-owned persistent processes for a granted project."),
    ProductMcpToolSpec(name="process_logs", action="process.logs", description="Read bounded tail logs from an ORDAX-owned persistent process."),
    ProductMcpToolSpec(name="process_start", action="process.start", effect="execute", description="Start a persistent project process owned and supervised by ORDAX."),
    ProductMcpToolSpec(name="process_write_stdin", action="process.write_stdin", effect="execute", description="Send bounded stdin to an ORDAX-owned persistent process."),
    ProductMcpToolSpec(name="process_stop", action="process.stop", effect="write", description="Stop an ORDAX-owned persistent process."),
    ProductMcpToolSpec(name="browser_status", action="browser.status", description="Read status for one ORDAX-managed Chromium session."),
    ProductMcpToolSpec(name="browser_list", action="browser.list", description="List ORDAX-managed Chromium sessions for the granted project."),
    ProductMcpToolSpec(name="browser_snapshot", action="browser.snapshot", description="Read a bounded DOM/text snapshot from an ORDAX-managed browser session, including interactive element ids."),
    ProductMcpToolSpec(name="browser_screenshot", action="browser.screenshot", description="Capture an ORDAX-managed browser page through CDP even when the ORDAX Studio preview pane is not foreground."),
    ProductMcpToolSpec(name="browser_start", action="browser.start", effect="write", description="Start an isolated ORDAX-managed Chromium session for a granted project."),
    ProductMcpToolSpec(name="browser_navigate", action="browser.navigate", effect="write", description="Navigate an ORDAX-managed browser session to an http/https URL permitted by the browser policy."),
    ProductMcpToolSpec(name="browser_click", action="browser.click", effect="write", description="Click one element from the latest ORDAX browser snapshot."),
    ProductMcpToolSpec(name="browser_type", action="browser.type", effect="write", description="Enter text into one editable element in an ORDAX-managed browser session."),
    ProductMcpToolSpec(name="browser_stop", action="browser.stop", effect="write", description="Stop an ORDAX-owned browser session."),
    ProductMcpToolSpec(name="computer_windows", action="computer.windows", description="List visible Windows desktop windows for a granted ORDAX project context."),
    ProductMcpToolSpec(name="computer_active_window", action="computer.active_window", description="Inspect the current foreground Windows desktop window."),
    ProductMcpToolSpec(name="computer_screenshot", action="computer.screenshot", description="Capture the Windows desktop or foreground window for visual inspection."),
    ProductMcpToolSpec(name="computer_screen_info", action="computer.screen_info", description="Inspect monitor geometry, virtual desktop bounds and current cursor position."),
    ProductMcpToolSpec(name="computer_clipboard_read", action="computer.clipboard_read", description="Read bounded Unicode text from the authorized interactive Windows clipboard."),
    ProductMcpToolSpec(name="computer_access_status", action="computer.access_status", description="Read the local ORDAX computer-access policy and allowed filesystem roots."),
    ProductMcpToolSpec(name="computer_file_stat", action="computer.file_stat", description="Inspect a file or directory allowed by the local computer-access policy."),
    ProductMcpToolSpec(name="computer_directory_list", action="computer.directory_list", description="List a directory tree allowed by the local computer-access policy."),
    ProductMcpToolSpec(name="computer_text_read", action="computer.text_read", description="Read bounded UTF-8 text from an allowed computer path."),
    ProductMcpToolSpec(name="computer_search", action="computer.search", description="Search names or bounded text content under an allowed computer root."),
    ProductMcpToolSpec(name="computer_processes", action="computer.processes", description="List bounded system process metadata on the authorized computer."),
    ProductMcpToolSpec(name="computer_focus_window", action="computer.focus_window", effect="write", description="Bring one visible Windows window to the foreground."),
    ProductMcpToolSpec(name="computer_click", action="computer.click", effect="write", description="Send a bounded mouse click to the interactive Windows desktop."),
    ProductMcpToolSpec(name="computer_mouse_move", action="computer.mouse_move", effect="write", description="Move the pointer to a validated coordinate on the virtual Windows desktop."),
    ProductMcpToolSpec(name="computer_drag", action="computer.drag", effect="write", description="Perform one bounded drag gesture between validated desktop coordinates."),
    ProductMcpToolSpec(name="computer_clipboard_write", action="computer.clipboard_write", effect="write", description="Replace Unicode text in the authorized interactive Windows clipboard."),
    ProductMcpToolSpec(name="computer_launch_app", action="computer.launch_app", effect="write", description="Launch one validated Windows executable without shell expansion."),
    ProductMcpToolSpec(name="computer_scroll", action="computer.scroll", effect="write", description="Send bounded vertical or horizontal scrolling to the interactive Windows desktop."),
    ProductMcpToolSpec(name="computer_type", action="computer.type", effect="write", description="Type bounded Unicode text into the interactive Windows desktop."),
    ProductMcpToolSpec(name="computer_hotkey", action="computer.hotkey", effect="write", description="Send a bounded validated hotkey chord to the interactive Windows desktop."),
    ProductMcpToolSpec(name="computer_terminate_process", action="computer.terminate_process", effect="write", description="Terminate one non-critical process by PID only when expected_name still matches the current process identity."),
    ProductMcpToolSpec(name="computer_text_write", action="computer.text_write", effect="write", description="Create or replace text at an allowed computer path with SHA-256 concurrency protection."),
    ProductMcpToolSpec(name="computer_text_patch", action="computer.text_patch", effect="write", description="Patch allowed computer text using exact matches and a SHA-256 precondition."),
    ProductMcpToolSpec(name="computer_directory_create", action="computer.directory_create", effect="write", description="Create a directory within the local computer-access policy."),
    ProductMcpToolSpec(name="computer_path_move", action="computer.path_move", effect="write", description="Move or rename an allowed computer path."),
    ProductMcpToolSpec(name="computer_path_remove", action="computer.path_remove", effect="write", description="Remove an allowed computer path; recursive directory removal requires explicit recursive=true."),
    ProductMcpToolSpec(
        name="repository_catalog",
        action="workspace.repository_catalog",
        description="List canonical ORDAX Studio repositories without exposing local roots; use it to disambiguate a named project/repository before resuming work.",
    ),
    ProductMcpToolSpec(
        name="project_health",
        action="agent.project_health",
        description="Read sanitized ORDAX Studio project health, Git, memory and adapter state.",
    ),
    ProductMcpToolSpec(
        name="project_briefing",
        action="agent.project_briefing",
        description="Load durable project state plus bounded relevant memory/source context. In a fresh chat, call this after identifying the project whenever the user asks to continue, resume, pick up, or review ongoing project work; pass the user intent as query when useful.",
    ),
    ProductMcpToolSpec(
        name="continuity_state",
        action="continuity.get",
        description="Read the non-expiring continuation state for one granted project.",
    ),
    ProductMcpToolSpec(
        name="continuity_update",
        action="continuity.update",
        effect="write",
        description="Persist non-expiring project progress for future conversations.",
    ),
    ProductMcpToolSpec(
        name="project_search",
        action="project.search_text",
        description="Search bounded approved text sources in a granted project.",
    ),
    ProductMcpToolSpec(
        name="project_read_batch",
        action="project.text_read_batch",
        description="Read a bounded batch of granted project text files.",
    ),
    ProductMcpToolSpec(
        name="project_preview_status",
        action="project.preview_status",
        description="Read sanitized ORDAX Studio preview/runtime state for a granted project.",
    ),
    ProductMcpToolSpec(
        name="git_status",
        action="git.status",
        description="Read Git working-tree status for a granted project.",
    ),
    ProductMcpToolSpec(
        name="git_diff",
        action="git.diff",
        description="Read a bounded Git diff for a granted project.",
    ),
    ProductMcpToolSpec(
        name="artifacts_list",
        action="artifacts.list",
        description="List bounded metadata for artifacts in a granted project.",
    ),
    ProductMcpToolSpec(
        name="artifact_preview",
        action="artifact.preview",
        description="Read a bounded preview of a granted project artifact.",
    ),
    ProductMcpToolSpec(name="project_text_write", action="project.text_write", effect="write", description="Write a granted project text file with SHA-256 concurrency protection."),
    ProductMcpToolSpec(name="project_text_patch", action="project.text_patch", effect="write", description="Patch a granted project text file with exact-match and SHA-256 guards."),
    ProductMcpToolSpec(name="blender_status", action="blender.live_status", description="Read sanitized live Blender status."),
    ProductMcpToolSpec(name="blender_scene_snapshot", action="blender.live_scene_snapshot", description="Inspect a bounded live Blender scene snapshot."),
    ProductMcpToolSpec(name="blender_object_inspect", action="blender.live_object_inspect", description="Inspect one Blender object by name or ORDAX id."),
    ProductMcpToolSpec(name="blender_modeling_schema", action="blender.live_modeling_schema", description="Read validated Blender modeling contracts."),
    ProductMcpToolSpec(name="blender_start", action="blender.live_start", effect="write", description="Adopt or start the project's visible Blender session without duplicate windows."),
    ProductMcpToolSpec(name="blender_transform", action="blender.live_object_transform", effect="write", description="Apply a validated transform to one Blender object."),
    ProductMcpToolSpec(name="blender_create_primitive", action="blender.live_create_primitive", effect="write", description="Create a bounded validated Blender primitive."),
    ProductMcpToolSpec(name="blender_apply_material", action="blender.live_material_apply", effect="write", description="Apply a validated material to one Blender object."),
    ProductMcpToolSpec(name="blender_save", action="blender.live_save", effect="write", description="Save the granted Blender project."),
)

_TOOL_BY_NAME = {tool.name: tool for tool in PRODUCT_MCP_TOOLS}


def product_mcp_tool_catalog(gateway: ProductActionGateway) -> list[dict[str, Any]]:
    """Return only Product MCP tools whose gateway actions are locally available."""
    available = {entry["name"]: entry for entry in gateway.catalog()}
    return [
        {
            "name": tool.name,
            "action": tool.action,
            "effect": available[tool.action]["effect"],
            "description": tool.description,
        }
        for tool in PRODUCT_MCP_TOOLS
        if tool.action in available
    ]


class ProductMcpFacade:
    """Typed MCP-facing facade over ProductActionGateway.

    This is deliberately not an MCP server and performs no authentication,
    network I/O, grant lookup, or credential handling. A future authenticated
    MCP host supplies verified context + resolved grant for every invocation.
    """

    def __init__(self, gateway: ProductActionGateway):
        self.gateway = gateway

    def tools(self) -> list[dict[str, Any]]:
        return product_mcp_tool_catalog(self.gateway)

    def call(
        self,
        tool_name: str,
        arguments: dict[str, Any] | None,
        *,
        context: ProductRequestContext,
        grant: ProductGrant,
    ) -> ActionResult:
        tool = _TOOL_BY_NAME.get(tool_name)
        if tool is None:
            return ActionResult(
                False,
                f"Product MCP tool is not exposed: {tool_name}",
                {"error_code": "product_mcp_tool_not_exposed"},
            )

        available_actions = {entry["name"] for entry in self.gateway.catalog()}
        if tool.action not in available_actions:
            return ActionResult(
                False,
                f"Product MCP action is unavailable: {tool.action}",
                {"error_code": "product_mcp_action_unavailable"},
            )

        return self.gateway.execute(
            tool.action,
            arguments or {},
            context=context,
            grant=grant,
        )
