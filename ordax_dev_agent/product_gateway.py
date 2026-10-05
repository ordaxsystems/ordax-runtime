from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Any, Callable, Protocol

from .models import ActionResult


class ActionExecutor(Protocol):
    @property
    def names(self) -> list[str]: ...

    def execute(self, action: str, payload: dict[str, Any]) -> ActionResult: ...


@dataclass(frozen=True)
class ProductActionSpec:
    name: str
    local_action: str
    allowed_fields: frozenset[str]
    project_required: bool = True
    effect: str = "read"


@dataclass(frozen=True)
class ProductRequestContext:
    """Verified caller/device context supplied by the authenticated Control Plane."""

    request_id: str
    subject_id: str
    device_id: str
    space_id: str | None = None


@dataclass(frozen=True)
class ProductGrant:
    """Resolved grant provenance supplied by the Control Plane.

    This object is not a bearer credential and it is never accepted from an
    unauthenticated network request. The gateway validates that its subject and
    optional Space/device scopes match the verified request context.
    """

    grant_id: str
    subject_id: str
    actions: frozenset[str]
    projects: frozenset[str] = frozenset()
    space_id: str | None = None
    device_id: str | None = None
    expires_at_unix: int | None = None


@dataclass(frozen=True)
class ProductAuditEvent:
    occurred_at_unix: int
    request_id: str
    subject_id: str
    grant_id: str | None
    action: str
    project: str | None
    phase: str
    decision: str
    reason: str
    payload_fields: tuple[str, ...]
    result_ok: bool | None = None


class ProductAuditSink(Protocol):
    def record(self, event: ProductAuditEvent) -> None: ...


