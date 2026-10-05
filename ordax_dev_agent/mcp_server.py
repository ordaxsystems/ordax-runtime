"""Local provider-neutral MCP front end for the ORDAX Runtime.

The historical repository name is ``mcp-blender``. Blender is one capability of
the broader ORDAX Runtime alongside workspace, Git, preview, memory, Unity and
other typed adapters. Remote jobs continue through the paired Control Plane;
this stdio server itself is not public.
"""
from __future__ import annotations

import base64
import json
import time
from pathlib import Path

from mcp.server.fastmcp import FastMCP
from mcp.types import ImageContent, TextContent

from .actions import ActionRegistry
from .config import AgentConfig

mcp = FastMCP("ordax-runtime")
_registry: ActionRegistry | None = None


def registry() -> ActionRegistry:
    global _registry
    if _registry is None:
        _registry = ActionRegistry(AgentConfig.from_env())
    return _registry


@mcp.tool()
def projects_list() -> dict:
    """Discover connected projects, app profiles and permitted Git branches before executing."""
    return registry().projects_list({}).data


@mcp.tool()
def repository_catalog() -> dict:
    """List the canonical Git repositories shown on the ORDAX Studio project home."""
    result = registry().execute("workspace.repository_catalog", {})
    return {"ok": result.ok, "summary": result.summary, "data": result.data}


@mcp.tool()
def agent_capabilities() -> dict:
    """Discover ORDAX Studio action names, adapters and registered projects."""
    return registry().agent_status({}).data


@mcp.tool()
def studio_status(project: str | None = None) -> dict:
    """Return the active ORDAX Studio project, health, preview and capability summary."""
    agent = registry()
    selected = agent.select_available_project(project)
    health = agent.execute("agent.project_health", {"project": selected})
    preview = agent.execute("project.preview_status", {"project": selected})
    return {
        "product": "ORDAX Studio",
        "server": "ordax-runtime",
        "repository_alias": "mcp-blender",
        "project": selected,
        "health": {"ok": health.ok, "summary": health.summary, "data": health.data},
        "preview": {"ok": preview.ok, "summary": preview.summary, "data": preview.data},
        "capabilities": agent.agent_status({}).data,
    }


@mcp.tool()
def workspace_discover(query: str = "", max_depth: int = 3, max_entries: int = 200) -> dict:
    """Discover repository/workspace folders that can be bound into ORDAX Studio."""
    result = registry().execute("workspace.list_projects", {
        "query": query, "max_depth": max_depth, "max_entries": max_entries,
    })
    return {"ok": result.ok, "summary": result.summary, "data": result.data}


@mcp.tool()
def project_create(
    slug: str,
    name: str = "",
    apps: list[str] | None = None,
    set_default: bool = True,
    git_init: bool = True,
    readme: bool = True,
) -> dict:
    """Create and register a new project inside the configured ORDAX workspace."""
    result = registry().execute(
        "workspace.project_create",
        {
            "slug": slug,
            "name": name or slug,
            "apps": list(apps or []),
            "set_default": set_default,
            "git_init": git_init,
            "readme": readme,
        },
    )
    return {"ok": result.ok, "summary": result.summary, "data": result.data}


@mcp.tool()
def project_inventory(project: str | None = None, max_depth: int = 4, max_entries: int = 500) -> dict:
    """List a bounded inventory of approved files in one ORDAX Studio project."""
    agent = registry()
    selected = agent.select_available_project(project)
    result = agent.execute("project.inventory", {
        "project": selected, "max_depth": max_depth, "max_entries": max_entries,
    })
    return {"ok": result.ok, "summary": result.summary, "data": result.data}


@mcp.tool()
def project_read(project: str | None = None, path: str = "") -> dict:
    """Read one approved project-relative text file and return its SHA-256 precondition."""
    agent = registry()
    selected = agent.select_available_project(project)
    result = agent.execute("project.text_read", {"project": selected, "path": path})
    return {"ok": result.ok, "summary": result.summary, "data": result.data}


@mcp.tool()
def project_write(
    project: str | None = None,
    path: str = "",
    content: str = "",
    expected_sha256: str = "",
    create: bool = False,
) -> dict:
    """Safely write a project text file using optimistic SHA-256 concurrency control."""
    agent = registry()
    selected = agent.select_available_project(project)
    result = agent.execute("project.text_write", {
        "project": selected, "path": path, "content": content,
        "expected_sha256": expected_sha256, "create": create,
    })
    return {"ok": result.ok, "summary": result.summary, "data": result.data}


