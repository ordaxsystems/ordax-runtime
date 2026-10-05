"""Component identities and update planning for the OrdaX Device Agent.

Development delivery remains Git-first.  This module separates component
identity/failure domains now so production can later activate signed component
slots without redefining the runtime model.
"""
from __future__ import annotations

from pathlib import PurePosixPath
from typing import Any, Iterable

from . import __version__ as device_agent_version
from mcp_blender_unity import __version__ as bridge_package_version


COMPONENT_CATALOG_SCHEMA = "ordax.device-agent-components/1"
UPDATE_PLAN_SCHEMA = "ordax.device-agent-update-plan/1"

_COMPONENTS: tuple[dict[str, Any], ...] = (
    {
        "id": "device-agent-core",
        "version": device_agent_version,
        "kind": "runtime",
        "failure_domain": "device-agent",
        "development_delivery": "git-main",
        "production_delivery_target": "signed-component-slot",
        "restart_policy": "device-agent",
        "independent_activation_ready": False,
        "paths": (
            "ordax_dev_agent/actions.py",
            "ordax_dev_agent/agent_actions.py",
            "ordax_dev_agent/config.py",
            "ordax_dev_agent/main.py",
            "ordax_dev_agent/models.py",
            "ordax_dev_agent/process_runner.py",
            "ordax_dev_agent/projects.py",
            "ordax_dev_agent/execution_lock.py",
            "ordax_dev_agent/component_actions.py",
            "ordax_dev_agent/component_updates.py",
            "ordax_device_agent/",
        ),
    },
    {
        "id": "device-mcp",
        "version": "0.1.0",
        "kind": "interface",
        "failure_domain": "mcp-interface",
        "development_delivery": "git-main",
        "production_delivery_target": "signed-component-slot",
        "restart_policy": "mcp-client-session",
        "independent_activation_ready": False,
        "paths": (
            "ordax_dev_agent/mcp_server.py",
            "ordax_dev_agent/product_gateway.py",
            "ordax_device_agent/mcp_server.py",
        ),
    },
    {
        "id": "adapter-blender",
        "version": "0.1.0",
        "kind": "adapter",
        "failure_domain": "blender",
        "development_delivery": "git-main",
        "production_delivery_target": "signed-component-slot",
        "restart_policy": "adapter-or-device-agent",
        "independent_activation_ready": False,
        "paths": (
            "ordax_dev_agent/blender_",
            "scripts/blender_",
        ),
    },
    {
        "id": "adapter-game-assets",
        "version": "0.1.0",
        "kind": "adapter",
        "failure_domain": "game-asset-generation",
        "development_delivery": "git-main + provider-apis + local-workflows",
        "production_delivery_target": "signed-component-slot",
        "restart_policy": "adapter-or-device-agent",
        "independent_activation_ready": False,
        "paths": (
            "ordax_dev_agent/game_asset_",
            "ordax_dev_agent/generated_asset_",
            "ordax_dev_agent/mixamo_",
            "ordax_dev_agent/rodin_",
            "ordax_dev_agent/comfyui_",
            "ordax_dev_agent/visual_environment.py",
            "ordax_dev_agent/visual_environment_actions.py",
            "ordax_dev_agent/assets/OrdaXGameAssetAgent.cs",
            "ordax_dev_agent/assets/OrdaXGameAssetAnimationAgent.cs",
            "ordax_dev_agent/assets/blender_game_asset_pipeline.py",
            "ordax_dev_agent/assets/blender_fbx_ingest.py",
            "ordax_dev_agent/assets/blender_generated_asset_ingest.py",
            "ordax_dev_agent/assets/blender_runtime_budget.py",
            "ordax_dev_agent/assets/blender_static_lod.py",
        ),
    },
    {
        "id": "adapter-unity",
        "version": "0.1.0",
        "kind": "adapter",
        "failure_domain": "unity",
        "development_delivery": "git-main",
        "production_delivery_target": "signed-component-slot",
        "restart_policy": "adapter-or-device-agent",
        "independent_activation_ready": False,
        "paths": (
            "ordax_dev_agent/unity_",
            "ordax_dev_agent/assets/OrdaXGenericAgent.cs",
            "scripts/windows/unity-",
        ),
    },
    {
        "id": "adapter-alephgeo",
        "version": "0.1.0",
        "kind": "external-adapter",
        "failure_domain": "geospatial-capture",
        "development_delivery": "git-main + managed-upstream-pin",
        "production_delivery_target": "signed-component-slot + pinned-upstream-cache",
        "restart_policy": "adapter-or-device-agent",
        "independent_activation_ready": False,
        "upstream": "Belluxx/Aleph",
        "upstream_package_version": "0.1.0",
        "upstream_pinned_commit": "502667d0b46e67555c7956d4ff281be5e8511a30",
        "notes": "Runtime is isolated in the Device Agent state directory; upstream uses undocumented remote APIs.",
        "paths": (
            "ordax_dev_agent/aleph_",
            "ordax_dev_agent/assets/blender_aleph_",
        ),
    },
    {
        "id": "adapter-git",
        "version": "0.1.0",
        "kind": "adapter",
        "failure_domain": "git",
        "development_delivery": "git-main",
        "production_delivery_target": "signed-component-slot",
        "restart_policy": "device-agent",
        "independent_activation_ready": False,
        "paths": ("ordax_dev_agent/git_actions.py",),
    },
    {
        "id": "bridge-blender-unity-cli",
        "version": bridge_package_version,
        "kind": "bridge",
        "failure_domain": "cli-bridge",
        "development_delivery": "git-main",
        "production_delivery_target": "signed-component-slot",
        "restart_policy": "next-invocation",
        "independent_activation_ready": False,
        "paths": ("mcp_blender_unity/",),
    },
)