PRODUCT_READ_ONLY_ACTIONS: dict[str, ProductActionSpec] = {
    "projects.list": ProductActionSpec(
        name="projects.list",
        local_action="projects.list",
        allowed_fields=frozenset(),
        project_required=False,
    ),
    "project.inventory": ProductActionSpec(
        name="project.inventory",
        local_action="project.inventory",
        allowed_fields=frozenset({"project", "max_depth", "max_entries"}),
    ),
    "project.text_read": ProductActionSpec(
        name="project.text_read",
        local_action="project.text_read",
        allowed_fields=frozenset({"project", "path"}),
    ),
    "workspace.file_stat": ProductActionSpec(
        name="workspace.file_stat",
        local_action="workspace.file_stat",
        allowed_fields=frozenset({"project", "path"}),
    ),
    "workspace.directory_list": ProductActionSpec(
        name="workspace.directory_list",
        local_action="workspace.directory_list",
        allowed_fields=frozenset({"project", "path", "max_depth", "max_entries", "include_hidden"}),
    ),
    "workspace.text_read": ProductActionSpec(
        name="workspace.text_read",
        local_action="workspace.text_read",
        allowed_fields=frozenset({"project", "path", "start_line", "end_line"}),
    ),
    "project.search_text": ProductActionSpec(
        name="project.search_text",
        local_action="project.search_text",
        allowed_fields=frozenset({"project", "query", "max_results", "max_files", "case_sensitive"}),
    ),
    "project.text_read_batch": ProductActionSpec(
        name="project.text_read_batch",
        local_action="project.text_read_batch",
        allowed_fields=frozenset({"project", "paths", "max_total_bytes"}),
    ),
    "project.preview_status": ProductActionSpec(
        name="project.preview_status",
        local_action="project.preview_status",
        allowed_fields=frozenset({"project"}),
    ),
    "agent.project_health": ProductActionSpec(
        name="agent.project_health",
        local_action="agent.project_health",
        allowed_fields=frozenset({"project"}),
    ),
    "agent.project_briefing": ProductActionSpec(
        name="agent.project_briefing",
        local_action="agent.project_briefing",
        allowed_fields=frozenset({"project", "query", "recall_limit"}),
    ),
    "continuity.get": ProductActionSpec(
        name="continuity.get",
        local_action="continuity.get",
        allowed_fields=frozenset({"project"}),
    ),
    "workspace.repository_catalog": ProductActionSpec(
        name="workspace.repository_catalog",
        local_action="workspace.repository_catalog",
        allowed_fields=frozenset(),
        project_required=False,
    ),
    "artifacts.list": ProductActionSpec(
        name="artifacts.list",
        local_action="artifacts.list",
        allowed_fields=frozenset({"project", "max_items"}),
    ),
    "git.status": ProductActionSpec(
        name="git.status",
        local_action="git.status",
        allowed_fields=frozenset({"project"}),
    ),
    "git.diff": ProductActionSpec(
        name="git.diff",
        local_action="git.diff",
        allowed_fields=frozenset({"project", "paths"}),
    ),
    "handoff.get": ProductActionSpec(
        name="handoff.get",
        local_action="handoff.get",
        allowed_fields=frozenset({"project", "handoff_id"}),
    ),
    "artifact.preview": ProductActionSpec(
        name="artifact.preview",
        local_action="artifact.preview",
        allowed_fields=frozenset(
            {
                "project",
                "artifact_name",
                "project_artifact_path",
                "max_bytes",
                "thumbnail",
                "max_width",
                "max_height",
                "quality",
            }
        ),
    ),
    "browser.status": ProductActionSpec(
        name="browser.status",
        local_action="browser.status",
        allowed_fields=frozenset({"project", "session_id"}),
    ),
    "browser.list": ProductActionSpec(
        name="browser.list",
        local_action="browser.list",
        allowed_fields=frozenset({"project"}),
    ),
    "browser.snapshot": ProductActionSpec(
        name="browser.snapshot",
        local_action="browser.snapshot",
        allowed_fields=frozenset({"project", "session_id", "max_elements"}),
    ),
    "browser.screenshot": ProductActionSpec(
        name="browser.screenshot",
        local_action="browser.screenshot",
        allowed_fields=frozenset({"project", "session_id", "width", "height"}),
    ),
    "computer.windows": ProductActionSpec(
        name="computer.windows",
        local_action="computer.windows",
        allowed_fields=frozenset({"max_items"}),
        project_required=False,
    ),
    "computer.active_window": ProductActionSpec(
        name="computer.active_window",
        local_action="computer.active_window",
        allowed_fields=frozenset(),
        project_required=False,
    ),
    "computer.screenshot": ProductActionSpec(
        name="computer.screenshot",
        local_action="computer.screenshot",
        allowed_fields=frozenset({"mode"}),
        project_required=False,
    ),
    "computer.screen_info": ProductActionSpec(
        name="computer.screen_info",
        local_action="computer.screen_info",
        allowed_fields=frozenset(),
        project_required=False,
    ),
    "computer.clipboard_read": ProductActionSpec(
        name="computer.clipboard_read",
        local_action="computer.clipboard_read",
        allowed_fields=frozenset({"max_bytes"}),
        project_required=False,
    ),
    "computer.access_status": ProductActionSpec(
        name="computer.access_status",
        local_action="computer.access_status",
        allowed_fields=frozenset(),
        project_required=False,
    ),
    "computer.file_stat": ProductActionSpec(
        name="computer.file_stat",
        local_action="computer.file_stat",
        allowed_fields=frozenset({"path"}),
        project_required=False,
    ),
    "computer.directory_list": ProductActionSpec(
        name="computer.directory_list",
        local_action="computer.directory_list",
        allowed_fields=frozenset({"path", "max_depth", "max_entries", "include_hidden"}),
        project_required=False,
    ),
    "computer.text_read": ProductActionSpec(
        name="computer.text_read",
        local_action="computer.text_read",
        allowed_fields=frozenset({"path", "start_line", "end_line"}),
        project_required=False,
    ),
    "computer.search": ProductActionSpec(
        name="computer.search",
        local_action="computer.search",
        allowed_fields=frozenset({"root", "query", "mode", "max_results", "max_depth", "include_hidden"}),
        project_required=False,
    ),
    "computer.processes": ProductActionSpec(
        name="computer.processes",
        local_action="computer.processes",
        allowed_fields=frozenset({"query", "max_items"}),
        project_required=False,
    ),
    "process.status": ProductActionSpec("process.status", "process.status", frozenset({"project", "process_id"})),
    "process.list": ProductActionSpec("process.list", "process.list", frozenset({"project"})),
    "process.logs": ProductActionSpec("process.logs", "process.logs", frozenset({"project", "process_id", "max_bytes"})),
}