@mcp.tool()
def project_patch(
    project: str | None = None,
    path: str = "",
    expected_sha256: str = "",
    replacements: list[dict] | None = None,
) -> dict:
    """Apply exact bounded replacements to a text file with stale-write protection."""
    agent = registry()
    selected = agent.select_available_project(project)
    result = agent.execute("project.text_patch", {
        "project": selected, "path": path, "expected_sha256": expected_sha256,
        "replacements": replacements or [],
    })
    return {"ok": result.ok, "summary": result.summary, "data": result.data}


@mcp.tool()
def git_quick_status(project: str | None = None) -> dict:
    """Read a bounded tracked-file Git status for responsive UI/health checks."""
    agent = registry()
    selected = agent.select_available_project(project)
    result = agent.execute("git.quick_status", {"project": selected})
    return {"ok": result.ok, "summary": result.summary, "data": result.data}


@mcp.tool()
def git_status(project: str | None = None) -> dict:
    """Read bounded Git status for one ORDAX Studio project."""
    agent = registry()
    selected = agent.select_available_project(project)
    result = agent.execute("git.status", {"project": selected})
    return {"ok": result.ok, "summary": result.summary, "data": result.data}


@mcp.tool()
def git_diff(project: str | None = None, paths: list[str] | None = None) -> dict:
    """Read a bounded no-color Git diff, optionally restricted to project-relative paths."""
    agent = registry()
    selected = agent.select_available_project(project)
    result = agent.execute("git.diff", {"project": selected, "paths": paths or []})
    return {"ok": result.ok, "summary": result.summary, "data": result.data}


@mcp.tool()
def project_health(project: str | None = None) -> dict:
    """Read project memory, Git and Blender/Unity health without mutating applications."""
    agent = registry()
    selected = agent.select_available_project(project)
    result = agent.execute("agent.project_health", {"project": selected})
    return {"ok": result.ok, "summary": result.summary, "data": result.data}


@mcp.tool()
def agent_briefing(project: str | None = None) -> dict:
    """Load one compact project handoff: repo, memory, tasks, Git, preview, adapters and context files."""
    agent = registry()
    selected = agent.select_available_project(project)
    result = agent.execute("agent.project_briefing", {"project": selected})
    return {"ok": result.ok, "summary": result.summary, "data": result.data}


@mcp.tool()
def project_search(project: str | None = None, query: str = "", max_results: int = 40) -> dict:
    """Search approved project text sources with bounded literal matching and line snippets."""
    agent = registry()
    selected = agent.select_available_project(project)
    result = agent.execute("project.search_text", {
        "project": selected, "query": query, "max_results": max_results,
    })
    return {"ok": result.ok, "summary": result.summary, "data": result.data}


@mcp.tool()
def project_read_batch(project: str, paths: list[str], max_total_bytes: int = 393216) -> dict:
    """Read several approved project text files in one bounded context call."""
    result = registry().execute("project.text_read_batch", {
        "project": project, "paths": paths, "max_total_bytes": max_total_bytes,
    })
    return {"ok": result.ok, "summary": result.summary, "data": result.data}


@mcp.tool()
def project_preview_status(project: str | None = None) -> dict:
    """Read the project-level preview runtime state and URL without mutating it."""
    agent = registry()
    selected = agent.select_available_project(project)
    result = agent.execute("project.preview_status", {"project": selected})
    return {"ok": result.ok, "summary": result.summary, "data": result.data}


@mcp.tool()
def project_preview_logs(project: str | None = None, max_bytes: int = 32768) -> dict:
    """Read the tail of the supervised project preview runtime log."""
    agent = registry()
    selected = agent.select_available_project(project)
    result = agent.execute("project.preview_logs", {"project": selected, "max_bytes": max_bytes})
    return {"ok": result.ok, "summary": result.summary, "data": result.data}


@mcp.tool()
def project_preview_start(project: str | None = None) -> dict:
    """Start the typed local web preview runtime for a project."""
    agent = registry()
    selected = agent.select_available_project(project)
    result = agent.execute("project.preview_start", {"project": selected})
    return {"ok": result.ok, "summary": result.summary, "data": result.data}


@mcp.tool()
def project_preview_stop(project: str | None = None) -> dict:
    """Stop the typed local web preview runtime for a project."""
    agent = registry()
    selected = agent.select_available_project(project)
    result = agent.execute("project.preview_stop", {"project": selected})
    return {"ok": result.ok, "summary": result.summary, "data": result.data}


