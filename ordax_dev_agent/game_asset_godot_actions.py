"""Verified Godot import/runtime-load validation for OrdaX engine exports."""
from __future__ import annotations

import json
import os
import shutil
import uuid
from pathlib import Path
from typing import Any

from .game_asset_engine_export_actions import verify_engine_export
from .models import ActionResult
from .process_runner import run_command as _run


_SUPPORTED_GODOT_MODELS = frozenset({".glb", ".gltf"})
_DEFAULT_TIMEOUT = 300


def _timeout(value: Any) -> int:
    if value is None:
        return _DEFAULT_TIMEOUT
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("timeout_seconds must be an integer")
    if value < 10 or value > 1200:
        raise ValueError("timeout_seconds must be between 10 and 1200")
    return value


def _minimum(value: Any, field: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 1 or value > 1000000:
        raise ValueError(f"{field} must be an integer between 1 and 1000000")
    return value


def _find_godot() -> str:
    configured = os.environ.get("ORDAX_GODOT_BIN")
    if configured:
        path = Path(configured).expanduser()
        if not path.is_file():
            raise FileNotFoundError("ORDAX_GODOT_BIN does not point to a file")
        return str(path)
    for name in ("godot", "godot4"):
        resolved = shutil.which(name)
        if resolved:
            return resolved
    raise FileNotFoundError(
        "Godot editor binary not found; configure ORDAX_GODOT_BIN or place godot/godot4 in PATH"
    )


def _under(child: Path, parent: Path, field: str) -> Path:
    child = child.resolve()
    parent = parent.resolve()
    try:
        child.relative_to(parent)
    except ValueError as error:
        raise ValueError(f"{field} must stay inside the Godot project directory") from error
    return child


def _atomic_copy(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temp = destination.with_name(f".{destination.name}.ordax-{uuid.uuid4().hex}.tmp")
    temp.unlink(missing_ok=True)
    try:
        shutil.copy2(source, temp)
        temp.replace(destination)
    finally:
        temp.unlink(missing_ok=True)


def _validator_script() -> str:
    return """extends SceneTree

func _walk(node: Node, stats: Dictionary, material_ids: Dictionary):
    stats["nodes"] += 1
    if node is MeshInstance3D:
        stats["mesh_instances"] += 1
        var mesh = node.mesh
        if mesh != null:
            var surfaces = mesh.get_surface_count()
            stats["mesh_surfaces"] += surfaces
            if mesh.has_method("get_blend_shape_count"):
                stats["blend_shapes"] += mesh.get_blend_shape_count()
            for surface in range(surfaces):
                var material = mesh.surface_get_material(surface)
                if material != null:
                    material_ids[material.get_instance_id()] = true
    if node is Skeleton3D:
        stats["skeletons"] += 1
        stats["bones"] += node.get_bone_count()
    if node is AnimationPlayer:
        stats["animation_players"] += 1
        stats["animations"] += node.get_animation_list().size()
    if node is CollisionObject3D:
        stats["collision_objects"] += 1
    if node is CollisionShape3D and node.shape != null:
        stats["collision_shapes"] += 1
    for child in node.get_children():
        _walk(child, stats, material_ids)

func _init():
    var args = OS.get_cmdline_user_args()
    if args.size() != 1:
        push_error("ORDAX expected one resource path")
        quit(2)
        return
    var resource_path = args[0]
    if not ResourceLoader.exists(resource_path):
        push_error("ORDAX resource does not exist: " + resource_path)
        quit(3)
        return
    var resource = ResourceLoader.load(resource_path)
    if resource == null:
        push_error("ORDAX ResourceLoader failed: " + resource_path)
        quit(4)
        return
    print("ORDAX_GODOT_RESOURCE_OK|" + resource_path + "|" + resource.get_class())
    var stats = {
        "packed_scene": resource is PackedScene,
        "root_class": null,
        "nodes": 0,
        "mesh_instances": 0,
        "mesh_surfaces": 0,
        "materials": 0,
        "blend_shapes": 0,
        "skeletons": 0,
        "bones": 0,
        "animation_players": 0,
        "animations": 0,
        "collision_objects": 0,
        "collision_shapes": 0,
    }
    if resource is PackedScene:
        if not resource.can_instantiate():
            push_error("ORDAX PackedScene cannot instantiate: " + resource_path)
            quit(5)
            return
        var root = resource.instantiate()
        if root == null:
            push_error("ORDAX PackedScene instantiate returned null: " + resource_path)
            quit(6)
            return
        stats["root_class"] = root.get_class()
        var material_ids = {}
        _walk(root, stats, material_ids)
        stats["materials"] = material_ids.size()
        root.free()
    print("ORDAX_GODOT_SEMANTIC_OK|" + JSON.stringify(stats))
    quit(0)
"""


def _marker(stdout: str, expected_resource: str) -> dict[str, str] | None:
    prefix = "ORDAX_GODOT_RESOURCE_OK|"
    for line in stdout.splitlines():
        if not line.startswith(prefix):
            continue
        parts = line.split("|", 2)
        if len(parts) == 3 and parts[1] == expected_resource and parts[2]:
            return {"resource_path": parts[1], "resource_class": parts[2]}
    return None


def _semantic_marker(stdout: str) -> dict[str, Any] | None:
    prefix = "ORDAX_GODOT_SEMANTIC_OK|"
    for line in stdout.splitlines():
        if not line.startswith(prefix):
            continue
        try:
            data = json.loads(line[len(prefix) :])
        except json.JSONDecodeError:
            return None
        if not isinstance(data, dict):
            return None
        required = {
            "packed_scene",
            "root_class",
            "nodes",
            "mesh_instances",
            "mesh_surfaces",
            "materials",
            "blend_shapes",
            "skeletons",
            "bones",
            "animation_players",
            "animations",
            "collision_objects",
            "collision_shapes",
        }
        if not required.issubset(data):
            return None
        numeric = required - {"packed_scene", "root_class"}
        if any(isinstance(data[key], bool) or not isinstance(data[key], int) or data[key] < 0 for key in numeric):
            return None
        if not isinstance(data["packed_scene"], bool):
            return None
        return data
    return None


def _semantic_failures(
    semantic: dict[str, Any],
    *,
    min_mesh_instances: int | None,
    min_skeletons: int | None,
    min_bones: int | None,
    min_animations: int | None,
    require_collision: bool,
) -> list[str]:
    failures: list[str] = []
    if min_mesh_instances is not None and semantic["mesh_instances"] < min_mesh_instances:
        failures.append(f"Godot scene has fewer than {min_mesh_instances} MeshInstance3D nodes")
    if min_skeletons is not None and semantic["skeletons"] < min_skeletons:
        failures.append(f"Godot scene has fewer than {min_skeletons} Skeleton3D nodes")
    if min_bones is not None and semantic["bones"] < min_bones:
        failures.append(f"Godot scene has fewer than {min_bones} total skeleton bones")
    if min_animations is not None and semantic["animations"] < min_animations:
        failures.append(f"Godot scene has fewer than {min_animations} animations")
    if require_collision and semantic["collision_shapes"] < 1:
        failures.append("Godot scene has no CollisionShape3D with an assigned shape")
    return failures


class GameAssetGodotActions:
    """Copy a verified Godot derivative into a project and prove Godot can load it."""

    def game_assets_godot_import_validate(self, payload: dict[str, Any]) -> ActionResult:
        supported = {
            "project",
            "artifact_path",
            "manifest_path",
            "godot_project_dir",
            "destination_path",
            "overwrite",
            "timeout_seconds",
            "min_mesh_instances",
            "min_skeletons",
            "min_bones",
            "min_animations",
            "require_collision",
        }
        unsupported = set(payload) - supported
        if unsupported:
            return ActionResult(False, f"unsupported fields: {', '.join(sorted(unsupported))}")

        project = self._project(payload)
        validator_path: Path | None = None
        try:
            artifact = project.path(str(payload.get("artifact_path") or ""))
            if artifact.suffix.lower() not in _SUPPORTED_GODOT_MODELS:
                raise ValueError("Godot validation requires a GLB or glTF artifact")
            raw_manifest = payload.get("manifest_path")
            manifest = (
                project.path(str(raw_manifest))
                if raw_manifest is not None
                else artifact.with_name(artifact.name + ".ordax.json")
            )
            verification = verify_engine_export(project, artifact, manifest)
            if str(verification.get("engine") or "").strip().lower() != "godot":
                raise ValueError("engine export provenance is not targeted to Godot")

            godot_dir_raw = payload.get("godot_project_dir")
            if not isinstance(godot_dir_raw, str) or not godot_dir_raw.strip():
                raise ValueError("godot_project_dir is required")
            godot_root = project.path(godot_dir_raw.strip())
            if not godot_root.is_dir() or not (godot_root / "project.godot").is_file():
                raise ValueError("godot_project_dir must contain project.godot")

            destination_raw = payload.get("destination_path")
            if destination_raw is None:
                destination = godot_root / "ordax_generated" / artifact.name
            else:
                if not isinstance(destination_raw, str) or not destination_raw.strip():
                    raise ValueError("destination_path must be a non-empty project-relative path")
                destination = project.path(destination_raw.strip(), must_exist=False)
            destination = _under(destination, godot_root, "destination_path")
            if destination.suffix.lower() != artifact.suffix.lower():
                raise ValueError("destination_path extension must match the verified artifact")
            destination_manifest = destination.with_name(destination.name + ".ordax.json")
            overwrite = bool(payload.get("overwrite", False))
            if (destination.exists() or destination_manifest.exists()) and not overwrite:
                raise ValueError("Godot destination already exists; set overwrite=true explicitly")

            min_mesh_instances = _minimum(payload.get("min_mesh_instances"), "min_mesh_instances")
            min_skeletons = _minimum(payload.get("min_skeletons"), "min_skeletons")
            min_bones = _minimum(payload.get("min_bones"), "min_bones")
            min_animations = _minimum(payload.get("min_animations"), "min_animations")
            require_collision = bool(payload.get("require_collision", False))

            godot = _find_godot()
            timeout = _timeout(payload.get("timeout_seconds"))
            _atomic_copy(artifact, destination)
            _atomic_copy(manifest, destination_manifest)
            resource_path = "res://" + destination.relative_to(godot_root).as_posix()

            imported = _run(
                [
                    godot,
                    "--headless",
                    "--recovery-mode",
                    "--path",
                    str(godot_root),
                    "--import",
                ],
                cwd=godot_root,
                timeout=timeout,
            )
            if not imported.ok:
                return ActionResult(
                    False,
                    "verified asset was copied but Godot headless import failed",
                    {
                        "artifact_path": str(artifact),
                        "destination_path": str(destination),
                        "resource_path": resource_path,
                        "source_preserved": True,
                        "copy_retained_for_diagnostics": True,
                        "import": imported.data,
                    },
                )

            state_dir = self.config.state_dir
            state_dir.mkdir(parents=True, exist_ok=True)
            validator_path = state_dir / f"godot-resource-check-{uuid.uuid4().hex}.gd"
            validator_path.write_text(_validator_script(), encoding="utf-8")
            loaded = _run(
                [
                    godot,
                    "--headless",
                    "--recovery-mode",
                    "--path",
                    str(godot_root),
                    "--script",
                    str(validator_path),
                    "--",
                    resource_path,
                ],
                cwd=godot_root,
                timeout=timeout,
            )
            if not loaded.ok:
                return ActionResult(
                    False,
                    "Godot imported the project but ResourceLoader validation failed",
                    {
                        "artifact_path": str(artifact),
                        "destination_path": str(destination),
                        "resource_path": resource_path,
                        "source_preserved": True,
                        "copy_retained_for_diagnostics": True,
                        "load": loaded.data,
                    },
                )
            proof = _marker(str(loaded.data.get("stdout") or ""), resource_path)
            semantic = _semantic_marker(str(loaded.data.get("stdout") or ""))
            if proof is None:
                return ActionResult(
                    False,
                    "Godot process exited successfully without OrdaX ResourceLoader proof",
                    {
                        "destination_path": str(destination),
                        "resource_path": resource_path,
                        "source_preserved": True,
                    },
                )
            if semantic is None:
                return ActionResult(
                    False,
                    "Godot process exited successfully without OrdaX semantic scene proof",
                    {
                        "destination_path": str(destination),
                        "resource_path": resource_path,
                        "source_preserved": True,
                        "engine_loaded": True,
                    },
                )
            failures = _semantic_failures(
                semantic,
                min_mesh_instances=min_mesh_instances,
                min_skeletons=min_skeletons,
                min_bones=min_bones,
                min_animations=min_animations,
                require_collision=require_collision,
            )
        except (ValueError, OSError, FileNotFoundError) as error:
            return ActionResult(False, str(error))
        finally:
            if validator_path is not None:
                validator_path.unlink(missing_ok=True)

        data = {
            "engine": "godot",
            "engine_validated": True,
            "engine_loaded": True,
            "semantic_requirements_passed": not failures,
            "artifact_path": str(artifact),
            "destination_path": str(destination),
            "destination_manifest": str(destination_manifest),
            "resource_path": resource_path,
            "resource_class": proof["resource_class"],
            "semantic": semantic,
            "requirements": {
                "min_mesh_instances": min_mesh_instances,
                "min_skeletons": min_skeletons,
                "min_bones": min_bones,
                "min_animations": min_animations,
                "require_collision": require_collision,
            },
            "failures": failures,
            "sha256": verification["sha256"],
            "source_current_matches": verification["source_current_matches"],
            "source_preserved": True,
            "recovery_mode": True,
        }
        if failures:
            return ActionResult(False, "Godot imported and loaded the asset but semantic requirements failed", data)
        return ActionResult(True, "Godot imported, loaded and semantically audited the verified engine export", data)