# Product v2 exposes only bounded typed operations. No generic action executor or shell.
PRODUCT_TYPED_ACTIONS: dict[str, ProductActionSpec] = {
    "continuity.update": ProductActionSpec(
        "continuity.update",
        "continuity.update",
        frozenset({"project", "summary", "next_action", "completed", "blockers", "changed_paths"}),
        effect="write",
    ),
    "workspace.project_create": ProductActionSpec(
        "workspace.project_create",
        "workspace.project_create",
        frozenset({"slug", "name", "apps", "set_default", "git_init", "readme", "blender_scripts_dir"}),
        project_required=False,
        effect="write",
    ),
    "workspace.bind_project": ProductActionSpec(
        "workspace.bind_project",
        "workspace.bind_project",
        frozenset({"slug", "relative_path", "apps", "set_default", "blender_scripts_dir", "blend_file"}),
        project_required=False,
        effect="write",
    ),
    "handoff.create": ProductActionSpec(
        "handoff.create",
        "handoff.create",
        frozenset({"project", "summary", "next_action", "completed", "blockers", "changed_paths", "ttl_hours"}),
        effect="write",
    ),
    "workspace.text_write": ProductActionSpec(
        "workspace.text_write",
        "workspace.text_write",
        frozenset({"project", "path", "content", "expected_sha256", "create"}),
        effect="write",
    ),
    "workspace.text_patch": ProductActionSpec(
        "workspace.text_patch",
        "workspace.text_patch",
        frozenset({"project", "path", "expected_sha256", "replacements"}),
        effect="write",
    ),
    "workspace.directory_create": ProductActionSpec(
        "workspace.directory_create",
        "workspace.directory_create",
        frozenset({"project", "path", "parents"}),
        effect="write",
    ),
    "workspace.path_remove": ProductActionSpec(
        "workspace.path_remove",
        "workspace.path_remove",
        frozenset({"project", "path", "expected_sha256", "recursive"}),
        effect="write",
    ),
    "workspace.path_move": ProductActionSpec(
        "workspace.path_move",
        "workspace.path_move",
        frozenset({"project", "source", "destination", "expected_sha256", "overwrite"}),
        effect="write",
    ),
    "git.command": ProductActionSpec(
        "git.command",
        "git.command",
        frozenset({"project", "args", "timeout_seconds"}),
        effect="execute",
    ),
    "terminal.exec": ProductActionSpec(
        "terminal.exec",
        "terminal.exec",
        frozenset({"project", "cwd", "argv", "command", "shell", "timeout_seconds", "env"}),
        effect="execute",
    ),
    "process.start": ProductActionSpec(
        "process.start", "process.start", frozenset({"project", "argv", "cwd", "env", "wait_seconds"}), effect="execute",
    ),
    "process.write_stdin": ProductActionSpec(
        "process.write_stdin", "process.write_stdin", frozenset({"project", "process_id", "text", "newline"}), effect="execute",
    ),
    "process.stop": ProductActionSpec(
        "process.stop", "process.stop", frozenset({"project", "process_id"}), effect="write",
    ),
    "browser.start": ProductActionSpec(
        "browser.start",
        "browser.start",
        frozenset({"project", "url", "headless", "wait_seconds"}),
        effect="write",
    ),
    "browser.navigate": ProductActionSpec(
        "browser.navigate",
        "browser.navigate",
        frozenset({"project", "session_id", "url", "wait_seconds"}),
        effect="write",
    ),
    "browser.click": ProductActionSpec(
        "browser.click",
        "browser.click",
        frozenset({"project", "session_id", "node_id"}),
        effect="write",
    ),
    "browser.type": ProductActionSpec(
        "browser.type",
        "browser.type",
        frozenset({"project", "session_id", "node_id", "text", "clear"}),
        effect="write",
    ),
    "browser.stop": ProductActionSpec(
        "browser.stop",
        "browser.stop",
        frozenset({"project", "session_id"}),
        effect="write",
    ),
    "computer.terminate_process": ProductActionSpec(
        "computer.terminate_process",
        "computer.terminate_process",
        frozenset({"pid", "expected_name", "force", "tree"}),
        project_required=False,
        effect="write",
    ),
    "computer.focus_window": ProductActionSpec(
        "computer.focus_window",
        "computer.focus_window",
        frozenset({"handle"}),
        project_required=False,
        effect="write",
    ),
    "computer.click": ProductActionSpec(
        "computer.click",
        "computer.click",
        frozenset({"x", "y", "button", "clicks"}),
        project_required=False,
        effect="write",
    ),
    "computer.mouse_move": ProductActionSpec(
        "computer.mouse_move",
        "computer.mouse_move",
        frozenset({"x", "y", "duration_ms"}),
        project_required=False,
        effect="write",
    ),
    "computer.drag": ProductActionSpec(
        "computer.drag",
        "computer.drag",
        frozenset({"from_x", "from_y", "to_x", "to_y", "button", "duration_ms"}),
        project_required=False,
        effect="write",
    ),
    "computer.clipboard_write": ProductActionSpec(
        "computer.clipboard_write",
        "computer.clipboard_write",
        frozenset({"text"}),
        project_required=False,
        effect="write",
    ),
    "computer.launch_app": ProductActionSpec(
        "computer.launch_app",
        "computer.launch_app",
        frozenset({"application", "args"}),
        project_required=False,
        effect="write",
    ),
    "computer.scroll": ProductActionSpec(
        "computer.scroll",
        "computer.scroll",
        frozenset({"amount", "horizontal"}),
        project_required=False,
        effect="write",
    ),
    "computer.type": ProductActionSpec(
        "computer.type",
        "computer.type",
        frozenset({"text"}),
        project_required=False,
        effect="write",
    ),
    "computer.hotkey": ProductActionSpec(
        "computer.hotkey",
        "computer.hotkey",
        frozenset({"keys"}),
        project_required=False,
        effect="write",
    ),
    "computer.text_write": ProductActionSpec(
        "computer.text_write",
        "computer.text_write",
        frozenset({"path", "content", "expected_sha256", "create"}),
        project_required=False,
        effect="write",
    ),
    "computer.text_patch": ProductActionSpec(
        "computer.text_patch",
        "computer.text_patch",
        frozenset({"path", "expected_sha256", "replacements"}),
        project_required=False,
        effect="write",
    ),
    "computer.directory_create": ProductActionSpec(
        "computer.directory_create",
        "computer.directory_create",
        frozenset({"path", "parents"}),
        project_required=False,
        effect="write",
    ),
    "computer.path_move": ProductActionSpec(
        "computer.path_move",
        "computer.path_move",
        frozenset({"source", "destination", "overwrite"}),
        project_required=False,
        effect="write",
    ),
    "computer.path_remove": ProductActionSpec(
        "computer.path_remove",
        "computer.path_remove",
        frozenset({"path", "recursive"}),
        project_required=False,
        effect="write",
    ),
    "project.text_write": ProductActionSpec("project.text_write", "project.text_write", frozenset({"project", "path", "content", "expected_sha256", "create"}), effect="write"),
    "project.text_patch": ProductActionSpec("project.text_patch", "project.text_patch", frozenset({"project", "path", "expected_sha256", "replacements"}), effect="write"),
    "blender.live_status": ProductActionSpec("blender.live_status", "blender.live_status", frozenset({"project"})),
    "blender.live_scene_snapshot": ProductActionSpec("blender.live_scene_snapshot", "blender.live_scene_snapshot", frozenset({"project", "max_objects", "object_names", "timeout_seconds"})),
    "blender.live_object_inspect": ProductActionSpec("blender.live_object_inspect", "blender.live_object_inspect", frozenset({"project", "object_name", "ordax_object_id", "timeout_seconds"})),
    "blender.live_modeling_schema": ProductActionSpec("blender.live_modeling_schema", "blender.live_modeling_schema", frozenset({"project"})),
    "blender.live_start": ProductActionSpec("blender.live_start", "blender.live_start", frozenset({"project", "wait_seconds", "timeout_seconds", "pid", "adopt_blank"}), effect="write"),
    "blender.live_object_transform": ProductActionSpec("blender.live_object_transform", "blender.live_object_transform", frozenset({"project", "object_name", "ordax_object_id", "location", "rotation_euler", "scale", "dimensions", "timeout_seconds"}), effect="write"),
    "blender.live_create_primitive": ProductActionSpec("blender.live_create_primitive", "blender.live_create_primitive", frozenset({"project", "name", "primitive", "location", "size", "radius", "depth", "segments", "timeout_seconds"}), effect="write"),
    "blender.live_material_apply": ProductActionSpec("blender.live_material_apply", "blender.live_material_apply", frozenset({"project", "object_name", "ordax_object_id", "material_name", "base_color", "roughness", "metallic", "transmission", "alpha", "ior", "surface_render_method", "transparency_overlap", "timeout_seconds"}), effect="write"),
    "blender.live_save": ProductActionSpec("blender.live_save", "blender.live_save", frozenset({"project", "target_path", "timeout_seconds"}), effect="write"),
}
PRODUCT_ACTIONS: dict[str, ProductActionSpec] = {**PRODUCT_READ_ONLY_ACTIONS, **PRODUCT_TYPED_ACTIONS}