def _blender_action(action: str, project: str | None, arguments: dict | None = None) -> dict:
    agent = registry()
    selected = agent.select_available_project(project)
    result = agent.execute(action, {**(arguments or {}), "project": selected})
    return {"ok": result.ok, "summary": result.summary, "data": result.data}


def _blender_image_result(project: str, result) -> list[TextContent | ImageContent]:
    if not result.ok:
        return [TextContent(type="text", text=json.dumps({"ok": False, "summary": result.summary, "data": result.data}))]
    artifact = result.data.get("artifact") if isinstance(result.data, dict) else None
    if not isinstance(artifact, str):
        return [TextContent(type="text", text=json.dumps({"ok": False, "summary": "Blender capture did not return an artifact", "data": result.data}))]
    agent = registry()
    selected = agent._project({"project": project})
    root = (agent.config.state_dir / "artifacts" / selected.slug).resolve()
    path = Path(artifact).resolve()
    if not path.is_relative_to(root) or path.suffix.lower() not in (".png", ".jpg", ".jpeg"):
        raise ValueError("Blender capture must belong to the selected project artifact root")
    if path.stat().st_size > 10 * 1024 * 1024:
        raise ValueError("Blender capture exceeds 10 MiB; request a lower resolution")
    mime = "image/png" if path.suffix.lower() == ".png" else "image/jpeg"
    meta = {"ok": True, "summary": result.summary, "project": selected.slug, "artifact": str(path), "sha256": result.data.get("sha256")}
    return [TextContent(type="text", text=json.dumps(meta)), ImageContent(type="image", mimeType=mime, data=base64.b64encode(path.read_bytes()).decode("ascii"))]


@mcp.tool()
def install_blender_adoption() -> dict:
    """Install/sync the lightweight ORDAX bootstrap used to adopt manually opened Blender windows."""
    result = registry().execute("blender.adoption_install", {})
    return {"ok": result.ok, "summary": result.summary, "data": result.data}


@mcp.tool()
def blender_instances() -> dict:
    """List fresh Blender windows discovered by the ORDAX startup bootstrap."""
    result = registry().execute("blender.instances", {})
    return {"ok": result.ok, "summary": result.summary, "data": result.data}


@mcp.tool()
def adopt_blender(
    project: str | None = None,
    pid: int | None = None,
    wait_seconds: float = 8.0,
    allow_blank: bool = False,
) -> dict:
    """Adopt one already-open Blender window for a registered project without opening another window."""
    agent = registry()
    selected = agent.select_available_project(project)
    result = agent.execute(
        "blender.adopt",
        {"project": selected, "pid": pid, "wait_seconds": wait_seconds, "allow_blank": allow_blank},
    )
    return {"ok": result.ok, "summary": result.summary, "data": result.data}


@mcp.tool()
def get_blender_status(project: str | None = None) -> dict:
    """Read the visible Blender companion status for an ORDAX Studio project."""
    return _blender_action("blender.live_status", project)


@mcp.tool()
def start_blender(
    project: str | None = None,
    blend_file: str | None = None,
    wait_seconds: float = 60.0,
    pid: int | None = None,
    adopt_blank: bool = False,
) -> dict:
    """Start or adopt a Blender window; never opens a duplicate while Blender is already running."""
    args: dict = {"wait_seconds": wait_seconds, "adopt_blank": adopt_blank}
    if blend_file:
        args["blend_file"] = blend_file
    if pid is not None:
        args["pid"] = pid
    return _blender_action("blender.live_start", project, args)


@mcp.tool()
def get_scene_info(project: str | None = None, max_objects: int = 200) -> dict:
    """Inspect the current Blender scene through the ORDAX live companion."""
    return _blender_action("blender.live_inspect", project, {"max_objects": max_objects})


@mcp.tool()
def get_object_info(project: str | None = None, object_name: str = "", ordax_object_id: str = "") -> dict:
    """Inspect exactly one Blender object by name or stable ORDAX object id."""
    args = {"object_name": object_name} if object_name else {"ordax_object_id": ordax_object_id}
    return _blender_action("blender.live_object_inspect", project, args)


@mcp.tool()
def get_viewport_screenshot(project: str | None = None, timeout_seconds: float = 120.0) -> list[TextContent | ImageContent]:
    """Capture the visible Blender viewport and return actual image pixels to the model."""
    agent = registry()
    selected = agent.select_available_project(project)
    result = agent.execute("blender.live_capture", {"project": selected, "timeout_seconds": timeout_seconds})
    return _blender_image_result(selected, result)


