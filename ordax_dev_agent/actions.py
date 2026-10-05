from __future__ import annotations

import subprocess
import threading
import re
from importlib.metadata import entry_points
from pathlib import Path
from typing import Any, Callable

from .config import AgentConfig
from .models import ActionResult
from .projects import load_projects, Project
from .observations import ObservationActions
from .references import ReferenceActions
from .blender_actions import BlenderActions
from .unity_status_actions import UnityStatusActions
from .unity_actions import UnityActions
from .agent_actions import AgentActions
from .agent_orchestration_actions import AgentOrchestrationActions
from .artifact_actions import ArtifactActions
from .git_actions import GitActions
from .developer_actions import DeveloperActions
from .persistent_process_actions import PersistentProcessActions
from .project_text_actions import ProjectTextActions
from .workspace_actions import WorkspaceActions
from .preview_actions import PreviewActions
from .browser_session_actions import BrowserSessionActions
from .computer_control_actions import ComputerControlActions
from .computer_parity_actions import ComputerParityActions
from .computer_filesystem_actions import ComputerFilesystemActions
from .memory_actions import MemoryActions
from .component_actions import ComponentActions
from .game_asset_catalog_actions import GameAssetCatalogActions
from .game_asset_status_actions import GameAssetStatusActions
from .game_asset_actions import GameAssetActions
from .game_asset_artifact_actions import GameAssetArtifactActions
from .game_asset_engine_export_actions import GameAssetEngineExportActions
from .game_asset_threejs_actions import GameAssetThreeJsActions
from .game_asset_web_runtime_actions import GameAssetWebRuntimeActions
from .game_asset_threejs_viewer_runtime_actions import GameAssetThreeJsViewerRuntimeActions
from .game_asset_godot_actions import GameAssetGodotActions
from .game_asset_image_actions import GameAssetImageActions
from .game_asset_lod_actions import GameAssetLodActions
from .game_asset_runtime_actions import GameAssetRuntimeActions
from .game_asset_unity_actions import GameAssetUnityActions
from .game_asset_unity_semantic_actions import GameAssetUnitySemanticActions
from .game_asset_unity_import_config_actions import GameAssetUnityImportConfigActions
from .game_asset_unity_animation_actions import GameAssetUnityAnimationActions
from .game_asset_unreal_actions import GameAssetUnrealActions
from .game_asset_unreal_semantic_actions import GameAssetUnrealSemanticActions
from .visual_environment_actions import VisualEnvironmentActions
from .generated_asset_actions import GeneratedAssetActions
from .mixamo_actions import MixamoActions
from .rodin_actions import RodinActions
from .comfyui_actions import ComfyUIActions
from .aleph_actions import AlephActions
from .aleph_scene_actions import AlephSceneActions
from .execution_lock import ExecutionLock
from .windows_dpi import ensure_physical_desktop_coordinates
from .adapter_contracts import (
    adapter_contract_catalog,
    builtin_adapter_contracts,
    external_adapter_contract,
)


Action = Callable[[dict[str, Any]], ActionResult]

# Pure observation calls must remain usable while an unrelated long-running
# action owns the global execution lock. They do not mutate project/runtime
# state or synthesize input; mutating Computer Control stays serialized below.
_NONBLOCKING_OBSERVATION_ACTIONS = frozenset({
    "computer.access_status",
    "computer.file_stat",
    "computer.directory_list",
    "computer.text_read",
    "computer.search",
    "computer.windows",
    "computer.active_window",
    "computer.screen_info",
    "computer.processes",
    "computer.clipboard_read",
    "computer.screenshot",
})