_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@/-]{0,199}$")


def product_action_catalog() -> list[dict[str, Any]]:
    return [
        {
            "name": spec.name,
            "effect": spec.effect,
            "project_required": spec.project_required,
            "allowed_fields": sorted(spec.allowed_fields),
        }
        for spec in PRODUCT_ACTIONS.values()
    ]


_LOCAL_RESULT_KEYS = frozenset({
    "path", "root", "file", "project_root", "project_path", "workspace_root",
    "context_path", "command", "control_root", "bootstrap_config", "discovery_path",
    "output_path", "snapshot_path", "image_path", "profile", "browser",
})

def _redact_local_result_paths(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _redact_local_result_paths(item) for key, item in value.items() if key not in _LOCAL_RESULT_KEYS}
    if isinstance(value, list):
        return [_redact_local_result_paths(item) for item in value]
    return value

def _sanitize_product_result(
    action: str,
    result: ActionResult,
    *,
    allowed_projects: frozenset[str] | None = None,
) -> ActionResult:
    data = dict(result.data) if isinstance(result.data, dict) else {}

    def public_project(item: dict[str, Any]) -> dict[str, Any]:
        return {
            key: item[key]
            for key in ("slug", "apps", "available", "allowed_branches", "preview_mode")
            if key in item
        }

    def public_process(item: dict[str, Any]) -> dict[str, Any]:
        return {
            key: item[key]
            for key in (
                "process_id", "project", "state", "cwd", "started_at_unix",
                "manager_ready_at_unix", "running_at_unix", "stopping_at_unix",
                "ended_at_unix", "returncode", "running", "ownership_valid", "child_pid",
            )
            if key in item
        }

    if action in {
        "workspace.project_create",
        "workspace.bind_project",
        "browser.start",
        "browser.status",
        "browser.list",
        "browser.screenshot",
        "browser.stop",
        "computer.screenshot",
    }:
        data = _redact_local_result_paths(data)
    elif action == "projects.list":
        projects = data.get("projects")
        if isinstance(projects, list):
            data["projects"] = [
                public_project(item)
                for item in projects
                if isinstance(item, dict)
                and (
                    allowed_projects is None
                    or item.get("slug") in allowed_projects
                )
            ]
        default_project = data.get("default_project")
        if (
            isinstance(default_project, str)
            and allowed_projects is not None
            and default_project not in allowed_projects
        ):
            data.pop("default_project", None)
    elif action == "workspace.repository_catalog":
        projects = data.get("projects")
        if isinstance(projects, list):
            safe_projects: list[dict[str, Any]] = []
            for item in projects:
                if (
                    not isinstance(item, dict)
                    or (
                        allowed_projects is not None
                        and item.get("slug") not in allowed_projects
                    )
                ):
                    continue
                safe = public_project(item)
                repository = item.get("repository")
                if isinstance(repository, dict):
                    safe["repository"] = {
                        key: repository[key]
                        for key in (
                            "is_repository", "branch", "remote", "has_origin", "dirty",
                            "changed_entries", "status_available",
                        )
                        if key in repository
                    }
                safe_projects.append(safe)
            data["projects"] = safe_projects
        active_project = data.get("active_project")
        if (
            isinstance(active_project, str)
            and allowed_projects is not None
            and active_project not in allowed_projects
        ):
            data.pop("active_project", None)
    elif action == "project.inventory":
        data.pop("project_root", None)
    elif action == "agent.project_briefing":
        project = data.get("project")
        if isinstance(project, dict):
            data["project"] = public_project(project)
        repository = data.get("repository")
        if isinstance(repository, dict):
            data["repository"] = {
                key: value
                for key, value in repository.items()
                if key not in {"path", "root", "command"}
            }
        health = data.get("health")
        if isinstance(health, dict):
            sanitized_health = _sanitize_product_result(
                "agent.project_health",
                ActionResult(True, "health", health),
                allowed_projects=allowed_projects,
            )
            data["health"] = sanitized_health.data
        preview = data.get("preview")
        if isinstance(preview, dict):
            preview.pop("url", None)
            runtime = preview.get("runtime")
            if isinstance(runtime, dict):
                preview["runtime"] = {
                    key: runtime[key]
                    for key in (
                        "state", "running", "url_ready", "ownership_valid",
                        "started_at_unix", "ready_at_unix", "stopped_at_unix", "exit_code",
                    )
                    if key in runtime
                }
            latest = preview.get("latest_image")
            if isinstance(latest, dict):
                latest.pop("path", None)
        continuity = data.get("continuity")
        if isinstance(continuity, dict):
            continuity.pop("context_path", None)
    elif action == "agent.project_health":
        project = data.get("project")
        if isinstance(project, dict):
            data["project"] = public_project(project)
        memory = data.get("memory")
        if isinstance(memory, dict):
            data["memory"] = {key: value for key, value in memory.items() if key not in {"db_path", "context_dir"}}
        git = data.get("git")
        if isinstance(git, dict):
            data["git"] = {key: value for key, value in git.items() if key not in {"command", "path", "root"}}
    elif action == "project.preview_status":
        data.pop("url", None)
        runtime = data.get("runtime")
        if isinstance(runtime, dict):
            data["runtime"] = {
                key: runtime[key]
                for key in (
                    "state", "running", "url_ready", "ownership_valid", "started_at_unix",
                    "ready_at_unix", "stopped_at_unix", "exit_code",
                )
                if key in runtime
            }
        latest = data.get("latest_image")
        if isinstance(latest, dict):
            data["latest_image"] = {
                key: latest[key]
                for key in (
                    "source", "relative_path", "modified_at_ns", "size_bytes",
                    "artifact_preview_payload",
                )
                if key in latest
            }
    elif action in {"git.status", "git.diff"}:
        data.pop("command", None)
    elif action == "artifact.preview":
        data.pop("path", None)
    elif action in {"process.start", "process.status", "process.stop"}:
        data = public_process(data)
    elif action == "process.list":
        items = data.get("processes")
        data = {"project": data.get("project"), "processes": [public_process(item) for item in items if isinstance(item, dict)] if isinstance(items, list) else []}
    elif action == "process.logs":
        process = data.get("process")
        data = {"process": public_process(process) if isinstance(process, dict) else {}, "tail": str(data.get("tail") or ""), "size_bytes": int(data.get("size_bytes") or 0), "truncated": bool(data.get("truncated"))}
    elif action == "process.write_stdin":
        data = {key: data[key] for key in ("process_id", "queued_bytes", "newline") if key in data}
    elif action == "computer.processes":
        processes = data.get("processes")
        if isinstance(processes, list):
            data["processes"] = [
                {
                    key: item[key]
                    for key in ("pid", "parent_pid", "name", "executable")
                    if key in item
                }
                for item in processes
                if isinstance(item, dict)
            ]
    elif action == "computer.terminate_process":
        data.pop("command_line", None)
    elif action.startswith("blender."):
        data = _redact_local_result_paths(data)

    return ActionResult(result.ok, result.summary, data)