@mcp.tool()
def add_primitive(
    project: str | None = None,
    name: str = "",
    primitive: str = "cube",
    location: list[float] | None = None,
    size: float | None = None,
    radius: float | None = None,
    depth: float | None = None,
    segments: int | None = None,
) -> dict:
    """Create a contract-validated cube, sphere or cylinder in the visible Blender scene."""
    args: dict = {"name": name, "primitive": primitive}
    for key, value in (("location", location), ("size", size), ("radius", radius), ("depth", depth), ("segments", segments)):
        if value is not None:
            args[key] = value
    return _blender_action("blender.live_create_primitive", project, args)


@mcp.tool()
def modify_object(
    project: str | None = None,
    object_name: str = "",
    ordax_object_id: str = "",
    location: list[float] | None = None,
    rotation_euler: list[float] | None = None,
    scale: list[float] | None = None,
    dimensions: list[float] | None = None,
) -> dict:
    """Apply bounded transforms to one Blender object using a typed selector."""
    args: dict = {"object_name": object_name} if object_name else {"ordax_object_id": ordax_object_id}
    for key, value in (("location", location), ("rotation_euler", rotation_euler), ("scale", scale), ("dimensions", dimensions)):
        if value is not None:
            args[key] = value
    return _blender_action("blender.live_object_transform", project, args)


@mcp.tool()
def scatter_on_surface(
    project: str | None = None,
    object_name: str = "",
    ordax_object_id: str = "",
    source_object_name: str = "",
    name: str = "SurfaceScatter",
    density: float = 1.0,
    seed: int = 0,
    max_instances: int = 1000,
    scale_min: float = 1.0,
    scale_max: float = 1.0,
    align_to_normal: bool = True,
    keep_surface: bool = True,
) -> dict:
    """Create a bounded fixed-seed Geometry Nodes scatter on a mesh surface."""
    args: dict = {
        "source_object_name": source_object_name,
        "name": name,
        "density": density,
        "seed": seed,
        "max_instances": max_instances,
        "scale_min": scale_min,
        "scale_max": scale_max,
        "align_to_normal": align_to_normal,
        "keep_surface": keep_surface,
    }
    args.update({"object_name": object_name} if object_name else {"ordax_object_id": ordax_object_id})
    return _blender_action("blender.live_surface_scatter", project, args)


@mcp.tool()
def preview_boolean_cut(
    project: str | None = None,
    object_name: str = "",
    ordax_object_id: str = "",
    name: str = "BooleanCut",
    profiles: list[dict] | None = None,
) -> dict:
    """Create a bounded non-destructive exact-boolean cutter preview on a mesh target."""
    args: dict = {"name": name, "profiles": profiles or []}
    args.update({"object_name": object_name} if object_name else {"ordax_object_id": ordax_object_id})
    return _blender_action("blender.live_boolean_cut_preview", project, args)


@mcp.tool()
def commit_boolean_cut(project: str | None = None, preview_id: str = "") -> dict:
    """Commit an ORDAX boolean preview non-destructively by keeping live modifiers and hiding cutters."""
    return _blender_action("blender.live_boolean_cut_commit", project, {"preview_id": preview_id})


@mcp.tool()
def cancel_boolean_cut(project: str | None = None, preview_id: str = "") -> dict:
    """Rollback an ORDAX boolean preview or committed cutter workflow in one operation."""
    return _blender_action("blender.live_boolean_cut_cancel", project, {"preview_id": preview_id})


@mcp.tool()
def cleanup_mesh(
    project: str | None = None,
    object_name: str = "",
    ordax_object_id: str = "",
    repair: str = "remove_loose_vertices",
    expected_base_geometry_sha256: str = "",
    expected_loose_vertices: int | None = None,
) -> dict:
    """Apply one revision-guarded bounded topology cleanup to the base mesh."""
    args: dict = {
        "repair": repair,
        "expected_base_geometry_sha256": expected_base_geometry_sha256,
    }
    args.update({"object_name": object_name} if object_name else {"ordax_object_id": ordax_object_id})
    if expected_loose_vertices is not None:
        args["expected_loose_vertices"] = expected_loose_vertices
    return _blender_action("blender.live_mesh_cleanup", project, args)


