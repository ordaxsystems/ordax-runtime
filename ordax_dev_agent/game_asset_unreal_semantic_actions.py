"""Semantic Unreal asset audits after verified import/load gates."""
from __future__ import annotations

import json
import re
import uuid
from pathlib import Path
from typing import Any

from .game_asset_unreal_actions import _find_unreal_editor, _timeout
from .models import ActionResult
from .process_runner import run_command as _run


_ASSET_PATH_RE = re.compile(r"/Game(?:/[A-Za-z0-9_-]+)+(?:\.[A-Za-z0-9_-]+)?")
_CLASS_RE = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,63}")


def _asset_paths(value: Any) -> list[str]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list) or not value or len(value) > 64:
        raise ValueError("asset_paths must contain 1-64 Unreal /Game asset paths")
    result: list[str] = []
    for item in value:
        if not isinstance(item, str):
            raise ValueError("asset_paths must contain only strings")
        path = item.strip()
        if not _ASSET_PATH_RE.fullmatch(path):
            raise ValueError("asset_paths must contain normalized /Game/... asset paths")
        if path in result:
            continue
        result.append(path)
    return result


def _expected_classes(value: Any) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > 16:
        raise ValueError("expected_classes must contain at most 16 Unreal class names")
    result: list[str] = []
    for item in value:
        if not isinstance(item, str) or not _CLASS_RE.fullmatch(item.strip()):
            raise ValueError("expected_classes contains an invalid Unreal class name")
        name = item.strip()
        if name not in result:
            result.append(name)
    return result