class ActionRegistry(
    ObservationActions,
    ReferenceActions,
    BlenderActions,
    UnityStatusActions,
    UnityActions,
    AgentActions,
    AgentOrchestrationActions,
    ArtifactActions,
    GitActions,
    DeveloperActions,
    PersistentProcessActions,
    ProjectTextActions,
    WorkspaceActions,
    PreviewActions,
    BrowserSessionActions,
    ComputerControlActions,
    ComputerParityActions,
    ComputerFilesystemActions,
    MemoryActions,
    ComponentActions,
    GameAssetCatalogActions,
    GameAssetStatusActions,
    GameAssetActions,
    GameAssetArtifactActions,
    GameAssetEngineExportActions,
    GameAssetThreeJsActions,
    GameAssetWebRuntimeActions,
    GameAssetThreeJsViewerRuntimeActions,
    GameAssetGodotActions,
    GameAssetImageActions,
    GameAssetLodActions,
    GameAssetRuntimeActions,
    GameAssetUnityActions,
    GameAssetUnitySemanticActions,
    GameAssetUnityImportConfigActions,
    GameAssetUnityAnimationActions,
    GameAssetUnrealActions,
    GameAssetUnrealSemanticActions,
    VisualEnvironmentActions,
    GeneratedAssetActions,
    MixamoActions,
    RodinActions,
    ComfyUIActions,
    AlephActions,
    AlephSceneActions,
):
    """Canonical typed action registry for local and remote ORDAX capabilities."""

    def __init__(self, config: AgentConfig):
        self.desktop_coordinate_space = ensure_physical_desktop_coordinates()
        self.config = config
        self.projects = load_projects(config)
        self.on_observation = None
        self._execution_lock = threading.Lock()
        self._actions: dict[str, Action] = {
            "projects.list": self.projects_list,
            "workspace.repository_catalog": self.workspace_repository_catalog,
            "workspace.list_projects": self.workspace_list_projects,
            "workspace.project_create": self.workspace_project_create,
            "workspace.bind_project": self.workspace_bind_project,
            "workspace.file_stat": self.workspace_file_stat,
            "workspace.directory_list": self.workspace_directory_list,
            "workspace.text_read": self.workspace_text_read,
            "workspace.text_write": self.workspace_text_write,
            "workspace.text_patch": self.workspace_text_patch,
            "workspace.directory_create": self.workspace_directory_create,
            "workspace.path_remove": self.workspace_path_remove,
            "workspace.path_move": self.workspace_path_move,
            "terminal.exec": self.terminal_exec,
            "git.command": self.git_command,
            "process.start": self.process_start,
            "process.status": self.process_status,
            "process.list": self.process_list,
            "process.logs": self.process_logs,
            "process.write_stdin": self.process_write_stdin,
            "process.stop": self.process_stop,
            "browser.start": self.browser_start,
            "browser.status": self.browser_status,
            "browser.list": self.browser_list,
            "browser.navigate": self.browser_navigate,
            "browser.snapshot": self.browser_snapshot,
            "browser.click": self.browser_click,
            "browser.type": self.browser_type,
            "browser.screenshot": self.browser_screenshot,
            "browser.stop": self.browser_stop,
            "computer.windows": self.computer_windows,
            "computer.active_window": self.computer_active_window,
            "computer.screenshot": self.computer_screenshot,
            "computer.screen_info": self.computer_screen_info,
            "computer.mouse_move": self.computer_mouse_move,
            "computer.drag": self.computer_drag,
            "computer.clipboard_read": self.computer_clipboard_read,
            "computer.clipboard_write": self.computer_clipboard_write,
            "computer.launch_app": self.computer_launch_app,
            "computer.focus_window": self.computer_focus_window,
            "computer.click": self.computer_click,
            "computer.type": self.computer_type,
            "computer.hotkey": self.computer_hotkey,
            "computer.scroll": self.computer_scroll,
            "computer.processes": self.computer_processes,
            "computer.terminate_process": self.computer_terminate_process,
            "computer.access_status": self.computer_access_status,
            "computer.file_stat": self.computer_file_stat,
            "computer.directory_list": self.computer_directory_list,
            "computer.text_read": self.computer_text_read,
            "computer.text_write": self.computer_text_write,
            "computer.text_patch": self.computer_text_patch,
            "computer.directory_create": self.computer_directory_create,
            "computer.path_move": self.computer_path_move,
            "computer.path_remove": self.computer_path_remove,
            "computer.search": self.computer_search,
            "memory.status": self.memory_status,
            "memory.context": self.memory_context,
            "memory.remember": self.memory_remember,
            "memory.task_add": self.memory_task_add,
            "memory.task_toggle": self.memory_task_toggle,
            "memory.checkpoint": self.memory_checkpoint,
            "continuity.get": self.continuity_get,
            "continuity.update": self.continuity_update,
            "handoff.create": self.handoff_create,
            "handoff.get": self.handoff_get,
            "session.resume": self.session_resume,
            "session.finish": self.session_finish,
            "orchestrator.status": self.orchestrator_status,
            "orchestrator.agent_create": self.orchestrator_agent_create,
            "orchestrator.agent_state": self.orchestrator_agent_state,
            "orchestrator.goal_create": self.orchestrator_goal_create,
            "orchestrator.goal_state": self.orchestrator_goal_state,
            "orchestrator.session_start": self.orchestrator_session_start,
            "orchestrator.session_usage": self.orchestrator_session_usage,
            "orchestrator.session_checkpoint": self.orchestrator_session_checkpoint,
            "orchestrator.session_rotate": self.orchestrator_session_rotate,
            "orchestrator.continuation": self.orchestrator_continuation,
            "orchestrator.message_send": self.orchestrator_message_send,
            "orchestrator.inbox": self.orchestrator_inbox,
            "orchestrator.message_read": self.orchestrator_message_read,
            "orchestrator.work_enqueue": self.orchestrator_work_enqueue,
            "orchestrator.work_claim": self.orchestrator_work_claim,
            "orchestrator.work_heartbeat": self.orchestrator_work_heartbeat,
            "orchestrator.work_complete": self.orchestrator_work_complete,
            "orchestrator.work_fail": self.orchestrator_work_fail,
            "orchestrator.work_list": self.orchestrator_work_list,
            "project.archive_to_hordax": self.project_archive_to_hordax,
            "project.observe": self.project_observe,
            "project.inventory": self.project_inventory,
            "project.preview_status": self.project_preview_status,
            "project.preview_capture": self.project_preview_capture,
            "project.preview_start": self.project_preview_start,
            "project.preview_stop": self.project_preview_stop,
            "project.preview_logs": self.project_preview_logs,
            "project.references": self.project_references,
            "project.reference_images": self.project_reference_images,
            "project.text_read": self.project_text_read,
            "project.text_read_batch": self.project_text_read_batch,
            "project.search_text": self.project_search_text,
            "project.text_write": self.project_text_write,
            "project.text_patch": self.project_text_patch,
            "observation.capture": self.observation_capture,
            "visual.environment_schema": self.visual_environment_schema,
            "visual.environment_preset": self.visual_environment_preset,
            "visual.environment_write": self.visual_environment_write,
            "blender.inspect": self.blender_inspect,
            "blender.benchmark": self.blender_benchmark,
            "blender.render_preview": self.blender_render_preview,
            "blender.adoption_install": self.blender_adoption_install,
            "blender.instances": self.blender_instances,
            "blender.adopt": self.blender_adopt,
            "blender.live_start": self.blender_live_start,
            "blender.live_status": self.blender_live_status,
            "blender.live_inspect": self.blender_live_inspect,
            "blender.live_scene_snapshot": self.blender_live_scene_snapshot,
            "blender.live_scene_reset": self.blender_live_scene_reset,
            "blender.live_object_inspect": self.blender_live_object_inspect,
            "blender.live_object_fingerprints": self.blender_live_object_fingerprints,
            "blender.live_contact_audit": self.blender_live_contact_audit,
            "blender.live_quality_gate": self.blender_live_quality_gate,
            "blender.live_modeling_schema": self.blender_live_modeling_schema,
            "blender.live_modeling_plan": self.blender_live_modeling_plan,
            "blender.live_object_transform": self.blender_live_object_transform,
            "blender.live_object_remove": self.blender_live_object_remove,
            "blender.live_extract_region": self.blender_live_extract_region,
            "blender.live_cleanup_orphans": self.blender_live_cleanup_orphans,
            "blender.live_create_primitive": self.blender_live_create_primitive,
            "blender.live_create_box_with_cutouts": self.blender_live_create_box_with_cutouts,
            "blender.live_add_modifier": self.blender_live_add_modifier,
            "blender.live_surface_scatter": self.blender_live_surface_scatter,
            "blender.live_boolean_cut_preview": self.blender_live_boolean_cut_preview,
            "blender.live_boolean_cut_commit": self.blender_live_boolean_cut_commit,
            "blender.live_boolean_cut_cancel": self.blender_live_boolean_cut_cancel,
            "blender.live_mesh_cleanup": self.blender_live_mesh_cleanup,
            "blender.live_degenerate_repair_preview": self.blender_live_degenerate_repair_preview,
            "blender.live_degenerate_repair_commit": self.blender_live_degenerate_repair_commit,
            "blender.live_degenerate_repair_cancel": self.blender_live_degenerate_repair_cancel,
            "blender.live_merge_by_distance_preview": self.blender_live_merge_by_distance_preview,
            "blender.live_merge_by_distance_commit": self.blender_live_merge_by_distance_commit,
            "blender.live_merge_by_distance_cancel": self.blender_live_merge_by_distance_cancel,
            "blender.live_boundary_hole_fill_preview": self.blender_live_boundary_hole_fill_preview,
            "blender.live_boundary_hole_fill_commit": self.blender_live_boundary_hole_fill_commit,
            "blender.live_boundary_hole_fill_cancel": self.blender_live_boundary_hole_fill_cancel,
            "blender.live_material_apply": self.blender_live_material_apply,
            "blender.live_create_camera": self.blender_live_create_camera,
            "blender.live_create_light": self.blender_live_create_light,
            "blender.live_scene_presentation": self.blender_live_scene_presentation,
            "blender.live_import_asset": self.blender_live_import_asset,
            "blender.live_animate_transform": self.blender_live_animate_transform,
            "blender.live_batch": self.blender_live_batch,
            "blender.live_viewport_proxy": self.blender_live_viewport_proxy,
            "blender.live_bake_work_proxy": self.blender_live_bake_work_proxy,
            "blender.live_object_metadata": self.blender_live_object_metadata,
            "blender.live_api_schema": self.blender_live_api_schema,
            "blender.live_api_lookup": self.blender_live_api_lookup,
            "blender.live_node_schema": self.blender_live_node_schema,
            "blender.live_export": self.blender_live_export,
            "blender.export_headless": self.blender_export_headless,
            "blender.extract_region_headless": self.blender_extract_region_headless,
            "blender.live_checkpoint_create": self.blender_live_checkpoint_create,
            "blender.live_checkpoint_list": self.blender_live_checkpoint_list,
            "blender.live_checkpoint_restore": self.blender_live_checkpoint_restore,
            "blender.live_trajectory": self.blender_live_trajectory,
            "blender.live_generation_pass": self.blender_live_generation_pass,
            "blender.live_result": self.blender_live_result,
            "blender.live_run_script": self.blender_live_run_script,
            "blender.live_capture": self.blender_live_capture,
            "blender.live_multiview_capture": self.blender_live_multiview_capture,
            "blender.live_save": self.blender_live_save,
            "blender.live_stop": self.blender_live_stop,
            "blender.asset_search": self.blender_asset_search,
            "blender.asset_manifest": self.blender_asset_manifest,
            "blender.multiview_compare": self.blender_multiview_compare,
            "blender.reference_review": self.blender_reference_review,
            "blender.reference_generation_pass": self.blender_reference_generation_pass,
            "blender.reference_decision": self.blender_reference_decision,
            "game_assets.providers": self.game_assets_providers,
            "game_assets.ecosystem_catalog": self.game_assets_ecosystem_catalog,
            "game_assets.export_profiles": self.game_assets_export_profiles,
            "game_assets.mixamo_handoff": self.game_assets_mixamo_handoff,
            "game_assets.provider_submit": self.game_assets_provider_submit,
            "game_assets.provider_status": self.game_assets_provider_status,
            "game_assets.provider_download": self.game_assets_provider_download,
            "game_assets.meshy_submit_images": self.game_assets_meshy_submit_images,
            "game_assets.tripo_submit_images": self.game_assets_tripo_submit_images,
            "game_assets.rodin_submit_images": self.game_assets_rodin_submit_images,
            "game_assets.artifact_verify": self.game_assets_artifact_verify,
            "game_assets.blender_ingest_generated": self.game_assets_blender_ingest_generated,
            "game_assets.blender_runtime_audit": self.game_assets_blender_runtime_audit,
            "game_assets.blender_generate_static_lods": self.game_assets_blender_generate_static_lods,
            "game_assets.blender_export_verified": self.game_assets_blender_export_verified,
            "game_assets.engine_export_verify": self.game_assets_engine_export_verify,
            "game_assets.engine_handoff_audit": self.game_assets_engine_handoff_audit,
            "game_assets.web_glb_audit": self.game_assets_web_glb_audit,
            "game_assets.threejs_prepare_viewer": self.game_assets_threejs_prepare_viewer,
            "game_assets.threejs_runtime_audit": self.game_assets_threejs_runtime_audit,
            "game_assets.threejs_browser_validate": self.game_assets_web_runtime_validate,
            "game_assets.threejs_viewer_validate": self.game_assets_threejs_viewer_validate,
            "game_assets.godot_import_validate": self.game_assets_godot_import_validate,
            "game_assets.unreal_import_validate": self.game_assets_unreal_import_validate,
            "game_assets.unreal_asset_audit": self.game_assets_unreal_asset_audit,
            "game_assets.unity_import_generated": self.game_assets_unity_import_generated,
            "game_assets.unity_import_engine_export": self.game_assets_unity_import_engine_export,
            "game_assets.unity_model_audit": self.game_assets_unity_model_audit,
            "game_assets.unity_semantic_audit": self.game_assets_unity_semantic_audit,
            "game_assets.unity_character_import_configure": self.game_assets_unity_character_import_configure,
            "game_assets.unity_animation_sample_audit": self.game_assets_unity_animation_sample_audit,
            "game_assets.unity_build_static_lod_prefab": self.game_assets_unity_build_static_lod_prefab,
            "game_assets.blender_character_preflight": self.game_assets_blender_character_preflight,
            "game_assets.blender_export": self.game_assets_blender_export,
            "game_assets.blender_import_fbx": self.game_assets_blender_import_fbx,
            "game_assets.rodin_submit_text": self.game_assets_rodin_submit_text,
            "game_assets.rodin_status": self.game_assets_rodin_status,
            "game_assets.rodin_download_manifest": self.game_assets_rodin_download_manifest,
            "game_assets.comfyui_status": self.game_assets_comfyui_status,
            "game_assets.comfyui_node_info": self.game_assets_comfyui_node_info,
            "game_assets.comfyui_run_workflow": self.game_assets_comfyui_run_workflow,
            "game_assets.comfyui_history": self.game_assets_comfyui_history,
            "geo.aleph_status": self.geo_aleph_status,
            "geo.aleph_ensure": self.geo_aleph_ensure,
            "geo.aleph_update": self.geo_aleph_update,
            "geo.aleph_resolve": self.geo_aleph_resolve,
            "geo.aleph_satellite": self.geo_aleph_satellite,
            "geo.aleph_streetview": self.geo_aleph_streetview,
            "geo.aleph_capture": self.geo_aleph_capture,
            "geo.aleph_capture_resume": self.geo_aleph_capture_resume,
            "geo.aleph_capture_export": self.geo_aleph_capture_export,
            "geo.aleph_capture_inspect": self.geo_aleph_capture_inspect,
            "geo.aleph_blender_stage": self.geo_aleph_blender_stage,
            "unity.install_companion": self.unity_install_companion,
            "unity.project_profile": self.unity_project_profile,
            "unity.capabilities": self.unity_capabilities,
            "unity.skill_catalog": self.unity_skill_catalog,
            "unity.cli_status": self.unity_cli_status,
            "unity.pipeline_install": self.unity_pipeline_install,
            "unity.pipeline_catalog": self.unity_pipeline_catalog,
            "unity.pipeline_command": self.unity_pipeline_command,
            "unity.asset_inventory": self.unity_asset_inventory,
            "unity.asset_import": self.unity_asset_import,
            "unity.scene_open": self.unity_scene_open,
            "unity.scene_summary": self.unity_scene_summary,
            "unity.physics_audit": self.unity_physics_audit,
            "unity.spatial_audit": self.unity_spatial_audit,
            "unity.benchmark_islands_generate": self.unity_benchmark_islands_generate,
            "agent.status": self.agent_status,
            "agent.project_health": self.agent_project_health,
            "agent.project_briefing": self.agent_project_briefing,
            "agent.component_catalog": self.agent_component_catalog,
            "agent.component_update_plan": self.agent_component_update_plan,
            "agent.resilience_status": self.agent_resilience_status,
            "agent.resilience_repair": self.agent_resilience_repair,
            "agent.update": self.agent_update,
            "agent.self_test": self.agent_self_test,
            "artifacts.list": self.artifacts_list,
            "artifact.preview": self.artifact_preview,
            "artifact.read_chunk": self.artifact_read_chunk,
            "git.repository_info": self.git_repository_info,
            "git.quick_status": self.git_quick_status,
            "git.status": self.git_status,
            "git.diff": self.git_diff,
            "git.sync": self.git_sync,
            "unity.editor_status": self.unity_editor_status,
            "unity.editor_diagnostics": self.unity_editor_diagnostics,
            "unity.editor_start": self.unity_editor_start,
            "unity.editor_terminate_stuck": self.unity_editor_terminate_stuck,
            "unity.installations": self.unity_installations,
            "unity.hub_install_editor": self.unity_hub_install_editor,
            "unity.direct_install_editor": self.unity_direct_install_editor,
            "unity.recover_resume": self.unity_recover_resume,
            "unity.refresh_editor": self.unity_refresh_editor,
            "unity.play_start": self.unity_play_start,
            "unity.play_stop": self.unity_play_stop,
            "unity.stop_play": self.unity_play_stop,
            "unity.compile": self.unity_compile,
            "unity.validate": self.unity_validate,
            "unity.capture": self.unity_capture,
            "unity.run_method": self.unity_run_method,
            "blender.version": self.blender_version,
            "blender.run_python": self.blender_run_python,
        }
        self._adapter_contracts = builtin_adapter_contracts()
        available = {entry.name: entry for entry in entry_points(group="ordax_dev_agent.adapters")}
        for name in config.adapters:
            if not re.fullmatch(r"[a-z][a-z0-9_]*", name) or name in {"agent", "artifact", "git", "project", "projects", "workspace", "terminal", "process", "browser", "computer", "orchestrator", "observation", "unity", "blender", "game_assets", "geo", "visual", "memory", "continuity"}:
                raise ValueError(f"invalid or reserved adapter name: {name}")
            if name not in available:
                raise ValueError(f"configured adapter is not installed: {name}")
            handlers = available[name].load()(config)
            for operation, handler in handlers.items():
                if not re.fullmatch(r"[a-z][a-z0-9_]*", operation) or not callable(handler):
                    raise ValueError(f"invalid action in adapter {name}: {operation}")
                self._actions[f"{name}.{operation}"] = (
                    lambda payload, fn=handler: fn(self._project(payload), payload))
            self._adapter_contracts[name] = external_adapter_contract(name)

    @property
    def names(self) -> list[str]:
        return sorted(self._actions)

    @property
    def adapter_contracts(self) -> dict[str, Any]:
        return adapter_contract_catalog(self._adapter_contracts)

    def select_available_project(self, requested: str | None = None) -> str:
        """Resolve one usable project consistently across CLI, MCP and desktop clients."""
        if requested:
            if requested not in self.projects:
                raise ValueError(f"project not registered: {requested}")
            project = self.projects[requested]
            if not project.root.is_dir():
                raise FileNotFoundError(f"Project directory not found: {project.root}")
            return requested

        active = self._memory_store_instance().active_project()
        active_name = str(active.get("name") or "") if active else ""
        if active_name in self.projects and self.projects[active_name].root.is_dir():
            return active_name

        default = self.config.default_project
        if default in self.projects and self.projects[default].root.is_dir():
            return default

        for slug, project in self.projects.items():
            if project.root.is_dir():
                return slug
        raise ValueError("no registered project directories are available")

    def execute(self, action: str, payload: dict[str, Any]) -> ActionResult:
        handler = self._actions.get(action)
        if handler is None:
            return ActionResult(False, f"action not allowed: {action}")
        payload = payload or {}
        if action in _NONBLOCKING_OBSERVATION_ACTIONS:
            try:
                return handler(payload)
            except (ValueError, FileNotFoundError, OSError, subprocess.TimeoutExpired) as error:
                return ActionResult(False, f"{type(error).__name__}: {error}")
        if action in (
            "agent.status",
            "agent.component_catalog",
            "agent.component_update_plan",
            "projects.list",
            "workspace.repository_catalog",
            "project.preview_status",
            "git.quick_status",
        ):
            return handler(payload)
        if not self._execution_lock.acquire(blocking=False):
            return ActionResult(False, "Agent is busy; retry after the current action", {"retryable": True})
        process_lock = ExecutionLock(self.config.state_dir)
        try:
            if not process_lock.acquire():
                return ActionResult(False, "Another agent/MCP action is running", {"retryable": True})
            prefix = action.split(".", 1)[0]
            adapter = self._adapter_contracts.get(prefix)
            if adapter is not None and action not in adapter.global_actions:
                project = self._project(payload)
                if adapter.project_app not in project.apps:
                    raise ValueError(
                        f"application not enabled for project {project.slug}: "
                        f"{adapter.project_app}"
                    )
            result = handler(payload)
            return result
        except (ValueError, FileNotFoundError, OSError, subprocess.TimeoutExpired) as error:
            return ActionResult(False, f"{type(error).__name__}: {error}")
        finally:
            process_lock.release()
            self._execution_lock.release()

    def _project_path(self, payload: dict[str, Any]) -> Path:
        return self._project(payload).root

    def _project(self, payload: dict[str, Any]) -> Project:
        slug = payload.get("project") or self.config.default_project
        if slug not in self.projects:
            raise ValueError(f"project not registered: {slug}")
        project = self.projects[slug]
        if not project.root.is_dir():
            raise FileNotFoundError(f"Project directory not found: {project.root}")
        return project