@mcp.tool()
def preview_degenerate_repair(
    project: str | None = None,
    object_name: str = "",
    ordax_object_id: str = "",
    expected_base_geometry_sha256: str = "",
    expected_zero_length_edges: int = 0,
    expected_degenerate_faces: int = 0,
    threshold: float = 1e-10,
) -> dict:
    """Preview Degenerate Dissolve on a reversible candidate mesh copy."""
    args: dict = {
        "expected_base_geometry_sha256": expected_base_geometry_sha256,
        "expected_zero_length_edges": expected_zero_length_edges,
        "expected_degenerate_faces": expected_degenerate_faces,
        "threshold": threshold,
    }
    args.update({"object_name": object_name} if object_name else {"ordax_object_id": ordax_object_id})
    return _blender_action("blender.live_degenerate_repair_preview", project, args)


@mcp.tool()
def commit_degenerate_repair(project: str | None = None, preview_id: str = "") -> dict:
    """Commit a reviewed degenerate-repair candidate mesh."""
    return _blender_action("blender.live_degenerate_repair_commit", project, {"preview_id": preview_id})


@mcp.tool()
def cancel_degenerate_repair(project: str | None = None, preview_id: str = "") -> dict:
    """Cancel a degenerate-repair preview and restore the exact original mesh."""
    return _blender_action("blender.live_degenerate_repair_cancel", project, {"preview_id": preview_id})


@mcp.tool()
def preview_merge_by_distance(
    project: str | None = None,
    object_name: str = "",
    ordax_object_id: str = "",
    expected_base_geometry_sha256: str = "",
    vertex_indices: list[int] | None = None,
    distance: float = 1e-5,
) -> dict:
    """Preview Merge by Distance on explicit base-mesh vertex indices only."""
    args: dict = {
        "expected_base_geometry_sha256": expected_base_geometry_sha256,
        "vertex_indices": list(vertex_indices or []),
        "distance": distance,
    }
    args.update({"object_name": object_name} if object_name else {"ordax_object_id": ordax_object_id})
    return _blender_action("blender.live_merge_by_distance_preview", project, args)


@mcp.tool()
def commit_merge_by_distance(project: str | None = None, preview_id: str = "") -> dict:
    """Commit a reviewed explicit-selection Merge by Distance candidate."""
    return _blender_action("blender.live_merge_by_distance_commit", project, {"preview_id": preview_id})


@mcp.tool()
def cancel_merge_by_distance(project: str | None = None, preview_id: str = "") -> dict:
    """Cancel a Merge by Distance preview and restore the exact original mesh."""
    return _blender_action("blender.live_merge_by_distance_cancel", project, {"preview_id": preview_id})


@mcp.tool()
def preview_boundary_hole_fill(
    project: str | None = None,
    object_name: str = "",
    ordax_object_id: str = "",
    expected_base_geometry_sha256: str = "",
    edge_indices: list[int] | None = None,
) -> dict:
    """Preview a localized fill for one explicit closed base-mesh boundary loop."""
    args: dict = {
        "expected_base_geometry_sha256": expected_base_geometry_sha256,
        "edge_indices": list(edge_indices or []),
    }
    args.update({"object_name": object_name} if object_name else {"ordax_object_id": ordax_object_id})
    return _blender_action("blender.live_boundary_hole_fill_preview", project, args)


@mcp.tool()
def commit_boundary_hole_fill(project: str | None = None, preview_id: str = "") -> dict:
    """Commit a reviewed boundary hole-fill candidate."""
    return _blender_action("blender.live_boundary_hole_fill_commit", project, {"preview_id": preview_id})


@mcp.tool()
def cancel_boundary_hole_fill(project: str | None = None, preview_id: str = "") -> dict:
    """Cancel a boundary hole-fill preview and restore the exact original mesh."""
    return _blender_action("blender.live_boundary_hole_fill_cancel", project, {"preview_id": preview_id})


@mcp.tool()
def delete_object(project: str | None = None, object_name: str = "", missing_ok: bool = False) -> dict:
    """Remove one named Blender object through the bounded live mutation contract."""
    return _blender_action("blender.live_object_remove", project, {"object_names": [object_name], "missing_ok": missing_ok})