def _bounded_int(value: Any, field: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 1 or value > 64:
        raise ValueError(f"{field} must be an integer between 1 and 64")
    return value


def _script(asset_paths: list[str]) -> str:
    payload = json.dumps({"asset_paths": asset_paths})
    return f'''import json
import unreal

cfg = json.loads({payload!r})


def prop(obj, name, default=None):
    try:
        return obj.get_editor_property(name)
    except Exception:
        return default


def obj_path(obj):
    if obj is None:
        return None
    try:
        return obj.get_path_name()
    except Exception:
        return str(obj)


def safe_len(value):
    try:
        return len(value)
    except Exception:
        return 0


def static_collision(mesh):
    setup = prop(mesh, "body_setup")
    if setup is None:
        return {{"body_setup": False, "simple_shapes": 0, "trace_flag": None}}
    agg = prop(setup, "agg_geom")
    shape_count = 0
    if agg is not None:
        for name in ("box_elems", "sphere_elems", "sphyl_elems", "convex_elems", "tapered_capsule_elems"):
            shape_count += safe_len(prop(agg, name, []))
    return {{
        "body_setup": True,
        "simple_shapes": int(shape_count),
        "trace_flag": str(prop(setup, "collision_trace_flag")),
    }}


def audit(obj, requested_path):
    class_name = obj.get_class().get_name()
    item = {{
        "requested_path": requested_path,
        "object_path": obj_path(obj),
        "class": class_name,
    }}
    if isinstance(obj, unreal.StaticMesh):
        lod_count = int(obj.get_num_lods())
        lods = []
        for index in range(lod_count):
            lods.append({{
                "index": index,
                "triangles": int(obj.get_num_triangles(index)),
                "vertices": int(obj.get_num_vertices(index)),
                "sections": int(obj.get_num_sections(index)),
                "texcoords": int(obj.get_num_tex_coords(index)),
            }})
        item.update({{
            "kind": "static_mesh",
            "lod_count": lod_count,
            "lods": lods,
            "material_count": safe_len(prop(obj, "static_materials", [])),
            "collision": static_collision(obj),
        }})
    elif isinstance(obj, unreal.SkeletalMesh):
        skeleton = prop(obj, "skeleton")
        physics_asset = prop(obj, "physics_asset")
        try:
            lod_count = int(unreal.SkeletalMeshEditorSubsystem.get_lod_count(obj))
        except Exception:
            lod_count = safe_len(prop(obj, "source_models", []))
        bone_count = safe_len(prop(skeleton, "bone_tree", [])) if skeleton is not None else 0
        try:
            morph_count = len(list(obj.get_all_morph_target_names()))
        except Exception:
            morph_count = safe_len(prop(obj, "morph_targets", []))
        item.update({{
            "kind": "skeletal_mesh",
            "lod_count": int(lod_count),
            "material_count": safe_len(prop(obj, "materials", [])),
            "skeleton_path": obj_path(skeleton),
            "bone_count": int(bone_count),
            "morph_target_count": int(morph_count),
            "physics_asset_path": obj_path(physics_asset),
        }})
    elif isinstance(obj, unreal.AnimationAsset):
        try:
            skeleton = obj.get_skeleton()
        except Exception:
            skeleton = prop(obj, "skeleton")
        try:
            play_length = float(obj.get_play_length())
        except Exception:
            play_length = float(prop(obj, "sequence_length", 0.0) or 0.0)
        item.update({{
            "kind": "animation",
            "skeleton_path": obj_path(skeleton),
            "play_length_seconds": play_length,
        }})
    elif isinstance(obj, unreal.Skeleton):
        item.update({{
            "kind": "skeleton",
            "bone_count": safe_len(prop(obj, "bone_tree", [])),
        }})
    else:
        item["kind"] = "other"
    return item


records = []
for path in cfg["asset_paths"]:
    obj = unreal.EditorAssetLibrary.load_asset(path)
    if obj is None:
        raise RuntimeError("ORDAX Unreal failed to load asset for semantic audit: " + path)
    records.append(audit(obj, path))
print("ORDAX_UNREAL_SEMANTIC_OK|" + json.dumps(records, separators=(",", ":")))
'''


def _proof(stdout: str, requested: list[str]) -> list[dict[str, Any]] | None:
    prefix = "ORDAX_UNREAL_SEMANTIC_OK|"
    for line in stdout.splitlines():
        if not line.startswith(prefix):
            continue
        try:
            payload = json.loads(line[len(prefix) :])
        except json.JSONDecodeError:
            return None
        if not isinstance(payload, list) or len(payload) != len(requested):
            return None
        by_requested: dict[str, dict[str, Any]] = {}
        for item in payload:
            if not isinstance(item, dict):
                return None
            path = item.get("requested_path")
            class_name = item.get("class")
            kind = item.get("kind")
            if path not in requested or not isinstance(class_name, str) or not class_name:
                return None
            if kind not in {"static_mesh", "skeletal_mesh", "animation", "skeleton", "other"}:
                return None
            by_requested[str(path)] = item
        if set(by_requested) != set(requested):
            return None
        return [by_requested[path] for path in requested]
    return None


def _requirement_failures(
    records: list[dict[str, Any]],
    *,
    expected_classes: list[str],
    min_static_lods: int | None,
    min_skeletal_lods: int | None,
    require_skeletal_skeleton: bool,
    require_skeletal_physics_asset: bool,
    require_animation_skeleton: bool,
    require_static_collision: bool,
) -> list[str]:
    failures: list[str] = []
    observed_classes = {str(item.get("class")) for item in records}
    missing_classes = [name for name in expected_classes if name not in observed_classes]
    if missing_classes:
        failures.append("missing expected Unreal classes: " + ", ".join(missing_classes))
    for item in records:
        path = str(item.get("requested_path"))
        kind = item.get("kind")
        if kind == "static_mesh":
            if min_static_lods is not None and int(item.get("lod_count") or 0) < min_static_lods:
                failures.append(f"{path} has fewer than {min_static_lods} static mesh LODs")
            if require_static_collision:
                collision = item.get("collision")
                if not isinstance(collision, dict) or int(collision.get("simple_shapes") or 0) < 1:
                    failures.append(f"{path} has no simple static-mesh collision shapes")
        elif kind == "skeletal_mesh":
            if min_skeletal_lods is not None and int(item.get("lod_count") or 0) < min_skeletal_lods:
                failures.append(f"{path} has fewer than {min_skeletal_lods} skeletal mesh LODs")
            if require_skeletal_skeleton and not item.get("skeleton_path"):
                failures.append(f"{path} has no assigned skeleton")
            if require_skeletal_physics_asset and not item.get("physics_asset_path"):
                failures.append(f"{path} has no assigned physics asset")
        elif kind == "animation" and require_animation_skeleton and not item.get("skeleton_path"):
            failures.append(f"{path} animation has no assigned skeleton")
    return failures


class GameAssetUnrealSemanticActions:
    """Audit semantic runtime properties of already-imported Unreal assets."""

    def game_assets_unreal_asset_audit(self, payload: dict[str, Any]) -> ActionResult:
        supported = {
            "project",
            "uproject_path",
            "asset_paths",
            "expected_classes",
            "min_static_mesh_lods",
            "min_skeletal_mesh_lods",
            "require_skeletal_skeleton",
            "require_skeletal_physics_asset",
            "require_animation_skeleton",
            "require_static_collision",
            "timeout_seconds",
        }
        unsupported = set(payload) - supported
        if unsupported:
            return ActionResult(False, f"unsupported fields: {', '.join(sorted(unsupported))}")

        project = self._project(payload)
        script_path: Path | None = None
        try:
            uproject_raw = payload.get("uproject_path")
            if not isinstance(uproject_raw, str) or not uproject_raw.strip():
                raise ValueError("uproject_path is required")
            uproject = project.path(uproject_raw.strip())
            if uproject.suffix.lower() != ".uproject" or not uproject.is_file():
                raise ValueError("uproject_path must point to a project-local .uproject file")
            requested = _asset_paths(payload.get("asset_paths"))
            expected = _expected_classes(payload.get("expected_classes"))
            min_static = _bounded_int(payload.get("min_static_mesh_lods"), "min_static_mesh_lods")
            min_skeletal = _bounded_int(payload.get("min_skeletal_mesh_lods"), "min_skeletal_mesh_lods")
            timeout = _timeout(payload.get("timeout_seconds"))
            editor = _find_unreal_editor()

            self.config.state_dir.mkdir(parents=True, exist_ok=True)
            script_path = self.config.state_dir / f"unreal-semantic-audit-{uuid.uuid4().hex}.py"
            script_path.write_text(_script(requested), encoding="utf-8")
            command = [
                editor,
                str(uproject.resolve()),
                "-unattended",
                "-nop4",
                "-nosplash",
                "-nullrhi",
                "-run=pythonscript",
                f"-script={script_path.resolve()}",
            ]
            result = _run(command, cwd=uproject.parent, timeout=timeout)
            if not result.ok:
                return ActionResult(
                    False,
                    "Unreal commandlet failed during semantic asset audit",
                    {
                        "uproject_path": str(uproject),
                        "asset_paths": requested,
                        "command": result.data,
                    },
                )
            records = _proof(str(result.data.get("stdout") or ""), requested)
            if records is None:
                return ActionResult(
                    False,
                    "Unreal process exited successfully without OrdaX semantic audit proof",
                    {"uproject_path": str(uproject), "asset_paths": requested},
                )
            failures = _requirement_failures(
                records,
                expected_classes=expected,
                min_static_lods=min_static,
                min_skeletal_lods=min_skeletal,
                require_skeletal_skeleton=bool(payload.get("require_skeletal_skeleton", False)),
                require_skeletal_physics_asset=bool(payload.get("require_skeletal_physics_asset", False)),
                require_animation_skeleton=bool(payload.get("require_animation_skeleton", False)),
                require_static_collision=bool(payload.get("require_static_collision", False)),
            )
        except (ValueError, OSError, FileNotFoundError) as error:
            return ActionResult(False, str(error))
        finally:
            if script_path is not None:
                script_path.unlink(missing_ok=True)

        data = {
            "engine": "unreal",
            "engine_loaded": True,
            "semantic_requirements_passed": not failures,
            "uproject_path": str(uproject),
            "assets": records,
            "requirements": {
                "expected_classes": expected,
                "min_static_mesh_lods": min_static,
                "min_skeletal_mesh_lods": min_skeletal,
                "require_skeletal_skeleton": bool(payload.get("require_skeletal_skeleton", False)),
                "require_skeletal_physics_asset": bool(payload.get("require_skeletal_physics_asset", False)),
                "require_animation_skeleton": bool(payload.get("require_animation_skeleton", False)),
                "require_static_collision": bool(payload.get("require_static_collision", False)),
            },
            "failures": failures,
            "unattended": True,
            "null_rhi": True,
        }
        if failures:
            return ActionResult(False, "Unreal assets loaded but semantic requirements failed", data)
        return ActionResult(True, "Unreal semantic asset audit passed", data)