def _valid_id(value: str | None) -> bool:
    return isinstance(value, str) and bool(_ID_RE.fullmatch(value))


class ProductActionGateway:
    """Fail-closed typed Product MCP/OrdaX Web action facade.

    The gateway is not a network server. A future Product MCP/Web endpoint must
    authenticate first, resolve a grant in the Control Plane, provide a verified
    request context and persist audit events through the required audit sink.
    """

    def __init__(
        self,
        executor: ActionExecutor,
        audit_sink: ProductAuditSink,
        *,
        clock: Callable[[], float] = time.time,
    ):
        self.executor = executor
        self.audit_sink = audit_sink
        self.clock = clock

    def catalog(self) -> list[dict[str, Any]]:
        available = set(self.executor.names)
        return [
            entry
            for entry in product_action_catalog()
            if PRODUCT_ACTIONS[entry["name"]].local_action in available
        ]

    def _event(
        self,
        *,
        context: ProductRequestContext,
        grant: ProductGrant | None,
        action: str,
        body: dict[str, Any],
        phase: str,
        decision: str,
        reason: str,
        result_ok: bool | None = None,
    ) -> ProductAuditEvent:
        project = body.get("project")
        return ProductAuditEvent(
            occurred_at_unix=int(self.clock()),
            request_id=context.request_id,
            subject_id=context.subject_id,
            grant_id=grant.grant_id if grant is not None else None,
            action=action,
            project=project if isinstance(project, str) else None,
            phase=phase,
            decision=decision,
            reason=reason,
            payload_fields=tuple(sorted(body)),
            result_ok=result_ok,
        )

    def _deny(
        self,
        *,
        context: ProductRequestContext,
        grant: ProductGrant | None,
        action: str,
        body: dict[str, Any],
        summary: str,
        error_code: str,
    ) -> ActionResult:
        try:
            self.audit_sink.record(
                self._event(
                    context=context,
                    grant=grant,
                    action=action,
                    body=body,
                    phase="decision",
                    decision="deny",
                    reason=error_code,
                )
            )
        except Exception:
            return ActionResult(
                False,
                "product action denied; audit sink unavailable",
                {"error_code": "audit_unavailable", "original_error_code": error_code},
            )
        return ActionResult(False, summary, {"error_code": error_code})

    def execute(
        self,
        action: str,
        payload: dict[str, Any] | None,
        *,
        context: ProductRequestContext,
        grant: ProductGrant,
    ) -> ActionResult:
        body = dict(payload or {})

        if not (
            _valid_id(context.request_id)
            and _valid_id(context.subject_id)
            and _valid_id(context.device_id)
            and (context.space_id is None or _valid_id(context.space_id))
        ):
            return self._deny(
                context=context,
                grant=grant,
                action=action,
                body=body,
                summary="verified product request context is invalid",
                error_code="invalid_request_context",
            )

        grant_structure_valid = (
            _valid_id(grant.grant_id)
            and _valid_id(grant.subject_id)
            and isinstance(grant.actions, frozenset)
            and all(_valid_id(item) for item in grant.actions)
            and isinstance(grant.projects, frozenset)
            and all(_valid_id(item) for item in grant.projects)
            and (grant.device_id is None or _valid_id(grant.device_id))
            and (grant.space_id is None or _valid_id(grant.space_id))
            and (
                grant.expires_at_unix is None
                or (
                    type(grant.expires_at_unix) is int
                    and grant.expires_at_unix > 0
                )
            )
        )
        if not grant_structure_valid:
            return self._deny(
                context=context,
                grant=grant,
                action=action,
                body=body,
                summary="resolved product grant provenance is invalid",
                error_code="invalid_grant_provenance",
            )

        if grant.subject_id != context.subject_id:
            return self._deny(
                context=context,
                grant=grant,
                action=action,
                body=body,
                summary="grant subject does not match authenticated subject",
                error_code="grant_subject_mismatch",
            )

        if grant.device_id is not None and grant.device_id != context.device_id:
            return self._deny(
                context=context,
                grant=grant,
                action=action,
                body=body,
                summary="grant is scoped to another device",
                error_code="grant_device_mismatch",
            )

        if grant.space_id is not None and grant.space_id != context.space_id:
            return self._deny(
                context=context,
                grant=grant,
                action=action,
                body=body,
                summary="grant is scoped to another Space",
                error_code="grant_space_mismatch",
            )

        now = int(self.clock())
        if grant.expires_at_unix is not None and grant.expires_at_unix <= now:
            return self._deny(
                context=context,
                grant=grant,
                action=action,
                body=body,
                summary="product grant is expired",
                error_code="grant_expired",
            )

        spec = PRODUCT_ACTIONS.get(action)
        if spec is None:
            return self._deny(
                context=context,
                grant=grant,
                action=action,
                body=body,
                summary=f"product action is not exposed: {action}",
                error_code="action_not_exposed",
            )

        if action not in grant.actions:
            return self._deny(
                context=context,
                grant=grant,
                action=action,
                body=body,
                summary=f"product action is not granted: {action}",
                error_code="grant_required",
            )

        unsupported = sorted(set(body) - spec.allowed_fields)
        if unsupported:
            return self._deny(
                context=context,
                grant=grant,
                action=action,
                body=body,
                summary="unsupported field(s): " + ", ".join(unsupported),
                error_code="invalid_product_action_payload",
            )

        if spec.project_required:
            project = body.get("project")
            if not isinstance(project, str) or not project:
                return self._deny(
                    context=context,
                    grant=grant,
                    action=action,
                    body=body,
                    summary="project is required",
                    error_code="project_required",
                )
            if project not in grant.projects:
                return self._deny(
                    context=context,
                    grant=grant,
                    action=action,
                    body=body,
                    summary=f"project is not granted: {project}",
                    error_code="project_grant_required",
                )
        elif "project" in body:
            return self._deny(
                context=context,
                grant=grant,
                action=action,
                body=body,
                summary="project is not accepted for this global action",
                error_code="invalid_product_action_payload",
            )

        if spec.local_action not in set(self.executor.names):
            return self._deny(
                context=context,
                grant=grant,
                action=action,
                body=body,
                summary=f"local action is unavailable: {spec.local_action}",
                error_code="local_action_unavailable",
            )

        try:
            self.audit_sink.record(
                self._event(
                    context=context,
                    grant=grant,
                    action=action,
                    body=body,
                    phase="decision",
                    decision="allow",
                    reason="grant_validated",
                )
            )
        except Exception:
            return ActionResult(
                False,
                "product action refused because audit sink is unavailable",
                {"error_code": "audit_unavailable"},
            )

        result = _sanitize_product_result(
            action,
            self.executor.execute(spec.local_action, body),
            allowed_projects=grant.projects,
        )

        try:
            self.audit_sink.record(
                self._event(
                    context=context,
                    grant=grant,
                    action=action,
                    body=body,
                    phase="result",
                    decision="allow",
                    reason="local_action_completed",
                    result_ok=result.ok,
                )
            )
        except Exception:
            return ActionResult(
                False,
                "product result withheld because audit persistence failed",
                {
                    "error_code": "audit_unavailable",
                    "local_result_ok": bool(result.ok),
                },
            )

        return result