@mcp.tool()
def set_material(
    project: str | None = None,
    object_name: str = "",
    ordax_object_id: str = "",
    material_name: str = "",
    base_color: list[float] | None = None,
    roughness: float = 0.4,
    metallic: float = 0.0,
    transmission: float = 0.0,
    alpha: float = 1.0,
    ior: float = 1.45,
) -> dict:
    """Apply a normalized Principled material to one Blender object."""
    args: dict = {"material_name": material_name, "roughness": roughness, "metallic": metallic, "transmission": transmission, "alpha": alpha, "ior": ior}
    args.update({"object_name": object_name} if object_name else {"ordax_object_id": ordax_object_id})
    if base_color is not None:
        args["base_color"] = base_color
    return _blender_action("blender.live_material_apply", project, args)


@mcp.tool()
def batch_edit(project: str | None = None, steps: list[dict] | None = None, stop_on_error: bool = True) -> dict:
    """Execute up to 128 allow-listed Blender edits as one typed ORDAX batch."""
    return _blender_action("blender.live_batch", project, {"steps": steps or [], "stop_on_error": stop_on_error})


@mcp.tool()
def save_blender(project: str | None = None) -> dict:
    """Save the currently attached Blender project through the live companion."""
    return _blender_action("blender.live_save", project)


@mcp.tool()
def run_blender_project_script(project: str | None = None, script_path: str = "", timeout_seconds: float = 300.0) -> dict:
    """Run an approved project-relative .py automation file; arbitrary inline Python is not accepted."""
    return _blender_action("blender.live_run_script", project, {"script_path": script_path, "timeout_seconds": timeout_seconds})


@mcp.tool()
def continuity_state(project: str) -> dict:
    """Load the non-expiring continuation state for one ORDAX project."""
    result = registry().execute("continuity.get", {"project": project})
    return {"ok": result.ok, "summary": result.summary, "data": result.data}


@mcp.tool()
def continuity_update(
    project: str,
    summary: str,
    next_action: str = "",
    completed: list[str] | None = None,
    blockers: list[str] | None = None,
    changed_paths: list[str] | None = None,
) -> dict:
    """Persist non-expiring project progress for future sessions and chats."""
    result = registry().execute(
        "continuity.update",
        {
            "project": project,
            "summary": summary,
            "next_action": next_action,
            "completed": list(completed or []),
            "blockers": list(blockers or []),
            "changed_paths": list(changed_paths or []),
        },
    )
    return {"ok": result.ok, "summary": result.summary, "data": result.data}


@mcp.tool()
def session_context(project: str) -> dict:
    """Load persistent project memory, tasks and checkpoints before continuing work."""
    result = registry().execute("memory.context", {"project": project})
    return {"ok": result.ok, "summary": result.summary, "data": result.data}

@mcp.tool()
def session_resume(project: str | None = None) -> dict:
    """Resume a persistent ORDAX Studio session using the active or configured project."""
    agent = registry()
    selected = agent.select_available_project(project)
    result = agent.execute("session.resume", {"project": selected})
    return {"ok": result.ok, "summary": result.summary, "data": result.data}


@mcp.tool()
def session_finish(session_id: int) -> dict:
    """Mark an ORDAX Studio session as finished without deleting history."""
    result = registry().execute("session.finish", {"session_id": session_id})
    return {"ok": result.ok, "summary": result.summary, "data": result.data}

@mcp.tool()
def memory_remember(project: str, content: str, kind: str = "note") -> dict:
    """Persist an important project fact or decision for future sessions."""
    result = registry().execute("memory.remember", {"project": project, "content": content, "kind": kind})
    return {"ok": result.ok, "summary": result.summary, "data": result.data}

@mcp.tool()
def session_checkpoint(project: str, summary: str) -> dict:
    """Save a resumable checkpoint including the current Git state."""
    result = registry().execute("memory.checkpoint", {"project": project, "summary": summary})
    return {"ok": result.ok, "summary": result.summary, "data": result.data}

@mcp.tool()
def action_execute(action: str, project: str, arguments: dict | None = None) -> dict:
    """Execute a registered action on one project. Returns ok, summary and structured evidence.

Examples: project.observe {app:unity}, blender.inspect {blend_file:scene.blend},
blender.render_preview {blend_file:scene.blend,width:1280,height:720},
observation.capture {app:unity,frames:3,interval_seconds:1},
project.references {asset:wooden-boat},
blender.reference_review {asset:wooden-boat,reference_ids:[front],object_names:[Hull]},
blender.reference_generation_pass {asset:wooden-boat,reference_ids:[front],object_names:[Hull],script_path:automation/blender/refine.py},
git.sync {branch:main}, unity.install_companion {} (adds an Editor script).
Use artifact_image for managed reference/model PNG/JPEG evidence returned by actions.
"""
    payload = {**(arguments or {}), "project": project}
    result = registry().execute(action, payload)
    return {"ok": result.ok, "summary": result.summary, "data": result.data}