_GLOBAL_INSTALL_PATHS = ("pyproject.toml",)
_RUNTIME_SHARED_PATHS = (
    "ordax_dev_agent/action_contracts.py",
    "ordax_dev_agent/adapter_contracts.py",
    "ordax_dev_agent/capability_contracts.py",
    "ordax_dev_agent/versioning.py",
)


def _public_component(component: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in component.items() if key != "paths"}


def component_catalog() -> dict[str, Any]:
    return {
        "schema": COMPONENT_CATALOG_SCHEMA,
        "delivery_policy": {
            "development": "git-main",
            "production_target": "signed-component-slot",
            "whole_os_reinstall_required_for_component_update": False,
            "production_independent_activation_ready": False,
            "health_promotion_rollback_required_before_independent_activation": True,
        },
        "components": [_public_component(component) for component in _COMPONENTS],
    }


def _normalize_path(value: str) -> str:
    path = str(value).replace("\\", "/").strip()
    if not path or "\0" in path:
        raise ValueError("changed path must be a non-empty relative path")
    pure = PurePosixPath(path)
    if pure.is_absolute() or ".." in pure.parts:
        raise ValueError("changed path must stay inside the managed repository")
    return pure.as_posix()


def _matches(path: str, pattern: str) -> bool:
    if pattern.endswith("/"):
        return path.startswith(pattern)
    if pattern.endswith(("_", "-")):
        return path.startswith(pattern)
    return path == pattern


def plan_component_update(
    changed_paths: Iterable[str],
    *,
    install_contract_changed: bool = False,
) -> dict[str, Any]:
    normalized = sorted({_normalize_path(path) for path in changed_paths})
    affected: set[str] = set()

    for path in normalized:
        for component in _COMPONENTS:
            if any(_matches(path, pattern) for pattern in component["paths"]):
                affected.add(component["id"])
        if any(_matches(path, pattern) for pattern in _RUNTIME_SHARED_PATHS):
            affected.add("device-agent-core")

    install_refresh = bool(install_contract_changed) or any(
        _matches(path, pattern)
        for path in normalized
        for pattern in _GLOBAL_INSTALL_PATHS
    )
    if install_refresh:
        affected.update(component["id"] for component in _COMPONENTS)

    component_by_id = {component["id"]: component for component in _COMPONENTS}
    restart_policies = sorted({
        component_by_id[component_id]["restart_policy"]
        for component_id in affected
    })
    runtime_restart = any(
        policy in {"device-agent", "adapter-or-device-agent"}
        for policy in restart_policies
    )

    return {
        "schema": UPDATE_PLAN_SCHEMA,
        "changed_paths": normalized,
        "affected_components": sorted(affected),
        "install_refresh_required": install_refresh,
        "device_agent_restart_required": runtime_restart,
        "whole_os_reinstall_required": False,
        "whole_os_reboot_required": False,
        "restart_policies": restart_policies,
        "unknown_paths": [
            path for path in normalized
            if not any(
                _matches(path, pattern)
                for component in _COMPONENTS
                for pattern in component["paths"]
            )
            and not any(_matches(path, pattern) for pattern in _RUNTIME_SHARED_PATHS)
            and not any(_matches(path, pattern) for pattern in _GLOBAL_INSTALL_PATHS)
        ],
    }