@mcp.tool()
def project_preview_image(project: str | None = None, refresh: bool = False) -> list[TextContent | ImageContent]:
    """Return the current ORDAX project preview as actual image pixels for visual reasoning."""
    agent = registry()
    selected = agent.select_available_project(project)
    if refresh:
        captured = agent.execute("project.preview_capture", {"project": selected})
        if not captured.ok:
            return [TextContent(type="text", text=json.dumps({"ok": False, "summary": captured.summary}))]
    status = agent.execute("project.preview_status", {"project": selected})
    image = status.data.get("latest_image") if status.ok else None
    if not image:
        return [TextContent(type="text", text=json.dumps({"ok": False, "summary": "No preview image is available", "data": status.data}))]
    preview_payload = {"project": selected, **image["artifact_preview_payload"], "thumbnail": True, "max_width": 1600, "max_height": 1200}
    preview = agent.execute("artifact.preview", preview_payload)
    if not preview.ok:
        return [TextContent(type="text", text=json.dumps({"ok": False, "summary": preview.summary}))]
    meta = {"project": selected, "mode": status.data.get("mode"), "artifact": image.get("path"), "sha256": preview.data.get("sha256")}
    return [TextContent(type="text", text=json.dumps(meta)), ImageContent(type="image", mimeType=preview.data["mime_type"], data=preview.data["base64"])]


def _artifact_image_contents(
    agent: ActionRegistry,
    project: str,
    artifact_path: str,
    *,
    metadata: dict | None = None,
) -> list[TextContent | ImageContent]:
    selected = agent._project({"project": project})
    root = (agent.config.state_dir / "artifacts" / selected.slug).resolve()
    path = Path(artifact_path).resolve()
    if not path.is_relative_to(root) or path.suffix.lower() not in (".png", ".jpg", ".jpeg"):
        raise ValueError("Image must be an artifact belonging to the selected project")
    if not path.is_file():
        raise FileNotFoundError(f"Image artifact not found: {path}")
    if path.stat().st_size > 10 * 1024 * 1024:
        raise ValueError("Image exceeds 10 MiB; request a lower resolution")

    details = {"project": project, "artifact": str(path), **(metadata or {})}
    mime_type = "image/png" if path.suffix.lower() == ".png" else "image/jpeg"
    return [
        TextContent(type="text", text=json.dumps(details)),
        ImageContent(
            type="image",
            mimeType=mime_type,
            data=base64.b64encode(path.read_bytes()).decode("ascii"),
        ),
    ]


@mcp.tool()
def artifact_image(project: str, artifact_path: str) -> list[TextContent | ImageContent]:
    """Return actual PNG/JPEG pixels to the model, not just a local file path (max 10 MiB)."""
    return _artifact_image_contents(registry(), project, artifact_path)


def _ensure_blender_live(
    agent: ActionRegistry,
    project: str,
    *,
    blend_file: str | None,
    wait_seconds: float,
) -> dict:
    """Ensure a current visible Blender companion without risking dirty-session loss."""
    status = agent.execute("blender.live_status", {"project": project})
    data = status.data if isinstance(status.data, dict) else {}
    current = (
        status.ok
        and data.get("presence_fresh") is True
        and data.get("protocol_compatible") is True
        and data.get("companion_current") is True
    )
    if current:
        presence = data.get("presence") if isinstance(data.get("presence"), dict) else {}
        if not blend_file:
            return {"auto_started": False, "status": data}
        selected = agent._project({"project": project})
        requested = selected.path(blend_file).resolve()
        loaded_raw = str(presence.get("file") or "").strip()
        loaded = Path(loaded_raw).resolve() if loaded_raw else None
        if loaded == requested:
            return {"auto_started": False, "status": data}
        if bool(presence.get("is_dirty")):
            raise RuntimeError(
                "A different Blender scene is open with unsaved changes; save it before switching scenes"
            )
        stopped = agent.execute("blender.live_stop", {"project": project})
        if not stopped.ok:
            raise RuntimeError(stopped.summary)
        deadline = time.monotonic() + 15.0
        while time.monotonic() < deadline:
            probe = agent.execute("blender.live_status", {"project": project})
            probe_data = probe.data if isinstance(probe.data, dict) else {}
            if not probe.ok or probe_data.get("presence_fresh") is not True:
                break
            time.sleep(0.1)

    if not blend_file:
        raise RuntimeError(
            "Visible Blender live session is unavailable or outdated; "
            "pass blend_file to let this MCP tool start or adopt the correct scene safely"
        )

    started = agent.execute(
        "blender.live_start",
        {
            "project": project,
            "blend_file": blend_file,
            "wait_seconds": max(3.0, min(float(wait_seconds), 600.0)),
        },
    )
    if not started.ok:
        raise RuntimeError(started.summary)
    started_data = started.data if isinstance(started.data, dict) else {}
    return {"auto_started": True, "status": started_data}


@mcp.tool()
def blender_live_view(
    project: str,
    blend_file: str | None = None,
    timeout_seconds: float = 120.0,
) -> list[TextContent | ImageContent]:
    """Capture the visible Blender viewport, recovering the requested scene when needed."""
    timeout = max(5.0, min(float(timeout_seconds), 600.0))
    agent = registry()
    session = _ensure_blender_live(
        agent, project, blend_file=blend_file, wait_seconds=timeout
    )
    result = agent.execute(
        "blender.live_capture",
        {"project": project, "timeout_seconds": timeout},
    )
    if not result.ok:
        raise RuntimeError(result.summary)

    artifact = result.data.get("artifact")
    if not isinstance(artifact, str) or not artifact.strip():
        raise RuntimeError("Blender live capture completed without an image artifact")
    return _artifact_image_contents(
        agent,
        project,
        artifact,
        metadata={
            "summary": result.summary,
            "sha256": result.data.get("sha256"),
            "snapshot_path": result.data.get("snapshot_path"),
            "transport": result.data.get("transport"),
            "session": session,
        },
    )


@mcp.tool()
def blender_live_multiview(
    project: str,
    views: list[str] | None = None,
    blend_file: str | None = None,
    object_names: list[str] | None = None,
    width: int = 512,
    height: int = 512,
    mode: str = "material",
    timeout_seconds: float = 240.0,
) -> list[TextContent | ImageContent]:
    """Capture deterministic Blender views and return every image to the MCP client."""
    requested_views = views or ["front", "right", "top", "three_quarter"]
    if not 1 <= len(requested_views) <= 6:
        raise ValueError("views must contain between 1 and 6 entries")
    if not 128 <= int(width) <= 1024 or not 128 <= int(height) <= 1024:
        raise ValueError("direct MCP multiview resolution must be between 128 and 1024")
    normalized_mode = str(mode).strip().lower()
    if normalized_mode not in {"material", "silhouette"}:
        raise ValueError("mode must be material or silhouette")

    timeout = max(15.0, min(float(timeout_seconds), 600.0))
    payload: dict = {
        "project": project,
        "views": requested_views,
        "width": int(width),
        "height": int(height),
        "mode": normalized_mode,
        "timeout_seconds": timeout,
    }
    if object_names is not None:
        payload["object_names"] = object_names

    agent = registry()
    session = _ensure_blender_live(
        agent, project, blend_file=blend_file, wait_seconds=timeout
    )
    result = agent.execute("blender.live_multiview_capture", payload)
    if not result.ok:
        raise RuntimeError(result.summary)

    artifacts = result.data.get("artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        raise RuntimeError("Blender multiview completed without image artifacts")

    summary = {
        "project": project,
        "summary": result.summary,
        "primary_artifact": result.data.get("primary_artifact"),
        "manifest": result.data.get("manifest"),
        "bounds": result.data.get("bounds"),
        "objects": result.data.get("objects"),
        "resolution": result.data.get("resolution"),
        "projection": result.data.get("projection"),
        "render_engine": result.data.get("render_engine"),
        "mode": result.data.get("mode"),
        "session": session,
    }
    contents: list[TextContent | ImageContent] = [
        TextContent(type="text", text=json.dumps(summary))
    ]
    for item in artifacts:
        if not isinstance(item, dict):
            raise RuntimeError("Blender multiview returned an invalid artifact entry")
        artifact = item.get("artifact")
        if not isinstance(artifact, str) or not artifact.strip():
            raise RuntimeError("Blender multiview artifact path is missing")
        validated = _artifact_image_contents(
            agent,
            project,
            artifact,
            metadata={"view": item.get("view"), "sha256": item.get("sha256")},
        )
        contents.extend(validated)
    return contents
def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
