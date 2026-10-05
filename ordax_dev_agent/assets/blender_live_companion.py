# OrdaX Dev Agent - visible Blender companion.
# Runs inside an interactive Blender window and executes only typed commands
# emitted by the local allow-listed agent.
from __future__ import annotations

import argparse
import difflib
import hashlib
import importlib
import importlib.util
import json
import math
import os
import re
import runpy
import struct
import sys
import time
from pathlib import Path

import bpy
import bmesh
from mathutils import Euler, Matrix, Vector
from mathutils.bvhtree import BVHTree


def _args():
    argv = sys.argv
    if "--" in argv:
        argv = argv[argv.index("--") + 1 :]
    else:
        argv = []

    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--ordax-control-root", required=True)
    parser.add_argument("--ordax-project-root", required=True)
    parser.add_argument("--ordax-scripts-root", required=True)
    parser.add_argument("--ordax-artifacts-root", required=True)
    parser.add_argument("--ordax-project-slug", required=True)
    parser.add_argument("--ordax-smoke-output-dir")
    parser.add_argument(
        "--ordax-smoke-mode",
        choices=("material", "silhouette"),
        default="silhouette",
    )
    parser.add_argument("--ordax-smoke-width", type=int, default=320)
    parser.add_argument("--ordax-smoke-height", type=int, default=320)
    parser.add_argument("--ordax-smoke-quality-fixture", action="store_true")
    parser.add_argument("--ordax-smoke-modeling-fixture", action="store_true")
    return parser.parse_args(argv)


def _companion_bundle_fingerprint() -> str:
    root = Path(__file__).resolve().parent
    manifest_path = (root / "blender_companion_bundle.json").resolve()
    if not manifest_path.is_relative_to(root):
        raise RuntimeError("Blender companion bundle manifest escaped asset root")

    data = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    if not isinstance(data, dict) or data.get("version") != 1:
        raise RuntimeError("Blender companion bundle manifest version is invalid")
    raw_files = data.get("files")
    if (
        not isinstance(raw_files, list)
        or not raw_files
        or len(raw_files) > 32
        or not all(isinstance(name, str) and name.strip() for name in raw_files)
    ):
        raise RuntimeError("Blender companion bundle files are invalid")

    names = [name.strip().replace("\\", "/") for name in raw_files]
    if len(set(names)) != len(names):
        raise RuntimeError("Blender companion bundle files must be unique")

    digest = hashlib.sha256()
    digest.update(b"ordax-blender-companion-bundle\0")
    digest.update(b"1\0")
    for name in sorted(names):
        relative = Path(name)
        if relative.is_absolute() or ".." in relative.parts:
            raise RuntimeError(f"invalid Blender companion bundle path: {name}")
        path = (root / relative).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            raise RuntimeError(f"Blender companion bundle file missing: {name}")
        digest.update(name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


COMPANION_FINGERPRINT = _companion_bundle_fingerprint()


def _load_companion_asset_module(filename: str, module_name: str):
    path = (Path(__file__).resolve().parent / filename).resolve()
    if not path.is_relative_to(Path(__file__).resolve().parent):
        raise RuntimeError(f"companion helper escaped asset root: {filename}")
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load companion helper: {filename}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_UV_MATH = _load_companion_asset_module(
    "blender_uv_math.py",
    "_ordax_blender_uv_math",
)
_triangle_area_2d = _UV_MATH.triangle_area_2d
_signed_area_2d = _UV_MATH.signed_area_2d
_line_intersection_2d = _UV_MATH.line_intersection_2d
_triangle_overlap_area_2d = _UV_MATH.triangle_overlap_area_2d
_triangle_shape_distortion = _UV_MATH.triangle_shape_distortion

_SPATIAL_MATH = _load_companion_asset_module(
    "blender_spatial_math.py",
    "_ordax_blender_spatial_math",
)
_aabb_overlap = _SPATIAL_MATH.aabb_overlap
_aabb_contains = _SPATIAL_MATH.aabb_contains

_QUALITY_RULES = _load_companion_asset_module(
    "blender_quality_rules.py",
    "_ordax_blender_quality_rules",
)
_quality_axis = _QUALITY_RULES.quality_axis
_quality_tolerance = _QUALITY_RULES.quality_tolerance

_MODELING_CONTRACTS = _load_companion_asset_module(
    "blender_modeling_contracts.py",
    "_ordax_blender_modeling_contracts",
)
_normalize_transform_request = _MODELING_CONTRACTS.normalize_transform_request
_plan_modeling_operation = _MODELING_CONTRACTS.plan_modeling_operation
_evaluate_modifier_runtime_budget = _MODELING_CONTRACTS.evaluate_modifier_runtime_budget
_evaluate_surface_scatter_runtime_budget = _MODELING_CONTRACTS.evaluate_surface_scatter_runtime_budget
_evaluate_boolean_cut_runtime_budget = _MODELING_CONTRACTS.evaluate_boolean_cut_runtime_budget

_MATERIAL_CONTRACTS = _load_companion_asset_module(
    "blender_material_contracts.py",
    "_ordax_blender_material_contracts",
)
_normalize_material_request = _MATERIAL_CONTRACTS.normalize_material_request

CFG = _args()
CONTROL_ROOT = Path(CFG.ordax_control_root).resolve()
PROJECT_ROOT = Path(CFG.ordax_project_root).resolve()
SCRIPTS_ROOT = Path(CFG.ordax_scripts_root).resolve()
ARTIFACTS_ROOT = Path(CFG.ordax_artifacts_root).resolve()
CHECKPOINTS = ARTIFACTS_ROOT / "checkpoints"
TRAJECTORY = CONTROL_ROOT / "trajectory.jsonl"
INBOX = CONTROL_ROOT / "inbox"
RESPONSES = CONTROL_ROOT / "responses"
RESULTS = CONTROL_ROOT / "results"
INFLIGHT = CONTROL_ROOT / "inflight"
PRESENCE = CONTROL_ROOT / "presence.json"

PROTOCOL_VERSION = 9
CAPABILITIES = [
    "ping",
    "inspect",
    "scene_snapshot",
    "scene_reset",
    "object_inspect",
    "object_fingerprints",
    "contact_audit",
    "quality_gate",
    "object_transform",
    "object_remove",
    "extract_region",
    "cleanup_orphans",
    "create_primitive",
    "create_box_with_cutouts",
    "add_modifier",
    "surface_scatter",
    "boolean_cut_preview",
    "boolean_cut_commit",
    "boolean_cut_cancel",
    "mesh_cleanup",
    "degenerate_repair_preview",
    "degenerate_repair_commit",
    "degenerate_repair_cancel",
    "merge_by_distance_preview",
    "merge_by_distance_commit",
    "merge_by_distance_cancel",
    "boundary_hole_fill_preview",
    "boundary_hole_fill_commit",
    "boundary_hole_fill_cancel",
    "material_apply",
    "create_camera",
    "create_light",
    "scene_presentation",
    "import_asset",
    "animate_transform",
    "viewport_proxy",
    "bake_work_proxy",
    "object_metadata",
    "api_schema",
    "api_lookup",
    "node_schema",
    "export_scene",
    "checkpoint_create",
    "checkpoint_restore",
    "checkpoint_list",
    "run_script",
    "capture_viewport",
    "multiview_capture",
    "save",
    "quit",
]
_LAST_PRESENCE_AT = 0.0

for path in (CONTROL_ROOT, INBOX, RESPONSES, RESULTS, INFLIGHT, ARTIFACTS_ROOT, CHECKPOINTS):
    path.mkdir(parents=True, exist_ok=True)


def _inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _write_json_atomic(path: Path, data: dict) -> None:
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    temp.replace(path)


def _scene_snapshot() -> dict:
    try:
        selected = [obj.name for obj in bpy.context.selected_objects[:40]]
    except (ReferenceError, RuntimeError):
        selected = []

    active_name = None
    try:
        active = bpy.context.view_layer.objects.active
        if active is not None:
            active_name = active.name
    except (ReferenceError, RuntimeError):
        active_name = None

    scene = bpy.context.scene
    return {
        "project": CFG.ordax_project_slug,
        "pid": os.getpid(),
        "timestamp": time.time(),
        "blender_version": ".".join(str(v) for v in bpy.app.version),
        "file": bpy.data.filepath or "",
        "is_dirty": bool(getattr(bpy.data, "is_dirty", False)),
        "scene": scene.name if scene else "",
        "frame": int(scene.frame_current) if scene else 0,
        "mode": getattr(bpy.context, "mode", "UNKNOWN"),
        "objects": len(bpy.data.objects),
        "meshes": len(bpy.data.meshes),
        "materials": len(bpy.data.materials),
        "selected": selected,
        "active_object": active_name,
    }


def _write_presence(force: bool = False) -> None:
    global _LAST_PRESENCE_AT
    now = time.time()
    if not force and now - _LAST_PRESENCE_AT < 1.0:
        return
    _write_json_atomic(
        PRESENCE,
        {
            "ok": True,
            "summary": "OrdaX visible Blender companion ready",
            "protocol_version": PROTOCOL_VERSION,
            "companion_fingerprint": COMPANION_FINGERPRINT,
            "capabilities": CAPABILITIES,
            **_scene_snapshot(),
        },
    )
    _LAST_PRESENCE_AT = now


def _prune_results(limit: int = 200) -> None:
    try:
        entries = sorted(
            (p for p in RESULTS.glob("*.json") if p.is_file()),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        for stale in entries[limit:]:
            stale.unlink(missing_ok=True)
    except OSError:
        pass


def _response(command_id: str, ok: bool, summary: str, **data) -> None:
    snapshot = _scene_snapshot()
    # Heavy Blender operations pause the timer heartbeat. Refresh presence
    # synchronously before publishing the response so the next typed command
    # cannot observe a stale session between two successful operations.
    _write_presence(force=True)
    body = {
        "id": command_id,
        "ok": ok,
        "summary": summary,
        **data,
        "snapshot": snapshot,
        "completed_at": time.time(),
    }
    # Persist first. The response inbox is ephemeral; results survive timeouts,
    # agent restarts and Blender closing so callers can query before retrying.
    _write_json_atomic(RESULTS / f"{command_id}.json", body)
    _write_json_atomic(RESPONSES / f"{command_id}.json", body)
    _prune_results()


def _ordax_properties(owner) -> dict:
    data = {}
    try:
        keys = owner.keys()
    except Exception:
        return data
    for key in keys:
        if not str(key).startswith("ordax_"):
            continue
        try:
            value = owner.get(key)
            if value is None or isinstance(value, (str, int, float, bool)):
                data[str(key)] = value
            elif isinstance(value, (list, tuple)):
                data[str(key)] = list(value)[:50]
            else:
                data[str(key)] = str(value)
        except Exception:
            continue
    return data


def _round_vector(values, digits: int = 6) -> list[float]:
    return [round(float(value), digits) for value in values]


def _world_bounds(obj) -> dict:
    try:
        corners = [obj.matrix_world @ Vector(corner) for corner in obj.bound_box]
        xs = [corner.x for corner in corners]
        ys = [corner.y for corner in corners]
        zs = [corner.z for corner in corners]
        return {
            "aabb_min": [round(min(xs), 6), round(min(ys), 6), round(min(zs), 6)],
            "aabb_max": [round(max(xs), 6), round(max(ys), 6), round(max(zs), 6)],
            "dimensions": _round_vector(obj.dimensions),
        }
    except Exception:
        return {}


def _object_details(obj) -> dict:
    world = obj.matrix_world
    details = {
        "name": obj.name,
        "type": obj.type,
        "visible": bool(obj.visible_get()),
        "selected": bool(obj.select_get()),
        "location": _round_vector(obj.location),
        "rotation_euler": _round_vector(obj.rotation_euler),
        "scale": _round_vector(obj.scale),
        "world_location": _round_vector(world.translation),
        "world_rotation_euler": _round_vector(world.to_euler()),
        "parent": obj.parent.name if obj.parent else None,
        "children": [child.name for child in list(obj.children)[:100]],
        "collections": [collection.name for collection in list(obj.users_collection)[:50]],
        "ordax": _ordax_properties(obj),
        **_world_bounds(obj),
    }

    material_names = []
    for slot in list(getattr(obj, "material_slots", []))[:50]:
        material = getattr(slot, "material", None)
        material_names.append(material.name if material else None)
    if material_names:
        details["materials"] = material_names

    modifiers = []
    for modifier in list(getattr(obj, "modifiers", []))[:50]:
        modifiers.append(
            {
                "name": modifier.name,
                "type": modifier.type,
                "show_viewport": bool(getattr(modifier, "show_viewport", True)),
                "show_render": bool(getattr(modifier, "show_render", True)),
            }
        )
    if modifiers:
        details["modifiers"] = modifiers

    constraints = []
    for constraint in list(getattr(obj, "constraints", []))[:50]:
        target = getattr(constraint, "target", None)
        constraints.append(
            {
                "name": constraint.name,
                "type": constraint.type,
                "target": target.name if target else None,
                "influence": round(float(getattr(constraint, "influence", 1.0)), 6),
            }
        )
    if constraints:
        details["constraints"] = constraints

    if obj.type == "MESH" and getattr(obj, "data", None) is not None:
        mesh = obj.data
        details["mesh"] = {
            "name": mesh.name,
            "vertices": len(mesh.vertices),
            "edges": len(mesh.edges),
            "polygons": len(mesh.polygons),
        }

    animation = getattr(obj, "animation_data", None)
    if animation is not None:
        action = getattr(animation, "action", None)
        details["animation"] = {
            "action": action.name if action else None,
            "drivers": len(getattr(animation, "drivers", []) or []),
            "nla_tracks": [
                track.name for track in list(getattr(animation, "nla_tracks", []) or [])[:20]
            ],
        }

    return details


def _scene_objects(command: dict, *, default_limit: int = 200):
    try:
        limit = max(1, min(500, int(command.get("max_objects", default_limit))))
    except (TypeError, ValueError):
        raise ValueError("max_objects must be an integer")

    requested = command.get("object_names")
    if requested is not None:
        if not isinstance(requested, list) or not all(isinstance(name, str) for name in requested):
            raise ValueError("object_names must be a list of object names")
        wanted = set(requested[:500])
        source = [obj for obj in bpy.context.scene.objects if obj.name in wanted]
    else:
        source = list(bpy.context.scene.objects)
    return source[:limit], len(source) > limit


def _scene_snapshot_rich(command: dict) -> None:
    command_id = command["id"]
    try:
        objects, truncated = _scene_objects(command)
    except ValueError as error:
        _response(command_id, False, str(error))
        return

    scene = bpy.context.scene
    camera = scene.camera
    _response(
        command_id,
        True,
        "Rich Blender scene snapshot ready",
        object_count=len(scene.objects),
        returned_objects=len(objects),
        truncated=truncated,
        objects=[_object_details(obj) for obj in objects],
        collections=[
            {
                "name": collection.name,
                "objects": len(collection.objects),
                "children": len(collection.children),
            }
            for collection in list(bpy.data.collections)[:200]
        ],
        active_camera=(
            {
                "name": camera.name,
                "location": _round_vector(camera.matrix_world.translation),
                "rotation_euler": _round_vector(camera.matrix_world.to_euler()),
                "lens": round(float(getattr(camera.data, "lens", 0.0)), 6),
                "sensor_width": round(float(getattr(camera.data, "sensor_width", 0.0)), 6),
            }
            if camera
            else None
        ),
        scene_ordax=_ordax_properties(scene),
    )


def _scene_reset(command: dict) -> None:
    command_id = command["id"]
    try:
        bpy.ops.object.select_all(action="DESELECT")
        for obj in list(bpy.context.scene.objects):
            bpy.data.objects.remove(obj, do_unlink=True)

        for collection in list(bpy.data.collections):
            if collection.users == 0:
                try:
                    bpy.data.collections.remove(collection)
                except Exception:
                    pass

        for blocks in (
            bpy.data.meshes,
            bpy.data.curves,
            bpy.data.cameras,
            bpy.data.lights,
        ):
            for block in list(blocks):
                if block.users == 0 and not getattr(block, "use_fake_user", False):
                    try:
                        blocks.remove(block)
                    except Exception:
                        pass

        bpy.context.scene.frame_set(1)
        _response(
            command_id,
            True,
            "Blender scene reset for isolated generation",
            removed_to_object_count=len(bpy.context.scene.objects),
        )
    except Exception as error:
        _response(
            command_id,
            False,
            f"{type(error).__name__}: {error}",
        )



def _digest_text(digest, value: str) -> None:
    digest.update(str(value).encode("utf-8", errors="surrogatepass"))
    digest.update(b"\0")


def _transform_fingerprint(obj) -> str:
    digest = hashlib.sha256()
    _digest_text(digest, obj.type)
    for row in obj.matrix_world:
        for value in row:
            digest.update(struct.pack("<d", float(value)))
    return digest.hexdigest()


def _mesh_fingerprint(obj, *, evaluated: bool = False) -> tuple[str | None, dict]:
    if obj.type != "MESH":
        return None, {}

    evaluated_obj = None
    mesh = None
    must_clear = False
    try:
        if evaluated:
            depsgraph = bpy.context.evaluated_depsgraph_get()
            evaluated_obj = obj.evaluated_get(depsgraph)
            mesh = evaluated_obj.to_mesh()
            must_clear = True
        else:
            mesh = obj.data

        if mesh is None:
            return None, {}

        digest = hashlib.sha256()
        _digest_text(digest, "evaluated" if evaluated else "base")
        digest.update(struct.pack(
            "<QQQ",
            int(len(mesh.vertices)),
            int(len(mesh.edges)),
            int(len(mesh.polygons)),
        ))

        for vertex in mesh.vertices:
            co = vertex.co
            digest.update(struct.pack("<3d", float(co.x), float(co.y), float(co.z)))

        for edge in mesh.edges:
            vertices = tuple(int(index) for index in edge.vertices)
            digest.update(struct.pack("<2Q", vertices[0], vertices[1]))

        for polygon in mesh.polygons:
            vertices = tuple(int(index) for index in polygon.vertices)
            digest.update(struct.pack("<Q", len(vertices)))
            for index in vertices:
                digest.update(struct.pack("<Q", index))

        return digest.hexdigest(), {
            "vertices": len(mesh.vertices),
            "edges": len(mesh.edges),
            "polygons": len(mesh.polygons),
        }
    finally:
        if must_clear and evaluated_obj is not None:
            try:
                evaluated_obj.to_mesh_clear()
            except Exception:
                pass


def _fingerprint_selector(selector: dict) -> tuple[str, object]:
    object_name = str(selector.get("object_name") or "").strip()
    object_id = str(selector.get("ordax_object_id") or "").strip()
    if bool(object_name) == bool(object_id):
        raise ValueError(
            "each fingerprint selector must provide exactly one of object_name or ordax_object_id"
        )
    obj = _resolve_object(
        {"object_name": object_name} if object_name else {"ordax_object_id": object_id}
    )
    key = f"name:{object_name}" if object_name else f"id:{object_id}"
    return key, obj


def _object_fingerprint_entry(selector: dict) -> dict:
    key, obj = _fingerprint_selector(selector)
    evaluated = bool(selector.get("evaluated", True))
    transform_sha256 = _transform_fingerprint(obj)
    geometry_sha256, mesh_counts = _mesh_fingerprint(obj, evaluated=evaluated)

    digest = hashlib.sha256()
    _digest_text(digest, obj.type)
    _digest_text(digest, transform_sha256)
    _digest_text(digest, geometry_sha256 or "")
    combined = digest.hexdigest()

    return {
        "selector_key": key,
        "object_name": obj.name,
        "ordax_object_id": str(obj.get("ordax_object_id") or "") or None,
        "type": obj.type,
        "evaluated": evaluated,
        "transform_sha256": transform_sha256,
        "geometry_sha256": geometry_sha256,
        "combined_sha256": combined,
        "mesh": mesh_counts,
    }


def _object_fingerprints(command: dict) -> None:
    command_id = command["id"]
    selectors = command.get("selectors")
    if not isinstance(selectors, list) or not selectors:
        _response(command_id, False, "selectors must be a non-empty list")
        return
    if len(selectors) > 100:
        _response(command_id, False, "fingerprint request is limited to 100 objects")
        return

    fingerprints = []
    seen = set()
    try:
        for index, selector in enumerate(selectors):
            if not isinstance(selector, dict):
                raise ValueError(f"fingerprint selector {index} must be an object")
            key, _ = _fingerprint_selector(selector)
            if key in seen:
                raise ValueError(f"duplicate fingerprint selector: {key}")
            seen.add(key)
            fingerprints.append(_object_fingerprint_entry(selector))
    except ValueError as error:
        _response(command_id, False, str(error))
        return

    _response(
        command_id,
        True,
        "Blender object fingerprints ready",
        fingerprints=fingerprints,
    )


def _object_inspect(command: dict) -> None:
    command_id = command["id"]
    try:
        obj = _resolve_object(command)
    except ValueError as error:
        _response(command_id, False, str(error))
        return

    _response(
        command_id,
        True,
        "Blender object inspected",
        object=_object_details(obj),
    )


def _evaluated_world_bvh(obj, depsgraph):
    evaluated = obj.evaluated_get(depsgraph)
    mesh = evaluated.to_mesh()
    try:
        matrix = evaluated.matrix_world.copy()
        vertices = [matrix @ vertex.co for vertex in mesh.vertices]
        polygons = [tuple(poly.vertices) for poly in mesh.polygons if len(poly.vertices) >= 3]
        if not vertices or not polygons:
            return None, []
        tree = BVHTree.FromPolygons(
            vertices,
            polygons,
            all_triangles=False,
            epsilon=0.00001,
        )
        return tree, vertices
    finally:
        evaluated.to_mesh_clear()


def _sample_points(points, limit: int = 160):
    if len(points) <= limit:
        return points
    step = max(1, len(points) // limit)
    return points[::step][:limit]


def _point_inside_closed_surface(tree, point, tolerance: float = 1e-5) -> bool:
    try:
        nearest = tree.find_nearest(point)
    except Exception:
        return False
    if not nearest:
        return False
    surface_point, normal, _, distance = nearest
    if surface_point is None or normal is None or distance is None:
        return False
    # For consistently outward-facing closed meshes, an interior point lies
    # behind the closest surface plane relative to its outward normal.
    signed = (point - surface_point).dot(normal)
    return signed < -max(tolerance, float(distance) * 1e-6)


def _approx_surface_clearance(source_points, target_tree) -> float | None:
    minimum = None
    for point in _sample_points(source_points, 96):
        try:
            nearest = target_tree.find_nearest(point)
        except Exception:
            continue
        if not nearest or nearest[3] is None:
            continue
        distance = float(nearest[3])
        minimum = distance if minimum is None else min(minimum, distance)
    return minimum


def _contact_audit(command: dict) -> None:
    command_id = command["id"]
    raw_pairs = command.get("pairs")
    if not isinstance(raw_pairs, list) or not raw_pairs:
        _response(command_id, False, "pairs must be a non-empty list of [object_a, object_b]")
        return
    if len(raw_pairs) > 200:
        _response(command_id, False, "pairs is limited to 200 object pairs")
        return

    depsgraph = bpy.context.evaluated_depsgraph_get()
    cache = {}

    def geometry_for(obj):
        if obj.name not in cache:
            try:
                cache[obj.name] = _evaluated_world_bvh(obj, depsgraph)
            except (RuntimeError, TypeError, ValueError, ReferenceError):
                cache[obj.name] = (None, [])
        return cache[obj.name]

    results = []
    invalid = []
    resolution_errors = 0
    intersection_pairs = 0

    for item in raw_pairs:
        if (
            not isinstance(item, list)
            or len(item) != 2
            or not all(isinstance(name, str) and name for name in item)
        ):
            invalid.append(item)
            continue

        left = bpy.context.scene.objects.get(item[0])
        right = bpy.context.scene.objects.get(item[1])
        if left is None or right is None:
            resolution_errors += 1
            results.append(
                {
                    "a": item[0],
                    "b": item[1],
                    "ok": False,
                    "error": "object_not_found",
                }
            )
            continue

        left_tree, left_points = geometry_for(left)
        right_tree, right_points = geometry_for(right)
        if left_tree is None or right_tree is None:
            resolution_errors += 1
            results.append(
                {
                    "a": left.name,
                    "b": right.name,
                    "ok": False,
                    "error": "evaluated_geometry_required",
                }
            )
            continue

        left_bounds = _world_bounds(left)
        right_bounds = _world_bounds(right)
        broad_phase_overlap = _aabb_overlap(left_bounds, right_bounds)

        overlaps = left_tree.overlap(right_tree)
        triangle_overlap_count = len(overlaps)
        surface_intersection = triangle_overlap_count > 0

        left_inside_right = False
        right_inside_left = False
        if broad_phase_overlap and not surface_intersection:
            # Full containment without surface intersection is only possible
            # when the containing object's world AABB also contains the other
            # object's AABB. This avoids nearest-normal false positives on
            # tubular/curved geometry that merely wraps around another object.
            if _aabb_contains(right_bounds, left_bounds):
                left_inside_right = any(
                    _point_inside_closed_surface(right_tree, point)
                    for point in _sample_points(left_points)
                )
            if _aabb_contains(left_bounds, right_bounds):
                right_inside_left = any(
                    _point_inside_closed_surface(left_tree, point)
                    for point in _sample_points(right_points)
                )

        contained = left_inside_right or right_inside_left
        intersects = surface_intersection or contained
        if intersects:
            intersection_pairs += 1

        left_to_right = _approx_surface_clearance(left_points, right_tree)
        right_to_left = _approx_surface_clearance(right_points, left_tree)
        clearances = [
            distance
            for distance in (left_to_right, right_to_left)
            if distance is not None
        ]
        min_clearance = min(clearances) if clearances else None

        reason = None
        if surface_intersection:
            reason = "surface_intersection"
        elif contained:
            reason = "containment"

        results.append(
            {
                "a": left.name,
                "b": right.name,
                "ok": True,
                "intersects": intersects,
                "reason": reason,
                "broad_phase_aabb_overlap": broad_phase_overlap,
                "triangle_overlap_count": triangle_overlap_count,
                "left_inside_right": left_inside_right,
                "right_inside_left": right_inside_left,
                "approx_min_surface_distance": (
                    round(min_clearance, 6)
                    if min_clearance is not None
                    else None
                ),
                "a_bounds": left_bounds,
                "b_bounds": right_bounds,
            }
        )

    if invalid:
        _response(
            command_id,
            False,
            "One or more contact audit pairs are invalid",
            invalid_pairs=invalid[:20],
            results=results,
        )
        return

    passed = resolution_errors == 0 and intersection_pairs == 0
    if resolution_errors:
        summary = (
            f"Blender contact audit could not resolve {resolution_errors} pair(s)"
        )
    elif intersection_pairs:
        summary = (
            f"Blender contact audit found {intersection_pairs} intersecting object pair(s)"
        )
    else:
        summary = "Blender contact audit passed"

    _response(
        command_id,
        passed,
        summary,
        audited_pairs=len(results),
        resolution_errors=resolution_errors,
        intersection_pairs=intersection_pairs,
        results=results,
    )


def _resolve_object(command: dict):
    name = str(command.get("object_name") or "").strip()
    object_id = str(command.get("ordax_object_id") or "").strip()

    if bool(name) == bool(object_id):
        raise ValueError("provide exactly one of object_name or ordax_object_id")

    if name:
        obj = bpy.context.scene.objects.get(name)
        if obj is None:
            raise ValueError(f"object not found in current scene: {name}")
        return obj

    matches = [
        obj
        for obj in bpy.context.scene.objects
        if str(obj.get("ordax_object_id") or "") == object_id
    ]
    if not matches:
        raise ValueError(f"ordax_object_id not found: {object_id}")
    if len(matches) > 1:
        raise ValueError(f"ordax_object_id is not unique: {object_id}")
    return matches[0]


def _object_transform(command: dict) -> None:
    command_id = command["id"]
    try:
        normalized = _normalize_transform_request(
            command,
            transport_fields={"id", "operation"},
        )
        obj = _resolve_object(normalized)
    except ValueError as error:
        _response(command_id, False, str(error))
        return

    location = normalized.get("location")
    rotation = normalized.get("rotation_euler")
    scale = normalized.get("scale")
    dimensions = normalized.get("dimensions")

    before = _object_details(obj)
    try:
        if location is not None:
            obj.location = location
        if rotation is not None:
            obj.rotation_mode = "XYZ"
            obj.rotation_euler = rotation
        if scale is not None:
            obj.scale = scale
        if dimensions is not None:
            obj.dimensions = dimensions
        bpy.context.view_layer.update()
    except Exception as error:
        _response(command_id, False, f"{type(error).__name__}: {error}", before=before)
        return

    _response(
        command_id,
        True,
        "Blender object transform updated",
        before=before,
        object=_object_details(obj),
    )


def _object_remove(command: dict) -> None:
    command_id = command["id"]
    try:
        _modeling_runtime_preconditions()
        allowed = {"id", "operation", "object_names", "missing_ok"}
        unsupported = sorted(set(command) - allowed)
        if unsupported:
            raise ValueError("unsupported field(s): " + ", ".join(unsupported))

        names = command.get("object_names")
        if (
            not isinstance(names, list)
            or not names
            or len(names) > 64
            or not all(isinstance(name, str) and name.strip() for name in names)
        ):
            raise ValueError(
                "object_names must be a non-empty list of at most 64 object names"
            )
        missing_ok = command.get("missing_ok", False)
        if not isinstance(missing_ok, bool):
            raise ValueError("missing_ok must be boolean")

        removed = []
        missing = []
        for raw_name in names:
            name = raw_name.strip()
            obj = bpy.data.objects.get(name)
            if obj is None:
                missing.append(name)
                continue
            if obj.library is not None or obj.override_library is not None:
                raise ValueError(f"linked object cannot be removed: {name}")

            data = getattr(obj, "data", None)
            data_type = getattr(obj, "type", "")
            bpy.data.objects.remove(obj, do_unlink=True)
            if data is not None and getattr(data, "users", 0) == 0:
                if data_type == "MESH":
                    bpy.data.meshes.remove(data)
                elif data_type == "CURVE":
                    bpy.data.curves.remove(data)
                elif data_type == "CAMERA":
                    bpy.data.cameras.remove(data)
                elif data_type == "LIGHT":
                    bpy.data.lights.remove(data)
            removed.append(name)

        if missing and not missing_ok:
            raise ValueError("object(s) not found: " + ", ".join(missing))

        bpy.context.view_layer.update()
        _response(
            command_id,
            True,
            "Blender object(s) removed",
            operation="object_remove",
            removed=removed,
            missing=missing,
        )
    except Exception as error:
        _response(
            command_id,
            False,
            str(error) if isinstance(error, ValueError) else f"{type(error).__name__}: {error}",
            operation="object_remove",
        )


def _extract_region(command: dict) -> None:
    command_id = command["id"]
    try:
        _modeling_runtime_preconditions()
        allowed = {
            "id",
            "operation",
            "axis",
            "minimum",
            "maximum",
            "translate",
            "keep_names",
        }
        unsupported = sorted(set(command) - allowed)
        if unsupported:
            raise ValueError("unsupported field(s): " + ", ".join(unsupported))

        axis = str(command.get("axis") or "X").strip().upper()
        if axis not in {"X", "Y", "Z"}:
            raise ValueError("axis must be X, Y, or Z")
        axis_index = {"X": 0, "Y": 1, "Z": 2}[axis]

        minimum = command.get("minimum")
        maximum = command.get("maximum")
        if (
            isinstance(minimum, bool)
            or isinstance(maximum, bool)
            or not isinstance(minimum, (int, float))
            or not isinstance(maximum, (int, float))
        ):
            raise ValueError("minimum and maximum must be numeric")
        minimum = float(minimum)
        maximum = float(maximum)
        if not math.isfinite(minimum) or not math.isfinite(maximum):
            raise ValueError("minimum and maximum must be finite")
        if maximum <= minimum:
            raise ValueError("maximum must be greater than minimum")

        translate = command.get("translate", [0.0, 0.0, 0.0])
        if not isinstance(translate, list) or len(translate) != 3:
            raise ValueError("translate must be a list of three numbers")
        delta_values = []
        for value in translate:
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError("translate must contain only numbers")
            numeric = float(value)
            if not math.isfinite(numeric) or abs(numeric) > 100000.0:
                raise ValueError("translate contains a value outside the allowed range")
            delta_values.append(numeric)
        delta = Vector(delta_values)

        raw_keep_names = command.get("keep_names", [])
        if (
            not isinstance(raw_keep_names, list)
            or len(raw_keep_names) > 64
            or not all(isinstance(name, str) and name.strip() for name in raw_keep_names)
        ):
            raise ValueError(
                "keep_names must be a list of at most 64 non-empty object names"
            )
        keep_names = {name.strip() for name in raw_keep_names}

        bpy.context.view_layer.update()
        scene_objects = list(bpy.context.scene.objects)
        keep_objects = []
        remove_objects = []

        for obj in scene_objects:
            if obj.name in keep_names:
                keep_objects.append(obj)
                continue

            bounds = _world_bounds(obj)
            aabb_min = bounds.get("aabb_min")
            aabb_max = bounds.get("aabb_max")
            world_coordinate = float(obj.matrix_world.translation[axis_index])
            local_coordinate = float(obj.location[axis_index])
            if (
                isinstance(aabb_min, list)
                and isinstance(aabb_max, list)
                and len(aabb_min) == 3
                and len(aabb_max) == 3
            ):
                coordinate = (
                    float(aabb_min[axis_index]) + float(aabb_max[axis_index])
                ) / 2.0
            else:
                coordinate = world_coordinate

            # Hidden/legacy Blender objects can retain a stale matrix_world while
            # their root-level location contains the actual authored placement.
            # Detect that specific disagreement without breaking meshes whose
            # geometry is intentionally offset from an origin at zero.
            if obj.parent is None:
                local_world_delta = abs(local_coordinate - world_coordinate)
                bounds_world_delta = abs(coordinate - world_coordinate)
                if local_world_delta > 1e-5 and bounds_world_delta <= 1e-4:
                    coordinate = local_coordinate

            if minimum <= coordinate <= maximum:
                keep_objects.append(obj)
            else:
                remove_objects.append(obj)

        keep_set = set(keep_objects)
        for obj in keep_objects:
            parent = obj.parent
            if parent is not None and parent not in keep_set:
                world_matrix = obj.matrix_world.copy()
                obj.parent = None
                obj.matrix_world = world_matrix

        for obj in remove_objects:
            if obj.library is not None or obj.override_library is not None:
                raise ValueError(f"linked object cannot be removed: {obj.name}")

        removed_names = [obj.name for obj in remove_objects]
        for obj in remove_objects:
            data = getattr(obj, "data", None)
            data_type = getattr(obj, "type", "")
            bpy.data.objects.remove(obj, do_unlink=True)
            if data is not None and getattr(data, "users", 0) == 0:
                try:
                    if data_type == "MESH":
                        bpy.data.meshes.remove(data)
                    elif data_type in {"CURVE", "FONT", "SURFACE"}:
                        bpy.data.curves.remove(data)
                    elif data_type == "CAMERA":
                        bpy.data.cameras.remove(data)
                    elif data_type == "LIGHT":
                        bpy.data.lights.remove(data)
                except Exception:
                    pass

        if delta.length > 0.0:
            kept_after = [
                bpy.data.objects.get(obj.name)
                for obj in keep_objects
                if bpy.data.objects.get(obj.name) is not None
            ]
            kept_after = [obj for obj in kept_after if obj is not None]
            kept_set_after = set(kept_after)
            for obj in kept_after:
                if obj.parent is None or obj.parent not in kept_set_after:
                    if obj.parent is None:
                        obj.location = obj.location + delta
                    else:
                        matrix = obj.matrix_world.copy()
                        matrix.translation = matrix.translation + delta
                        obj.matrix_world = matrix

        scene = bpy.context.scene
        if scene.camera is None or scene.camera.name not in bpy.data.objects:
            cameras = [obj for obj in scene.objects if obj.type == "CAMERA"]
            scene.camera = cameras[0] if cameras else None

        bpy.context.view_layer.update()
        kept_names = [
            obj.name
            for obj in bpy.context.scene.objects
        ]
        _response(
            command_id,
            True,
            "Blender scene region extracted",
            operation="extract_region",
            axis=axis,
            minimum=minimum,
            maximum=maximum,
            translate=delta_values,
            kept_count=len(kept_names),
            removed_count=len(removed_names),
            kept_names=kept_names[:200],
            removed_names=removed_names[:200],
            truncated_kept=len(kept_names) > 200,
            truncated_removed=len(removed_names) > 200,
        )
    except Exception as error:
        _response(
            command_id,
            False,
            str(error) if isinstance(error, ValueError) else f"{type(error).__name__}: {error}",
            operation="extract_region",
        )


def _create_box_with_cutouts(command: dict) -> None:
    command_id = command["id"]
    created = []
    body = None
    try:
        _modeling_runtime_preconditions()
        allowed = {
            "id",
            "operation",
            "name",
            "location",
            "dimensions",
            "cutouts",
        }
        unsupported = sorted(set(command) - allowed)
        if unsupported:
            raise ValueError("unsupported field(s): " + ", ".join(unsupported))

        name = str(command.get("name") or "").strip()
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_. +\\-]{0,126}", name):
            raise ValueError("name contains unsupported characters")
        if bpy.data.objects.get(name) is not None:
            raise ValueError("object name already exists; creation refused")

        location = command.get("location")
        dimensions = command.get("dimensions")
        cutouts = command.get("cutouts")
        if (
            not isinstance(location, list)
            or len(location) != 3
            or not all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in location)
        ):
            raise ValueError("location must contain three numbers")
        if (
            not isinstance(dimensions, list)
            or len(dimensions) != 3
            or not all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in dimensions)
        ):
            raise ValueError("dimensions must contain three numbers")
        dimensions = [float(v) for v in dimensions]
        if not all(math.isfinite(v) and 0.001 <= v <= 1000.0 for v in dimensions):
            raise ValueError("dimensions contain a value outside the allowed range")
        location = [float(v) for v in location]
        if not all(math.isfinite(v) and abs(v) <= 1000.0 for v in location):
            raise ValueError("location contains a value outside the allowed range")
        if not isinstance(cutouts, list) or not (1 <= len(cutouts) <= 8):
            raise ValueError("cutouts must contain 1 to 8 entries")

        normalized_cutouts = []
        for index, cutout in enumerate(cutouts):
            if not isinstance(cutout, dict):
                raise ValueError(f"cutouts[{index}] must be an object")
            unknown = sorted(set(cutout) - {"offset", "dimensions"})
            if unknown:
                raise ValueError(
                    f"cutouts[{index}] unsupported field(s): " + ", ".join(unknown)
                )
            offset = cutout.get("offset")
            cut_dimensions = cutout.get("dimensions")
            if (
                not isinstance(offset, list)
                or len(offset) != 3
                or not all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in offset)
            ):
                raise ValueError(f"cutouts[{index}].offset must contain three numbers")
            if (
                not isinstance(cut_dimensions, list)
                or len(cut_dimensions) != 3
                or not all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in cut_dimensions)
            ):
                raise ValueError(f"cutouts[{index}].dimensions must contain three numbers")
            offset = [float(v) for v in offset]
            cut_dimensions = [float(v) for v in cut_dimensions]
            if not all(math.isfinite(v) and abs(v) <= 1000.0 for v in offset):
                raise ValueError(f"cutouts[{index}].offset is outside the allowed range")
            if not all(math.isfinite(v) and 0.001 <= v <= 1000.0 for v in cut_dimensions):
                raise ValueError(f"cutouts[{index}].dimensions is outside the allowed range")
            normalized_cutouts.append(
                {"offset": offset, "dimensions": cut_dimensions}
            )

        bpy.ops.object.select_all(action="DESELECT")
        bpy.ops.mesh.primitive_cube_add(size=1.0, location=location)
        body = bpy.context.active_object
        if body is None:
            raise RuntimeError("failed to create box body")
        body.name = name
        body.data.name = name
        body.dimensions = Vector(dimensions)
        bpy.context.view_layer.update()
        bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
        created.append(body)

        for index, cutout in enumerate(normalized_cutouts):
            cutter_location = [
                location[i] + cutout["offset"][i]
                for i in range(3)
            ]
            bpy.ops.mesh.primitive_cube_add(
                size=1.0,
                location=cutter_location,
            )
            cutter = bpy.context.active_object
            if cutter is None:
                raise RuntimeError(f"failed to create cutout {index}")
            cutter.name = f"__ordax_cut_{command_id[:8]}_{index}"
            cutter.dimensions = Vector(cutout["dimensions"])
            bpy.context.view_layer.update()
            bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
            created.append(cutter)

            bpy.context.view_layer.objects.active = body
            body.select_set(True)
            cutter.select_set(False)
            modifier = body.modifiers.new(
                name=f"OrdaX_Cutout_{index}",
                type="BOOLEAN",
            )
            modifier.operation = "DIFFERENCE"
            modifier.solver = "EXACT"
            modifier.object = cutter
            bpy.ops.object.modifier_apply(modifier=modifier.name)

            cutter_data = cutter.data
            bpy.data.objects.remove(cutter, do_unlink=True)
            if cutter in created:
                created.remove(cutter)
            if cutter_data is not None and cutter_data.users == 0:
                bpy.data.meshes.remove(cutter_data)

        bpy.context.view_layer.objects.active = body
        body.select_set(True)
        bpy.context.view_layer.update()
        _response(
            command_id,
            True,
            "Blender box with cutouts created",
            operation="create_box_with_cutouts",
            object=_object_details(body),
            cutouts=normalized_cutouts,
        )
    except Exception as error:
        for obj in list(created):
            try:
                data = getattr(obj, "data", None)
                bpy.data.objects.remove(obj, do_unlink=True)
                if data is not None and getattr(data, "users", 0) == 0:
                    bpy.data.meshes.remove(data)
            except Exception:
                pass
        _response(
            command_id,
            False,
            str(error) if isinstance(error, ValueError) else f"{type(error).__name__}: {error}",
            operation="create_box_with_cutouts",
        )


def _cleanup_orphans(command: dict) -> None:
    command_id = command["id"]
    try:
        _modeling_runtime_preconditions()
        allowed = {
            "id",
            "operation",
            "remove_empty_collections",
        }
        unsupported = sorted(set(command) - allowed)
        if unsupported:
            raise ValueError("unsupported field(s): " + ", ".join(unsupported))

        remove_empty_collections = command.get(
            "remove_empty_collections",
            False,
        )
        if not isinstance(remove_empty_collections, bool):
            raise ValueError("remove_empty_collections must be boolean")

        removed = {}
        datablock_groups = {
            "meshes": bpy.data.meshes,
            "materials": bpy.data.materials,
            "images": bpy.data.images,
            "curves": bpy.data.curves,
            "cameras": bpy.data.cameras,
            "lights": bpy.data.lights,
            "textures": bpy.data.textures,
            "node_groups": bpy.data.node_groups,
        }
        for label, datablocks in datablock_groups.items():
            count = 0
            for datablock in list(datablocks):
                if getattr(datablock, "library", None) is not None:
                    continue
                if getattr(datablock, "users", 0) != 0:
                    continue
                if bool(getattr(datablock, "use_fake_user", False)):
                    continue
                try:
                    datablocks.remove(datablock)
                    count += 1
                except Exception:
                    pass
            removed[label] = count

        removed_collections = []
        if remove_empty_collections:
            for collection in list(bpy.data.collections):
                if getattr(collection, "library", None) is not None:
                    continue
                if len(collection.objects) != 0 or len(collection.children) != 0:
                    continue
                try:
                    removed_collections.append(collection.name)
                    bpy.data.collections.remove(collection)
                except Exception:
                    pass

        bpy.context.view_layer.update()
        _response(
            command_id,
            True,
            "Blender orphan data cleaned",
            operation="cleanup_orphans",
            removed=removed,
            removed_empty_collections=removed_collections,
        )
    except Exception as error:
        _response(
            command_id,
            False,
            str(error) if isinstance(error, ValueError) else f"{type(error).__name__}: {error}",
            operation="cleanup_orphans",
        )


def _modeling_runtime_preconditions() -> None:
    if bpy.context.mode != "OBJECT":
        raise ValueError("modeling requires Object Mode")
    if bpy.app.is_job_running("RENDER"):
        raise ValueError("modeling requires no running render job")


def _modeling_plan_from_command(operation: str, command: dict) -> dict:
    payload = {
        key: value
        for key, value in command.items()
        if key not in {"id", "operation"}
    }
    return _plan_modeling_operation(operation, payload)


def _modeling_create_primitive(command: dict) -> None:
    command_id = command["id"]
    try:
        _modeling_runtime_preconditions()
        plan = _modeling_plan_from_command("create_primitive", command)
        arguments = plan["arguments"]
        name = arguments["name"]
        if bpy.context.scene.objects.get(name) is not None or bpy.data.objects.get(name) is not None:
            raise ValueError("object name already exists; creation refused")
    except ValueError as error:
        _response(command_id, False, str(error))
        return

    import bmesh

    bm = bmesh.new()
    mesh = None
    obj = None
    try:
        primitive = arguments["primitive"]
        if primitive == "cube":
            bmesh.ops.create_cube(
                bm,
                size=arguments["size"],
            )
        elif primitive == "sphere":
            segments = arguments["segments"]
            bmesh.ops.create_uvsphere(
                bm,
                u_segments=segments,
                v_segments=max(4, segments // 2),
                radius=arguments["radius"],
            )
        else:
            bmesh.ops.create_cone(
                bm,
                cap_ends=True,
                cap_tris=False,
                segments=arguments["segments"],
                radius1=arguments["radius"],
                radius2=arguments["radius"],
                depth=arguments["depth"],
            )

        mesh = bpy.data.meshes.new(name)
        bm.to_mesh(mesh)
        mesh.update()
        obj = bpy.data.objects.new(name, mesh)
        bpy.context.scene.collection.objects.link(obj)
        obj.location = arguments["location"]
        bpy.context.view_layer.update()
        _response(
            command_id,
            True,
            "Blender primitive created",
            operation="create_primitive",
            object=_object_details(obj),
        )
    except Exception as error:
        if obj is not None:
            try:
                bpy.data.objects.remove(obj, do_unlink=True)
            except Exception:
                pass
        if mesh is not None and getattr(mesh, "users", 0) == 0:
            try:
                bpy.data.meshes.remove(mesh)
            except Exception:
                pass
        _response(
            command_id,
            False,
            f"{type(error).__name__}: {error}",
            operation="create_primitive",
        )
    finally:
        bm.free()


def _modeling_add_modifier(command: dict) -> None:
    command_id = command["id"]
    modifier = None
    obj = None
    before = None
    try:
        _modeling_runtime_preconditions()
        plan = _modeling_plan_from_command("add_modifier", command)
        if not plan["executable"]:
            raise ValueError(
                f"{plan['status']}: modifier variant is not enabled for execution"
            )
        arguments = plan["arguments"]
        obj = _resolve_object(arguments)

        if (
            obj.type != "MESH"
            or obj.library is not None
            or obj.override_library is not None
            or getattr(obj.data, "library", None) is not None
        ):
            raise ValueError(
                "modifier requires an existing local, non-linked mesh object"
            )
        if obj.animation_data is not None or len(obj.constraints) > 0:
            raise ValueError(
                "animated or constrained targets require a dedicated workflow"
            )
        if obj.modifiers.get(arguments["name"]) is not None:
            raise ValueError("modifier name already exists; insertion refused")

        depsgraph = bpy.context.evaluated_depsgraph_get()
        evaluated = obj.evaluated_get(depsgraph)
        evaluated_faces = len(evaluated.data.polygons)
        budget = _evaluate_modifier_runtime_budget(
            modifier_type=arguments["type"],
            modifier_count=len(obj.modifiers),
            evaluated_faces=evaluated_faces,
            levels=arguments.get("levels", 1),
            array_count=arguments.get("count", 1),
        )
        if not budget["allowed"]:
            raise ValueError("; ".join(budget["reasons"]))

        before = _object_details(obj)
        modifier = obj.modifiers.new(arguments["name"], arguments["type"])
        if modifier.type == "BEVEL":
            modifier.width = arguments["width"]
            modifier.segments = arguments["segments"]
        elif modifier.type == "SUBSURF":
            modifier.levels = arguments["levels"]
            modifier.render_levels = arguments["levels"]
        elif modifier.type == "SOLIDIFY":
            modifier.thickness = arguments["thickness"]
        elif modifier.type == "ARRAY":
            modifier.fit_type = "FIXED_COUNT"
            modifier.count = arguments["count"]
            modifier.use_relative_offset = True
            modifier.relative_offset_displace = arguments["relative_offset"]
            modifier.use_constant_offset = "constant_offset" in arguments
            if modifier.use_constant_offset:
                modifier.constant_offset_displace = arguments["constant_offset"]
        else:
            axis = arguments["axis"]
            modifier.use_axis = [candidate == axis for candidate in "XYZ"]

        bpy.context.view_layer.update()
        _response(
            command_id,
            True,
            "Blender modifier inserted",
            operation="add_modifier",
            runtime_budget=budget,
            array_parameters=(
                {
                    "count": arguments["count"],
                    "relative_offset": arguments["relative_offset"],
                    "constant_offset": arguments.get("constant_offset"),
                    "offsets_combined": "constant_offset" in arguments,
                }
                if modifier.type == "ARRAY"
                else None
            ),
            before=before,
            object=_object_details(obj),
        )
    except (ValueError, Exception) as error:
        if modifier is not None and obj is not None:
            try:
                obj.modifiers.remove(modifier)
                bpy.context.view_layer.update()
            except Exception:
                pass
        _response(
            command_id,
            False,
            str(error) if isinstance(error, ValueError) else f"{type(error).__name__}: {error}",
            operation="add_modifier",
            before=before,
        )


def _modeling_surface_scatter(command: dict) -> None:
    command_id = command["id"]
    modifier = None
    node_group = None
    target = None
    before = None
    try:
        _modeling_runtime_preconditions()
        plan = _modeling_plan_from_command("surface_scatter", command)
        if not plan["executable"]:
            raise ValueError(
                f"{plan['status']}: surface scatter is not enabled for execution"
            )
        arguments = plan["arguments"]
        target = _resolve_object(arguments)
        source = bpy.context.scene.objects.get(arguments["source_object_name"])
        if source is None:
            raise ValueError("surface scatter source object was not found in the scene")
        if target == source:
            raise ValueError("surface scatter source and target must be different objects")
        for label, obj in (("target", target), ("source", source)):
            if (
                obj.type != "MESH"
                or obj.library is not None
                or obj.override_library is not None
                or getattr(obj.data, "library", None) is not None
            ):
                raise ValueError(
                    f"surface scatter {label} must be an existing local, non-linked mesh object"
                )
        if target.animation_data is not None or len(target.constraints) > 0:
            raise ValueError("animated or constrained scatter targets require a dedicated workflow")
        if target.modifiers.get(arguments["name"]) is not None:
            raise ValueError("modifier name already exists; scatter insertion refused")

        depsgraph = bpy.context.evaluated_depsgraph_get()
        target_eval = target.evaluated_get(depsgraph)
        source_eval = source.evaluated_get(depsgraph)
        budget = _evaluate_surface_scatter_runtime_budget(
            modifier_count=len(target.modifiers),
            target_faces=len(target_eval.data.polygons),
            source_faces=len(source_eval.data.polygons),
            max_instances=arguments["max_instances"],
        )
        if not budget["allowed"]:
            raise ValueError("; ".join(budget["reasons"]))

        def _instance_count() -> int:
            graph = bpy.context.evaluated_depsgraph_get()
            graph.update()
            count = 0
            for item in graph.object_instances:
                if item.is_instance and item.parent is not None and item.parent.name == target.name:
                    count += 1
            return count

        before_instances = _instance_count()
        before = _object_details(target)
        modifier = target.modifiers.new(arguments["name"], "NODES")
        node_group = bpy.data.node_groups.new(
            f"ORDAX Scatter - {target.name} - {arguments['name']}", "GeometryNodeTree"
        )
        node_group.is_modifier = True
        node_group.interface.new_socket(
            name="Geometry", in_out="INPUT", socket_type="NodeSocketGeometry"
        )
        node_group.interface.new_socket(
            name="Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry"
        )
        modifier.node_group = node_group

        nodes = node_group.nodes
        links = node_group.links
        group_input = nodes.new("NodeGroupInput")
        group_output = nodes.new("NodeGroupOutput")
        distribute = nodes.new("GeometryNodeDistributePointsOnFaces")
        distribute.distribute_method = "RANDOM"
        distribute.inputs["Density"].default_value = arguments["density"]
        distribute.inputs["Seed"].default_value = arguments["seed"]
        index = nodes.new("GeometryNodeInputIndex")
        compare = nodes.new("FunctionNodeCompare")
        compare.data_type = "INT"
        compare.operation = "LESS_THAN"
        compare.inputs["B"].default_value = arguments["max_instances"]
        object_info = nodes.new("GeometryNodeObjectInfo")
        object_info.transform_space = "ORIGINAL"
        object_info.inputs["Object"].default_value = source
        random_scale = nodes.new("FunctionNodeRandomValue")
        random_scale.data_type = "FLOAT"
        random_scale.inputs["Min"].default_value = arguments["scale_min"]
        random_scale.inputs["Max"].default_value = arguments["scale_max"]
        random_scale.inputs["Seed"].default_value = (arguments["seed"] + 1) % 2147483647
        instance_on_points = nodes.new("GeometryNodeInstanceOnPoints")

        links.new(group_input.outputs["Geometry"], distribute.inputs["Mesh"])
        links.new(index.outputs["Index"], compare.inputs["A"])
        links.new(compare.outputs["Result"], instance_on_points.inputs["Selection"])
        links.new(distribute.outputs["Points"], instance_on_points.inputs["Points"])
        links.new(object_info.outputs["Geometry"], instance_on_points.inputs["Instance"])
        links.new(random_scale.outputs["Value"], instance_on_points.inputs["Scale"])
        if arguments["align_to_normal"]:
            links.new(distribute.outputs["Rotation"], instance_on_points.inputs["Rotation"])
        if arguments["keep_surface"]:
            join = nodes.new("GeometryNodeJoinGeometry")
            links.new(group_input.outputs["Geometry"], join.inputs["Geometry"])
            links.new(instance_on_points.outputs["Instances"], join.inputs["Geometry"])
            links.new(join.outputs["Geometry"], group_output.inputs["Geometry"])
        else:
            links.new(instance_on_points.outputs["Instances"], group_output.inputs["Geometry"])

        bpy.context.view_layer.update()
        after_instances = _instance_count()
        actual_instances = max(0, after_instances - before_instances)
        if actual_instances > arguments["max_instances"]:
            raise RuntimeError("surface scatter exceeded its hard instance cap")
        if actual_instances <= 0:
            raise ValueError("surface scatter produced zero instances; increase density")
        _response(
            command_id,
            True,
            "Blender Geometry Nodes surface scatter created",
            operation="surface_scatter",
            runtime_budget=budget,
            instance_count=actual_instances,
            scatter_parameters={
                "source_object_name": source.name,
                "density": arguments["density"],
                "seed": arguments["seed"],
                "max_instances": arguments["max_instances"],
                "scale_min": arguments["scale_min"],
                "scale_max": arguments["scale_max"],
                "align_to_normal": arguments["align_to_normal"],
                "keep_surface": arguments["keep_surface"],
            },
            node_group=node_group.name,
            before=before,
            object=_object_details(target),
        )
    except Exception as error:
        if modifier is not None and target is not None:
            try:
                target.modifiers.remove(modifier)
                bpy.context.view_layer.update()
            except Exception:
                pass
        if node_group is not None and getattr(node_group, "users", 0) == 0:
            try:
                bpy.data.node_groups.remove(node_group)
            except Exception:
                pass
        _response(
            command_id,
            False,
            str(error) if isinstance(error, ValueError) else f"{type(error).__name__}: {error}",
            operation="surface_scatter",
            before=before,
        )


def _boolean_cut_preview_items(preview_id: str) -> list:
    return [
        obj for obj in bpy.context.scene.objects
        if str(obj.get("ordax_cut_preview_id") or "") == preview_id
    ]


def _boolean_cut_target(preview_id: str):
    cutters = _boolean_cut_preview_items(preview_id)
    if not cutters:
        raise ValueError("boolean cut preview was not found")
    names = {str(obj.get("ordax_cut_target") or "") for obj in cutters}
    names.discard("")
    if len(names) != 1:
        raise ValueError("boolean cut preview target metadata is inconsistent")
    target = bpy.context.scene.objects.get(next(iter(names)))
    if target is None:
        raise ValueError("boolean cut preview target no longer exists")
    return target, cutters


def _boolean_cut_matrix(target, offset, rotation):
    local = Matrix.Translation(Vector(offset)) @ Euler(tuple(rotation), "XYZ").to_matrix().to_4x4()
    return target.matrix_world @ local


def _create_extruded_cutter(*, name: str, points: list, depth: float, target, offset: list, rotation: list, collection, preview_id: str, workflow_name: str):
    if len(points) < 3:
        raise ValueError("cutter profile requires at least three points")
    half = float(depth) * 0.5
    vertices = [(float(x), float(y), -half) for x, y in points]
    vertices += [(float(x), float(y), half) for x, y in points]
    count = len(points)
    faces = [tuple(reversed(range(count))), tuple(range(count, count * 2))]
    for index in range(count):
        nxt = (index + 1) % count
        faces.append((index, nxt, nxt + count, index + count))
    mesh = bpy.data.meshes.new(name + "Mesh")
    mesh.from_pydata(vertices, [], faces)
    mesh.update()
    obj = bpy.data.objects.new(name, mesh)
    collection.objects.link(obj)
    obj.matrix_world = _boolean_cut_matrix(target, offset, rotation)
    obj.display_type = "WIRE"
    obj.hide_render = True
    obj.show_in_front = True
    obj["ordax_cut_preview_id"] = preview_id
    obj["ordax_cut_target"] = target.name
    obj["ordax_cut_name"] = workflow_name
    obj["ordax_cut_state"] = "preview"
    return obj


def _slot_profile_points(length: float, width: float, segments: int) -> list[tuple[float, float]]:
    radius = float(width) * 0.5
    straight = max(0.0, float(length) - float(width)) * 0.5
    per_arc = max(4, int(segments) // 2)
    points = []
    for index in range(per_arc):
        angle = -math.pi * 0.5 + math.pi * index / (per_arc - 1)
        points.append((straight + math.cos(angle) * radius, math.sin(angle) * radius))
    for index in range(per_arc):
        angle = math.pi * 0.5 + math.pi * index / (per_arc - 1)
        points.append((-straight + math.cos(angle) * radius, math.sin(angle) * radius))
    return points


def _profile_cutters(arguments: dict, target, collection, preview_id: str) -> list:
    created = []
    serial = 0
    for profile in arguments["profiles"]:
        kind = profile["type"]
        base_offset = list(profile["offset"])
        rotation = profile["rotation_euler"]
        expanded = [base_offset]
        if kind == "vent":
            count = profile["count"]
            span = (count - 1) * profile["spacing"]
            expanded = []
            for index in range(count):
                offset = list(base_offset)
                delta = index * profile["spacing"] - span * 0.5
                offset[0 if profile["axis"] == "X" else 1] += delta
                expanded.append(offset)
        for offset in expanded:
            cutter_name = f"__ordax_cut_{preview_id[:8]}_{serial}"
            if kind == "box":
                width, height, depth = profile["dimensions"]
                points = [(-width/2, -height/2), (width/2, -height/2), (width/2, height/2), (-width/2, height/2)]
            elif kind == "circle":
                depth = profile["depth"]
                points = [
                    (math.cos(2 * math.pi * i / profile["segments"]) * profile["radius"],
                     math.sin(2 * math.pi * i / profile["segments"]) * profile["radius"])
                    for i in range(profile["segments"])
                ]
            elif kind in {"slot", "vent"}:
                depth = profile["depth"]
                points = _slot_profile_points(profile["length"], profile["width"], profile["segments"])
            else:
                depth = profile["depth"]
                points = [tuple(point) for point in profile["points"]]
            cutter = _create_extruded_cutter(
                name=cutter_name,
                points=points,
                depth=depth,
                target=target,
                offset=offset,
                rotation=rotation,
                collection=collection,
                preview_id=preview_id,
                workflow_name=arguments["name"],
            )
            created.append(cutter)
            serial += 1
    return created


def _modeling_boolean_cut_preview(command: dict) -> None:
    command_id = command["id"]
    target = None
    collection = None
    created = []
    modifiers = []
    before = None
    try:
        _modeling_runtime_preconditions()
        plan = _modeling_plan_from_command("boolean_cut_preview", command)
        if not plan["executable"]:
            raise ValueError(f"{plan['status']}: boolean cutter preview is not enabled for execution")
        arguments = plan["arguments"]
        target = _resolve_object(arguments)
        if (
            target.type != "MESH"
            or target.library is not None
            or target.override_library is not None
            or getattr(target.data, "library", None) is not None
        ):
            raise ValueError("boolean cut target must be an existing local, non-linked mesh object")
        if target.animation_data is not None or len(target.constraints) > 0:
            raise ValueError("animated or constrained boolean targets require a dedicated workflow")
        for obj in bpy.context.scene.objects:
            if (
                str(obj.get("ordax_cut_target") or "") == target.name
                and str(obj.get("ordax_cut_name") or "") == arguments["name"]
            ):
                raise ValueError("an active boolean cut workflow already uses this target/name")
        depsgraph = bpy.context.evaluated_depsgraph_get()
        evaluated = target.evaluated_get(depsgraph)
        budget = _evaluate_boolean_cut_runtime_budget(
            modifier_count=len(target.modifiers),
            target_faces=len(evaluated.data.polygons),
            expanded_cutters=arguments["expanded_cutters"],
            generated_cutter_faces=arguments["estimated_cutter_faces"],
        )
        if not budget["allowed"]:
            raise ValueError("; ".join(budget["reasons"]))
        before = _object_details(target)
        preview_id = str(command_id)
        collection_name = f"__ORDAX_CUT_PREVIEW_{preview_id[:12]}"
        collection = bpy.data.collections.new(collection_name)
        bpy.context.scene.collection.children.link(collection)
        collection["ordax_cut_preview_id"] = preview_id
        collection["ordax_cut_target"] = target.name
        collection["ordax_cut_name"] = arguments["name"]
        created = _profile_cutters(arguments, target, collection, preview_id)
        for index, cutter in enumerate(created):
            modifier = target.modifiers.new(f"__ORDAX_CUT_{preview_id[:8]}_{index}", "BOOLEAN")
            modifier.operation = "DIFFERENCE"
            modifier.solver = "EXACT"
            modifier.object = cutter
            cutter["ordax_cut_modifier"] = modifier.name
            modifiers.append(modifier)
        bpy.context.view_layer.update()
        _response(
            command_id,
            True,
            "Blender boolean cutter preview created",
            operation="boolean_cut_preview",
            preview_id=preview_id,
            workflow_name=arguments["name"],
            state="preview",
            runtime_budget=budget,
            profiles=arguments["profiles"],
            cutters=[obj.name for obj in created],
            before=before,
            object=_object_details(target),
        )
    except Exception as error:
        if target is not None:
            for modifier in list(modifiers):
                try:
                    target.modifiers.remove(modifier)
                except Exception:
                    pass
        for obj in list(created):
            try:
                mesh = obj.data if obj.type == "MESH" else None
                bpy.data.objects.remove(obj, do_unlink=True)
                if mesh is not None and mesh.users == 0:
                    bpy.data.meshes.remove(mesh)
            except Exception:
                pass
        if collection is not None:
            try:
                if collection.users == 0 or (len(collection.objects) == 0 and len(collection.children) == 0):
                    bpy.data.collections.remove(collection)
            except Exception:
                pass
        _response(
            command_id,
            False,
            str(error) if isinstance(error, ValueError) else f"{type(error).__name__}: {error}",
            operation="boolean_cut_preview",
            before=before,
        )


def _normalize_preview_id(command: dict) -> str:
    allowed = {"id", "operation", "preview_id"}
    unknown = sorted(set(command) - allowed)
    if unknown:
        raise ValueError("unsupported field(s): " + ", ".join(unknown))
    preview_id = str(command.get("preview_id") or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", preview_id):
        raise ValueError("preview_id must be a bounded identifier")
    return preview_id


def _modeling_boolean_cut_commit(command: dict) -> None:
    command_id = command["id"]
    try:
        _modeling_runtime_preconditions()
        preview_id = _normalize_preview_id(command)
        target, cutters = _boolean_cut_target(preview_id)
        for cutter in cutters:
            if str(cutter.get("ordax_cut_state") or "") not in {"preview", "committed"}:
                raise ValueError("boolean cut preview state is invalid")
        for cutter in cutters:
            cutter["ordax_cut_state"] = "committed"
            cutter.hide_render = True
            cutter.hide_set(True)
        bpy.context.view_layer.update()
        _response(
            command_id,
            True,
            "Blender boolean cutter preview committed non-destructively",
            operation="boolean_cut_commit",
            preview_id=preview_id,
            state="committed",
            cutters=[obj.name for obj in cutters],
            object=_object_details(target),
        )
    except Exception as error:
        _response(command_id, False, str(error) if isinstance(error, ValueError) else f"{type(error).__name__}: {error}", operation="boolean_cut_commit")


def _modeling_boolean_cut_cancel(command: dict) -> None:
    command_id = command["id"]
    target = None
    try:
        _modeling_runtime_preconditions()
        preview_id = _normalize_preview_id(command)
        target, cutters = _boolean_cut_target(preview_id)
        before = _object_details(target)
        removed_modifiers = []
        removed_cutters = []
        collections = set()
        for cutter in cutters:
            collections.update(cutter.users_collection)
            modifier_name = str(cutter.get("ordax_cut_modifier") or "")
            modifier = target.modifiers.get(modifier_name) if modifier_name else None
            if modifier is not None:
                removed_modifiers.append(modifier.name)
                target.modifiers.remove(modifier)
            removed_cutters.append(cutter.name)
            mesh = cutter.data if cutter.type == "MESH" else None
            bpy.data.objects.remove(cutter, do_unlink=True)
            if mesh is not None and mesh.users == 0:
                bpy.data.meshes.remove(mesh)
        for collection in collections:
            if len(collection.objects) == 0 and len(collection.children) == 0:
                bpy.data.collections.remove(collection)
        bpy.context.view_layer.update()
        _response(
            command_id,
            True,
            "Blender boolean cutter workflow cancelled and rolled back",
            operation="boolean_cut_cancel",
            preview_id=preview_id,
            state="cancelled",
            removed_modifiers=removed_modifiers,
            removed_cutters=removed_cutters,
            before=before,
            object=_object_details(target),
        )
    except Exception as error:
        _response(command_id, False, str(error) if isinstance(error, ValueError) else f"{type(error).__name__}: {error}", operation="boolean_cut_cancel")


def _degenerate_issue_counts(mesh, threshold: float) -> dict:
    bm = bmesh.new()
    try:
        bm.from_mesh(mesh)
        bm.verts.ensure_lookup_table()
        bm.edges.ensure_lookup_table()
        bm.faces.ensure_lookup_table()
        zero_length_edges = sum(
            1
            for edge in bm.edges
            if float((edge.verts[0].co - edge.verts[1].co).length) <= threshold
        )
        degenerate_faces = sum(
            1 for face in bm.faces if float(face.calc_area()) <= threshold
        )
        return {
            "vertices": len(bm.verts),
            "edges": len(bm.edges),
            "faces": len(bm.faces),
            "zero_length_edges": zero_length_edges,
            "degenerate_faces": degenerate_faces,
        }
    finally:
        bm.free()


_MESH_REPAIR_KINDS = ("degenerate", "merge", "hole")


def _target_has_active_mesh_repair_preview(obj) -> bool:
    for kind in _MESH_REPAIR_KINDS:
        if str(obj.get(f"ordax_{kind}_preview_id") or ""):
            return True
    for item in bpy.data.objects:
        for kind in _MESH_REPAIR_KINDS:
            if bool(item.get(f"ordax_{kind}_backup")) and str(item.get(f"ordax_{kind}_target") or "") == obj.name:
                return True
    return False


def _mesh_repair_preview_backups(kind: str, preview_id: str) -> list:
    preview_key = f"ordax_{kind}_preview_id"
    backup_key = f"ordax_{kind}_backup"
    return [
        obj
        for obj in bpy.data.objects
        if str(obj.get(preview_key) or "") == preview_id
        and bool(obj.get(backup_key))
    ]


def _mesh_repair_preview_target(kind: str, preview_id: str):
    backups = _mesh_repair_preview_backups(kind, preview_id)
    if len(backups) != 1:
        raise ValueError(f"{kind} repair preview was not found or is inconsistent")
    backup = backups[0]
    target_name = str(backup.get(f"ordax_{kind}_target") or "")
    target = bpy.context.scene.objects.get(target_name)
    if target is None:
        raise ValueError(f"{kind} repair preview target no longer exists")
    if str(target.get(f"ordax_{kind}_preview_id") or "") != preview_id:
        raise ValueError(f"{kind} repair preview target metadata is inconsistent")
    return target, backup


def _clear_mesh_repair_preview_metadata(target, kind: str) -> None:
    for suffix in (
        "preview_id",
        "preview_state",
        "original_sha256",
        "candidate_sha256",
        "backup_object",
    ):
        try:
            del target[f"ordax_{kind}_{suffix}"]
        except Exception:
            pass


def _remove_mesh_repair_backup(backup) -> None:
    collections = list(backup.users_collection)
    bpy.data.objects.remove(backup, do_unlink=True)
    for collection in collections:
        try:
            if len(collection.objects) == 0 and len(collection.children) == 0:
                bpy.data.collections.remove(collection)
        except Exception:
            pass


def _degenerate_preview_backups(preview_id: str) -> list:
    return _mesh_repair_preview_backups("degenerate", preview_id)


def _degenerate_preview_target(preview_id: str):
    return _mesh_repair_preview_target("degenerate", preview_id)


def _clear_degenerate_preview_metadata(target) -> None:
    _clear_mesh_repair_preview_metadata(target, "degenerate")


def _remove_degenerate_backup(backup) -> None:
    _remove_mesh_repair_backup(backup)


def _modeling_mesh_cleanup(command: dict) -> None:
    command_id = command["id"]
    obj = None
    original_mesh = None
    working_mesh = None
    before = None
    try:
        _modeling_runtime_preconditions()
        plan = _modeling_plan_from_command("mesh_cleanup", command)
        if not plan["executable"]:
            raise ValueError(f"{plan['status']}: mesh cleanup is not enabled for execution")
        arguments = plan["arguments"]
        obj = _resolve_object(arguments)
        if (
            obj.type != "MESH"
            or obj.library is not None
            or obj.override_library is not None
            or getattr(obj.data, "library", None) is not None
        ):
            raise ValueError("mesh cleanup target must be an existing local, non-linked mesh object")
        if obj.animation_data is not None or len(obj.constraints) > 0:
            raise ValueError("animated or constrained cleanup targets require a dedicated workflow")
        if getattr(obj.data, "users", 0) != 1:
            raise ValueError("mesh cleanup requires single-user mesh data")
        if getattr(obj.data, "shape_keys", None) is not None:
            raise ValueError("mesh cleanup does not modify meshes with shape keys")
        if len(obj.modifiers) > 0:
            raise ValueError("mesh cleanup requires a target without modifiers")

        current_sha256, current_counts = _mesh_fingerprint(obj, evaluated=False)
        if current_sha256 != arguments["expected_base_geometry_sha256"]:
            raise ValueError("stale mesh cleanup refused: base geometry fingerprint changed")

        probe = bmesh.new()
        try:
            probe.from_mesh(obj.data)
            loose_vertices = [vertex for vertex in probe.verts if not vertex.link_edges]
            loose_count = len(loose_vertices)
        finally:
            probe.free()
        if loose_count <= 0:
            raise ValueError("mesh cleanup found no isolated loose vertices")
        expected_loose = arguments.get("expected_loose_vertices")
        if expected_loose is not None and loose_count != expected_loose:
            raise ValueError(
                f"stale mesh cleanup refused: expected {expected_loose} loose vertices, found {loose_count}"
            )

        before = _object_details(obj)
        original_mesh = obj.data
        original_name = original_mesh.name
        working_mesh = original_mesh.copy()
        work = bmesh.new()
        try:
            work.from_mesh(working_mesh)
            removable = [vertex for vertex in work.verts if not vertex.link_edges]
            if arguments["repair"] == "remove_loose_vertices" and removable:
                bmesh.ops.delete(work, geom=removable, context="VERTS")
            work.to_mesh(working_mesh)
            working_mesh.update()
        finally:
            work.free()

        obj.data = working_mesh
        bpy.context.view_layer.update()
        after_sha256, after_counts = _mesh_fingerprint(obj, evaluated=False)
        removed = int(current_counts.get("vertices", 0)) - int(after_counts.get("vertices", 0))
        if removed != loose_count:
            raise RuntimeError(
                f"mesh cleanup removed {removed} vertices but diagnosed {loose_count} isolated vertices"
            )

        _response(
            command_id,
            True,
            "Blender mesh cleanup completed",
            operation="mesh_cleanup",
            repair=arguments["repair"],
            removed_vertices=removed,
            before_geometry_sha256=current_sha256,
            after_geometry_sha256=after_sha256,
            before_mesh=current_counts,
            after_mesh=after_counts,
            before=before,
            object=_object_details(obj),
        )
        if original_mesh is not None and original_mesh.users == 0:
            try:
                bpy.data.meshes.remove(original_mesh)
                working_mesh.name = original_name
            except Exception:
                pass
    except Exception as error:
        if obj is not None and original_mesh is not None and working_mesh is not None:
            try:
                if obj.data == working_mesh:
                    obj.data = original_mesh
                    bpy.context.view_layer.update()
            except Exception:
                pass
        if working_mesh is not None and getattr(working_mesh, "users", 0) == 0:
            try:
                bpy.data.meshes.remove(working_mesh)
            except Exception:
                pass
        _response(
            command_id,
            False,
            str(error) if isinstance(error, ValueError) else f"{type(error).__name__}: {error}",
            operation="mesh_cleanup",
            before=before,
        )


def _modeling_degenerate_repair_preview(command: dict) -> None:
    command_id = command["id"]
    obj = None
    original_mesh = None
    candidate_mesh = None
    backup = None
    collection = None
    before = None
    try:
        _modeling_runtime_preconditions()
        plan = _modeling_plan_from_command("degenerate_repair_preview", command)
        if not plan["executable"]:
            raise ValueError(
                f"{plan['status']}: degenerate repair preview is not enabled for execution"
            )
        arguments = plan["arguments"]
        obj = _resolve_object(arguments)
        if (
            obj.type != "MESH"
            or obj.library is not None
            or obj.override_library is not None
            or getattr(obj.data, "library", None) is not None
        ):
            raise ValueError("degenerate repair target must be an existing local, non-linked mesh object")
        if obj.animation_data is not None or len(obj.constraints) > 0:
            raise ValueError("animated or constrained repair targets require a dedicated workflow")
        if getattr(obj.data, "users", 0) != 1:
            raise ValueError("degenerate repair preview requires single-user mesh data")
        if getattr(obj.data, "shape_keys", None) is not None:
            raise ValueError("degenerate repair preview does not support shape keys")
        if len(obj.modifiers) > 0:
            raise ValueError("degenerate repair preview requires a target without modifiers")
        if _target_has_active_mesh_repair_preview(obj):
            raise ValueError("target already has an active mesh-repair preview")

        max_faces = int(getattr(_MODELING_CONTRACTS, "MAX_EVALUATED_FACES", 200000))
        if len(obj.data.polygons) > max_faces:
            raise ValueError("degenerate repair target exceeds interactive face budget")
        current_sha256, current_mesh_counts = _mesh_fingerprint(obj, evaluated=False)
        if current_sha256 != arguments["expected_base_geometry_sha256"]:
            raise ValueError("stale degenerate repair preview refused: base geometry fingerprint changed")
        before_issues = _degenerate_issue_counts(obj.data, arguments["threshold"])
        if before_issues["zero_length_edges"] != arguments["expected_zero_length_edges"]:
            raise ValueError("stale degenerate repair preview refused: zero-length edge count changed")
        if before_issues["degenerate_faces"] != arguments["expected_degenerate_faces"]:
            raise ValueError("stale degenerate repair preview refused: degenerate face count changed")
        if before_issues["zero_length_edges"] + before_issues["degenerate_faces"] <= 0:
            raise ValueError("degenerate repair preview found no diagnosed degenerate geometry")

        before = _object_details(obj)
        original_mesh = obj.data
        candidate_mesh = original_mesh.copy()
        candidate_mesh.name = f"__ordax_degenerate_candidate_{str(command_id)[:12]}"
        work = bmesh.new()
        try:
            work.from_mesh(candidate_mesh)
            bmesh.ops.dissolve_degenerate(
                work,
                dist=float(arguments["threshold"]),
                edges=list(work.edges),
            )
            work.to_mesh(candidate_mesh)
            candidate_mesh.update()
        finally:
            work.free()
        after_issues = _degenerate_issue_counts(candidate_mesh, arguments["threshold"])
        before_total = before_issues["zero_length_edges"] + before_issues["degenerate_faces"]
        after_total = after_issues["zero_length_edges"] + after_issues["degenerate_faces"]
        if after_total >= before_total:
            raise ValueError("degenerate repair preview did not reduce diagnosed degenerate geometry")
        if (
            after_issues["zero_length_edges"] > before_issues["zero_length_edges"]
            or after_issues["degenerate_faces"] > before_issues["degenerate_faces"]
        ):
            raise ValueError("degenerate repair preview increased a diagnosed defect category")

        preview_id = str(command_id)
        collection = bpy.data.collections.new(f"__ORDAX_DEGENERATE_PREVIEW_{preview_id[:12]}")
        bpy.context.scene.collection.children.link(collection)
        backup = bpy.data.objects.new(f"__ordax_degenerate_backup_{preview_id[:12]}", original_mesh)
        collection.objects.link(backup)
        backup.hide_render = True
        backup.hide_set(True)
        backup["ordax_degenerate_preview_id"] = preview_id
        backup["ordax_degenerate_backup"] = True
        backup["ordax_degenerate_target"] = obj.name
        backup["ordax_degenerate_original_sha256"] = current_sha256

        obj.data = candidate_mesh
        bpy.context.view_layer.update()
        candidate_sha256, candidate_counts = _mesh_fingerprint(obj, evaluated=False)
        obj["ordax_degenerate_preview_id"] = preview_id
        obj["ordax_degenerate_preview_state"] = "preview"
        obj["ordax_degenerate_original_sha256"] = current_sha256
        obj["ordax_degenerate_candidate_sha256"] = candidate_sha256
        obj["ordax_degenerate_backup_object"] = backup.name
        _response(
            command_id,
            True,
            "Blender degenerate repair preview created on a candidate mesh",
            operation="degenerate_repair_preview",
            preview_id=preview_id,
            state="preview",
            threshold=arguments["threshold"],
            before_geometry_sha256=current_sha256,
            candidate_geometry_sha256=candidate_sha256,
            before_mesh=current_mesh_counts,
            candidate_mesh=candidate_counts,
            before_issues=before_issues,
            candidate_issues=after_issues,
            before=before,
            object=_object_details(obj),
        )
    except Exception as error:
        if obj is not None and original_mesh is not None and candidate_mesh is not None:
            try:
                if obj.data == candidate_mesh:
                    obj.data = original_mesh
                    bpy.context.view_layer.update()
            except Exception:
                pass
        if obj is not None:
            _clear_degenerate_preview_metadata(obj)
        if backup is not None:
            try:
                _remove_degenerate_backup(backup)
            except Exception:
                pass
        elif collection is not None:
            try:
                if len(collection.objects) == 0 and len(collection.children) == 0:
                    bpy.data.collections.remove(collection)
            except Exception:
                pass
        if candidate_mesh is not None and getattr(candidate_mesh, "users", 0) == 0:
            try:
                bpy.data.meshes.remove(candidate_mesh)
            except Exception:
                pass
        _response(
            command_id,
            False,
            str(error) if isinstance(error, ValueError) else f"{type(error).__name__}: {error}",
            operation="degenerate_repair_preview",
            before=before,
        )


def _modeling_degenerate_repair_commit(command: dict) -> None:
    command_id = command["id"]
    try:
        _modeling_runtime_preconditions()
        preview_id = _normalize_preview_id(command)
        target, backup = _degenerate_preview_target(preview_id)
        if str(target.get("ordax_degenerate_preview_state") or "") != "preview":
            raise ValueError("degenerate repair preview state is invalid")
        stored_candidate_sha256 = str(target.get("ordax_degenerate_candidate_sha256") or "")
        stored_original_sha256 = str(target.get("ordax_degenerate_original_sha256") or "")
        current_candidate_sha256, candidate_counts = _mesh_fingerprint(target, evaluated=False)
        original_sha256, original_counts = _mesh_fingerprint(backup, evaluated=False)
        if current_candidate_sha256 != stored_candidate_sha256:
            raise ValueError("degenerate repair candidate changed after preview; start a new preview")
        if original_sha256 != stored_original_sha256:
            raise ValueError("degenerate repair original backup changed after preview")
        candidate_mesh = target.data
        original_mesh = backup.data
        original_name = original_mesh.name
        _remove_degenerate_backup(backup)
        if getattr(original_mesh, "users", 0) == 0:
            try:
                bpy.data.meshes.remove(original_mesh)
            except Exception:
                pass
        try:
            candidate_mesh.name = original_name
        except Exception:
            pass
        _clear_degenerate_preview_metadata(target)
        bpy.context.view_layer.update()
        _response(
            command_id,
            True,
            "Blender degenerate repair preview committed",
            operation="degenerate_repair_commit",
            preview_id=preview_id,
            state="committed",
            original_geometry_sha256=stored_original_sha256,
            committed_geometry_sha256=current_candidate_sha256,
            original_mesh=original_counts,
            committed_mesh=candidate_counts,
            object=_object_details(target),
        )
    except Exception as error:
        _response(
            command_id,
            False,
            str(error) if isinstance(error, ValueError) else f"{type(error).__name__}: {error}",
            operation="degenerate_repair_commit",
        )


def _modeling_degenerate_repair_cancel(command: dict) -> None:
    command_id = command["id"]
    try:
        _modeling_runtime_preconditions()
        preview_id = _normalize_preview_id(command)
        target, backup = _degenerate_preview_target(preview_id)
        if str(target.get("ordax_degenerate_preview_state") or "") != "preview":
            raise ValueError("degenerate repair preview state is invalid")
        stored_original_sha256 = str(target.get("ordax_degenerate_original_sha256") or "")
        original_mesh = backup.data
        original_sha256, original_counts = _mesh_fingerprint(backup, evaluated=False)
        if original_sha256 != stored_original_sha256:
            raise ValueError("degenerate repair original backup changed after preview")
        candidate_mesh = target.data
        discarded_sha256, candidate_counts = _mesh_fingerprint(target, evaluated=False)
        target.data = original_mesh
        bpy.context.view_layer.update()
        restored_sha256, restored_counts = _mesh_fingerprint(target, evaluated=False)
        if restored_sha256 != stored_original_sha256:
            target.data = candidate_mesh
            bpy.context.view_layer.update()
            raise RuntimeError("degenerate repair cancel could not restore the original geometry fingerprint")
        _clear_degenerate_preview_metadata(target)
        _remove_degenerate_backup(backup)
        if candidate_mesh is not original_mesh and getattr(candidate_mesh, "users", 0) == 0:
            try:
                bpy.data.meshes.remove(candidate_mesh)
            except Exception:
                pass
        bpy.context.view_layer.update()
        _response(
            command_id,
            True,
            "Blender degenerate repair preview cancelled and original mesh restored",
            operation="degenerate_repair_cancel",
            preview_id=preview_id,
            state="cancelled",
            restored_geometry_sha256=restored_sha256,
            discarded_candidate_sha256=discarded_sha256,
            original_mesh=original_counts,
            discarded_candidate_mesh=candidate_counts,
            restored_mesh=restored_counts,
            object=_object_details(target),
        )
    except Exception as error:
        _response(
            command_id,
            False,
            str(error) if isinstance(error, ValueError) else f"{type(error).__name__}: {error}",
            operation="degenerate_repair_cancel",
        )


def _modeling_merge_by_distance_preview(command: dict) -> None:
    command_id = command["id"]
    obj = None
    original_mesh = None
    candidate_mesh = None
    backup = None
    collection = None
    before = None
    try:
        _modeling_runtime_preconditions()
        plan = _modeling_plan_from_command("merge_by_distance_preview", command)
        if not plan["executable"]:
            raise ValueError(
                f"{plan['status']}: merge-by-distance preview is not enabled for execution"
            )
        arguments = plan["arguments"]
        obj = _resolve_object(arguments)
        if (
            obj.type != "MESH"
            or obj.library is not None
            or obj.override_library is not None
            or getattr(obj.data, "library", None) is not None
        ):
            raise ValueError("merge-by-distance target must be an existing local, non-linked mesh object")
        if obj.animation_data is not None or len(obj.constraints) > 0:
            raise ValueError("animated or constrained merge targets require a dedicated workflow")
        if getattr(obj.data, "users", 0) != 1:
            raise ValueError("merge-by-distance preview requires single-user mesh data")
        if getattr(obj.data, "shape_keys", None) is not None:
            raise ValueError("merge-by-distance preview does not support shape keys")
        if len(obj.modifiers) > 0:
            raise ValueError("merge-by-distance preview requires a target without modifiers")
        if _target_has_active_mesh_repair_preview(obj):
            raise ValueError("target already has an active mesh-repair preview")

        max_faces = int(getattr(_MODELING_CONTRACTS, "MAX_EVALUATED_FACES", 200000))
        if len(obj.data.polygons) > max_faces:
            raise ValueError("merge-by-distance target exceeds interactive face budget")
        current_sha256, current_mesh_counts = _mesh_fingerprint(obj, evaluated=False)
        if current_sha256 != arguments["expected_base_geometry_sha256"]:
            raise ValueError("stale merge-by-distance preview refused: base geometry fingerprint changed")
        vertex_indices = list(arguments["vertex_indices"])
        vertex_count = len(obj.data.vertices)
        if not vertex_indices or vertex_indices[-1] >= vertex_count:
            raise ValueError("merge-by-distance vertex index is outside the current base mesh")
        selected_before = [
            {
                "vertex_index": int(index),
                "local_coordinate": [round(float(value), 12) for value in obj.data.vertices[index].co],
            }
            for index in vertex_indices
        ]

        before = _object_details(obj)
        original_mesh = obj.data
        candidate_mesh = original_mesh.copy()
        candidate_mesh.name = f"__ordax_merge_candidate_{str(command_id)[:12]}"
        work = bmesh.new()
        unselected_vertices_verified = 0
        try:
            work.from_mesh(candidate_mesh)
            work.verts.ensure_lookup_table()
            original_index_layer = work.verts.layers.int.new("__ordax_original_index")
            for vertex in work.verts:
                vertex[original_index_layer] = int(vertex.index) + 1
            selected_set = set(vertex_indices)
            selected = [work.verts[index] for index in vertex_indices]
            bmesh.ops.remove_doubles(
                work,
                verts=selected,
                dist=float(arguments["distance"]),
            )
            remaining_original_ids = {
                int(vertex[original_index_layer])
                for vertex in work.verts
                if int(vertex[original_index_layer]) > 0
            }
            missing_unselected = [
                index
                for index in range(vertex_count)
                if index not in selected_set and (index + 1) not in remaining_original_ids
            ]
            if missing_unselected:
                raise RuntimeError(
                    "merge-by-distance preview changed unselected vertex identity: "
                    + ", ".join(str(index) for index in missing_unselected[:16])
                )
            unselected_vertices_verified = vertex_count - len(vertex_indices)
            work.verts.layers.int.remove(original_index_layer)
            work.to_mesh(candidate_mesh)
            candidate_mesh.update()
        finally:
            work.free()
        merged_vertices = int(current_mesh_counts.get("vertices", 0)) - len(candidate_mesh.vertices)
        if merged_vertices <= 0:
            raise ValueError("merge-by-distance preview selected vertices produced no merge")
        if merged_vertices >= len(vertex_indices):
            raise RuntimeError("merge-by-distance preview removed more selected vertices than allowed")

        preview_id = str(command_id)
        collection = bpy.data.collections.new(f"__ORDAX_MERGE_PREVIEW_{preview_id[:12]}")
        bpy.context.scene.collection.children.link(collection)
        backup = bpy.data.objects.new(f"__ordax_merge_backup_{preview_id[:12]}", original_mesh)
        collection.objects.link(backup)
        backup.hide_render = True
        backup.hide_set(True)
        backup["ordax_merge_preview_id"] = preview_id
        backup["ordax_merge_backup"] = True
        backup["ordax_merge_target"] = obj.name
        backup["ordax_merge_original_sha256"] = current_sha256

        obj.data = candidate_mesh
        bpy.context.view_layer.update()
        candidate_sha256, candidate_counts = _mesh_fingerprint(obj, evaluated=False)
        obj["ordax_merge_preview_id"] = preview_id
        obj["ordax_merge_preview_state"] = "preview"
        obj["ordax_merge_original_sha256"] = current_sha256
        obj["ordax_merge_candidate_sha256"] = candidate_sha256
        obj["ordax_merge_backup_object"] = backup.name
        _response(
            command_id,
            True,
            "Blender merge-by-distance preview created on explicitly selected vertices",
            operation="merge_by_distance_preview",
            preview_id=preview_id,
            state="preview",
            distance=arguments["distance"],
            selected_vertex_indices=vertex_indices,
            selected_vertices_before=selected_before,
            merged_vertices=merged_vertices,
            unselected_vertices_preserved=True,
            unselected_vertices_verified=unselected_vertices_verified,
            before_geometry_sha256=current_sha256,
            candidate_geometry_sha256=candidate_sha256,
            before_mesh=current_mesh_counts,
            candidate_mesh=candidate_counts,
            before=before,
            object=_object_details(obj),
        )
    except Exception as error:
        if obj is not None and original_mesh is not None and candidate_mesh is not None:
            try:
                if obj.data == candidate_mesh:
                    obj.data = original_mesh
                    bpy.context.view_layer.update()
            except Exception:
                pass
        if obj is not None:
            _clear_mesh_repair_preview_metadata(obj, "merge")
        if backup is not None:
            try:
                _remove_mesh_repair_backup(backup)
            except Exception:
                pass
        elif collection is not None:
            try:
                if len(collection.objects) == 0 and len(collection.children) == 0:
                    bpy.data.collections.remove(collection)
            except Exception:
                pass
        if candidate_mesh is not None and getattr(candidate_mesh, "users", 0) == 0:
            try:
                bpy.data.meshes.remove(candidate_mesh)
            except Exception:
                pass
        _response(
            command_id,
            False,
            str(error) if isinstance(error, ValueError) else f"{type(error).__name__}: {error}",
            operation="merge_by_distance_preview",
            before=before,
        )


def _modeling_merge_by_distance_commit(command: dict) -> None:
    command_id = command["id"]
    try:
        _modeling_runtime_preconditions()
        preview_id = _normalize_preview_id(command)
        target, backup = _mesh_repair_preview_target("merge", preview_id)
        if str(target.get("ordax_merge_preview_state") or "") != "preview":
            raise ValueError("merge-by-distance preview state is invalid")
        stored_candidate_sha256 = str(target.get("ordax_merge_candidate_sha256") or "")
        stored_original_sha256 = str(target.get("ordax_merge_original_sha256") or "")
        current_candidate_sha256, candidate_counts = _mesh_fingerprint(target, evaluated=False)
        original_sha256, original_counts = _mesh_fingerprint(backup, evaluated=False)
        if current_candidate_sha256 != stored_candidate_sha256:
            raise ValueError("merge-by-distance candidate changed after preview; start a new preview")
        if original_sha256 != stored_original_sha256:
            raise ValueError("merge-by-distance original backup changed after preview")
        candidate_mesh = target.data
        original_mesh = backup.data
        original_name = original_mesh.name
        _remove_mesh_repair_backup(backup)
        if getattr(original_mesh, "users", 0) == 0:
            try:
                bpy.data.meshes.remove(original_mesh)
            except Exception:
                pass
        try:
            candidate_mesh.name = original_name
        except Exception:
            pass
        _clear_mesh_repair_preview_metadata(target, "merge")
        bpy.context.view_layer.update()
        _response(
            command_id,
            True,
            "Blender merge-by-distance preview committed",
            operation="merge_by_distance_commit",
            preview_id=preview_id,
            state="committed",
            original_geometry_sha256=stored_original_sha256,
            committed_geometry_sha256=current_candidate_sha256,
            original_mesh=original_counts,
            committed_mesh=candidate_counts,
            object=_object_details(target),
        )
    except Exception as error:
        _response(
            command_id,
            False,
            str(error) if isinstance(error, ValueError) else f"{type(error).__name__}: {error}",
            operation="merge_by_distance_commit",
        )


def _modeling_merge_by_distance_cancel(command: dict) -> None:
    command_id = command["id"]
    try:
        _modeling_runtime_preconditions()
        preview_id = _normalize_preview_id(command)
        target, backup = _mesh_repair_preview_target("merge", preview_id)
        if str(target.get("ordax_merge_preview_state") or "") != "preview":
            raise ValueError("merge-by-distance preview state is invalid")
        stored_original_sha256 = str(target.get("ordax_merge_original_sha256") or "")
        original_mesh = backup.data
        original_sha256, original_counts = _mesh_fingerprint(backup, evaluated=False)
        if original_sha256 != stored_original_sha256:
            raise ValueError("merge-by-distance original backup changed after preview")
        candidate_mesh = target.data
        discarded_sha256, candidate_counts = _mesh_fingerprint(target, evaluated=False)
        target.data = original_mesh
        bpy.context.view_layer.update()
        restored_sha256, restored_counts = _mesh_fingerprint(target, evaluated=False)
        if restored_sha256 != stored_original_sha256:
            target.data = candidate_mesh
            bpy.context.view_layer.update()
            raise RuntimeError("merge-by-distance cancel could not restore the original geometry fingerprint")
        _clear_mesh_repair_preview_metadata(target, "merge")
        _remove_mesh_repair_backup(backup)
        if candidate_mesh is not original_mesh and getattr(candidate_mesh, "users", 0) == 0:
            try:
                bpy.data.meshes.remove(candidate_mesh)
            except Exception:
                pass
        bpy.context.view_layer.update()
        _response(
            command_id,
            True,
            "Blender merge-by-distance preview cancelled and original mesh restored",
            operation="merge_by_distance_cancel",
            preview_id=preview_id,
            state="cancelled",
            restored_geometry_sha256=restored_sha256,
            discarded_candidate_sha256=discarded_sha256,
            original_mesh=original_counts,
            discarded_candidate_mesh=candidate_counts,
            restored_mesh=restored_counts,
            object=_object_details(target),
        )
    except Exception as error:
        _response(
            command_id,
            False,
            str(error) if isinstance(error, ValueError) else f"{type(error).__name__}: {error}",
            operation="merge_by_distance_cancel",
        )


def _modeling_boundary_hole_fill_preview(command: dict) -> None:
    command_id = command["id"]
    obj = None
    original_mesh = None
    candidate_mesh = None
    backup = None
    collection = None
    before = None
    try:
        _modeling_runtime_preconditions()
        plan = _modeling_plan_from_command("boundary_hole_fill_preview", command)
        if not plan["executable"]:
            raise ValueError(
                f"{plan['status']}: boundary hole-fill preview is not enabled for execution"
            )
        arguments = plan["arguments"]
        obj = _resolve_object(arguments)
        if (
            obj.type != "MESH"
            or obj.library is not None
            or obj.override_library is not None
            or getattr(obj.data, "library", None) is not None
        ):
            raise ValueError("boundary hole-fill target must be an existing local, non-linked mesh object")
        if obj.animation_data is not None or len(obj.constraints) > 0:
            raise ValueError("animated or constrained hole-fill targets require a dedicated workflow")
        if getattr(obj.data, "users", 0) != 1:
            raise ValueError("boundary hole-fill preview requires single-user mesh data")
        if getattr(obj.data, "shape_keys", None) is not None:
            raise ValueError("boundary hole-fill preview does not support shape keys")
        if len(obj.modifiers) > 0:
            raise ValueError("boundary hole-fill preview requires a target without modifiers")
        if _target_has_active_mesh_repair_preview(obj):
            raise ValueError("target already has an active mesh-repair preview")

        max_faces = int(getattr(_MODELING_CONTRACTS, "MAX_EVALUATED_FACES", 200000))
        max_new_faces = int(getattr(_MODELING_CONTRACTS, "MAX_HOLE_FILL_NEW_FACES", 32))
        if len(obj.data.polygons) > max_faces:
            raise ValueError("boundary hole-fill target exceeds interactive face budget")
        current_sha256, current_mesh_counts = _mesh_fingerprint(obj, evaluated=False)
        if current_sha256 != arguments["expected_base_geometry_sha256"]:
            raise ValueError("stale boundary hole-fill preview refused: base geometry fingerprint changed")
        edge_indices = list(arguments["edge_indices"])
        edge_count = len(obj.data.edges)
        if not edge_indices or edge_indices[-1] >= edge_count:
            raise ValueError("boundary hole-fill edge index is outside the current base mesh")

        before = _object_details(obj)
        original_mesh = obj.data
        candidate_mesh = original_mesh.copy()
        candidate_mesh.name = f"__ordax_hole_candidate_{str(command_id)[:12]}"
        work = bmesh.new()
        loop_vertex_ids = set()
        perimeter = 0.0
        new_face_count = 0
        new_edge_count = 0
        before_boundary_edges = 0
        after_boundary_edges = 0
        try:
            work.from_mesh(candidate_mesh)
            work.verts.ensure_lookup_table()
            work.edges.ensure_lookup_table()
            work.faces.ensure_lookup_table()
            selected_edges = [work.edges[index] for index in edge_indices]
            if not all(edge.is_boundary for edge in selected_edges):
                raise ValueError("boundary hole-fill selection must contain only boundary edges")
            selected_set = set(edge_indices)
            adjacency = {}
            for edge in selected_edges:
                perimeter += float((edge.verts[0].co - edge.verts[1].co).length)
                for vertex in edge.verts:
                    vertex_id = int(vertex.index)
                    loop_vertex_ids.add(vertex_id)
                    adjacency.setdefault(vertex_id, []).append(int(edge.index))
            if len(loop_vertex_ids) != len(selected_edges) or any(len(items) != 2 for items in adjacency.values()):
                raise ValueError("boundary hole-fill selection must form one simple closed edge cycle")
            visited_edges = set()
            stack = [edge_indices[0]]
            while stack:
                current_id = stack.pop()
                if current_id in visited_edges:
                    continue
                visited_edges.add(current_id)
                current = work.edges[current_id]
                for vertex in current.verts:
                    for linked_id in adjacency[int(vertex.index)]:
                        if linked_id not in visited_edges:
                            stack.append(linked_id)
            if visited_edges != selected_set:
                raise ValueError("boundary hole-fill selection must be one connected closed loop")
            if perimeter <= 0.0:
                raise ValueError("boundary hole-fill loop perimeter must be positive")

            vertex_layer = work.verts.layers.int.new("__ordax_original_vertex")
            edge_layer = work.edges.layers.int.new("__ordax_original_edge")
            face_layer = work.faces.layers.int.new("__ordax_original_face")
            for vertex in work.verts:
                vertex[vertex_layer] = int(vertex.index) + 1
            for edge in work.edges:
                edge[edge_layer] = int(edge.index) + 1
            for face in work.faces:
                face[face_layer] = int(face.index) + 1
            original_vertex_ids = {int(vertex[vertex_layer]) for vertex in work.verts}
            original_edge_ids = {int(edge[edge_layer]) for edge in work.edges}
            original_face_ids = {int(face[face_layer]) for face in work.faces}
            before_boundary_edges = sum(1 for edge in work.edges if edge.is_boundary)
            # Creating BMesh custom-data layers invalidates previously held BMEdge
            # references in Blender 5.2. Rebuild the explicit selection from its
            # validated stable indices immediately before invoking holes_fill.
            work.edges.ensure_lookup_table()
            selected_edges = [work.edges[index] for index in edge_indices]
            original_edge_elements = set(work.edges)

            result = bmesh.ops.holes_fill(work, edges=selected_edges, sides=0)
            created_faces = list(result.get("faces") or [])
            if not created_faces:
                raise ValueError("boundary hole-fill selection produced no face")
            new_faces = created_faces
            new_edges = [edge for edge in work.edges if edge not in original_edge_elements]
            new_face_count = len(new_faces)
            new_edge_count = len(new_edges)
            if len(work.verts) != len(original_vertex_ids):
                raise RuntimeError("boundary hole-fill preview created unexpected vertices")
            if not (1 <= new_face_count <= max_new_faces):
                raise RuntimeError("boundary hole-fill preview exceeded new-face budget")
            if {int(vertex[vertex_layer]) for vertex in work.verts} != original_vertex_ids:
                raise RuntimeError("boundary hole-fill preview changed original vertex identity")
            if not original_edge_ids.issubset({int(edge[edge_layer]) for edge in work.edges}):
                raise RuntimeError("boundary hole-fill preview removed an original edge")
            if not original_face_ids.issubset({int(face[face_layer]) for face in work.faces}):
                raise RuntimeError("boundary hole-fill preview removed an original face")
            loop_original_vertex_ids = {vertex_id + 1 for vertex_id in loop_vertex_ids}
            for face in new_faces:
                if not {int(vertex[vertex_layer]) for vertex in face.verts}.issubset(loop_original_vertex_ids):
                    raise RuntimeError("boundary hole-fill preview created a face outside the selected loop")
            for edge in new_edges:
                if not {int(vertex[vertex_layer]) for vertex in edge.verts}.issubset(loop_original_vertex_ids):
                    raise RuntimeError("boundary hole-fill preview created an edge outside the selected loop")
            after_boundary_edges = sum(1 for edge in work.edges if edge.is_boundary)
            if before_boundary_edges - after_boundary_edges != len(edge_indices):
                raise RuntimeError("boundary hole-fill preview did not close exactly the selected boundary loop")

            work.verts.layers.int.remove(vertex_layer)
            work.edges.layers.int.remove(edge_layer)
            work.faces.layers.int.remove(face_layer)
            work.to_mesh(candidate_mesh)
            candidate_mesh.update()
        finally:
            work.free()

        preview_id = str(command_id)
        collection = bpy.data.collections.new(f"__ORDAX_HOLE_PREVIEW_{preview_id[:12]}")
        bpy.context.scene.collection.children.link(collection)
        backup = bpy.data.objects.new(f"__ordax_hole_backup_{preview_id[:12]}", original_mesh)
        collection.objects.link(backup)
        backup.hide_render = True
        backup.hide_set(True)
        backup["ordax_hole_preview_id"] = preview_id
        backup["ordax_hole_backup"] = True
        backup["ordax_hole_target"] = obj.name
        backup["ordax_hole_original_sha256"] = current_sha256

        obj.data = candidate_mesh
        bpy.context.view_layer.update()
        candidate_sha256, candidate_counts = _mesh_fingerprint(obj, evaluated=False)
        obj["ordax_hole_preview_id"] = preview_id
        obj["ordax_hole_preview_state"] = "preview"
        obj["ordax_hole_original_sha256"] = current_sha256
        obj["ordax_hole_candidate_sha256"] = candidate_sha256
        obj["ordax_hole_backup_object"] = backup.name
        _response(
            command_id,
            True,
            "Blender boundary hole-fill preview created for one explicit closed boundary loop",
            operation="boundary_hole_fill_preview",
            preview_id=preview_id,
            state="preview",
            selected_edge_indices=edge_indices,
            loop_vertex_indices=sorted(loop_vertex_ids),
            perimeter=round(perimeter, 12),
            new_faces=new_face_count,
            new_edges=new_edge_count,
            boundary_edges_before=before_boundary_edges,
            boundary_edges_after=after_boundary_edges,
            original_topology_preserved=True,
            before_geometry_sha256=current_sha256,
            candidate_geometry_sha256=candidate_sha256,
            before_mesh=current_mesh_counts,
            candidate_mesh=candidate_counts,
            before=before,
            object=_object_details(obj),
        )
    except Exception as error:
        if obj is not None and original_mesh is not None and candidate_mesh is not None:
            try:
                if obj.data == candidate_mesh:
                    obj.data = original_mesh
                    bpy.context.view_layer.update()
            except Exception:
                pass
        if obj is not None:
            _clear_mesh_repair_preview_metadata(obj, "hole")
        if backup is not None:
            try:
                _remove_mesh_repair_backup(backup)
            except Exception:
                pass
        elif collection is not None:
            try:
                if len(collection.objects) == 0 and len(collection.children) == 0:
                    bpy.data.collections.remove(collection)
            except Exception:
                pass
        if candidate_mesh is not None and getattr(candidate_mesh, "users", 0) == 0:
            try:
                bpy.data.meshes.remove(candidate_mesh)
            except Exception:
                pass
        _response(
            command_id,
            False,
            str(error) if isinstance(error, ValueError) else f"{type(error).__name__}: {error}",
            operation="boundary_hole_fill_preview",
            before=before,
        )


def _modeling_boundary_hole_fill_commit(command: dict) -> None:
    command_id = command["id"]
    try:
        _modeling_runtime_preconditions()
        preview_id = _normalize_preview_id(command)
        target, backup = _mesh_repair_preview_target("hole", preview_id)
        if str(target.get("ordax_hole_preview_state") or "") != "preview":
            raise ValueError("boundary hole-fill preview state is invalid")
        stored_candidate_sha256 = str(target.get("ordax_hole_candidate_sha256") or "")
        stored_original_sha256 = str(target.get("ordax_hole_original_sha256") or "")
        current_candidate_sha256, candidate_counts = _mesh_fingerprint(target, evaluated=False)
        original_sha256, original_counts = _mesh_fingerprint(backup, evaluated=False)
        if current_candidate_sha256 != stored_candidate_sha256:
            raise ValueError("boundary hole-fill candidate changed after preview; start a new preview")
        if original_sha256 != stored_original_sha256:
            raise ValueError("boundary hole-fill original backup changed after preview")
        candidate_mesh = target.data
        original_mesh = backup.data
        original_name = original_mesh.name
        _remove_mesh_repair_backup(backup)
        if getattr(original_mesh, "users", 0) == 0:
            try:
                bpy.data.meshes.remove(original_mesh)
            except Exception:
                pass
        try:
            candidate_mesh.name = original_name
        except Exception:
            pass
        _clear_mesh_repair_preview_metadata(target, "hole")
        bpy.context.view_layer.update()
        _response(
            command_id,
            True,
            "Blender boundary hole-fill preview committed",
            operation="boundary_hole_fill_commit",
            preview_id=preview_id,
            state="committed",
            original_geometry_sha256=stored_original_sha256,
            committed_geometry_sha256=current_candidate_sha256,
            original_mesh=original_counts,
            committed_mesh=candidate_counts,
            object=_object_details(target),
        )
    except Exception as error:
        _response(
            command_id,
            False,
            str(error) if isinstance(error, ValueError) else f"{type(error).__name__}: {error}",
            operation="boundary_hole_fill_commit",
        )


def _modeling_boundary_hole_fill_cancel(command: dict) -> None:
    command_id = command["id"]
    try:
        _modeling_runtime_preconditions()
        preview_id = _normalize_preview_id(command)
        target, backup = _mesh_repair_preview_target("hole", preview_id)
        if str(target.get("ordax_hole_preview_state") or "") != "preview":
            raise ValueError("boundary hole-fill preview state is invalid")
        stored_original_sha256 = str(target.get("ordax_hole_original_sha256") or "")
        original_mesh = backup.data
        original_sha256, original_counts = _mesh_fingerprint(backup, evaluated=False)
        if original_sha256 != stored_original_sha256:
            raise ValueError("boundary hole-fill original backup changed after preview")
        candidate_mesh = target.data
        discarded_sha256, candidate_counts = _mesh_fingerprint(target, evaluated=False)
        target.data = original_mesh
        bpy.context.view_layer.update()
        restored_sha256, restored_counts = _mesh_fingerprint(target, evaluated=False)
        if restored_sha256 != stored_original_sha256:
            target.data = candidate_mesh
            bpy.context.view_layer.update()
            raise RuntimeError("boundary hole-fill cancel could not restore the original geometry fingerprint")
        _clear_mesh_repair_preview_metadata(target, "hole")
        _remove_mesh_repair_backup(backup)
        if candidate_mesh is not original_mesh and getattr(candidate_mesh, "users", 0) == 0:
            try:
                bpy.data.meshes.remove(candidate_mesh)
            except Exception:
                pass
        bpy.context.view_layer.update()
        _response(
            command_id,
            True,
            "Blender boundary hole-fill preview cancelled and original mesh restored",
            operation="boundary_hole_fill_cancel",
            preview_id=preview_id,
            state="cancelled",
            restored_geometry_sha256=restored_sha256,
            discarded_candidate_sha256=discarded_sha256,
            original_mesh=original_counts,
            discarded_candidate_mesh=candidate_counts,
            restored_mesh=restored_counts,
            object=_object_details(target),
        )
    except Exception as error:
        _response(
            command_id,
            False,
            str(error) if isinstance(error, ValueError) else f"{type(error).__name__}: {error}",
            operation="boundary_hole_fill_cancel",
        )


def _material_apply(command: dict) -> None:
    command_id = command["id"]
    obj = None
    before = None
    try:
        _modeling_runtime_preconditions()
        arguments = _normalize_material_request(command, transport_fields={"id", "operation"})
        obj = _resolve_object(arguments)
        if (
            obj.type != "MESH"
            or obj.library is not None
            or obj.override_library is not None
            or getattr(obj.data, "library", None) is not None
        ):
            raise ValueError("material assignment requires an existing local mesh object")

        before = _object_details(obj)
        material = bpy.data.materials.get(arguments["material_name"])
        if material is None:
            material = bpy.data.materials.new(arguments["material_name"])
        material.use_nodes = True

        rgba = arguments["base_color"]
        material.diffuse_color = tuple(rgba)
        nodes = material.node_tree.nodes if material.node_tree else None
        bsdf = nodes.get("Principled BSDF") if nodes else None
        if bsdf is None:
            raise ValueError("Principled BSDF node is unavailable")

        inputs = bsdf.inputs
        if "Base Color" in inputs:
            inputs["Base Color"].default_value = tuple(rgba)
        if "Roughness" in inputs:
            inputs["Roughness"].default_value = arguments["roughness"]
        if "Metallic" in inputs:
            inputs["Metallic"].default_value = arguments["metallic"]
        if "IOR" in inputs:
            inputs["IOR"].default_value = arguments["ior"]
        if "Alpha" in inputs:
            inputs["Alpha"].default_value = arguments["alpha"]
        if "Transmission Weight" in inputs:
            inputs["Transmission Weight"].default_value = arguments["transmission"]
        elif "Transmission" in inputs:
            inputs["Transmission"].default_value = arguments["transmission"]

        if hasattr(material, "surface_render_method"):
            material.surface_render_method = arguments["surface_render_method"]
        elif hasattr(material, "blend_method"):
            try:
                material.blend_method = (
                    "BLEND"
                    if arguments["surface_render_method"] == "BLENDED"
                    else "HASHED"
                )
            except Exception:
                pass

        if hasattr(material, "use_transparency_overlap"):
            material.use_transparency_overlap = arguments["transparency_overlap"]

        obj.data.materials.clear()
        obj.data.materials.append(material)
        bpy.context.view_layer.update()
        _response(
            command_id,
            True,
            "Blender material applied",
            operation="material_apply",
            before=before,
            object=_object_details(obj),
            material={
                "name": material.name,
                "base_color": list(rgba),
                "roughness": arguments["roughness"],
                "metallic": arguments["metallic"],
                "transmission": arguments["transmission"],
                "alpha": arguments["alpha"],
                "ior": arguments["ior"],
                "surface_render_method": arguments["surface_render_method"],
                "transparency_overlap": arguments["transparency_overlap"],
            },
        )
    except Exception as error:
        _response(
            command_id,
            False,
            str(error) if isinstance(error, ValueError) else f"{type(error).__name__}: {error}",
            operation="material_apply",
            before=before,
        )


def _import_asset(command: dict) -> None:
    command_id = command["id"]
    source = None
    created = []
    try:
        _modeling_runtime_preconditions()

        allowed = {"id", "operation", "source_path", "source_size_bytes", "object_name"}
        unsupported = sorted(set(command) - allowed)
        if unsupported:
            raise ValueError("unsupported field(s): " + ", ".join(unsupported))

        raw_source = command.get("source_path")
        if not isinstance(raw_source, str) or not raw_source.strip():
            raise ValueError("source_path is required")
        source = Path(raw_source.strip()).expanduser().resolve()
        home = Path.home().resolve()
        if not _inside(source, home):
            raise ValueError("source_path must be inside the current user's home directory")
        if not source.is_file():
            raise ValueError(f"source asset not found: {source}")
        if source.suffix.lower() not in {".obj", ".glb", ".gltf"}:
            raise ValueError("source asset must be OBJ, GLB, or glTF")
        expected_size = command.get("source_size_bytes")
        if expected_size is not None:
            try:
                expected_size = int(expected_size)
            except (TypeError, ValueError):
                raise ValueError("source_size_bytes must be an integer")
            if expected_size != source.stat().st_size:
                raise ValueError("source asset changed after host validation")

        requested_name = command.get("object_name")
        if requested_name is not None:
            if not isinstance(requested_name, str):
                raise ValueError("object_name must be a string")
            requested_name = requested_name.strip()
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_. -]{0,126}", requested_name):
                raise ValueError("object_name contains unsupported characters")

        before_names = set(bpy.data.objects.keys())
        bpy.ops.object.select_all(action="DESELECT")

        suffix = source.suffix.lower()
        if suffix == ".obj":
            result = bpy.ops.wm.obj_import(filepath=str(source))
        else:
            result = bpy.ops.import_scene.gltf(filepath=str(source))
        if "FINISHED" not in set(result):
            raise RuntimeError(f"Blender import operator returned: {sorted(result)}")

        created = [
            obj
            for obj in bpy.context.scene.objects
            if obj.name not in before_names
        ]
        if not created:
            raise RuntimeError("Blender import created no scene objects")

        if requested_name:
            if len(created) == 1:
                created[0].name = requested_name
                if getattr(created[0], "data", None) is not None:
                    try:
                        created[0].data.name = requested_name
                    except Exception:
                        pass
            else:
                for index, obj in enumerate(created, start=1):
                    obj.name = f"{requested_name}_{index:02d}"

        bpy.context.view_layer.update()
        _response(
            command_id,
            True,
            "Blender asset imported",
            operation="import_asset",
            source_path=str(source),
            source_size_bytes=source.stat().st_size,
            imported_object_count=len(created),
            objects=[_object_details(obj) for obj in created[:100]],
            truncated=len(created) > 100,
        )
    except Exception as error:
        _response(
            command_id,
            False,
            str(error) if isinstance(error, ValueError) else f"{type(error).__name__}: {error}",
            operation="import_asset",
            source_path=str(source) if source is not None else None,
            imported_object_count=len(created),
        )


def _animate_transform(command: dict) -> None:
    command_id = command["id"]
    obj = None
    before = None
    inserted = 0
    try:
        _modeling_runtime_preconditions()
        allowed = {
            "id",
            "operation",
            "object_name",
            "ordax_object_id",
            "keyframes",
            "interpolation",
            "replace_existing",
            "set_scene_range",
            "fps",
        }
        unsupported = sorted(set(command) - allowed)
        if unsupported:
            raise ValueError("unsupported field(s): " + ", ".join(unsupported))

        obj = _resolve_object(command)
        if (
            obj.library is not None
            or obj.override_library is not None
            or getattr(getattr(obj, "data", None), "library", None) is not None
        ):
            raise ValueError("animation requires an existing local object")

        raw_keyframes = command.get("keyframes")
        if not isinstance(raw_keyframes, list) or not (2 <= len(raw_keyframes) <= 64):
            raise ValueError("keyframes must contain 2 to 64 entries")

        interpolation = str(command.get("interpolation", "BEZIER")).strip().upper()
        if interpolation not in {"BEZIER", "LINEAR", "CONSTANT"}:
            raise ValueError("interpolation must be BEZIER, LINEAR, or CONSTANT")

        replace_existing = command.get("replace_existing", True)
        set_scene_range = command.get("set_scene_range", True)
        if not isinstance(replace_existing, bool):
            raise ValueError("replace_existing must be boolean")
        if not isinstance(set_scene_range, bool):
            raise ValueError("set_scene_range must be boolean")

        fps = command.get("fps", 24)
        if isinstance(fps, bool) or not isinstance(fps, int) or not (1 <= fps <= 120):
            raise ValueError("fps must be an integer between 1 and 120")

        normalized = []
        previous_frame = None
        for index, item in enumerate(raw_keyframes):
            if not isinstance(item, dict):
                raise ValueError(f"keyframes[{index}] must be an object")
            unknown = sorted(set(item) - {"frame", "location", "rotation_euler", "scale"})
            if unknown:
                raise ValueError(
                    f"keyframes[{index}] unsupported field(s): " + ", ".join(unknown)
                )
            frame = item.get("frame")
            if isinstance(frame, bool) or not isinstance(frame, int):
                raise ValueError(f"keyframes[{index}].frame must be an integer")
            if frame < 0 or frame > 100000:
                raise ValueError(f"keyframes[{index}].frame must be between 0 and 100000")
            if previous_frame is not None and frame <= previous_frame:
                raise ValueError("keyframe frames must be strictly increasing")
            previous_frame = frame

            values = {"frame": frame}
            for field in ("location", "rotation_euler", "scale"):
                if field not in item:
                    continue
                vector = item[field]
                if not isinstance(vector, list) or len(vector) != 3:
                    raise ValueError(
                        f"keyframes[{index}].{field} must be a list of three finite numbers"
                    )
                converted = []
                for component in vector:
                    if isinstance(component, bool) or not isinstance(component, (int, float)):
                        raise ValueError(
                            f"keyframes[{index}].{field} must contain finite numbers"
                        )
                    numeric = float(component)
                    if not math.isfinite(numeric):
                        raise ValueError(
                            f"keyframes[{index}].{field} must contain finite numbers"
                        )
                    converted.append(numeric)
                if field == "scale" and any(value <= 0.0 or value > 1000.0 for value in converted):
                    raise ValueError(
                        f"keyframes[{index}].scale components must be greater than 0 and at most 1000"
                    )
                if field != "scale" and any(abs(value) > 100000.0 for value in converted):
                    raise ValueError(
                        f"keyframes[{index}].{field} components exceed the allowed range"
                    )
                values[field] = converted
            if len(values) == 1:
                raise ValueError(
                    f"keyframes[{index}] must include location, rotation_euler, or scale"
                )
            normalized.append(values)

        before = _object_details(obj)
        if replace_existing:
            obj.animation_data_clear()

        preferences = getattr(bpy.context, "preferences", None)
        edit_preferences = getattr(preferences, "edit", None) if preferences else None
        old_interpolation = None
        if edit_preferences is not None and hasattr(
            edit_preferences, "keyframe_new_interpolation_type"
        ):
            old_interpolation = edit_preferences.keyframe_new_interpolation_type
            edit_preferences.keyframe_new_interpolation_type = interpolation

        try:
            for keyframe in normalized:
                frame = keyframe["frame"]
                for field in ("location", "rotation_euler", "scale"):
                    if field not in keyframe:
                        continue
                    setattr(obj, field, keyframe[field])
                    if not obj.keyframe_insert(
                        data_path=field,
                        frame=frame,
                        group="OrdaX Transform",
                    ):
                        raise RuntimeError(
                            f"could not insert {field} keyframe at frame {frame}"
                        )
                    inserted += 1
        finally:
            if (
                old_interpolation is not None
                and edit_preferences is not None
            ):
                edit_preferences.keyframe_new_interpolation_type = old_interpolation

        scene = bpy.context.scene
        scene.render.fps = fps
        first_frame = normalized[0]["frame"]
        last_frame = normalized[-1]["frame"]
        if set_scene_range:
            scene.frame_start = first_frame
            scene.frame_end = last_frame
        scene.frame_set(first_frame)
        bpy.context.view_layer.update()

        _response(
            command_id,
            True,
            "Blender transform animation created",
            operation="animate_transform",
            before=before,
            object=_object_details(obj),
            keyframe_count=len(normalized),
            inserted_channels=inserted,
            frame_start=first_frame,
            frame_end=last_frame,
            fps=fps,
            interpolation=interpolation,
            replace_existing=replace_existing,
            set_scene_range=set_scene_range,
        )
    except Exception as error:
        _response(
            command_id,
            False,
            str(error) if isinstance(error, ValueError) else f"{type(error).__name__}: {error}",
            operation="animate_transform",
            before=before,
            inserted_channels=inserted,
        )


def _viewport_proxy(command: dict) -> None:
    command_id = command["id"]
    obj = None
    modifier = None
    try:
        _modeling_runtime_preconditions()
        allowed = {
            "id",
            "operation",
            "object_name",
            "ordax_object_id",
            "target_faces",
            "enabled",
        }
        unsupported = sorted(set(command) - allowed)
        if unsupported:
            raise ValueError("unsupported field(s): " + ", ".join(unsupported))

        obj = _resolve_object(command)
        if (
            obj.type != "MESH"
            or obj.library is not None
            or obj.override_library is not None
            or getattr(obj.data, "library", None) is not None
        ):
            raise ValueError("viewport proxy requires an existing local mesh object")
        if obj.animation_data is not None or len(obj.constraints) > 0:
            raise ValueError(
                "animated or constrained targets require a dedicated proxy workflow"
            )

        enabled = command.get("enabled", True)
        if not isinstance(enabled, bool):
            raise ValueError("enabled must be boolean")
        target_faces = command.get("target_faces", 150000)
        if (
            isinstance(target_faces, bool)
            or not isinstance(target_faces, int)
            or target_faces < 10000
            or target_faces > 500000
        ):
            raise ValueError(
                "target_faces must be an integer between 10000 and 500000"
            )

        modifier_name = "OrdaX_Viewport_Proxy"
        existing = obj.modifiers.get(modifier_name)
        source_faces = len(obj.data.polygons)

        if not enabled:
            if existing is not None:
                obj.modifiers.remove(existing)
            bpy.context.view_layer.update()
            _response(
                command_id,
                True,
                "Blender viewport proxy disabled",
                operation="viewport_proxy",
                enabled=False,
                source_faces=source_faces,
                object=_object_details(obj),
            )
            return

        ratio = 1.0 if source_faces <= 0 else min(
            1.0,
            max(0.01, float(target_faces) / float(source_faces)),
        )

        if existing is not None and existing.type != "DECIMATE":
            raise ValueError(
                "OrdaX viewport proxy name is occupied by a non-DECIMATE modifier"
            )
        modifier = existing or obj.modifiers.new(modifier_name, "DECIMATE")
        modifier.decimate_type = "COLLAPSE"
        modifier.ratio = ratio
        modifier.show_viewport = True
        modifier.show_render = False
        if hasattr(modifier, "show_in_editmode"):
            modifier.show_in_editmode = False

        bpy.context.view_layer.update()
        _response(
            command_id,
            True,
            "Blender viewport proxy enabled",
            operation="viewport_proxy",
            enabled=True,
            source_faces=source_faces,
            target_faces=target_faces,
            estimated_viewport_faces=max(1, int(source_faces * ratio)),
            ratio=ratio,
            render_preserves_source=True,
            object=_object_details(obj),
        )
    except Exception as error:
        _response(
            command_id,
            False,
            str(error) if isinstance(error, ValueError) else f"{type(error).__name__}: {error}",
            operation="viewport_proxy",
        )


def _bake_work_proxy(command: dict) -> None:
    command_id = command["id"]
    source = None
    proxy = None
    try:
        _modeling_runtime_preconditions()
        allowed = {
            "id",
            "operation",
            "object_name",
            "ordax_object_id",
            "proxy_name",
            "target_faces",
            "remove_source",
        }
        unsupported = sorted(set(command) - allowed)
        if unsupported:
            raise ValueError("unsupported field(s): " + ", ".join(unsupported))

        source = _resolve_object(command)
        if (
            source.type != "MESH"
            or source.library is not None
            or source.override_library is not None
            or getattr(source.data, "library", None) is not None
        ):
            raise ValueError("baked work proxy requires an existing local mesh object")
        if source.animation_data is not None or len(source.constraints) > 0:
            raise ValueError(
                "animated or constrained targets require a dedicated proxy workflow"
            )

        proxy_name = str(command.get("proxy_name") or "").strip()
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_. -]{0,126}", proxy_name):
            raise ValueError("proxy_name contains unsupported characters")
        if bpy.data.objects.get(proxy_name) is not None:
            raise ValueError(f"proxy object already exists: {proxy_name}")

        target_faces = command.get("target_faces", 120000)
        if (
            isinstance(target_faces, bool)
            or not isinstance(target_faces, int)
            or target_faces < 10000
            or target_faces > 500000
        ):
            raise ValueError(
                "target_faces must be an integer between 10000 and 500000"
            )
        remove_source = command.get("remove_source", False)
        if not isinstance(remove_source, bool):
            raise ValueError("remove_source must be boolean")

        unsupported_modifiers = [
            modifier.name
            for modifier in source.modifiers
            if modifier.name != "OrdaX_Viewport_Proxy"
        ]
        if unsupported_modifiers:
            raise ValueError(
                "source has unsupported modifiers for baked proxy: "
                + ", ".join(unsupported_modifiers)
            )

        source_faces = len(source.data.polygons)
        if source_faces <= 0:
            raise ValueError("source mesh has no polygons")

        proxy = source.copy()
        proxy.data = source.data.copy()
        proxy.name = proxy_name
        proxy.data.name = proxy_name
        for collection in list(source.users_collection):
            collection.objects.link(proxy)

        for modifier in list(proxy.modifiers):
            proxy.modifiers.remove(modifier)

        ratio = min(
            1.0,
            max(0.005, float(target_faces) / float(source_faces)),
        )
        modifier = proxy.modifiers.new("OrdaX_Baked_Work_Proxy", "DECIMATE")
        modifier.decimate_type = "COLLAPSE"
        modifier.ratio = ratio
        modifier.show_viewport = True
        modifier.show_render = True

        bpy.ops.object.select_all(action="DESELECT")
        proxy.select_set(True)
        bpy.context.view_layer.objects.active = proxy
        apply_result = bpy.ops.object.modifier_apply(modifier=modifier.name)
        if "FINISHED" not in set(apply_result):
            raise RuntimeError(
                f"Blender modifier apply returned: {sorted(apply_result)}"
            )

        baked_faces = len(proxy.data.polygons)
        proxy["ordax_work_proxy"] = True
        proxy["ordax_proxy_source_name"] = source.name
        proxy["ordax_proxy_source_faces"] = int(source_faces)
        proxy["ordax_proxy_target_faces"] = int(target_faces)
        proxy["ordax_proxy_baked_faces"] = int(baked_faces)

        source_name = source.name
        source_transform = {
            "location": _round_vector(source.location),
            "rotation_euler": _round_vector(source.rotation_euler),
            "scale": _round_vector(source.scale),
        }

        if remove_source:
            source_mesh = source.data
            bpy.data.objects.remove(source, do_unlink=True)
            source = None
            if source_mesh.users == 0:
                bpy.data.meshes.remove(source_mesh)

        bpy.context.view_layer.update()
        _response(
            command_id,
            True,
            "Blender baked work proxy created",
            operation="bake_work_proxy",
            source_name=source_name,
            source_faces=source_faces,
            target_faces=target_faces,
            baked_faces=baked_faces,
            ratio=ratio,
            source_removed=remove_source,
            source_transform=source_transform,
            object=_object_details(proxy),
        )
    except Exception as error:
        if proxy is not None:
            try:
                proxy_mesh = proxy.data
                bpy.data.objects.remove(proxy, do_unlink=True)
                if proxy_mesh is not None and proxy_mesh.users == 0:
                    bpy.data.meshes.remove(proxy_mesh)
            except Exception:
                pass
        _response(
            command_id,
            False,
            str(error) if isinstance(error, ValueError) else f"{type(error).__name__}: {error}",
            operation="bake_work_proxy",
        )


def _look_at_rotation(location, target):
    location_v = Vector(location)
    target_v = Vector(target)
    direction = target_v - location_v
    if direction.length <= 1e-9:
        raise ValueError("target must differ from location")
    return direction.to_track_quat("-Z", "Y").to_euler()


def _create_camera(command: dict) -> None:
    command_id = command["id"]
    obj = None
    try:
        allowed = {
            "id",
            "operation",
            "name",
            "location",
            "target",
            "lens_mm",
            "sensor_width_mm",
            "clip_start",
            "clip_end",
            "make_active",
        }
        unsupported = sorted(set(command) - allowed)
        if unsupported:
            raise ValueError("unsupported field(s): " + ", ".join(unsupported))

        name = str(command.get("name") or "").strip()
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_. -]{0,126}", name):
            raise ValueError("name contains unsupported characters")
        location = command.get("location")
        target = command.get("target")
        if not isinstance(location, list) or len(location) != 3:
            raise ValueError("location must be a list of three numbers")
        if not isinstance(target, list) or len(target) != 3:
            raise ValueError("target must be a list of three numbers")

        existing = bpy.data.objects.get(name)
        if existing is not None and existing.type != "CAMERA":
            raise ValueError("camera name is occupied by a non-camera object")

        if existing is None:
            data = bpy.data.cameras.new(f"{name}_Data")
            obj = bpy.data.objects.new(name, data)
            bpy.context.scene.collection.objects.link(obj)
        else:
            obj = existing
            data = obj.data

        obj.location = [float(v) for v in location]
        obj.rotation_euler = _look_at_rotation(location, target)
        data.lens = float(command.get("lens_mm", 50.0))
        data.sensor_width = float(command.get("sensor_width_mm", 36.0))
        data.clip_start = float(command.get("clip_start", 0.001))
        data.clip_end = float(command.get("clip_end", 100.0))
        if bool(command.get("make_active", True)):
            bpy.context.scene.camera = obj

        bpy.context.view_layer.update()
        _response(
            command_id,
            True,
            "Blender camera ready",
            operation="create_camera",
            object=_object_details(obj),
            camera={
                "lens_mm": float(data.lens),
                "sensor_width_mm": float(data.sensor_width),
                "clip_start": float(data.clip_start),
                "clip_end": float(data.clip_end),
                "active": bpy.context.scene.camera == obj,
                "target": [float(v) for v in target],
            },
        )
    except Exception as error:
        _response(
            command_id,
            False,
            str(error) if isinstance(error, ValueError) else f"{type(error).__name__}: {error}",
            operation="create_camera",
            object=_object_details(obj) if obj is not None else None,
        )


def _create_light(command: dict) -> None:
    command_id = command["id"]
    obj = None
    try:
        allowed = {
            "id",
            "operation",
            "name",
            "light_type",
            "location",
            "target",
            "energy",
            "color",
            "size",
            "angle_degrees",
            "spot_size_degrees",
            "spot_blend",
        }
        unsupported = sorted(set(command) - allowed)
        if unsupported:
            raise ValueError("unsupported field(s): " + ", ".join(unsupported))

        name = str(command.get("name") or "").strip()
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_. -]{0,126}", name):
            raise ValueError("name contains unsupported characters")
        light_type = str(command.get("light_type") or "AREA").strip().upper()
        if light_type not in {"AREA", "POINT", "SUN", "SPOT"}:
            raise ValueError("light_type must be AREA, POINT, SUN, or SPOT")
        location = command.get("location")
        target = command.get("target")
        if not isinstance(location, list) or len(location) != 3:
            raise ValueError("location must be a list of three numbers")
        if not isinstance(target, list) or len(target) != 3:
            raise ValueError("target must be a list of three numbers")

        existing = bpy.data.objects.get(name)
        if existing is not None and existing.type != "LIGHT":
            raise ValueError("light name is occupied by a non-light object")
        if existing is not None and existing.data.type != light_type:
            raise ValueError("existing light type does not match requested light_type")

        if existing is None:
            data = bpy.data.lights.new(f"{name}_Data", type=light_type)
            obj = bpy.data.objects.new(name, data)
            bpy.context.scene.collection.objects.link(obj)
        else:
            obj = existing
            data = obj.data

        obj.location = [float(v) for v in location]
        if light_type in {"AREA", "SUN", "SPOT"}:
            obj.rotation_euler = _look_at_rotation(location, target)

        data.energy = float(command.get("energy", 100.0))
        data.color = tuple(float(v) for v in command.get("color", [1.0, 1.0, 1.0]))

        if light_type == "AREA":
            data.shape = "DISK"
            data.size = float(command.get("size", 0.1))
        elif light_type == "SUN":
            data.angle = math.radians(float(command.get("angle_degrees", 0.526)))
        elif light_type == "SPOT":
            data.spot_size = math.radians(float(command.get("spot_size_degrees", 45.0)))
            data.spot_blend = float(command.get("spot_blend", 0.15))
            data.shadow_soft_size = float(command.get("size", 0.1))
        elif light_type == "POINT":
            data.shadow_soft_size = float(command.get("size", 0.1))

        bpy.context.view_layer.update()
        _response(
            command_id,
            True,
            "Blender light ready",
            operation="create_light",
            object=_object_details(obj),
            light={
                "type": light_type,
                "energy": float(data.energy),
                "color": [float(v) for v in data.color],
                "target": [float(v) for v in target],
            },
        )
    except Exception as error:
        _response(
            command_id,
            False,
            str(error) if isinstance(error, ValueError) else f"{type(error).__name__}: {error}",
            operation="create_light",
            object=_object_details(obj) if obj is not None else None,
        )


def _scene_presentation(command: dict) -> None:
    command_id = command["id"]
    try:
        allowed = {
            "id",
            "operation",
            "render_engine",
            "resolution_x",
            "resolution_y",
            "resolution_percentage",
            "world_color",
            "world_strength",
            "transparent_film",
        }
        unsupported = sorted(set(command) - allowed)
        if unsupported:
            raise ValueError("unsupported field(s): " + ", ".join(unsupported))

        scene = bpy.context.scene
        engine = str(command.get("render_engine") or "BLENDER_EEVEE").strip().upper()
        try:
            scene.render.engine = engine
        except Exception as error:
            raise ValueError(f"render engine unavailable: {engine}") from error

        scene.render.resolution_x = int(command.get("resolution_x", 1080))
        scene.render.resolution_y = int(command.get("resolution_y", 1080))
        scene.render.resolution_percentage = int(command.get("resolution_percentage", 100))
        scene.render.film_transparent = bool(command.get("transparent_film", False))

        world = scene.world
        if world is None:
            world = bpy.data.worlds.new("OrdaX_World")
            scene.world = world
        world.use_nodes = True
        background = world.node_tree.nodes.get("Background") if world.node_tree else None
        if background is None:
            raise RuntimeError("World Background node is unavailable")
        color = [float(v) for v in command.get("world_color", [0.035, 0.035, 0.035])]
        background.inputs["Color"].default_value = (*color, 1.0)
        background.inputs["Strength"].default_value = float(command.get("world_strength", 0.35))

        _response(
            command_id,
            True,
            "Blender scene presentation configured",
            operation="scene_presentation",
            render_engine=scene.render.engine,
            resolution=[
                int(scene.render.resolution_x),
                int(scene.render.resolution_y),
                int(scene.render.resolution_percentage),
            ],
            transparent_film=bool(scene.render.film_transparent),
            world_color=color,
            world_strength=float(background.inputs["Strength"].default_value),
            active_camera=scene.camera.name if scene.camera else None,
        )
    except Exception as error:
        _response(
            command_id,
            False,
            str(error) if isinstance(error, ValueError) else f"{type(error).__name__}: {error}",
            operation="scene_presentation",
        )


_OBJECT_METADATA_KEYS = frozenset(
    {
        "ordax_asset",
        "ordax_component",
        "ordax_role",
        "ordax_object_id",
        "ordax_standard_version",
        "ordax_source_provider",
        "ordax_source_asset_id",
        "ordax_source_license",
        "ordax_source_url",
    }
)


def _object_metadata(command: dict) -> None:
    command_id = command["id"]
    try:
        obj = _resolve_object(command)
    except ValueError as error:
        _response(command_id, False, str(error))
        return

    metadata = command.get("metadata")
    if not isinstance(metadata, dict) or not metadata:
        _response(command_id, False, "metadata must be a non-empty object")
        return

    unsupported = sorted(set(metadata) - _OBJECT_METADATA_KEYS)
    if unsupported:
        _response(
            command_id,
            False,
            "unsupported metadata key(s): " + ", ".join(unsupported),
        )
        return

    normalized = {}
    for key, value in metadata.items():
        if value is None:
            normalized[key] = None
            continue
        if not isinstance(value, (str, int, float, bool)):
            _response(command_id, False, f"{key} must be a scalar or null")
            return
        if isinstance(value, str) and len(value) > 1024:
            _response(command_id, False, f"{key} exceeds 1024 characters")
            return
        normalized[key] = value

    new_id = normalized.get("ordax_object_id")
    if isinstance(new_id, str) and new_id:
        duplicate = next(
            (
                candidate
                for candidate in bpy.context.scene.objects
                if candidate != obj
                and str(candidate.get("ordax_object_id") or "") == new_id
            ),
            None,
        )
        if duplicate is not None:
            _response(
                command_id,
                False,
                f"ordax_object_id already belongs to {duplicate.name}",
            )
            return

    before = _object_details(obj)
    for key, value in normalized.items():
        if value is None:
            try:
                del obj[key]
            except KeyError:
                pass
        else:
            obj[key] = value

    _response(
        command_id,
        True,
        "Blender object metadata updated",
        before=before,
        object=_object_details(obj),
    )



_QUALITY_TYPES = {"dimensions", "symmetry", "proportion", "containment", "mesh_quality", "uv_quality"}


def _quality_object(name, field: str):
    object_name = str(name or "").strip()
    if not object_name:
        raise ValueError(f"{field} is required")
    obj = bpy.context.scene.objects.get(object_name)
    if obj is None:
        raise ValueError(f"{field} was not found: {object_name}")
    return obj


def _world_extents(obj) -> dict:
    bounds = _world_bounds(obj)
    mins = bounds.get("aabb_min")
    maxs = bounds.get("aabb_max")
    if not mins or not maxs:
        raise ValueError(f"could not measure world bounds for {obj.name}")
    dimensions = [float(maxs[i]) - float(mins[i]) for i in range(3)]
    center = [(float(maxs[i]) + float(mins[i])) / 2.0 for i in range(3)]
    return {
        "min": _round_vector(mins),
        "max": _round_vector(maxs),
        "dimensions": _round_vector(dimensions),
        "center": _round_vector(center),
    }


def _quality_dimensions(check: dict) -> dict:
    obj = _quality_object(check.get("object_name"), "object_name")
    expected = check.get("expected")
    if (
        not isinstance(expected, list)
        or len(expected) != 3
        or not all(isinstance(item, (int, float)) for item in expected)
    ):
        raise ValueError("dimensions.expected must contain exactly three numbers")

    tolerance = _quality_tolerance(check)
    world_space = bool(check.get("world_space", False))
    actual = (
        _world_extents(obj)["dimensions"]
        if world_space
        else _round_vector(obj.dimensions)
    )
    expected_values = [float(item) for item in expected]
    errors = [abs(float(actual[i]) - expected_values[i]) for i in range(3)]
    passed = all(error <= tolerance for error in errors)
    return {
        "type": "dimensions",
        "passed": passed,
        "object_name": obj.name,
        "world_space": world_space,
        "expected": _round_vector(expected_values),
        "actual": _round_vector(actual),
        "absolute_error": _round_vector(errors),
        "max_error": round(max(errors), 6),
        "tolerance": tolerance,
    }


def _quality_symmetry(check: dict) -> dict:
    left = _quality_object(check.get("left_object"), "left_object")
    right = _quality_object(check.get("right_object"), "right_object")
    axis, axis_index = _quality_axis(check.get("axis"), "axis")
    tolerance = _quality_tolerance(check)
    try:
        mirror_coordinate = float(check.get("mirror_coordinate", 0.0))
    except (TypeError, ValueError):
        raise ValueError("mirror_coordinate must be a number")

    left_bounds = _world_extents(left)
    right_bounds = _world_extents(right)
    left_center = left_bounds["center"]
    right_center = right_bounds["center"]
    left_dimensions = left_bounds["dimensions"]
    right_dimensions = right_bounds["dimensions"]

    center_errors = []
    for index in range(3):
        if index == axis_index:
            expected_left = (2.0 * mirror_coordinate) - float(right_center[index])
            center_errors.append(abs(float(left_center[index]) - expected_left))
        else:
            center_errors.append(abs(float(left_center[index]) - float(right_center[index])))
    dimension_errors = [
        abs(float(left_dimensions[index]) - float(right_dimensions[index]))
        for index in range(3)
    ]
    max_error = max(center_errors + dimension_errors)
    return {
        "type": "symmetry",
        "passed": max_error <= tolerance,
        "left_object": left.name,
        "right_object": right.name,
        "axis": axis,
        "mirror_coordinate": mirror_coordinate,
        "left_center": left_center,
        "right_center": right_center,
        "center_error": _round_vector(center_errors),
        "dimension_error": _round_vector(dimension_errors),
        "max_error": round(max_error, 6),
        "tolerance": tolerance,
    }


def _quality_proportion(check: dict) -> dict:
    obj = _quality_object(check.get("object_name"), "object_name")
    axis_a, axis_a_index = _quality_axis(check.get("axis_a"), "axis_a")
    tolerance = _quality_tolerance(check, default=0.02)
    try:
        expected_ratio = float(check.get("expected_ratio"))
    except (TypeError, ValueError):
        raise ValueError("expected_ratio must be a number")
    if not (expected_ratio >= 0):
        raise ValueError("expected_ratio must be non-negative")

    world_space = bool(check.get("world_space", False))
    object_dimensions = (
        _world_extents(obj)["dimensions"]
        if world_space
        else _round_vector(obj.dimensions)
    )
    numerator = float(object_dimensions[axis_a_index])

    reference_name = str(check.get("reference_object") or "").strip()
    if reference_name:
        reference = _quality_object(reference_name, "reference_object")
        reference_axis, reference_axis_index = _quality_axis(
            check.get("reference_axis"), "reference_axis"
        )
        reference_dimensions = (
            _world_extents(reference)["dimensions"]
            if world_space
            else _round_vector(reference.dimensions)
        )
        denominator = float(reference_dimensions[reference_axis_index])
        denominator_label = f"{reference.name}.{reference_axis}"
    else:
        axis_b, axis_b_index = _quality_axis(check.get("axis_b"), "axis_b")
        denominator = float(object_dimensions[axis_b_index])
        denominator_label = f"{obj.name}.{axis_b}"

    if abs(denominator) <= 1e-12:
        raise ValueError("proportion denominator is zero")

    actual_ratio = numerator / denominator
    error = abs(actual_ratio - expected_ratio)
    return {
        "type": "proportion",
        "passed": error <= tolerance,
        "object_name": obj.name,
        "numerator": f"{obj.name}.{axis_a}",
        "denominator": denominator_label,
        "world_space": world_space,
        "expected_ratio": expected_ratio,
        "actual_ratio": round(actual_ratio, 6),
        "absolute_error": round(error, 6),
        "tolerance": tolerance,
    }


def _quality_containment(check: dict) -> dict:
    inner = _quality_object(check.get("inner_object"), "inner_object")
    outer = _quality_object(check.get("outer_object"), "outer_object")
    tolerance = _quality_tolerance(check)
    try:
        min_clearance = float(check.get("min_clearance", 0.0))
    except (TypeError, ValueError):
        raise ValueError("min_clearance must be a number")
    if min_clearance < 0:
        raise ValueError("min_clearance must be non-negative")

    inner_bounds = _world_extents(inner)
    outer_bounds = _world_extents(outer)
    lower_clearance = [
        float(inner_bounds["min"][i]) - float(outer_bounds["min"][i])
        for i in range(3)
    ]
    upper_clearance = [
        float(outer_bounds["max"][i]) - float(inner_bounds["max"][i])
        for i in range(3)
    ]
    clearances = lower_clearance + upper_clearance
    minimum_actual = min(clearances)
    deficit = max(0.0, min_clearance - minimum_actual)
    passed = minimum_actual + tolerance >= min_clearance
    return {
        "type": "containment",
        "passed": passed,
        "method": "world_aabb",
        "inner_object": inner.name,
        "outer_object": outer.name,
        "required_clearance": min_clearance,
        "minimum_actual_clearance": round(minimum_actual, 6),
        "clearance_deficit": round(deficit, 6),
        "lower_clearance": _round_vector(lower_clearance),
        "upper_clearance": _round_vector(upper_clearance),
        "tolerance": tolerance,
    }




def _quality_mesh(check: dict) -> dict:
    obj = _quality_object(check.get("object_name"), "object_name")
    if obj.type != "MESH":
        raise ValueError(f"mesh_quality requires a MESH object: {obj.name}")

    evaluated = check.get("evaluated", True)
    if not isinstance(evaluated, bool):
        raise ValueError("evaluated must be boolean")
    try:
        raw_epsilon = check.get("epsilon", 1e-10)
        if isinstance(raw_epsilon, bool):
            raise ValueError
        epsilon = float(raw_epsilon)
    except (TypeError, ValueError):
        raise ValueError("epsilon must be a number")
    if not math.isfinite(epsilon) or epsilon <= 0 or epsilon > 1.0:
        raise ValueError("epsilon must be greater than 0 and at most 1")
    for flag in ("require_uv", "require_material", "require_manifold"):
        if flag in check and not isinstance(check[flag], bool):
            raise ValueError(f"{flag} must be boolean")
    diagnostic_limit = check.get("diagnostic_limit", 8)
    if (
        not isinstance(diagnostic_limit, int)
        or isinstance(diagnostic_limit, bool)
        or diagnostic_limit < 0
        or diagnostic_limit > 16
    ):
        raise ValueError("diagnostic_limit must be an integer between 0 and 16")

    depsgraph = bpy.context.evaluated_depsgraph_get()
    evaluated_obj = None
    mesh = None
    must_clear = False
    bm = None
    try:
        if evaluated:
            evaluated_obj = obj.evaluated_get(depsgraph)
            mesh = evaluated_obj.to_mesh()
            must_clear = True
        else:
            mesh = obj.data

        if mesh is None:
            raise ValueError(f"mesh data is unavailable for {obj.name}")

        try:
            mesh.calc_loop_triangles()
        except Exception:
            pass

        bm = bmesh.new()
        bm.from_mesh(mesh)
        bm.verts.ensure_lookup_table()
        bm.edges.ensure_lookup_table()
        bm.faces.ensure_lookup_table()
        bm.verts.index_update()
        bm.edges.index_update()
        bm.faces.index_update()

        triangle_count = len(getattr(mesh, "loop_triangles", []))
        face_count = len(bm.faces)

        def local_coordinate(coordinate) -> list[float | None]:
            values = []
            for component in coordinate:
                value = float(component)
                values.append(round(value, 6) if math.isfinite(value) else None)
            return values

        def edge_example(edge) -> dict:
            return {
                "edge_index": int(edge.index),
                "vertex_indices": [int(vertex.index) for vertex in edge.verts],
                "local_endpoints": [
                    local_coordinate(vertex.co) for vertex in edge.verts
                ],
                "linked_faces": len(edge.link_faces),
            }

        def face_example(face) -> dict:
            vertex_indices = []
            for vertex in face.verts:
                if len(vertex_indices) >= 32:
                    break
                vertex_indices.append(int(vertex.index))
            return {
                "face_index": int(face.index),
                "vertex_count": len(face.verts),
                "vertex_indices": vertex_indices,
                "vertex_indices_truncated": len(vertex_indices) < len(face.verts),
                "local_center": local_coordinate(face.calc_center_median()),
            }

        diagnostic_categories = (
            "boundary_edges",
            "boundary_loops",
            "wire_edges",
            "non_manifold_edges",
            "loose_vertices",
            "non_finite_vertices",
            "connected_components",
            "ngon_faces",
            "degenerate_faces",
            "zero_length_edges",
        )
        if diagnostic_limit:
            diagnostics = {
                category: {"total": 0, "examples": []}
                for category in diagnostic_categories
            }
        else:
            diagnostics = {}

        def record_sample(category: str, sample_factory) -> None:
            bucket = diagnostics.get(category)
            if bucket is None:
                return
            bucket["total"] += 1
            if len(bucket["examples"]) < diagnostic_limit:
                bucket["examples"].append(sample_factory())

        boundary_edges = 0
        boundary_edge_objects = []
        boundary_loops = 0
        boundary_loop_examples = []
        wire_edges = 0
        non_manifold_edges = 0
        zero_length_edges = 0
        # Union-find groups every vertex connected by an edge while keeping
        # auxiliary memory proportional to the vertex count.
        component_parent = [-1] * len(bm.verts)

        def component_root(index: int) -> int:
            root = index
            while component_parent[root] >= 0:
                root = component_parent[root]
            while index != root:
                parent = component_parent[index]
                component_parent[index] = root
                index = parent
            return root

        def join_component(first_index: int, second_index: int) -> None:
            first_root = component_root(first_index)
            second_root = component_root(second_index)
            if first_root == second_root:
                return
            if component_parent[first_root] > component_parent[second_root]:
                first_root, second_root = second_root, first_root
            component_parent[first_root] += component_parent[second_root]
            component_parent[second_root] = first_root

        for edge in bm.edges:
            join_component(edge.verts[0].index, edge.verts[1].index)
            if edge.is_boundary:
                boundary_edges += 1
                boundary_edge_objects.append(edge)
                record_sample("boundary_edges", lambda: edge_example(edge))
            if edge.is_wire:
                wire_edges += 1
                record_sample("wire_edges", lambda: edge_example(edge))
            if not edge.is_manifold:
                non_manifold_edges += 1
                record_sample("non_manifold_edges", lambda: edge_example(edge))
            length = float((edge.verts[0].co - edge.verts[1].co).length)
            if length <= epsilon:
                zero_length_edges += 1
                record_sample(
                    "zero_length_edges",
                    lambda: {**edge_example(edge), "length": round(length, 12)},
                )

        if boundary_edge_objects:
            boundary_vertex_edges = {}
            for boundary_edge in boundary_edge_objects:
                for boundary_vertex in boundary_edge.verts:
                    boundary_vertex_edges.setdefault(int(boundary_vertex.index), []).append(boundary_edge)
            remaining_boundary_edges = {int(edge.index): edge for edge in boundary_edge_objects}
            boundary_component_index = 0
            max_hole_edges = int(getattr(_MODELING_CONTRACTS, "MAX_HOLE_FILL_BOUNDARY_EDGES", 32))
            while remaining_boundary_edges:
                seed_index = min(remaining_boundary_edges)
                stack = [remaining_boundary_edges.pop(seed_index)]
                component_edges = []
                component_edge_ids = set()
                component_vertex_ids = set()
                while stack:
                    current_edge = stack.pop()
                    current_edge_id = int(current_edge.index)
                    if current_edge_id in component_edge_ids:
                        continue
                    component_edge_ids.add(current_edge_id)
                    component_edges.append(current_edge)
                    for current_vertex in current_edge.verts:
                        vertex_id = int(current_vertex.index)
                        component_vertex_ids.add(vertex_id)
                        for linked_edge in boundary_vertex_edges.get(vertex_id, []):
                            linked_id = int(linked_edge.index)
                            pending = remaining_boundary_edges.pop(linked_id, None)
                            if pending is not None:
                                stack.append(pending)
                degrees = {vertex_id: 0 for vertex_id in component_vertex_ids}
                for component_edge in component_edges:
                    for component_vertex in component_edge.verts:
                        degrees[int(component_vertex.index)] += 1
                closed_simple = (
                    len(component_edges) >= 3
                    and len(component_edges) == len(component_vertex_ids)
                    and all(degree == 2 for degree in degrees.values())
                )
                if closed_simple:
                    boundary_loops += 1
                    if diagnostic_limit and len(boundary_loop_examples) < diagnostic_limit:
                        ordered_edge_ids = sorted(component_edge_ids)
                        ordered_vertex_ids = sorted(component_vertex_ids)
                        perimeter = sum(float((edge.verts[0].co - edge.verts[1].co).length) for edge in component_edges)
                        coordinates = [bm.verts[index].co for index in ordered_vertex_ids]
                        local_min = [min(float(co[axis]) for co in coordinates) for axis in range(3)]
                        local_max = [max(float(co[axis]) for co in coordinates) for axis in range(3)]
                        boundary_loop_examples.append({
                            "loop_index": int(boundary_loops - 1),
                            "component_index": int(boundary_component_index),
                            "edge_count": len(ordered_edge_ids),
                            "vertex_count": len(ordered_vertex_ids),
                            "edge_indices": ordered_edge_ids[:max_hole_edges],
                            "edge_indices_truncated": len(ordered_edge_ids) > max_hole_edges,
                            "vertex_indices": ordered_vertex_ids[:max_hole_edges],
                            "vertex_indices_truncated": len(ordered_vertex_ids) > max_hole_edges,
                            "perimeter": round(perimeter, 12),
                            "local_bounds": {
                                "min": local_coordinate(local_min),
                                "max": local_coordinate(local_max),
                            },
                        })
                boundary_component_index += 1
            bucket = diagnostics.get("boundary_loops")
            if bucket is not None:
                bucket["total"] = boundary_loops
                bucket["examples"] = boundary_loop_examples

        loose_vertices = 0
        non_finite_vertices = 0
        connected_component_count = sum(
            1 for component_size in component_parent if component_size < 0
        )
        component_summaries = {}
        for vertex in bm.verts:
            if not vertex.link_edges:
                loose_vertices += 1
                record_sample(
                    "loose_vertices",
                    lambda: {
                        "vertex_index": int(vertex.index),
                        "local_coordinate": local_coordinate(vertex.co),
                    },
                )
            if any(not math.isfinite(float(component)) for component in vertex.co):
                non_finite_vertices += 1
                record_sample(
                    "non_finite_vertices",
                    lambda: {
                        "vertex_index": int(vertex.index),
                        "local_coordinate": local_coordinate(vertex.co),
                    },
                )
            if diagnostic_limit:
                root = component_root(vertex.index)
                summary = component_summaries.get(root)
                if summary is None and len(component_summaries) < diagnostic_limit:
                    summary = {
                        "seed_vertex_index": int(vertex.index),
                        "vertex_count": 0,
                        "edge_count": 0,
                        "face_count": 0,
                        "boundary_edge_count": 0,
                        "finite_bounds_vertex_count": 0,
                        "local_min": [math.inf, math.inf, math.inf],
                        "local_max": [-math.inf, -math.inf, -math.inf],
                    }
                    component_summaries[root] = summary
                if summary is not None:
                    summary["vertex_count"] += 1
                    coordinates = [float(component) for component in vertex.co]
                    if all(math.isfinite(component) for component in coordinates):
                        summary["finite_bounds_vertex_count"] += 1
                        for axis, coordinate in enumerate(coordinates):
                            summary["local_min"][axis] = min(
                                summary["local_min"][axis], coordinate
                            )
                            summary["local_max"][axis] = max(
                                summary["local_max"][axis], coordinate
                            )

        if component_summaries:
            for edge in bm.edges:
                summary = component_summaries.get(
                    component_root(edge.verts[0].index)
                )
                if summary is not None:
                    summary["edge_count"] += 1
                    if edge.is_boundary:
                        summary["boundary_edge_count"] += 1

            for face in bm.faces:
                summary = component_summaries.get(
                    component_root(face.verts[0].index)
                )
                if summary is not None:
                    summary["face_count"] += 1

            bucket = diagnostics.get("connected_components")
            if bucket is not None:
                bucket["total"] = connected_component_count
                ordered_components = sorted(
                    component_summaries.values(),
                    key=lambda component: component["seed_vertex_index"],
                )
                for component_index, component in enumerate(ordered_components):
                    finite_bounds_count = component.pop(
                        "finite_bounds_vertex_count"
                    )
                    local_min = component.pop("local_min")
                    local_max = component.pop("local_max")
                    component["component_index"] = component_index
                    component["bounds_complete"] = (
                        finite_bounds_count == component["vertex_count"]
                    )
                    component["local_bounds"] = {
                        "min": local_coordinate(local_min),
                        "max": local_coordinate(local_max),
                    }
                    bucket["examples"].append(component)

        triangle_faces = 0
        quad_faces = 0
        ngon_faces = 0
        degenerate_faces = 0
        for face in bm.faces:
            vertex_count = len(face.verts)
            if vertex_count == 3:
                triangle_faces += 1
            elif vertex_count == 4:
                quad_faces += 1
            elif vertex_count > 4:
                ngon_faces += 1
                record_sample("ngon_faces", lambda: face_example(face))
            area = float(face.calc_area())
            if area <= epsilon:
                degenerate_faces += 1
                record_sample(
                    "degenerate_faces",
                    lambda: {**face_example(face), "area": round(area, 12)},
                )

        for bucket in diagnostics.values():
            bucket["truncated"] = bucket["total"] > len(bucket["examples"])

        quad_ratio = (quad_faces / face_count) if face_count else 0.0
        uv_layers = len(getattr(mesh, "uv_layers", []))
        material_slots = len(getattr(obj, "material_slots", []))

        metrics = {
            "vertices": len(bm.verts),
            "edges": len(bm.edges),
            "faces": face_count,
            "triangles": triangle_count,
            "triangle_faces": triangle_faces,
            "quad_faces": quad_faces,
            "ngon_faces": ngon_faces,
            "quad_ratio": round(quad_ratio, 6),
            "boundary_edges": boundary_edges,
            "boundary_loops": boundary_loops,
            "wire_edges": wire_edges,
            "non_manifold_edges": non_manifold_edges,
            "loose_vertices": loose_vertices,
            "non_finite_vertices": non_finite_vertices,
            "connected_components": connected_component_count,
            "degenerate_faces": degenerate_faces,
            "zero_length_edges": zero_length_edges,
            "uv_layers": uv_layers,
            "material_slots": material_slots,
        }

        base_geometry_sha256, base_mesh_counts = _mesh_fingerprint(obj, evaluated=False)
        automatic_repair_blockers = []
        if evaluated:
            automatic_repair_blockers.append("diagnostic_must_use_base_mesh")
        if (
            obj.library is not None
            or obj.override_library is not None
            or getattr(obj.data, "library", None) is not None
        ):
            automatic_repair_blockers.append("local_nonlinked_mesh_required")
        if getattr(obj.data, "users", 0) != 1:
            automatic_repair_blockers.append("single_user_mesh_data_required")
        if getattr(obj.data, "shape_keys", None) is not None:
            automatic_repair_blockers.append("shape_keys_not_supported")
        if len(obj.modifiers) > 0:
            automatic_repair_blockers.append("modifiers_require_manual_review")
        if obj.animation_data is not None or len(obj.constraints) > 0:
            automatic_repair_blockers.append("animation_or_constraints_require_manual_review")
        if not base_geometry_sha256:
            automatic_repair_blockers.append("base_geometry_fingerprint_unavailable")
        repair_hints = []
        degenerate_preview_blockers = list(automatic_repair_blockers)
        max_repair_distance = float(
            getattr(_MODELING_CONTRACTS, "MAX_DEGENERATE_REPAIR_DISTANCE", 0.001)
        )
        max_repair_elements = int(
            getattr(_MODELING_CONTRACTS, "MAX_DEGENERATE_REPAIR_ELEMENTS", 10000)
        )
        if epsilon > max_repair_distance:
            degenerate_preview_blockers.append("diagnostic_epsilon_exceeds_preview_limit")
        if zero_length_edges + degenerate_faces > max_repair_elements:
            degenerate_preview_blockers.append("diagnostic_element_limit_exceeded")

        def add_repair_hint(
            code: str,
            category: str,
            count: int,
            severity: str,
            recommendation: str,
            *,
            auto_fix: bool = False,
        ) -> None:
            if count <= 0:
                return
            hint = {
                "code": code,
                "category": category,
                "count": int(count),
                "severity": severity,
                "recommendation": recommendation,
                "automatic": False,
            }
            if auto_fix:
                hint["automatic_blockers"] = list(automatic_repair_blockers)
            if auto_fix and not automatic_repair_blockers:
                hint["automatic"] = True
                hint["auto_fix"] = {
                    "action": "blender.live_mesh_cleanup",
                    "arguments": {
                        "object_name": obj.name,
                        "repair": "remove_loose_vertices",
                        "expected_base_geometry_sha256": base_geometry_sha256,
                        "expected_loose_vertices": int(loose_vertices),
                    },
                }
            repair_hints.append(hint)

        add_repair_hint(
            "remove_isolated_vertices",
            "loose_vertices",
            loose_vertices,
            "warning",
            "Isolated vertices have no linked edges. They can be removed without changing connected topology.",
            auto_fix=True,
        )
        add_repair_hint(
            "review_non_finite_vertices",
            "non_finite_vertices",
            non_finite_vertices,
            "error",
            "Non-finite coordinates require manual source inspection; automatic coordinate fabrication is refused.",
        )
        add_repair_hint(
            "review_zero_length_edges",
            "zero_length_edges",
            zero_length_edges,
            "warning",
            "Zero-length edges can encode collapsed or duplicated topology. Inspect before merge/dissolve operations.",
        )
        add_repair_hint(
            "review_degenerate_faces",
            "degenerate_faces",
            degenerate_faces,
            "warning",
            "Degenerate faces need local topology review; automatic dissolve may change neighboring surfaces.",
        )
        if zero_length_edges or degenerate_faces:
            preview_hint = {
                "code": "preview_degenerate_dissolve",
                "category": "degenerate_geometry",
                "count": int(zero_length_edges + degenerate_faces),
                "severity": "warning",
                "recommendation": (
                    "Preview Blender Degenerate Dissolve on a candidate mesh copy, inspect the resulting topology, "
                    "then commit or cancel explicitly."
                ),
                "automatic": False,
                "preview_available": not degenerate_preview_blockers,
                "preview_blockers": list(degenerate_preview_blockers),
            }
            if not degenerate_preview_blockers:
                preview_hint["preview_fix"] = {
                    "action": "blender.live_degenerate_repair_preview",
                    "arguments": {
                        "object_name": obj.name,
                        "expected_base_geometry_sha256": base_geometry_sha256,
                        "expected_zero_length_edges": int(zero_length_edges),
                        "expected_degenerate_faces": int(degenerate_faces),
                        "threshold": float(epsilon),
                    },
                }
            repair_hints.append(preview_hint)
        add_repair_hint(
            "review_wire_edges",
            "wire_edges",
            wire_edges,
            "info",
            "Wire edges may be intentional guides or incomplete topology. No automatic deletion is offered.",
        )
        add_repair_hint(
            "review_boundary_edges",
            "boundary_edges",
            boundary_edges,
            "info",
            "Boundary edges can be intentional on open surfaces. Inspect local examples before filling or closing geometry.",
        )
        if boundary_loops:
            hole_preview_blockers = list(automatic_repair_blockers)
            preview_candidates = []
            max_hole_edges = int(getattr(_MODELING_CONTRACTS, "MAX_HOLE_FILL_BOUNDARY_EDGES", 32))
            for loop in boundary_loop_examples:
                blockers = list(hole_preview_blockers)
                if loop.get("edge_indices_truncated") or int(loop.get("edge_count") or 0) > max_hole_edges:
                    blockers.append("boundary_loop_exceeds_preview_edge_limit")
                if float(loop.get("perimeter") or 0.0) <= epsilon:
                    blockers.append("boundary_loop_perimeter_is_degenerate")
                candidate = {
                    "loop_index": int(loop.get("loop_index") or 0),
                    "edge_count": int(loop.get("edge_count") or 0),
                    "perimeter": float(loop.get("perimeter") or 0.0),
                    "preview_available": not blockers,
                    "preview_blockers": blockers,
                }
                if not blockers:
                    candidate["preview_fix"] = {
                        "action": "blender.live_boundary_hole_fill_preview",
                        "arguments": {
                            "object_name": obj.name,
                            "expected_base_geometry_sha256": base_geometry_sha256,
                            "edge_indices": list(loop.get("edge_indices") or []),
                        },
                    }
                preview_candidates.append(candidate)
            repair_hints.append({
                "code": "preview_boundary_hole_fill",
                "category": "boundary_loops",
                "count": int(boundary_loops),
                "severity": "info",
                "recommendation": (
                    "Closed boundary loops can represent intentional open surfaces or holes. Review one explicit loop, "
                    "preview a localized fill on a candidate mesh, then commit or cancel explicitly."
                ),
                "automatic": False,
                "preview_candidates": preview_candidates,
            })
        if connected_component_count > 1:
            add_repair_hint(
                "review_connected_components",
                "connected_components",
                connected_component_count,
                "info",
                "Multiple mesh components can be intentional. Compare component bounds/examples before separating or deleting islands.",
            )
        add_repair_hint(
            "review_ngons",
            "ngon_faces",
            ngon_faces,
            "info",
            "N-gons are not inherently invalid. Review only where deformation, shading, export, or downstream topology requires it.",
        )
        add_repair_hint(
            "review_non_manifold_edges",
            "non_manifold_edges",
            non_manifold_edges,
            "warning",
            "Non-manifold edges include open boundaries and multi-face edges. Inspect the bounded edge examples before repair.",
        )

        rules = []

        def maximum(field: str, actual: int) -> None:
            if field not in check or check.get(field) is None:
                return
            raw = check.get(field)
            if not isinstance(raw, int) or isinstance(raw, bool) or raw < 0:
                raise ValueError(f"{field} must be a non-negative integer")
            rules.append({
                "rule": field,
                "passed": actual <= raw,
                "expected_max": raw,
                "actual": actual,
            })

        maximum("max_triangles", triangle_count)
        maximum("max_ngons", ngon_faces)
        maximum("max_boundary_edges", boundary_edges)
        maximum("max_wire_edges", wire_edges)
        maximum("max_non_manifold_edges", non_manifold_edges)
        maximum("max_loose_vertices", loose_vertices)
        maximum("max_non_finite_vertices", non_finite_vertices)
        maximum("max_connected_components", connected_component_count)
        maximum("max_degenerate_faces", degenerate_faces)
        maximum("max_zero_length_edges", zero_length_edges)

        if "min_quad_ratio" in check and check.get("min_quad_ratio") is not None:
            try:
                raw_minimum_quad_ratio = check.get("min_quad_ratio")
                if isinstance(raw_minimum_quad_ratio, bool):
                    raise ValueError
                minimum_quad_ratio = float(raw_minimum_quad_ratio)
            except (TypeError, ValueError):
                raise ValueError("min_quad_ratio must be a number")
            if (
                not math.isfinite(minimum_quad_ratio)
                or minimum_quad_ratio < 0
                or minimum_quad_ratio > 1
            ):
                raise ValueError("min_quad_ratio must be between 0 and 1")
            rules.append({
                "rule": "min_quad_ratio",
                "passed": quad_ratio >= minimum_quad_ratio,
                "expected_min": minimum_quad_ratio,
                "actual": round(quad_ratio, 6),
            })

        if check.get("require_uv", False):
            rules.append({
                "rule": "require_uv",
                "passed": uv_layers > 0,
                "expected_min": 1,
                "actual": uv_layers,
            })

        if check.get("require_material", False):
            rules.append({
                "rule": "require_material",
                "passed": material_slots > 0,
                "expected_min": 1,
                "actual": material_slots,
            })

        if check.get("require_manifold", False):
            rules.append({
                "rule": "require_manifold",
                "passed": non_manifold_edges == 0,
                "expected_max": 0,
                "actual": non_manifold_edges,
            })

        if not rules:
            raise ValueError(
                "mesh_quality requires at least one threshold or requirement"
            )

        failed = [rule for rule in rules if not rule["passed"]]
        return {
            "type": "mesh_quality",
            "passed": not failed,
            "object_name": obj.name,
            "evaluated": evaluated,
            "epsilon": epsilon,
            "diagnostic_limit": diagnostic_limit,
            "metrics": metrics,
            "diagnostics": diagnostics,
            "repair_context": {
                "evaluated": evaluated,
                "base_geometry_sha256": base_geometry_sha256,
                "base_mesh": base_mesh_counts,
                "automatic_repairs_require_base_mesh_diagnostics": True,
                "automatic_repair_eligible": not automatic_repair_blockers,
                "automatic_repair_blockers": list(automatic_repair_blockers),
            },
            "repair_hints": repair_hints,
            "rules": rules,
            "failed_rules": len(failed),
        }
    finally:
        if bm is not None:
            try:
                bm.free()
            except Exception:
                pass
        if must_clear and evaluated_obj is not None:
            try:
                evaluated_obj.to_mesh_clear()
            except Exception:
                pass



def _uv_coordinate(layer, loop_index: int) -> tuple[float, float]:
    modern = getattr(layer, "uv", None)
    if modern is not None:
        try:
            value = modern[loop_index]
            vector = getattr(value, "vector", None)
            if vector is not None:
                return float(vector[0]), float(vector[1])
        except (IndexError, TypeError, ValueError, ReferenceError):
            pass

    legacy = getattr(layer, "data", None)
    if legacy is not None:
        try:
            vector = legacy[loop_index].uv
            return float(vector[0]), float(vector[1])
        except (IndexError, TypeError, ValueError, ReferenceError, AttributeError):
            pass

    raise ValueError("could not read UV coordinates from the selected UV layer")


def _quality_uv(check: dict) -> dict:
    obj = _quality_object(check.get("object_name"), "object_name")
    if obj.type != "MESH":
        raise ValueError(f"uv_quality requires a MESH object: {obj.name}")

    evaluated = bool(check.get("evaluated", True))
    try:
        epsilon = float(check.get("epsilon", 1e-10))
        tile_tolerance = float(check.get("tile_tolerance", 1e-6))
    except (TypeError, ValueError):
        raise ValueError("epsilon and tile_tolerance must be numbers")
    if epsilon <= 0 or epsilon > 1.0:
        raise ValueError("epsilon must be greater than 0 and at most 1")
    if tile_tolerance < 0 or tile_tolerance > 1.0:
        raise ValueError("tile_tolerance must be between 0 and 1")

    try:
        max_analysis_triangles = int(check.get("max_analysis_triangles", 20000))
        max_pair_tests = int(check.get("max_overlap_pair_tests", 1000000))
    except (TypeError, ValueError):
        raise ValueError(
            "max_analysis_triangles and max_overlap_pair_tests must be integers"
        )
    if max_analysis_triangles < 1 or max_analysis_triangles > 100000:
        raise ValueError("max_analysis_triangles must be between 1 and 100000")
    if max_pair_tests < 1 or max_pair_tests > 10000000:
        raise ValueError("max_overlap_pair_tests must be between 1 and 10000000")

    depsgraph = bpy.context.evaluated_depsgraph_get()
    evaluated_obj = None
    mesh = None
    must_clear = False
    try:
        if evaluated:
            evaluated_obj = obj.evaluated_get(depsgraph)
            mesh = evaluated_obj.to_mesh()
            must_clear = True
        else:
            mesh = obj.data
        if mesh is None:
            raise ValueError(f"mesh data is unavailable for {obj.name}")

        uv_name = str(check.get("uv_map") or "").strip()
        uv_layers = getattr(mesh, "uv_layers", None)
        if uv_layers is None or len(uv_layers) == 0:
            raise ValueError(f"object has no UV maps: {obj.name}")
        uv_layer = uv_layers.get(uv_name) if uv_name else uv_layers.active
        if uv_layer is None:
            if uv_name:
                raise ValueError(f"UV map was not found: {uv_name}")
            raise ValueError(f"object has no active UV map: {obj.name}")

        mesh.calc_loop_triangles()
        triangles = list(mesh.loop_triangles)
        if len(triangles) > max_analysis_triangles:
            raise ValueError(
                f"UV analysis has {len(triangles)} triangles; "
                f"max_analysis_triangles is {max_analysis_triangles}"
            )

        uv_cache: dict[int, tuple[float, float]] = {}

        def uv(loop_index: int) -> tuple[float, float]:
            if loop_index not in uv_cache:
                uv_cache[loop_index] = _uv_coordinate(uv_layer, loop_index)
            return uv_cache[loop_index]

        out_of_bounds_loops = 0
        uv_min = [float("inf"), float("inf")]
        uv_max = [float("-inf"), float("-inf")]
        for loop_index in range(len(mesh.loops)):
            point = uv(loop_index)
            uv_min[0] = min(uv_min[0], point[0])
            uv_min[1] = min(uv_min[1], point[1])
            uv_max[0] = max(uv_max[0], point[0])
            uv_max[1] = max(uv_max[1], point[1])
            if (
                point[0] < -tile_tolerance
                or point[0] > 1.0 + tile_tolerance
                or point[1] < -tile_tolerance
                or point[1] > 1.0 + tile_tolerance
            ):
                out_of_bounds_loops += 1

        face_uv_area = [0.0] * len(mesh.polygons)
        triangle_records = []
        distortions = []
        degenerate_uv_triangles = 0
        for triangle in triangles:
            loop_indices = tuple(int(index) for index in triangle.loops)
            uv_points = tuple(uv(index) for index in loop_indices)
            uv_area = _triangle_area_2d(uv_points)
            polygon_index = int(triangle.polygon_index)
            if 0 <= polygon_index < len(face_uv_area):
                face_uv_area[polygon_index] += uv_area
            if uv_area <= epsilon:
                degenerate_uv_triangles += 1
            geometry_points = tuple(
                tuple(
                    float(value)
                    for value in mesh.vertices[
                        mesh.loops[index].vertex_index
                    ].co
                )
                for index in loop_indices
            )
            distortion = _triangle_shape_distortion(
                geometry_points,
                uv_points,
                epsilon,
            )
            if distortion is not None:
                distortions.append(distortion)
            triangle_records.append({
                "polygon_index": polygon_index,
                "uv": uv_points,
                "area": uv_area,
                "min_x": min(point[0] for point in uv_points),
                "max_x": max(point[0] for point in uv_points),
                "min_y": min(point[1] for point in uv_points),
                "max_y": max(point[1] for point in uv_points),
            })

        zero_area_faces = sum(1 for area in face_uv_area if area <= epsilon)
        maximum_distortion = max(distortions) if distortions else 0.0
        mean_distortion = (
            sum(distortions) / len(distortions)
            if distortions
            else 0.0
        )

        overlap_pair_count = None
        overlap_pair_tests = 0
        overlap_truncated = False
        overlap_examples = []
        overlap_limit_raw = check.get("max_overlap_pairs")
        report_overlap = bool(check.get("report_overlap", False))
        if overlap_limit_raw is not None or report_overlap:
            allowed_overlap = None
            if overlap_limit_raw is not None:
                if (
                    not isinstance(overlap_limit_raw, int)
                    or isinstance(overlap_limit_raw, bool)
                    or overlap_limit_raw < 0
                ):
                    raise ValueError("max_overlap_pairs must be a non-negative integer")
                allowed_overlap = int(overlap_limit_raw)

            overlap_pair_count = 0
            ordered = sorted(
                enumerate(triangle_records),
                key=lambda item: item[1]["min_x"],
            )
            stop = False
            for ordered_index, (left_index, left) in enumerate(ordered):
                if left["area"] <= epsilon:
                    continue
                for right_index, right in ordered[ordered_index + 1:]:
                    if right["min_x"] > left["max_x"] + epsilon:
                        break
                    if right["area"] <= epsilon:
                        continue
                    if (
                        right["max_y"] < left["min_y"] - epsilon
                        or right["min_y"] > left["max_y"] + epsilon
                    ):
                        continue
                    overlap_pair_tests += 1
                    if overlap_pair_tests > max_pair_tests:
                        raise ValueError(
                            "UV overlap analysis exceeded max_overlap_pair_tests"
                        )
                    area = _triangle_overlap_area_2d(left["uv"], right["uv"])
                    if area <= epsilon:
                        continue
                    overlap_pair_count += 1
                    if len(overlap_examples) < 20:
                        overlap_examples.append({
                            "triangle_a": left_index,
                            "triangle_b": right_index,
                            "polygon_a": left["polygon_index"],
                            "polygon_b": right["polygon_index"],
                            "overlap_area": round(float(area), 10),
                        })
                    if (
                        allowed_overlap is not None
                        and overlap_pair_count > allowed_overlap
                    ):
                        overlap_truncated = True
                        stop = True
                        break
                if stop:
                    break

        metrics = {
            "uv_map": uv_layer.name,
            "loops": len(mesh.loops),
            "faces": len(mesh.polygons),
            "triangles": len(triangles),
            "uv_bounds_min": [round(value, 8) for value in uv_min],
            "uv_bounds_max": [round(value, 8) for value in uv_max],
            "out_of_bounds_loops": out_of_bounds_loops,
            "zero_area_faces": zero_area_faces,
            "degenerate_uv_triangles": degenerate_uv_triangles,
            "max_shape_distortion": round(float(maximum_distortion), 8),
            "mean_shape_distortion": round(float(mean_distortion), 8),
            "measured_distortion_triangles": len(distortions),
            "overlap_triangle_pairs": overlap_pair_count,
            "overlap_pair_tests": overlap_pair_tests,
            "overlap_truncated": overlap_truncated,
            "overlap_examples": overlap_examples,
        }

        rules = []

        def maximum_integer(field: str, actual: int) -> None:
            if field not in check or check.get(field) is None:
                return
            raw = check.get(field)
            if not isinstance(raw, int) or isinstance(raw, bool) or raw < 0:
                raise ValueError(f"{field} must be a non-negative integer")
            rules.append({
                "rule": field,
                "passed": actual <= raw,
                "expected_max": raw,
                "actual": actual,
            })

        maximum_integer("max_out_of_bounds_loops", out_of_bounds_loops)
        maximum_integer("max_zero_area_faces", zero_area_faces)
        maximum_integer(
            "max_degenerate_uv_triangles",
            degenerate_uv_triangles,
        )
        if overlap_limit_raw is not None:
            maximum_integer(
                "max_overlap_pairs",
                int(overlap_pair_count or 0),
            )

        for field, actual in (
            ("max_shape_distortion", maximum_distortion),
            ("max_mean_shape_distortion", mean_distortion),
        ):
            if field not in check or check.get(field) is None:
                continue
            try:
                expected_max = float(check.get(field))
            except (TypeError, ValueError):
                raise ValueError(f"{field} must be a number")
            if expected_max < 0 or expected_max > 1:
                raise ValueError(f"{field} must be between 0 and 1")
            rules.append({
                "rule": field,
                "passed": actual <= expected_max,
                "expected_max": expected_max,
                "actual": round(float(actual), 8),
            })

        if bool(check.get("require_unit_tile", False)):
            rules.append({
                "rule": "require_unit_tile",
                "passed": out_of_bounds_loops == 0,
                "expected_max": 0,
                "actual": out_of_bounds_loops,
            })

        if not rules:
            raise ValueError(
                "uv_quality requires at least one threshold or requirement"
            )

        failed = [rule for rule in rules if not rule["passed"]]
        return {
            "type": "uv_quality",
            "passed": not failed,
            "object_name": obj.name,
            "evaluated": evaluated,
            "epsilon": epsilon,
            "tile_tolerance": tile_tolerance,
            "metrics": metrics,
            "rules": rules,
            "failed_rules": len(failed),
        }
    finally:
        if must_clear and evaluated_obj is not None:
            try:
                evaluated_obj.to_mesh_clear()
            except Exception:
                pass


def _quality_gate(command: dict) -> None:
    command_id = command["id"]
    checks = command.get("checks")
    if not isinstance(checks, list) or not checks:
        _response(command_id, False, "checks must be a non-empty list")
        return
    if len(checks) > 100:
        _response(command_id, False, "quality gate is limited to 100 checks")
        return

    results = []
    evaluators = {
        "dimensions": _quality_dimensions,
        "symmetry": _quality_symmetry,
        "proportion": _quality_proportion,
        "containment": _quality_containment,
        "mesh_quality": _quality_mesh,
        "uv_quality": _quality_uv,
    }
    for index, check in enumerate(checks):
        if not isinstance(check, dict):
            _response(command_id, False, f"quality check {index} must be an object")
            return
        if any(key in check for key in ("passed", "ok", "result")):
            _response(
                command_id,
                False,
                f"quality check {index} cannot provide its own completion claim",
            )
            return
        kind = str(check.get("type") or "").strip().lower()
        if kind not in _QUALITY_TYPES:
            _response(
                command_id,
                False,
                f"quality check {index} type must be one of: {', '.join(sorted(_QUALITY_TYPES))}",
            )
            return
        try:
            result = evaluators[kind](check)
        except ValueError as error:
            _response(
                command_id,
                False,
                f"quality check {index} is invalid: {error}",
                failed_check=index,
                check_type=kind,
            )
            return
        results.append(result)

    failed = [result for result in results if not result["passed"]]
    passed = not failed
    _response(
        command_id,
        passed,
        "Blender deterministic quality gate passed"
        if passed
        else "Blender deterministic quality gate failed",
        passed=passed,
        total_checks=len(results),
        passed_checks=len(results) - len(failed),
        failed_checks=len(failed),
        checks=results,
    )

def _checkpoint_name(label: str, command_id: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", label.strip()).strip("-._")
    if not cleaned:
        cleaned = "checkpoint"
    return f"{int(time.time())}-{cleaned[:80]}-{command_id[:8]}.blend"


def _checkpoint_create(command: dict) -> None:
    command_id = command["id"]
    label = str(command.get("label") or "checkpoint")
    target = (CHECKPOINTS / _checkpoint_name(label, command_id)).resolve()
    if not _inside(target, CHECKPOINTS):
        _response(command_id, False, "checkpoint path escaped managed directory")
        return

    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        bpy.ops.wm.save_as_mainfile(filepath=str(target), copy=True)
        metadata = {
            "id": target.stem,
            "label": label,
            "file": str(target),
            "created_at": time.time(),
            "source_file": bpy.data.filepath or "",
            "snapshot": _scene_snapshot(),
        }
        _write_json_atomic(target.with_suffix(".json"), metadata)
        _response(
            command_id,
            True,
            "Blender checkpoint created",
            checkpoint=metadata,
        )
    except Exception as error:
        _response(command_id, False, f"{type(error).__name__}: {error}")


def _checkpoint_list(command: dict) -> None:
    command_id = command["id"]
    entries = []
    for path in sorted(CHECKPOINTS.glob("*.blend"), key=lambda item: item.stat().st_mtime, reverse=True)[:100]:
        meta_path = path.with_suffix(".json")
        metadata = {}
        if meta_path.is_file():
            try:
                metadata = json.loads(meta_path.read_text(encoding="utf-8-sig"))
            except Exception:
                metadata = {}
        entries.append(
            {
                "id": path.stem,
                "file": str(path),
                "size_bytes": path.stat().st_size,
                "modified_at": path.stat().st_mtime,
                **metadata,
            }
        )

    _response(
        command_id,
        True,
        "Blender checkpoints listed",
        checkpoints=entries,
    )


def _checkpoint_restore(command: dict) -> None:
    command_id = command["id"]
    checkpoint_id = str(command.get("checkpoint_id") or "").strip()
    if not checkpoint_id or not re.fullmatch(r"[A-Za-z0-9._-]+", checkpoint_id):
        _response(command_id, False, "checkpoint_id is required and contains unsupported characters")
        return

    target = (CHECKPOINTS / f"{checkpoint_id}.blend").resolve()
    if not _inside(target, CHECKPOINTS) or not target.is_file():
        _response(command_id, False, "checkpoint does not exist in the managed checkpoint directory")
        return

    if bool(getattr(bpy.data, "is_dirty", False)) and not bool(command.get("discard_unsaved", False)):
        _response(
            command_id,
            False,
            "current Blender file has unsaved changes; set discard_unsaved=true to restore explicitly",
        )
        return

    _response(
        command_id,
        True,
        "Blender checkpoint restore scheduled",
        checkpoint_id=checkpoint_id,
        file=str(target),
    )

    def _restore_later():
        try:
            bpy.ops.wm.open_mainfile(filepath=str(target), load_ui=False)
        except Exception:
            pass
        return None

    bpy.app.timers.register(_restore_later, first_interval=0.20, persistent=True)


def _trajectory_append(event: dict) -> None:
    try:
        with TRAJECTORY.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n")
    except OSError:
        pass


def _inspect(command: dict) -> None:
    command_id = command["id"]
    try:
        objects, truncated = _scene_objects(command)
    except ValueError as error:
        _response(command_id, False, str(error))
        return

    _response(
        command_id,
        True,
        "Visible Blender scene inspected",
        object_count=len(bpy.context.scene.objects),
        returned_objects=len(objects),
        truncated=truncated,
        objects=[
            {
                "name": obj.name,
                "type": obj.type,
                "visible": bool(obj.visible_get()),
                "selected": bool(obj.select_get()),
                "location": _round_vector(obj.location),
                "rotation_euler": _round_vector(obj.rotation_euler),
                "scale": _round_vector(obj.scale),
                "parent": obj.parent.name if obj.parent else None,
                "dimensions": _round_vector(obj.dimensions),
                "ordax": _ordax_properties(obj),
            }
            for obj in objects
        ],
        collections=[collection.name for collection in bpy.data.collections[:200]],
        scene_ordax=_ordax_properties(bpy.context.scene),
    )



def _purge_script_modules() -> list[str]:
    """Drop cached project-script modules before a live generation pass.

    Blender Live is intentionally persistent. Python's normal import cache would
    otherwise keep modules loaded before a Git sync, making a newly-synced
    generation script execute stale dependencies until Blender restarts.
    """
    importlib.invalidate_caches()
    removed: list[str] = []
    scripts_root = SCRIPTS_ROOT.resolve()
    for name, module in list(sys.modules.items()):
        if not name or module is None:
            continue
        raw_file = getattr(module, "__file__", None)
        if not raw_file:
            continue
        try:
            module_path = Path(raw_file).expanduser().resolve()
            module_path.relative_to(scripts_root)
        except (OSError, RuntimeError, ValueError):
            continue
        sys.modules.pop(name, None)
        removed.append(name)
    importlib.invalidate_caches()
    return sorted(set(removed))

def _run_script(command: dict) -> None:
    command_id = command["id"]
    raw = str(command.get("script_path") or "")
    script = Path(raw).expanduser().resolve()

    if script.suffix.lower() != ".py" or not script.is_file():
        _response(command_id, False, "Live Blender script does not exist")
        return
    if not _inside(script, SCRIPTS_ROOT):
        _response(command_id, False, "Live Blender script is outside approved scripts directory")
        return

    before = _scene_snapshot()
    try:
        reloaded_modules = _purge_script_modules()
        runpy.run_path(str(script), run_name="__main__")
        _response(
            command_id,
            True,
            "Live Blender script executed",
            script_path=str(script),
            reloaded_modules=reloaded_modules,
            before=before,
        )
    except Exception as error:
        _response(
            command_id,
            False,
            f"{type(error).__name__}: {error}",
            script_path=str(script),
            before=before,
        )



def _serialize_rna_value(value):
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    try:
        return [float(item) for item in value]
    except Exception:
        return str(value)


def _rna_property_schema(prop) -> dict:
    data = {
        "identifier": str(getattr(prop, "identifier", "")),
        "name": str(getattr(prop, "name", "")),
        "description": str(getattr(prop, "description", "")),
        "type": str(getattr(prop, "type", "")),
        "subtype": str(getattr(prop, "subtype", "")),
        "is_readonly": bool(getattr(prop, "is_readonly", False)),
        "is_array": bool(getattr(prop, "is_array", False)),
        "array_length": int(getattr(prop, "array_length", 0) or 0),
    }
    if hasattr(prop, "default"):
        try:
            data["default"] = _serialize_rna_value(prop.default)
        except Exception:
            pass
    if str(getattr(prop, "type", "")) == "ENUM":
        try:
            data["enum_items"] = [
                {
                    "identifier": item.identifier,
                    "name": item.name,
                    "description": item.description,
                    "value": int(item.value),
                }
                for item in prop.enum_items
            ][:200]
        except Exception:
            pass
    for key in ("hard_min", "hard_max", "soft_min", "soft_max"):
        if hasattr(prop, key):
            try:
                data[key] = float(getattr(prop, key))
            except Exception:
                pass
    return data


def _api_schema(command: dict) -> None:
    command_id = command["id"]
    type_name = str(command.get("type_name") or "").strip()
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,127}", type_name):
        _response(command_id, False, "type_name must be a bpy.types class name")
        return
    blender_type = getattr(bpy.types, type_name, None)
    rna = getattr(blender_type, "bl_rna", None)
    if blender_type is None or rna is None:
        _response(command_id, False, f"Unknown Blender RNA type: {type_name}")
        return
    try:
        limit = max(1, min(500, int(command.get("max_properties", 200))))
    except (TypeError, ValueError):
        _response(command_id, False, "max_properties must be an integer")
        return

    properties = [
        _rna_property_schema(prop)
        for prop in list(rna.properties)
        if str(getattr(prop, "identifier", "")) != "rna_type"
    ]
    _response(
        command_id,
        True,
        "Blender RNA schema ready",
        type_name=type_name,
        base=str(getattr(rna, "base", None)),
        property_count=len(properties),
        truncated=len(properties) > limit,
        properties=properties[:limit],
    )



def _rna_function_schema(function) -> dict:
    parameters = []
    returns = []
    try:
        raw_parameters = list(function.parameters)
    except Exception:
        raw_parameters = []
    for param in raw_parameters:
        data = _rna_property_schema(param)
        if bool(getattr(param, "is_output", False)):
            returns.append(data)
        else:
            parameters.append(data)
    return {
        "identifier": str(getattr(function, "identifier", "")),
        "name": str(getattr(function, "name", "")),
        "description": str(getattr(function, "description", "")),
        "parameters": parameters,
        "returns": returns,
    }


def _did_you_mean(value: str, candidates, limit: int = 8) -> list[str]:
    normalized = [str(item) for item in candidates if str(item)]
    return difflib.get_close_matches(value, normalized, n=limit, cutoff=0.45)


def _api_lookup(command: dict) -> None:
    command_id = command["id"]
    query = str(command.get("query") or "").strip()
    if not query or len(query) > 300:
        _response(command_id, False, "query is required and must be at most 300 characters")
        return

    normalized = query
    for prefix in ("bpy.types.", "bpy."):
        if normalized.startswith(prefix):
            normalized = normalized[len(prefix):]

    # Operators: bpy.ops.mesh.primitive_cube_add
    if normalized.startswith("ops."):
        parts = normalized.split(".")
        if len(parts) != 3 or not all(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", part) for part in parts[1:]):
            _response(command_id, False, "Operator query must look like bpy.ops.mesh.primitive_cube_add")
            return
        category_name, operator_name = parts[1], parts[2]
        category = getattr(bpy.ops, category_name, None)
        operator = getattr(category, operator_name, None) if category is not None else None
        if operator is None:
            candidates = []
            if category is not None:
                try:
                    candidates = dir(category)
                except Exception:
                    pass
            _response(
                command_id,
                False,
                f"Unknown Blender operator: {query}",
                did_you_mean=_did_you_mean(operator_name, candidates),
            )
            return
        try:
            rna = operator.get_rna_type()
            properties = [
                _rna_property_schema(prop)
                for prop in list(rna.properties)
                if str(getattr(prop, "identifier", "")) != "rna_type"
            ]
            _response(
                command_id,
                True,
                "Blender operator schema ready",
                query=query,
                kind="operator",
                identifier=f"bpy.ops.{category_name}.{operator_name}",
                description=str(getattr(rna, "description", "")),
                parameters=properties,
            )
        except Exception as error:
            _response(command_id, False, f"{type(error).__name__}: {error}")
        return

    parts = normalized.split(".")
    type_name = parts[0]
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,127}", type_name):
        _response(command_id, False, "Type query must start with a bpy.types class name")
        return

    blender_type = getattr(bpy.types, type_name, None)
    rna = getattr(blender_type, "bl_rna", None)
    if blender_type is None or rna is None:
        candidates = [
            name for name in dir(bpy.types)
            if re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", name)
        ]
        _response(
            command_id,
            False,
            f"Unknown Blender RNA type: {type_name}",
            did_you_mean=_did_you_mean(type_name, candidates),
        )
        return

    if len(parts) == 1:
        properties = [
            _rna_property_schema(prop)
            for prop in list(rna.properties)
            if str(getattr(prop, "identifier", "")) != "rna_type"
        ]
        functions = [_rna_function_schema(fn) for fn in list(rna.functions)]
        _response(
            command_id,
            True,
            "Blender RNA type lookup ready",
            query=query,
            kind="type",
            type_name=type_name,
            properties=properties[:500],
            functions=functions[:300],
        )
        return

    if len(parts) != 2 or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", parts[1]):
        _response(command_id, False, "Member query must look like Object.ray_cast or Material.diffuse_color")
        return

    member = parts[1]
    prop = rna.properties.get(member)
    if prop is not None:
        _response(
            command_id,
            True,
            "Blender RNA property lookup ready",
            query=query,
            kind="property",
            type_name=type_name,
            property=_rna_property_schema(prop),
        )
        return

    function = rna.functions.get(member)
    if function is not None:
        _response(
            command_id,
            True,
            "Blender RNA function lookup ready",
            query=query,
            kind="function",
            type_name=type_name,
            function=_rna_function_schema(function),
        )
        return

    candidates = [
        str(getattr(prop, "identifier", "")) for prop in list(rna.properties)
    ] + [
        str(getattr(fn, "identifier", "")) for fn in list(rna.functions)
    ]
    _response(
        command_id,
        False,
        f"Unknown member on {type_name}: {member}",
        did_you_mean=_did_you_mean(member, candidates),
    )

def _socket_schema(socket) -> dict:
    data = {
        "name": str(getattr(socket, "name", "")),
        "identifier": str(getattr(socket, "identifier", "")),
        "bl_idname": str(getattr(socket, "bl_idname", "")),
        "enabled": bool(getattr(socket, "enabled", True)),
        "hide": bool(getattr(socket, "hide", False)),
        "is_linked": bool(getattr(socket, "is_linked", False)),
    }
    if hasattr(socket, "default_value"):
        try:
            data["default_value"] = _serialize_rna_value(socket.default_value)
        except Exception:
            pass
    for key in ("min_value", "max_value"):
        if hasattr(socket, key):
            try:
                data[key] = float(getattr(socket, key))
            except Exception:
                pass
    return data


def _node_schema(command: dict) -> None:
    command_id = command["id"]
    node_type = str(command.get("node_type") or "").strip()
    tree_type = str(command.get("tree_type") or "ShaderNodeTree").strip()
    overrides = command.get("property_overrides") or {}
    allowed_trees = {"ShaderNodeTree", "GeometryNodeTree", "CompositorNodeTree"}
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,127}", node_type):
        _response(command_id, False, "node_type must be a Blender node bl_idname")
        return
    if tree_type not in allowed_trees:
        _response(command_id, False, "tree_type must be ShaderNodeTree, GeometryNodeTree, or CompositorNodeTree")
        return
    if not isinstance(overrides, dict) or len(overrides) > 30:
        _response(command_id, False, "property_overrides must be an object with at most 30 entries")
        return
    for key, value in overrides.items():
        if not isinstance(key, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            _response(command_id, False, "property_overrides contains an invalid property name")
            return
        if not isinstance(value, (str, int, float, bool)):
            _response(command_id, False, "property_overrides values must be scalar")
            return

    group = None
    try:
        group = bpy.data.node_groups.new(
            name=f"__ORDAX_SCHEMA_{command_id[:8]}",
            type=tree_type,
        )
        node = group.nodes.new(node_type)
        applied = {}
        for key, value in overrides.items():
            if not hasattr(node, key):
                raise ValueError(f"Node {node_type} has no property: {key}")
            setattr(node, key, value)
            applied[key] = _serialize_rna_value(getattr(node, key))
        _response(
            command_id,
            True,
            "Blender node schema ready",
            node_type=node_type,
            tree_type=tree_type,
            node_name=node.name,
            label=str(getattr(node, "bl_label", "")),
            applied_overrides=applied,
            inputs=[_socket_schema(socket) for socket in node.inputs],
            outputs=[_socket_schema(socket) for socket in node.outputs],
        )
    except Exception as error:
        _response(command_id, False, f"{type(error).__name__}: {error}")
    finally:
        if group is not None:
            try:
                bpy.data.node_groups.remove(group)
            except Exception:
                pass


def _export_scene(command: dict) -> None:
    command_id = command["id"]
    raw = str(command.get("output_path") or "").strip()
    output = Path(raw).expanduser().resolve()
    export_format = str(command.get("format") or output.suffix.lstrip(".")).lower()

    expected_suffix = {"glb": ".glb", "fbx": ".fbx"}.get(export_format)
    if expected_suffix is None:
        _response(command_id, False, "format must be glb or fbx")
        return
    if output.suffix.lower() != expected_suffix:
        _response(command_id, False, f"output_path must end with {expected_suffix}")
        return
    if not _inside(output, PROJECT_ROOT):
        _response(command_id, False, "Export target must be inside registered project")
        return

    requested = command.get("object_names")
    if requested is not None and (
        not isinstance(requested, list)
        or len(requested) > 500
        or not all(isinstance(name, str) and name.strip() for name in requested)
    ):
        _response(command_id, False, "object_names must be a list of at most 500 object names")
        return

    previous_selected = []
    previous_active = None
    try:
        previous_selected = [obj.name for obj in bpy.context.selected_objects]
        previous_active = bpy.context.view_layer.objects.active.name if bpy.context.view_layer.objects.active else None
    except Exception:
        pass

    output.parent.mkdir(parents=True, exist_ok=True)
    exported_names = []
    try:
        use_selection = False
        if requested is not None:
            bpy.ops.object.select_all(action="DESELECT")
            missing = []
            for name in requested:
                obj = bpy.context.scene.objects.get(name.strip())
                if obj is None:
                    missing.append(name.strip())
                    continue
                obj.select_set(True)
                exported_names.append(obj.name)
            if missing:
                _response(command_id, False, "Some export objects were not found", missing=missing)
                return
            use_selection = True
        elif bool(command.get("selected_only", False)):
            use_selection = True
            exported_names = [obj.name for obj in bpy.context.selected_objects]

        animations = bool(command.get("animations", True))
        if export_format == "glb":
            bpy.ops.export_scene.gltf(
                filepath=str(output),
                check_existing=False,
                export_format="GLB",
                use_selection=use_selection,
                export_extras=True,
                export_apply=bool(command.get("apply_modifiers", False)),
                export_animations=animations,
            )
        else:
            bpy.ops.export_scene.fbx(
                filepath=str(output),
                check_existing=False,
                use_selection=use_selection,
                use_custom_props=True,
                use_mesh_modifiers=bool(command.get("apply_modifiers", True)),
                bake_anim=animations,
                add_leaf_bones=False,
            )

        ok = output.is_file() and output.stat().st_size > 0
        _response(
            command_id,
            ok,
            "Blender scene exported" if ok else "Blender export did not create a file",
            artifact=str(output),
            format=export_format,
            size_bytes=output.stat().st_size if ok else 0,
            sha256=hashlib.sha256(output.read_bytes()).hexdigest() if ok else None,
            exported_objects=exported_names,
            selection_only=use_selection,
        )
    except Exception as error:
        _response(command_id, False, f"{type(error).__name__}: {error}")
    finally:
        try:
            bpy.ops.object.select_all(action="DESELECT")
            for name in previous_selected:
                obj = bpy.context.scene.objects.get(name)
                if obj is not None:
                    obj.select_set(True)
            bpy.context.view_layer.objects.active = (
                bpy.context.scene.objects.get(previous_active)
                if previous_active
                else None
            )
        except Exception:
            pass


_MULTIVIEW_DIRECTIONS = {
    "front": (0.0, -1.0, 0.0),
    "back": (0.0, 1.0, 0.0),
    "left": (-1.0, 0.0, 0.0),
    "right": (1.0, 0.0, 0.0),
    "top": (0.0, 0.0, 1.0),
    "bottom": (0.0, 0.0, -1.0),
    "three_quarter": (1.0, -1.0, 0.75),
    "three_quarter_back": (-1.0, 1.0, 0.75),
}
_MULTIVIEW_DEFAULTS = (
    "front",
    "back",
    "left",
    "right",
    "top",
    "three_quarter",
)


def _multiview_target_objects(command: dict):
    requested = command.get("object_names")
    if requested is not None:
        if (
            not isinstance(requested, list)
            or not requested
            or len(requested) > 200
            or not all(isinstance(name, str) and name.strip() for name in requested)
        ):
            raise ValueError(
                "object_names must be a non-empty list of at most 200 object names"
            )
        missing = []
        objects = []
        allowed_types = {
            "MESH",
            "CURVE",
            "SURFACE",
            "META",
            "FONT",
            "VOLUME",
            "GREASEPENCIL",
        }
        unsupported = []
        for raw_name in requested:
            name = raw_name.strip()
            obj = bpy.context.scene.objects.get(name)
            if obj is None:
                missing.append(name)
            elif obj.type not in allowed_types:
                unsupported.append(f"{name}:{obj.type}")
            else:
                objects.append(obj)
        if missing:
            raise ValueError(
                "multiview objects were not found: " + ", ".join(missing[:20])
            )
        if unsupported:
            raise ValueError(
                "multiview objects are not renderable geometry: "
                + ", ".join(unsupported[:20])
            )
        return objects

    allowed_types = {
        "MESH",
        "CURVE",
        "SURFACE",
        "META",
        "FONT",
        "VOLUME",
        "GREASEPENCIL",
    }
    objects = [
        obj
        for obj in bpy.context.scene.objects
        if (
            obj.type in allowed_types
            and bool(obj.visible_get())
            and not bool(getattr(obj, "hide_render", False))
        )
    ]
    if not objects:
        raise ValueError("no visible renderable objects are available for multiview")
    return objects[:200]


def _multiview_world_corners(objects) -> tuple[list[Vector], list[str]]:
    depsgraph = bpy.context.evaluated_depsgraph_get()
    corners: list[Vector] = []
    used: list[str] = []
    for obj in objects:
        try:
            evaluated = obj.evaluated_get(depsgraph)
            bound_box = list(evaluated.bound_box)
            if len(bound_box) != 8:
                continue
            matrix = evaluated.matrix_world
            object_corners = [matrix @ Vector(corner) for corner in bound_box]
            if not all(all(abs(float(value)) < 1e15 for value in corner) for corner in object_corners):
                continue
            corners.extend(object_corners)
            used.append(obj.name)
        except Exception:
            continue
    if not corners:
        raise ValueError("selected multiview objects do not expose usable world bounds")
    return corners, used


def _multiview_bounds(corners: list[Vector]) -> dict:
    mins = Vector((
        min(point.x for point in corners),
        min(point.y for point in corners),
        min(point.z for point in corners),
    ))
    maxs = Vector((
        max(point.x for point in corners),
        max(point.y for point in corners),
        max(point.z for point in corners),
    ))
    center = (mins + maxs) * 0.5
    dimensions = maxs - mins
    return {
        "min": _round_vector(mins),
        "max": _round_vector(maxs),
        "center": _round_vector(center),
        "dimensions": _round_vector(dimensions),
        "diagonal": round(float(dimensions.length), 6),
    }


def _scene_unit_metadata(scene) -> tuple[str, float | None]:
    settings = getattr(scene, "unit_settings", None)
    system = str(getattr(settings, "system", "NONE") or "NONE").upper()
    if system == "NONE":
        return system, None
    try:
        scale = float(getattr(settings, "scale_length", 1.0))
    except (TypeError, ValueError):
        return system, None
    if not math.isfinite(scale) or scale <= 0:
        return system, None
    return system, scale


def _multiview_render_engine(
    scene,
    *,
    mode: str,
) -> tuple[str, str]:
    original = str(scene.render.engine)
    if mode == "silhouette":
        candidates = ("BLENDER_WORKBENCH_NEXT", "BLENDER_WORKBENCH")
        failure = (
            "silhouette multiview requires a supported Blender Workbench render engine"
        )
    elif mode == "material":
        candidates = ("BLENDER_EEVEE_NEXT", "BLENDER_EEVEE")
        failure = (
            "material multiview requires a supported Blender Eevee render engine"
        )
    else:
        raise ValueError("multiview mode must be material or silhouette")

    for candidate in candidates:
        try:
            scene.render.engine = candidate
            return original, candidate
        except Exception:
            continue

    scene.render.engine = original
    raise RuntimeError(failure)


def _multiview_add_material_lights(
    scene,
    *,
    center: Vector,
    diagonal: float,
    command_id: str,
) -> list[tuple[object, object]]:
    distance = max(float(diagonal) * 4.0, 0.4)
    size = max(float(diagonal) * 2.5, 0.2)
    base_energy = min(max(350.0 * (distance ** 2), 70.0), 100000.0)
    specs = (
        ("KEY", Vector((-0.8, -1.2, 1.3)), 1.0),
        ("FILL", Vector((1.2, -0.35, 0.65)), 0.50),
        ("RIM", Vector((0.25, 1.0, 1.45)), 0.65),
    )
    created: list[tuple[object, object]] = []
    for label, direction, factor in specs:
        light_data = bpy.data.lights.new(
            f"__ORDAX_MULTIVIEW_{label}_{command_id[:8]}",
            type="AREA",
        )
        light_data.energy = base_energy * factor
        light_data.shape = "DISK"
        light_data.size = size

        light = bpy.data.objects.new(
            f"__ORDAX_MULTIVIEW_{label}_{command_id[:8]}",
            light_data,
        )
        scene.collection.objects.link(light)
        light.location = center + (direction.normalized() * distance)
        light.rotation_euler = (
            center - light.location
        ).to_track_quat("-Z", "Y").to_euler()
        created.append((light, light_data))
    return created


def _multiview_shading_state(scene) -> tuple[object | None, dict]:
    display = getattr(scene, "display", None)
    shading = getattr(display, "shading", None) if display is not None else None
    if shading is None:
        return None, {}
    state = {}
    for field in (
        "light",
        "color_type",
        "single_color",
        "background_type",
        "background_color",
        "show_shadows",
        "show_cavity",
        "show_specular_highlight",
    ):
        if not hasattr(shading, field):
            continue
        try:
            value = getattr(shading, field)
            if field in {"single_color", "background_color"}:
                value = tuple(float(item) for item in value)
            state[field] = value
        except Exception:
            continue
    return shading, state


def _multiview_restore_shading(shading, state: dict) -> None:
    if shading is None:
        return
    for field, value in state.items():
        try:
            setattr(shading, field, value)
        except Exception:
            pass


def _multiview_apply_silhouette(scene) -> None:
    display = getattr(scene, "display", None)
    shading = getattr(display, "shading", None) if display is not None else None
    if shading is None:
        raise RuntimeError("silhouette multiview requires scene display shading")
    required = {
        "light": "FLAT",
        "color_type": "SINGLE",
        "single_color": (0.0, 0.0, 0.0),
        "background_type": "VIEWPORT",
        "background_color": (1.0, 1.0, 1.0),
        "show_shadows": False,
        "show_cavity": False,
        "show_specular_highlight": False,
    }
    for field, value in required.items():
        if not hasattr(shading, field):
            raise RuntimeError(
                f"silhouette multiview requires Workbench shading property: {field}"
            )
        setattr(shading, field, value)


def _multiview_capture(command: dict) -> None:
    command_id = command["id"]
    raw_dir = str(command.get("output_dir") or "").strip()
    output_dir = Path(raw_dir).expanduser().resolve()
    if not raw_dir or not _inside(output_dir, ARTIFACTS_ROOT):
        _response(
            command_id,
            False,
            "Multiview output_dir must be inside the managed artifact directory",
        )
        return

    raw_views = command.get("views", list(_MULTIVIEW_DEFAULTS))
    if (
        not isinstance(raw_views, list)
        or not raw_views
        or len(raw_views) > len(_MULTIVIEW_DIRECTIONS)
    ):
        _response(command_id, False, "views must be a non-empty bounded list")
        return
    views = []
    for raw in raw_views:
        view = str(raw or "").strip().lower()
        if view not in _MULTIVIEW_DIRECTIONS:
            _response(
                command_id,
                False,
                "unsupported multiview view: " + view,
                supported_views=sorted(_MULTIVIEW_DIRECTIONS),
            )
            return
        if view in views:
            _response(command_id, False, "multiview views must be unique")
            return
        views.append(view)

    mode = str(command.get("mode") or "material").strip().lower()
    if mode not in {"material", "silhouette"}:
        _response(command_id, False, "multiview mode must be material or silhouette")
        return

    try:
        width = int(command.get("width", 768))
        height = int(command.get("height", 768))
        margin = float(command.get("margin", 1.15))
    except (TypeError, ValueError):
        _response(command_id, False, "width, height and margin must be numeric")
        return
    if width < 128 or width > 4096 or height < 128 or height > 4096:
        _response(command_id, False, "multiview resolution must be between 128 and 4096")
        return
    if margin < 1.0 or margin > 3.0:
        _response(command_id, False, "multiview margin must be between 1.0 and 3.0")
        return

    try:
        objects = _multiview_target_objects(command)
        corners, used_objects = _multiview_world_corners(objects)
    except ValueError as error:
        _response(command_id, False, str(error))
        return

    bounds = _multiview_bounds(corners)
    center = Vector(bounds["center"])
    diagonal = max(float(bounds["diagonal"]), 0.001)
    output_dir.mkdir(parents=True, exist_ok=True)

    scene = bpy.context.scene
    unit_system, unit_scale_m = _scene_unit_metadata(scene)
    original_camera = scene.camera
    old_path = scene.render.filepath
    old_format = scene.render.image_settings.file_format
    old_resolution_x = scene.render.resolution_x
    old_resolution_y = scene.render.resolution_y
    old_resolution_percentage = scene.render.resolution_percentage
    old_film_transparent = bool(getattr(scene.render, "film_transparent", False))
    old_engine = str(scene.render.engine)
    shading, shading_state = _multiview_shading_state(scene)

    camera_data = None
    camera = None
    material_lights: list[tuple[object, object]] = []
    engine_used = old_engine
    records = []
    error = None
    requested_names = command.get("object_names")
    render_visibility = {
        obj.name: bool(getattr(obj, "hide_render", False))
        for obj in bpy.context.scene.objects
    }
    try:
        if requested_names is not None:
            target_names = {obj.name for obj in objects}
            for scene_object in bpy.context.scene.objects:
                try:
                    scene_object.hide_render = scene_object.name not in target_names
                except Exception:
                    pass
        camera_data = bpy.data.cameras.new(f"__ORDAX_MULTIVIEW_CAMERA_{command_id[:8]}")
        camera = bpy.data.objects.new(
            f"__ORDAX_MULTIVIEW_CAMERA_{command_id[:8]}",
            camera_data,
        )
        scene.collection.objects.link(camera)
        scene.camera = camera
        camera_data.type = "ORTHO"

        scene.render.resolution_x = width
        scene.render.resolution_y = height
        scene.render.resolution_percentage = 100
        scene.render.image_settings.file_format = "PNG"
        if hasattr(scene.render, "film_transparent"):
            scene.render.film_transparent = False

        _, engine_used = _multiview_render_engine(
            scene,
            mode=mode,
        )
        if mode == "silhouette":
            _multiview_apply_silhouette(scene)
        else:
            material_lights = _multiview_add_material_lights(
                scene,
                center=center,
                diagonal=diagonal,
                command_id=command_id,
            )
        distance = max(diagonal * 2.5, 2.0)
        aspect = float(width) / float(height)

        for view in views:
            direction = Vector(_MULTIVIEW_DIRECTIONS[view]).normalized()
            camera.location = center + (direction * distance)
            look_direction = center - camera.location
            camera.rotation_euler = look_direction.to_track_quat("-Z", "Y").to_euler()
            bpy.context.view_layer.update()

            camera_inverse = camera.matrix_world.inverted()
            projected = [camera_inverse @ corner for corner in corners]
            xs = [point.x for point in projected]
            ys = [point.y for point in projected]
            projected_width = max(xs) - min(xs)
            projected_height = max(ys) - min(ys)
            camera_data.ortho_scale = max(
                projected_height,
                projected_width / aspect,
                0.001,
            ) * margin
            camera_data.clip_start = max(0.001, distance - (diagonal * 1.5))
            camera_data.clip_end = max(
                camera_data.clip_start + 1.0,
                distance + (diagonal * 1.5),
            )

            output = output_dir / f"{view}.png"
            scene.render.filepath = str(output)
            bpy.ops.render.render(write_still=True)
            if not output.is_file() or output.stat().st_size <= 0:
                raise RuntimeError(f"multiview render did not create {view}.png")

            records.append({
                "view": view,
                "artifact": str(output),
                "size_bytes": output.stat().st_size,
                "sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
                "camera_location": _round_vector(camera.location),
                "camera_rotation_euler": _round_vector(camera.rotation_euler),
                "ortho_scale": round(float(camera_data.ortho_scale), 6),
                "direction_from_target": _round_vector(direction),
            })

        manifest = {
            "project": CFG.ordax_project_slug,
            "views": records,
            "objects": used_objects,
            "bounds": bounds,
            "resolution": [width, height],
            "margin": margin,
            "projection": "orthographic",
            "render_engine": engine_used,
            "mode": mode,
            "unit_system": unit_system,
            "unit_scale_m": unit_scale_m,
        }
        manifest_path = output_dir / "multiview.json"
        _write_json_atomic(manifest_path, manifest)
    except Exception as caught:
        error = caught
    finally:
        scene.camera = original_camera
        scene.render.filepath = old_path
        scene.render.image_settings.file_format = old_format
        scene.render.resolution_x = old_resolution_x
        scene.render.resolution_y = old_resolution_y
        scene.render.resolution_percentage = old_resolution_percentage
        _multiview_restore_shading(shading, shading_state)
        try:
            scene.render.engine = old_engine
        except Exception:
            pass
        if hasattr(scene.render, "film_transparent"):
            scene.render.film_transparent = old_film_transparent
        for object_name, hidden in render_visibility.items():
            obj = bpy.context.scene.objects.get(object_name)
            if obj is not None:
                try:
                    obj.hide_render = hidden
                except Exception:
                    pass
        for light, light_data in material_lights:
            try:
                bpy.data.objects.remove(light, do_unlink=True)
            except Exception:
                pass
            try:
                bpy.data.lights.remove(light_data)
            except Exception:
                pass
        if camera is not None:
            try:
                bpy.data.objects.remove(camera, do_unlink=True)
            except Exception:
                pass
        if camera_data is not None:
            try:
                bpy.data.cameras.remove(camera_data)
            except Exception:
                pass

    if error is not None:
        _response(
            command_id,
            False,
            f"{type(error).__name__}: {error}",
            artifacts=records,
            bounds=bounds,
            render_engine=engine_used,
            mode=mode,
        )
        return

    manifest_path = output_dir / "multiview.json"
    primary = next(
        (item["artifact"] for item in records if item["view"] == "three_quarter"),
        records[0]["artifact"] if records else None,
    )
    _response(
        command_id,
        bool(records),
        "Deterministic Blender multiview captured",
        artifacts=records,
        primary_artifact=primary,
        manifest=str(manifest_path),
        bounds=bounds,
        objects=used_objects,
        resolution=[width, height],
        margin=margin,
        projection="orthographic",
        render_engine=engine_used,
        mode=mode,
        unit_system=unit_system,
        unit_scale_m=unit_scale_m,
    )


def _capture_viewport(command: dict) -> None:
    command_id = command["id"]
    raw = str(command.get("output_path") or "")
    output = Path(raw).expanduser().resolve()

    if output.suffix.lower() not in (".png", ".jpg", ".jpeg"):
        _response(command_id, False, "Viewport output must be PNG or JPEG")
        return
    if not _inside(output, ARTIFACTS_ROOT):
        _response(command_id, False, "Viewport output is outside managed artifact directory")
        return

    output.parent.mkdir(parents=True, exist_ok=True)

    window = bpy.context.window
    screen = window.screen if window else None
    area = next((a for a in screen.areas if a.type == "VIEW_3D"), None) if screen else None
    region = next((r for r in area.regions if r.type == "WINDOW"), None) if area else None
    if not window or not area or not region:
        _response(command_id, False, "No visible 3D viewport is available")
        return

    scene = bpy.context.scene
    old_path = scene.render.filepath
    old_format = scene.render.image_settings.file_format
    try:
        scene.render.filepath = str(output)
        scene.render.image_settings.file_format = "PNG"
        with bpy.context.temp_override(window=window, area=area, region=region):
            bpy.ops.render.opengl(write_still=True, view_context=True)
        _response(
            command_id,
            output.is_file() and output.stat().st_size > 0,
            "Visible Blender viewport captured",
            artifact=str(output),
        )
    except Exception as error:
        _response(command_id, False, f"{type(error).__name__}: {error}")
    finally:
        scene.render.filepath = old_path
        scene.render.image_settings.file_format = old_format


def _save(command: dict) -> None:
    command_id = command["id"]
    raw = str(command.get("target_path") or "").strip()

    if raw:
        target = Path(raw).expanduser().resolve()
        if target.suffix.lower() != ".blend" or not _inside(target, PROJECT_ROOT):
            _response(command_id, False, "Save target must be a .blend inside registered project")
            return
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            bpy.ops.wm.save_as_mainfile(filepath=str(target))
            _response(command_id, True, "Visible Blender file saved", file=str(target))
        except Exception as error:
            _response(command_id, False, f"{type(error).__name__}: {error}")
        return

    current = Path(bpy.data.filepath).resolve() if bpy.data.filepath else None
    if current is None or not _inside(current, PROJECT_ROOT):
        _response(command_id, False, "Current Blender file is not inside registered project")
        return

    try:
        bpy.ops.wm.save_mainfile()
        _response(command_id, True, "Visible Blender file saved", file=str(current))
    except Exception as error:
        _response(command_id, False, f"{type(error).__name__}: {error}")


def _process(path: Path) -> None:
    try:
        command = json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        try:
            path.unlink()
        except OSError:
            pass
        return

    command_id = str(command.get("id") or "")
    operation = str(command.get("operation") or "")
    try:
        path.unlink()
    except OSError:
        pass

    if not command_id:
        return

    before_snapshot = _scene_snapshot()
    started_at = time.time()
    inflight_path = INFLIGHT / (command_id + ".json")
    _write_json_atomic(
        inflight_path,
        {
            "id": command_id,
            "operation": operation,
            "started_at": time.time(),
            "project": CFG.ordax_project_slug,
        },
    )

    try:
        if operation == "ping":
            _response(command_id, True, "Visible Blender companion ready")
        elif operation == "inspect":
            _inspect(command)
        elif operation == "scene_snapshot":
            _scene_snapshot_rich(command)
        elif operation == "scene_reset":
            _scene_reset(command)
        elif operation == "object_inspect":
            _object_inspect(command)
        elif operation == "object_fingerprints":
            _object_fingerprints(command)
        elif operation == "contact_audit":
            _contact_audit(command)
        elif operation == "quality_gate":
            _quality_gate(command)
        elif operation == "object_transform":
            _object_transform(command)
        elif operation == "object_remove":
            _object_remove(command)
        elif operation == "extract_region":
            _extract_region(command)
        elif operation == "cleanup_orphans":
            _cleanup_orphans(command)
        elif operation == "create_primitive":
            _modeling_create_primitive(command)
        elif operation == "create_box_with_cutouts":
            _create_box_with_cutouts(command)
        elif operation == "add_modifier":
            _modeling_add_modifier(command)
        elif operation == "surface_scatter":
            _modeling_surface_scatter(command)
        elif operation == "boolean_cut_preview":
            _modeling_boolean_cut_preview(command)
        elif operation == "boolean_cut_commit":
            _modeling_boolean_cut_commit(command)
        elif operation == "boolean_cut_cancel":
            _modeling_boolean_cut_cancel(command)
        elif operation == "mesh_cleanup":
            _modeling_mesh_cleanup(command)
        elif operation == "degenerate_repair_preview":
            _modeling_degenerate_repair_preview(command)
        elif operation == "degenerate_repair_commit":
            _modeling_degenerate_repair_commit(command)
        elif operation == "degenerate_repair_cancel":
            _modeling_degenerate_repair_cancel(command)
        elif operation == "merge_by_distance_preview":
            _modeling_merge_by_distance_preview(command)
        elif operation == "merge_by_distance_commit":
            _modeling_merge_by_distance_commit(command)
        elif operation == "merge_by_distance_cancel":
            _modeling_merge_by_distance_cancel(command)
        elif operation == "boundary_hole_fill_preview":
            _modeling_boundary_hole_fill_preview(command)
        elif operation == "boundary_hole_fill_commit":
            _modeling_boundary_hole_fill_commit(command)
        elif operation == "boundary_hole_fill_cancel":
            _modeling_boundary_hole_fill_cancel(command)
        elif operation == "material_apply":
            _material_apply(command)
        elif operation == "create_camera":
            _create_camera(command)
        elif operation == "create_light":
            _create_light(command)
        elif operation == "scene_presentation":
            _scene_presentation(command)
        elif operation == "import_asset":
            _import_asset(command)
        elif operation == "animate_transform":
            _animate_transform(command)
        elif operation == "viewport_proxy":
            _viewport_proxy(command)
        elif operation == "bake_work_proxy":
            _bake_work_proxy(command)
        elif operation == "object_metadata":
            _object_metadata(command)
        elif operation == "api_schema":
            _api_schema(command)
        elif operation == "api_lookup":
            _api_lookup(command)
        elif operation == "node_schema":
            _node_schema(command)
        elif operation == "export_scene":
            _export_scene(command)
        elif operation == "checkpoint_create":
            _checkpoint_create(command)
        elif operation == "checkpoint_restore":
            _checkpoint_restore(command)
        elif operation == "checkpoint_list":
            _checkpoint_list(command)
        elif operation == "run_script":
            _run_script(command)
        elif operation == "capture_viewport":
            _capture_viewport(command)
        elif operation == "multiview_capture":
            _multiview_capture(command)
        elif operation == "save":
            _save(command)
        elif operation == "quit":
            _response(command_id, True, "Visible Blender session closing")

            def _quit_later():
                bpy.ops.wm.quit_blender()
                return None

            bpy.app.timers.register(_quit_later, first_interval=0.15)
        else:
            _response(command_id, False, f"Unsupported Blender live operation: {operation}")
    finally:
        result_path = RESULTS / f"{command_id}.json"
        result = {}
        if result_path.is_file():
            try:
                result = json.loads(result_path.read_text(encoding="utf-8-sig"))
            except Exception:
                result = {}
        _trajectory_append(
            {
                "id": command_id,
                "operation": operation,
                "started_at": started_at,
                "completed_at": time.time(),
                "ok": bool(result.get("ok")),
                "summary": result.get("summary"),
                "before": before_snapshot,
                "after": result.get("snapshot") or _scene_snapshot(),
            }
        )
        try:
            inflight_path.unlink()
        except OSError:
            pass


def _tick():
    try:
        _write_presence()
        commands = sorted(INBOX.glob("*.json"))
        if commands:
            _process(commands[0])
            _write_presence(force=True)
    except Exception:
        # Keep the visible Blender session alive; the next presence write will
        # show whether the companion recovered.
        pass
    return 0.1


def _run_modeling_smoke_fixture() -> dict:
    object_name = "BenchmarkBody"
    obj = bpy.context.scene.objects.get(object_name)
    if obj is None:
        raise RuntimeError(
            f"modeling smoke requires object in scene: {object_name}"
        )

    expected_location = [1.25, -0.5, 0.75]
    expected_scale = [1.0, 1.25, 0.75]
    smoke_ids = {
        "transform": "smoke-model-transform",
        "transform_invalid": "smoke-model-invalid",
        "create": "smoke-model-create",
        "create_duplicate": "smoke-model-create-duplicate",
        "modifier": "smoke-model-modifier",
        "modifier_duplicate": "smoke-model-modifier-duplicate",
        "array": "smoke-model-array",
        "array_invalid": "smoke-model-array-invalid",
        "scatter": "smoke-model-scatter",
        "scatter_invalid": "smoke-model-scatter-invalid",
        "cut_preview": "smoke-model-cut-preview",
        "cut_invalid": "smoke-model-cut-invalid",
        "cut_commit": "smoke-model-cut-commit",
        "cut_cancel": "smoke-model-cut-cancel",
        "cleanup_blocked": "smoke-model-cleanup-blocked",
        "cleanup_diag": "smoke-model-cleanup-diagnostic",
        "cleanup": "smoke-model-cleanup",
        "cleanup_stale": "smoke-model-cleanup-stale",
        "degenerate_diag": "smoke-model-degenerate-diagnostic",
        "degenerate_preview": "smoke-model-degenerate-preview",
        "degenerate_cancel": "smoke-model-degenerate-cancel",
        "degenerate_preview_commit": "smoke-model-degenerate-preview-commit",
        "degenerate_commit": "smoke-model-degenerate-commit",
        "degenerate_stale": "smoke-model-degenerate-stale",
        "merge_preview": "smoke-model-merge-preview",
        "merge_cancel": "smoke-model-merge-cancel",
        "merge_preview_commit": "smoke-model-merge-preview-commit",
        "merge_commit": "smoke-model-merge-commit",
        "merge_stale": "smoke-model-merge-stale",
        "merge_noop": "smoke-model-merge-noop",
        "hole_diag": "smoke-model-hole-diagnostic",
        "hole_preview": "smoke-model-hole-preview",
        "hole_cancel": "smoke-model-hole-cancel",
        "hole_invalid": "smoke-model-hole-invalid",
        "hole_preview_commit": "smoke-model-hole-preview-commit",
        "hole_commit": "smoke-model-hole-commit",
        "hole_stale": "smoke-model-hole-stale",
    }
    result_paths = {
        key: RESULTS / f"{identifier}.json"
        for key, identifier in smoke_ids.items()
    }
    temporary_name = "SmokePrimitive"
    degenerate_name = "SmokeDegenerate"
    merge_name = "SmokeMergeSelection"
    hole_name = "SmokeBoundaryHole"
    summaries = {}

    if bpy.context.scene.objects.get(temporary_name) is not None:
        raise RuntimeError(
            f"modeling smoke temporary object already exists: {temporary_name}"
        )
    if bpy.context.scene.objects.get(degenerate_name) is not None:
        raise RuntimeError(
            f"modeling smoke degenerate object already exists: {degenerate_name}"
        )
    if bpy.context.scene.objects.get(merge_name) is not None:
        raise RuntimeError(
            f"modeling smoke merge object already exists: {merge_name}"
        )
    if bpy.context.scene.objects.get(hole_name) is not None:
        raise RuntimeError(
            f"modeling smoke hole object already exists: {hole_name}"
        )

    try:
        transform_path = INBOX / f"{smoke_ids['transform']}.json"
        _write_json_atomic(
            transform_path,
            {
                "id": smoke_ids["transform"],
                "operation": "object_transform",
                "object_name": object_name,
                "location": expected_location,
                "rotation_euler": [0.0, 0.0, 0.25],
                "scale": expected_scale,
            },
        )
        _process(transform_path)

        invalid_transform_path = INBOX / f"{smoke_ids['transform_invalid']}.json"
        _write_json_atomic(
            invalid_transform_path,
            {
                "id": smoke_ids["transform_invalid"],
                "operation": "object_transform",
                "object_name": object_name,
                "scale": [1.0, 0.0, 1.0],
            },
        )
        _process(invalid_transform_path)

        create_path = INBOX / f"{smoke_ids['create']}.json"
        _write_json_atomic(
            create_path,
            {
                "id": smoke_ids["create"],
                "operation": "create_primitive",
                "name": temporary_name,
                "primitive": "cube",
                "location": [3.0, 0.0, 0.0],
                "size": 0.5,
            },
        )
        _process(create_path)

        duplicate_create_path = INBOX / f"{smoke_ids['create_duplicate']}.json"
        _write_json_atomic(
            duplicate_create_path,
            {
                "id": smoke_ids["create_duplicate"],
                "operation": "create_primitive",
                "name": temporary_name,
                "primitive": "cube",
                "size": 0.25,
            },
        )
        _process(duplicate_create_path)

        modifier_path = INBOX / f"{smoke_ids['modifier']}.json"
        _write_json_atomic(
            modifier_path,
            {
                "id": smoke_ids["modifier"],
                "operation": "add_modifier",
                "object_name": temporary_name,
                "name": "SmokeBevel",
                "type": "BEVEL",
                "width": 0.05,
                "segments": 2,
            },
        )
        _process(modifier_path)

        duplicate_modifier_path = INBOX / f"{smoke_ids['modifier_duplicate']}.json"
        _write_json_atomic(
            duplicate_modifier_path,
            {
                "id": smoke_ids["modifier_duplicate"],
                "operation": "add_modifier",
                "object_name": temporary_name,
                "name": "SmokeBevel",
                "type": "BEVEL",
                "width": 0.02,
                "segments": 1,
            },
        )
        _process(duplicate_modifier_path)

        array_path = INBOX / f"{smoke_ids['array']}.json"
        _write_json_atomic(
            array_path,
            {
                "id": smoke_ids["array"],
                "operation": "add_modifier",
                "object_name": temporary_name,
                "name": "SmokeArray",
                "type": "ARRAY",
                "count": 4,
                "relative_offset": [1.5, 0.0, 0.0],
            },
        )
        _process(array_path)

        invalid_array_path = INBOX / f"{smoke_ids['array_invalid']}.json"
        _write_json_atomic(
            invalid_array_path,
            {
                "id": smoke_ids["array_invalid"],
                "operation": "add_modifier",
                "object_name": temporary_name,
                "name": "SmokeArrayTooLarge",
                "type": "ARRAY",
                "count": 65,
                "relative_offset": [1.0, 0.0, 0.0],
            },
        )
        _process(invalid_array_path)

        cut_preview_path = INBOX / f"{smoke_ids['cut_preview']}.json"
        _write_json_atomic(
            cut_preview_path,
            {
                "id": smoke_ids["cut_preview"],
                "operation": "boolean_cut_preview",
                "object_name": object_name,
                "name": "SmokeCutters",
                "profiles": [
                    {"type": "box", "offset": [0.0, 0.0, 0.0], "dimensions": [0.25, 0.25, 3.0]},
                    {"type": "circle", "offset": [0.6, 0.0, 0.0], "radius": 0.2, "depth": 3.0, "segments": 16},
                    {"type": "slot", "offset": [-0.6, 0.0, 0.0], "length": 0.8, "width": 0.25, "depth": 3.0, "segments": 16},
                    {"type": "polygon", "offset": [0.0, 0.6, 0.0], "points": [[-0.2, -0.2], [0.2, -0.2], [0.0, 0.25]], "depth": 3.0},
                    {"type": "vent", "offset": [0.0, -0.6, 0.0], "length": 0.6, "width": 0.12, "depth": 3.0, "segments": 16, "count": 3, "spacing": 0.25, "axis": "X"},
                ],
            },
        )
        _process(cut_preview_path)

        cut_invalid_path = INBOX / f"{smoke_ids['cut_invalid']}.json"
        _write_json_atomic(
            cut_invalid_path,
            {
                "id": smoke_ids["cut_invalid"],
                "operation": "boolean_cut_preview",
                "object_name": object_name,
                "name": "SmokeCuttersTooMany",
                "profiles": [
                    {"type": "vent", "length": 0.6, "width": 0.12, "depth": 3.0, "count": 8, "spacing": 0.25},
                    {"type": "circle", "radius": 0.2, "depth": 3.0},
                ],
            },
        )
        _process(cut_invalid_path)

        cut_commit_path = INBOX / f"{smoke_ids['cut_commit']}.json"
        _write_json_atomic(
            cut_commit_path,
            {
                "id": smoke_ids["cut_commit"],
                "operation": "boolean_cut_commit",
                "preview_id": smoke_ids["cut_preview"],
            },
        )
        _process(cut_commit_path)

        cut_cancel_path = INBOX / f"{smoke_ids['cut_cancel']}.json"
        _write_json_atomic(
            cut_cancel_path,
            {
                "id": smoke_ids["cut_cancel"],
                "operation": "boolean_cut_cancel",
                "preview_id": smoke_ids["cut_preview"],
            },
        )
        _process(cut_cancel_path)

        temporary = bpy.context.scene.objects.get(temporary_name)
        if temporary is None or temporary.type != "MESH":
            raise RuntimeError("cleanup smoke temporary mesh is unavailable")
        temporary.data.vertices.add(1)
        temporary.data.vertices[-1].co = (9.0, 9.0, 9.0)
        temporary.data.update()
        bpy.context.view_layer.update()

        cleanup_blocked_path = INBOX / f"{smoke_ids['cleanup_blocked']}.json"
        _write_json_atomic(
            cleanup_blocked_path,
            {
                "id": smoke_ids["cleanup_blocked"],
                "operation": "quality_gate",
                "checks": [
                    {
                        "type": "mesh_quality",
                        "object_name": temporary_name,
                        "evaluated": False,
                        "diagnostic_limit": 8,
                        "max_loose_vertices": 0,
                    }
                ],
            },
        )
        _process(cleanup_blocked_path)

        for modifier_name in ("SmokeArray", "SmokeBevel"):
            modifier = temporary.modifiers.get(modifier_name)
            if modifier is not None:
                temporary.modifiers.remove(modifier)
        bpy.context.view_layer.update()

        cleanup_diag_path = INBOX / f"{smoke_ids['cleanup_diag']}.json"
        _write_json_atomic(
            cleanup_diag_path,
            {
                "id": smoke_ids["cleanup_diag"],
                "operation": "quality_gate",
                "checks": [
                    {
                        "type": "mesh_quality",
                        "object_name": temporary_name,
                        "evaluated": False,
                        "diagnostic_limit": 8,
                        "max_loose_vertices": 0,
                    }
                ],
            },
        )
        _process(cleanup_diag_path)
        cleanup_diag = json.loads(
            result_paths["cleanup_diag"].read_text(encoding="utf-8-sig")
        )
        cleanup_check = (cleanup_diag.get("checks") or [{}])[0]
        cleanup_hints = cleanup_check.get("repair_hints") or []
        cleanup_hint = next(
            (
                hint for hint in cleanup_hints
                if hint.get("code") == "remove_isolated_vertices"
                and hint.get("automatic")
            ),
            None,
        )
        if cleanup_hint is None:
            raise RuntimeError("mesh quality did not expose the bounded loose-vertex auto-fix")
        cleanup_arguments = (cleanup_hint.get("auto_fix") or {}).get("arguments") or {}

        cleanup_path = INBOX / f"{smoke_ids['cleanup']}.json"
        _write_json_atomic(
            cleanup_path,
            {
                "id": smoke_ids["cleanup"],
                "operation": "mesh_cleanup",
                **cleanup_arguments,
            },
        )
        _process(cleanup_path)

        cleanup_stale_path = INBOX / f"{smoke_ids['cleanup_stale']}.json"
        _write_json_atomic(
            cleanup_stale_path,
            {
                "id": smoke_ids["cleanup_stale"],
                "operation": "mesh_cleanup",
                **cleanup_arguments,
            },
        )
        _process(cleanup_stale_path)

        scatter_path = INBOX / f"{smoke_ids['scatter']}.json"
        _write_json_atomic(
            scatter_path,
            {
                "id": smoke_ids["scatter"],
                "operation": "surface_scatter",
                "object_name": object_name,
                "source_object_name": temporary_name,
                "name": "SmokeScatter",
                "density": 100.0,
                "seed": 37,
                "max_instances": 25,
                "scale_min": 0.8,
                "scale_max": 1.2,
                "align_to_normal": True,
                "keep_surface": True,
            },
        )
        _process(scatter_path)

        invalid_scatter_path = INBOX / f"{smoke_ids['scatter_invalid']}.json"
        _write_json_atomic(
            invalid_scatter_path,
            {
                "id": smoke_ids["scatter_invalid"],
                "operation": "surface_scatter",
                "object_name": object_name,
                "source_object_name": temporary_name,
                "name": "SmokeScatterTooLarge",
                "density": 1.0,
                "seed": 37,
                "max_instances": 5001,
            },
        )
        _process(invalid_scatter_path)

        degenerate_mesh = bpy.data.meshes.new(degenerate_name + "Mesh")
        degenerate_mesh.from_pydata(
            [
                (0.0, 0.0, 0.0),
                (0.0, 0.0, 0.0),
                (1.0, 0.0, 0.0),
                (0.0, 1.0, 0.0),
                (2.0, 0.0, 0.0),
                (3.0, 0.0, 0.0),
                (4.0, 0.0, 0.0),
            ],
            [
                (0, 1),
                (1, 2),
                (2, 3),
                (3, 0),
                (4, 5),
                (5, 6),
                (6, 4),
            ],
            [(0, 1, 2, 3), (4, 5, 6)],
        )
        degenerate_mesh.update()
        degenerate_object = bpy.data.objects.new(degenerate_name, degenerate_mesh)
        bpy.context.scene.collection.objects.link(degenerate_object)
        bpy.context.view_layer.update()

        degenerate_diag_path = INBOX / f"{smoke_ids['degenerate_diag']}.json"
        _write_json_atomic(
            degenerate_diag_path,
            {
                "id": smoke_ids["degenerate_diag"],
                "operation": "quality_gate",
                "checks": [
                    {
                        "type": "mesh_quality",
                        "object_name": degenerate_name,
                        "evaluated": False,
                        "epsilon": 1e-10,
                        "diagnostic_limit": 8,
                        "max_zero_length_edges": 0,
                        "max_degenerate_faces": 0,
                    }
                ],
            },
        )
        _process(degenerate_diag_path)
        degenerate_diag = json.loads(
            result_paths["degenerate_diag"].read_text(encoding="utf-8-sig")
        )
        degenerate_check = (degenerate_diag.get("checks") or [{}])[0]
        degenerate_hint = next(
            (
                hint for hint in (degenerate_check.get("repair_hints") or [])
                if hint.get("code") == "preview_degenerate_dissolve"
            ),
            None,
        )
        if not degenerate_hint or not degenerate_hint.get("preview_available"):
            raise RuntimeError("mesh quality did not expose the bounded degenerate repair preview")
        degenerate_arguments = (degenerate_hint.get("preview_fix") or {}).get("arguments") or {}
        original_degenerate_sha = str(degenerate_arguments.get("expected_base_geometry_sha256") or "")

        degenerate_preview_path = INBOX / f"{smoke_ids['degenerate_preview']}.json"
        _write_json_atomic(
            degenerate_preview_path,
            {
                "id": smoke_ids["degenerate_preview"],
                "operation": "degenerate_repair_preview",
                **degenerate_arguments,
            },
        )
        _process(degenerate_preview_path)

        degenerate_cancel_path = INBOX / f"{smoke_ids['degenerate_cancel']}.json"
        _write_json_atomic(
            degenerate_cancel_path,
            {
                "id": smoke_ids["degenerate_cancel"],
                "operation": "degenerate_repair_cancel",
                "preview_id": smoke_ids["degenerate_preview"],
            },
        )
        _process(degenerate_cancel_path)

        degenerate_preview_commit_path = INBOX / f"{smoke_ids['degenerate_preview_commit']}.json"
        _write_json_atomic(
            degenerate_preview_commit_path,
            {
                "id": smoke_ids["degenerate_preview_commit"],
                "operation": "degenerate_repair_preview",
                **degenerate_arguments,
            },
        )
        _process(degenerate_preview_commit_path)

        degenerate_commit_path = INBOX / f"{smoke_ids['degenerate_commit']}.json"
        _write_json_atomic(
            degenerate_commit_path,
            {
                "id": smoke_ids["degenerate_commit"],
                "operation": "degenerate_repair_commit",
                "preview_id": smoke_ids["degenerate_preview_commit"],
            },
        )
        _process(degenerate_commit_path)

        degenerate_stale_path = INBOX / f"{smoke_ids['degenerate_stale']}.json"
        _write_json_atomic(
            degenerate_stale_path,
            {
                "id": smoke_ids["degenerate_stale"],
                "operation": "degenerate_repair_preview",
                **degenerate_arguments,
            },
        )
        _process(degenerate_stale_path)

        merge_mesh = bpy.data.meshes.new(merge_name + "Mesh")
        merge_mesh.from_pydata(
            [
                (0.0, 0.0, 0.0),
                (0.00005, 0.0, 0.0),
                (0.00004, 0.0, 0.0),
                (1.0, 0.0, 0.0),
                (0.0, 1.0, 0.0),
                (1.0, 1.0, 0.0),
            ],
            [(0, 3), (1, 3), (2, 3), (0, 4), (3, 5), (4, 5)],
            [(0, 3, 5, 4)],
        )
        merge_mesh.update()
        merge_object = bpy.data.objects.new(merge_name, merge_mesh)
        bpy.context.scene.collection.objects.link(merge_object)
        bpy.context.view_layer.update()
        merge_original_sha, merge_original_counts = _mesh_fingerprint(merge_object, evaluated=False)
        merge_arguments = {
            "object_name": merge_name,
            "expected_base_geometry_sha256": merge_original_sha,
            "vertex_indices": [0, 1],
            "distance": 0.0001,
        }

        merge_preview_path = INBOX / f"{smoke_ids['merge_preview']}.json"
        _write_json_atomic(
            merge_preview_path,
            {
                "id": smoke_ids["merge_preview"],
                "operation": "merge_by_distance_preview",
                **merge_arguments,
            },
        )
        _process(merge_preview_path)
        merge_preview_result = json.loads(
            result_paths["merge_preview"].read_text(encoding="utf-8-sig")
        )
        merge_unselected_survived_preview = bool(
            merge_preview_result.get("unselected_vertices_preserved")
            and int(merge_preview_result.get("unselected_vertices_verified") or 0) == 4
        )

        merge_cancel_path = INBOX / f"{smoke_ids['merge_cancel']}.json"
        _write_json_atomic(
            merge_cancel_path,
            {
                "id": smoke_ids["merge_cancel"],
                "operation": "merge_by_distance_cancel",
                "preview_id": smoke_ids["merge_preview"],
            },
        )
        _process(merge_cancel_path)

        merge_noop_path = INBOX / f"{smoke_ids['merge_noop']}.json"
        _write_json_atomic(
            merge_noop_path,
            {
                "id": smoke_ids["merge_noop"],
                "operation": "merge_by_distance_preview",
                "object_name": merge_name,
                "expected_base_geometry_sha256": merge_original_sha,
                "vertex_indices": [3, 4],
                "distance": 0.0001,
            },
        )
        _process(merge_noop_path)

        merge_preview_commit_path = INBOX / f"{smoke_ids['merge_preview_commit']}.json"
        _write_json_atomic(
            merge_preview_commit_path,
            {
                "id": smoke_ids["merge_preview_commit"],
                "operation": "merge_by_distance_preview",
                **merge_arguments,
            },
        )
        _process(merge_preview_commit_path)
        merge_preview_commit_result = json.loads(
            result_paths["merge_preview_commit"].read_text(encoding="utf-8-sig")
        )
        merge_unselected_survived_commit = bool(
            merge_preview_commit_result.get("unselected_vertices_preserved")
            and int(merge_preview_commit_result.get("unselected_vertices_verified") or 0) == 4
        )

        merge_commit_path = INBOX / f"{smoke_ids['merge_commit']}.json"
        _write_json_atomic(
            merge_commit_path,
            {
                "id": smoke_ids["merge_commit"],
                "operation": "merge_by_distance_commit",
                "preview_id": smoke_ids["merge_preview_commit"],
            },
        )
        _process(merge_commit_path)

        merge_stale_path = INBOX / f"{smoke_ids['merge_stale']}.json"
        _write_json_atomic(
            merge_stale_path,
            {
                "id": smoke_ids["merge_stale"],
                "operation": "merge_by_distance_preview",
                **merge_arguments,
            },
        )
        _process(merge_stale_path)

        hole_mesh = bpy.data.meshes.new(hole_name + "Mesh")
        hole_mesh.from_pydata(
            [
                (-2.0, -2.0, 0.0),
                (2.0, -2.0, 0.0),
                (2.0, 2.0, 0.0),
                (-2.0, 2.0, 0.0),
                (-0.5, -0.5, 0.0),
                (0.5, -0.5, 0.0),
                (0.5, 0.5, 0.0),
                (-0.5, 0.5, 0.0),
            ],
            [],
            [
                (0, 1, 5, 4),
                (1, 2, 6, 5),
                (2, 3, 7, 6),
                (3, 0, 4, 7),
            ],
        )
        hole_mesh.update()
        hole_object = bpy.data.objects.new(hole_name, hole_mesh)
        bpy.context.scene.collection.objects.link(hole_object)
        bpy.context.view_layer.update()

        hole_diag_path = INBOX / f"{smoke_ids['hole_diag']}.json"
        _write_json_atomic(
            hole_diag_path,
            {
                "id": smoke_ids["hole_diag"],
                "operation": "quality_gate",
                "checks": [
                    {
                        "type": "mesh_quality",
                        "object_name": hole_name,
                        "evaluated": False,
                        "diagnostic_limit": 8,
                        "max_boundary_edges": 0,
                    }
                ],
            },
        )
        _process(hole_diag_path)
        hole_diag = json.loads(result_paths["hole_diag"].read_text(encoding="utf-8-sig"))
        hole_check = (hole_diag.get("checks") or [{}])[0]
        hole_metrics = hole_check.get("metrics") or {}
        if int(hole_metrics.get("boundary_loops") or 0) != 2:
            raise RuntimeError("boundary-hole diagnostic did not detect the inner and outer closed loops")
        hole_hint = next(
            (
                hint for hint in (hole_check.get("repair_hints") or [])
                if hint.get("code") == "preview_boundary_hole_fill"
            ),
            None,
        )
        if not hole_hint:
            raise RuntimeError("mesh quality did not expose boundary-loop preview candidates")
        candidates = [
            candidate for candidate in (hole_hint.get("preview_candidates") or [])
            if candidate.get("preview_available") and (candidate.get("preview_fix") or {}).get("arguments")
        ]
        if len(candidates) < 2:
            raise RuntimeError("boundary-hole diagnostic did not expose both bounded loop candidates")
        chosen_hole = min(candidates, key=lambda candidate: float(candidate.get("perimeter") or 0.0))
        hole_arguments = ((chosen_hole.get("preview_fix") or {}).get("arguments") or {})
        hole_original_sha = str(hole_arguments.get("expected_base_geometry_sha256") or "")
        hole_edges = list(hole_arguments.get("edge_indices") or [])
        if len(hole_edges) != 4:
            raise RuntimeError("boundary-hole diagnostic did not expose the four-edge inner loop")

        hole_preview_path = INBOX / f"{smoke_ids['hole_preview']}.json"
        _write_json_atomic(
            hole_preview_path,
            {
                "id": smoke_ids["hole_preview"],
                "operation": "boundary_hole_fill_preview",
                **hole_arguments,
            },
        )
        _process(hole_preview_path)

        hole_cancel_path = INBOX / f"{smoke_ids['hole_cancel']}.json"
        _write_json_atomic(
            hole_cancel_path,
            {
                "id": smoke_ids["hole_cancel"],
                "operation": "boundary_hole_fill_cancel",
                "preview_id": smoke_ids["hole_preview"],
            },
        )
        _process(hole_cancel_path)

        hole_invalid_path = INBOX / f"{smoke_ids['hole_invalid']}.json"
        _write_json_atomic(
            hole_invalid_path,
            {
                "id": smoke_ids["hole_invalid"],
                "operation": "boundary_hole_fill_preview",
                "object_name": hole_name,
                "expected_base_geometry_sha256": hole_original_sha,
                "edge_indices": hole_edges[:3],
            },
        )
        _process(hole_invalid_path)

        hole_preview_commit_path = INBOX / f"{smoke_ids['hole_preview_commit']}.json"
        _write_json_atomic(
            hole_preview_commit_path,
            {
                "id": smoke_ids["hole_preview_commit"],
                "operation": "boundary_hole_fill_preview",
                **hole_arguments,
            },
        )
        _process(hole_preview_commit_path)

        hole_commit_path = INBOX / f"{smoke_ids['hole_commit']}.json"
        _write_json_atomic(
            hole_commit_path,
            {
                "id": smoke_ids["hole_commit"],
                "operation": "boundary_hole_fill_commit",
                "preview_id": smoke_ids["hole_preview_commit"],
            },
        )
        _process(hole_commit_path)

        hole_stale_path = INBOX / f"{smoke_ids['hole_stale']}.json"
        _write_json_atomic(
            hole_stale_path,
            {
                "id": smoke_ids["hole_stale"],
                "operation": "boundary_hole_fill_preview",
                **hole_arguments,
            },
        )
        _process(hole_stale_path)

        for key, result_path in result_paths.items():
            if not result_path.is_file():
                raise RuntimeError(
                    f"modeling smoke did not create durable result for {key}"
                )
            summaries[key] = json.loads(
                result_path.read_text(encoding="utf-8-sig")
            )

        if not bool(summaries["transform"].get("ok")):
            raise RuntimeError(
                "modeling transform positive control failed: "
                + str(summaries["transform"].get("summary") or "unknown failure")
            )
        if bool(summaries["transform_invalid"].get("ok")):
            raise RuntimeError(
                "modeling transform negative control was incorrectly accepted"
            )
        if not bool(summaries["create"].get("ok")):
            raise RuntimeError(
                "primitive creation smoke failed: "
                + str(summaries["create"].get("summary") or "unknown failure")
            )
        if bool(summaries["create_duplicate"].get("ok")):
            raise RuntimeError(
                "duplicate primitive name was incorrectly accepted"
            )
        if not bool(summaries["modifier"].get("ok")):
            raise RuntimeError(
                "modifier insertion smoke failed: "
                + str(summaries["modifier"].get("summary") or "unknown failure")
            )
        if bool(summaries["modifier_duplicate"].get("ok")):
            raise RuntimeError(
                "duplicate modifier name was incorrectly accepted"
            )
        if not bool(summaries["array"].get("ok")):
            raise RuntimeError(
                "ARRAY modifier smoke failed: "
                + str(summaries["array"].get("summary") or "unknown failure")
            )
        if bool(summaries["array_invalid"].get("ok")):
            raise RuntimeError(
                "ARRAY count above the contract limit was incorrectly accepted"
            )
        if not bool(summaries["scatter"].get("ok")):
            raise RuntimeError(
                "surface scatter smoke failed: "
                + str(summaries["scatter"].get("summary") or "unknown failure")
            )
        if bool(summaries["scatter_invalid"].get("ok")):
            raise RuntimeError(
                "surface scatter instance cap overflow was incorrectly accepted"
            )
        if not bool(summaries["cut_preview"].get("ok")):
            raise RuntimeError(
                "boolean cutter preview smoke failed: "
                + str(summaries["cut_preview"].get("summary") or "unknown failure")
            )
        if bool(summaries["cut_invalid"].get("ok")):
            raise RuntimeError("boolean cutter expansion overflow was incorrectly accepted")
        if not bool(summaries["cut_commit"].get("ok")):
            raise RuntimeError(
                "boolean cutter commit smoke failed: "
                + str(summaries["cut_commit"].get("summary") or "unknown failure")
            )
        if not bool(summaries["cut_cancel"].get("ok")):
            raise RuntimeError(
                "boolean cutter cancel smoke failed: "
                + str(summaries["cut_cancel"].get("summary") or "unknown failure")
            )

        transformed = summaries["transform"].get("object") or {}
        if transformed.get("location") != expected_location:
            raise RuntimeError(
                "modeling smoke location mismatch: "
                + repr(transformed.get("location"))
            )
        if transformed.get("scale") != expected_scale:
            raise RuntimeError(
                "modeling smoke scale mismatch: "
                + repr(transformed.get("scale"))
            )

        current = _object_details(obj)
        if current.get("location") != expected_location:
            raise RuntimeError(
                "negative modeling smoke changed object location unexpectedly"
            )
        if current.get("scale") != expected_scale:
            raise RuntimeError(
                "negative modeling smoke changed object scale unexpectedly"
            )

        created = summaries["create"].get("object") or {}
        if created.get("name") != temporary_name or created.get("type") != "MESH":
            raise RuntimeError(
                "created primitive identity/type mismatch: " + repr(created)
            )
        if (created.get("mesh") or {}).get("vertices") != 8:
            raise RuntimeError(
                "created cube did not expose the expected 8 vertices"
            )

        modified = summaries["modifier"].get("object") or {}
        modifiers = modified.get("modifiers") or []
        if not any(
            item.get("name") == "SmokeBevel" and item.get("type") == "BEVEL"
            for item in modifiers
        ):
            raise RuntimeError(
                "smoke BEVEL modifier is missing from object details"
            )
        budget = summaries["modifier"].get("runtime_budget") or {}
        if not bool(budget.get("allowed")):
            raise RuntimeError(
                "smoke modifier unexpectedly exceeded runtime budget"
            )

        array_modified = summaries["array"].get("object") or {}
        array_modifiers = array_modified.get("modifiers") or []
        if not any(
            item.get("name") == "SmokeArray" and item.get("type") == "ARRAY"
            for item in array_modifiers
        ):
            raise RuntimeError("smoke ARRAY modifier is missing from object details")
        array_budget = summaries["array"].get("runtime_budget") or {}
        if not bool(array_budget.get("allowed")):
            raise RuntimeError("smoke ARRAY unexpectedly exceeded runtime budget")
        if int(array_budget.get("count") or 0) != 4:
            raise RuntimeError("smoke ARRAY runtime budget did not preserve count=4")

        scatter_modified = summaries["scatter"].get("object") or {}
        scatter_modifiers = scatter_modified.get("modifiers") or []
        if not any(
            item.get("name") == "SmokeScatter" and item.get("type") == "NODES"
            for item in scatter_modifiers
        ):
            raise RuntimeError("smoke surface scatter Geometry Nodes modifier is missing")
        scatter_budget = summaries["scatter"].get("runtime_budget") or {}
        if not bool(scatter_budget.get("allowed")):
            raise RuntimeError("smoke surface scatter unexpectedly exceeded runtime budget")
        if int(summaries["scatter"].get("instance_count") or 0) != 25:
            raise RuntimeError("smoke surface scatter did not enforce max_instances=25")
        scatter_parameters = summaries["scatter"].get("scatter_parameters") or {}
        if int(scatter_parameters.get("seed") or -1) != 37:
            raise RuntimeError("smoke surface scatter did not preserve seed=37")

        cut_preview = summaries["cut_preview"]
        if cut_preview.get("state") != "preview":
            raise RuntimeError("boolean cutter preview did not report preview state")
        if len(cut_preview.get("cutters") or []) != 7:
            raise RuntimeError("boolean cutter preview did not create exactly 7 bounded cutters")
        if not bool((cut_preview.get("runtime_budget") or {}).get("allowed")):
            raise RuntimeError("boolean cutter preview unexpectedly exceeded runtime budget")
        profile_types = {item.get("type") for item in (cut_preview.get("profiles") or [])}
        if profile_types != {"box", "circle", "slot", "polygon", "vent"}:
            raise RuntimeError("boolean cutter preview did not preserve all typed profile variants")
        if summaries["cut_commit"].get("state") != "committed":
            raise RuntimeError("boolean cutter commit did not report committed state")
        if len(summaries["cut_commit"].get("cutters") or []) != 7:
            raise RuntimeError("boolean cutter commit lost cutter membership")
        if summaries["cut_cancel"].get("state") != "cancelled":
            raise RuntimeError("boolean cutter cancel did not report cancelled state")
        if len(summaries["cut_cancel"].get("removed_cutters") or []) != 7:
            raise RuntimeError("boolean cutter cancel did not remove all cutters")
        if len(summaries["cut_cancel"].get("removed_modifiers") or []) != 7:
            raise RuntimeError("boolean cutter cancel did not remove all preview modifiers")
        before_names = [item.get("name") for item in ((cut_preview.get("before") or {}).get("modifiers") or [])]
        after_names = [item.get("name") for item in ((summaries["cut_cancel"].get("object") or {}).get("modifiers") or [])]
        if before_names != after_names:
            raise RuntimeError("boolean cutter cancel did not restore the original modifier stack")
        if _boolean_cut_preview_items(smoke_ids["cut_preview"]):
            raise RuntimeError("boolean cutter cancel left preview cutter objects in the scene")

        cleanup_blocked = summaries["cleanup_blocked"]
        if bool(cleanup_blocked.get("ok")):
            raise RuntimeError("modifier-blocked mesh cleanup diagnostic unexpectedly passed")
        cleanup_blocked_check = (cleanup_blocked.get("checks") or [{}])[0]
        blocked_hint = next(
            (
                hint for hint in (cleanup_blocked_check.get("repair_hints") or [])
                if hint.get("code") == "remove_isolated_vertices"
            ),
            None,
        )
        if not blocked_hint or blocked_hint.get("automatic"):
            raise RuntimeError("mesh cleanup diagnostic incorrectly offered auto-fix with modifiers")
        if "modifiers_require_manual_review" not in (blocked_hint.get("automatic_blockers") or []):
            raise RuntimeError("mesh cleanup modifier safety blocker was not reported")

        cleanup_diag = summaries["cleanup_diag"]
        if bool(cleanup_diag.get("ok")):
            raise RuntimeError("mesh cleanup diagnostic unexpectedly passed with an isolated vertex")
        cleanup_check = (cleanup_diag.get("checks") or [{}])[0]
        if int((cleanup_check.get("metrics") or {}).get("loose_vertices") or 0) != 1:
            raise RuntimeError("mesh cleanup diagnostic did not measure exactly one loose vertex")
        cleanup_hints = cleanup_check.get("repair_hints") or []
        cleanup_hint = next(
            (hint for hint in cleanup_hints if hint.get("code") == "remove_isolated_vertices"),
            None,
        )
        if not cleanup_hint or not cleanup_hint.get("automatic"):
            raise RuntimeError("mesh cleanup diagnostic did not expose its revision-guarded auto-fix")
        cleanup = summaries["cleanup"]
        if not bool(cleanup.get("ok")):
            raise RuntimeError(
                "mesh cleanup smoke failed: "
                + str(cleanup.get("summary") or "unknown failure")
            )
        if int(cleanup.get("removed_vertices") or 0) != 1:
            raise RuntimeError("mesh cleanup did not remove exactly one isolated vertex")
        if int((cleanup.get("before_mesh") or {}).get("vertices") or 0) != 9:
            raise RuntimeError("mesh cleanup did not report the expected 9-vertex pre-state")
        if int((cleanup.get("after_mesh") or {}).get("vertices") or 0) != 8:
            raise RuntimeError("mesh cleanup did not restore the expected 8-vertex base mesh")
        if cleanup.get("before_geometry_sha256") == cleanup.get("after_geometry_sha256"):
            raise RuntimeError("mesh cleanup did not produce a new base geometry fingerprint")
        cleanup_stale = summaries["cleanup_stale"]
        if bool(cleanup_stale.get("ok")):
            raise RuntimeError("stale mesh cleanup fingerprint was incorrectly accepted")
        if "fingerprint changed" not in str(cleanup_stale.get("summary") or ""):
            raise RuntimeError("stale mesh cleanup rejection did not report fingerprint mismatch")

        degenerate_diag = summaries["degenerate_diag"]
        if bool(degenerate_diag.get("ok")):
            raise RuntimeError("degenerate repair diagnostic unexpectedly passed")
        degenerate_check = (degenerate_diag.get("checks") or [{}])[0]
        degenerate_metrics = degenerate_check.get("metrics") or {}
        if int(degenerate_metrics.get("zero_length_edges") or 0) != 1:
            raise RuntimeError("degenerate repair diagnostic did not measure one zero-length edge")
        if int(degenerate_metrics.get("degenerate_faces") or 0) != 1:
            raise RuntimeError("degenerate repair diagnostic did not measure one degenerate face")
        preview_hint = next(
            (
                hint for hint in (degenerate_check.get("repair_hints") or [])
                if hint.get("code") == "preview_degenerate_dissolve"
            ),
            None,
        )
        if not preview_hint or not preview_hint.get("preview_available"):
            raise RuntimeError("degenerate repair diagnostic did not expose preview evidence")

        degenerate_preview = summaries["degenerate_preview"]
        if not bool(degenerate_preview.get("ok")) or degenerate_preview.get("state") != "preview":
            raise RuntimeError("degenerate repair preview smoke failed")
        preview_before = degenerate_preview.get("before_issues") or {}
        preview_after = degenerate_preview.get("candidate_issues") or {}
        if int(preview_before.get("zero_length_edges") or 0) != 1 or int(preview_before.get("degenerate_faces") or 0) != 1:
            raise RuntimeError("degenerate repair preview lost diagnosed pre-state")
        if int(preview_after.get("zero_length_edges") or 0) != 0 or int(preview_after.get("degenerate_faces") or 0) != 0:
            raise RuntimeError("degenerate repair candidate did not clear both diagnosed categories")

        degenerate_cancel = summaries["degenerate_cancel"]
        if not bool(degenerate_cancel.get("ok")) or degenerate_cancel.get("state") != "cancelled":
            raise RuntimeError("degenerate repair cancel smoke failed")
        if degenerate_cancel.get("restored_geometry_sha256") != original_degenerate_sha:
            raise RuntimeError("degenerate repair cancel did not restore the exact original fingerprint")
        if _degenerate_preview_backups(smoke_ids["degenerate_preview"]):
            raise RuntimeError("degenerate repair cancel left its hidden backup in the scene")

        degenerate_preview_commit = summaries["degenerate_preview_commit"]
        if not bool(degenerate_preview_commit.get("ok")):
            raise RuntimeError("second degenerate repair preview for commit failed")
        degenerate_commit = summaries["degenerate_commit"]
        if not bool(degenerate_commit.get("ok")) or degenerate_commit.get("state") != "committed":
            raise RuntimeError("degenerate repair commit smoke failed")
        if degenerate_commit.get("committed_geometry_sha256") == original_degenerate_sha:
            raise RuntimeError("degenerate repair commit did not preserve the candidate geometry")
        if _degenerate_preview_backups(smoke_ids["degenerate_preview_commit"]):
            raise RuntimeError("degenerate repair commit left its hidden backup in the scene")
        degenerate_stale = summaries["degenerate_stale"]
        if bool(degenerate_stale.get("ok")):
            raise RuntimeError("stale degenerate repair preview was incorrectly accepted")
        if "fingerprint changed" not in str(degenerate_stale.get("summary") or ""):
            raise RuntimeError("stale degenerate repair preview did not report fingerprint mismatch")
        degenerate_object = bpy.context.scene.objects.get(degenerate_name)
        if degenerate_object is None:
            raise RuntimeError("degenerate repair target disappeared after commit")
        committed_issues = _degenerate_issue_counts(degenerate_object.data, 1e-10)
        if committed_issues["zero_length_edges"] or committed_issues["degenerate_faces"]:
            raise RuntimeError("degenerate repair commit left diagnosed degenerate geometry")

        merge_preview = summaries["merge_preview"]
        if not bool(merge_preview.get("ok")) or merge_preview.get("state") != "preview":
            raise RuntimeError("merge-by-distance preview smoke failed")
        if merge_preview.get("selected_vertex_indices") != [0, 1]:
            raise RuntimeError("merge-by-distance preview did not preserve the explicit selection")
        if int(merge_preview.get("merged_vertices") or 0) != 1:
            raise RuntimeError("merge-by-distance preview did not merge exactly one selected vertex")
        if int((merge_preview.get("before_mesh") or {}).get("vertices") or 0) != 6:
            raise RuntimeError("merge-by-distance preview did not report the expected six-vertex pre-state")
        if int((merge_preview.get("candidate_mesh") or {}).get("vertices") or 0) != 5:
            raise RuntimeError("merge-by-distance preview did not report the expected five-vertex candidate")
        if not merge_unselected_survived_preview:
            raise RuntimeError("merge-by-distance preview incorrectly merged an unselected nearby vertex")

        merge_cancel = summaries["merge_cancel"]
        if not bool(merge_cancel.get("ok")) or merge_cancel.get("state") != "cancelled":
            raise RuntimeError("merge-by-distance cancel smoke failed")
        if merge_cancel.get("restored_geometry_sha256") != merge_original_sha:
            raise RuntimeError("merge-by-distance cancel did not restore the exact original fingerprint")
        if _mesh_repair_preview_backups("merge", smoke_ids["merge_preview"]):
            raise RuntimeError("merge-by-distance cancel left its hidden backup in the scene")

        merge_noop = summaries["merge_noop"]
        if bool(merge_noop.get("ok")):
            raise RuntimeError("merge-by-distance no-op selection was incorrectly accepted")
        if "produced no merge" not in str(merge_noop.get("summary") or ""):
            raise RuntimeError("merge-by-distance no-op rejection did not report that no merge occurred")

        merge_preview_commit = summaries["merge_preview_commit"]
        if not bool(merge_preview_commit.get("ok")):
            raise RuntimeError("second merge-by-distance preview for commit failed")
        merge_commit = summaries["merge_commit"]
        if not bool(merge_commit.get("ok")) or merge_commit.get("state") != "committed":
            raise RuntimeError("merge-by-distance commit smoke failed")
        if merge_commit.get("committed_geometry_sha256") == merge_original_sha:
            raise RuntimeError("merge-by-distance commit did not preserve the candidate geometry")
        if merge_commit.get("committed_geometry_sha256") != merge_preview_commit.get("candidate_geometry_sha256"):
            raise RuntimeError("merge-by-distance commit changed the reviewed candidate fingerprint")
        if int((merge_commit.get("committed_mesh") or {}).get("vertices") or 0) != 5:
            raise RuntimeError("merge-by-distance commit did not preserve the five-vertex candidate")
        if not merge_unselected_survived_commit:
            raise RuntimeError("merge-by-distance commit incorrectly removed an unselected nearby vertex")
        if _mesh_repair_preview_backups("merge", smoke_ids["merge_preview_commit"]):
            raise RuntimeError("merge-by-distance commit left its hidden backup in the scene")

        merge_stale = summaries["merge_stale"]
        if bool(merge_stale.get("ok")):
            raise RuntimeError("stale merge-by-distance preview was incorrectly accepted")
        if "fingerprint changed" not in str(merge_stale.get("summary") or ""):
            raise RuntimeError("stale merge-by-distance preview did not report fingerprint mismatch")

        hole_diag = summaries["hole_diag"]
        if bool(hole_diag.get("ok")):
            raise RuntimeError("boundary-hole diagnostic unexpectedly passed with open boundaries")
        hole_check = (hole_diag.get("checks") or [{}])[0]
        if int((hole_check.get("metrics") or {}).get("boundary_loops") or 0) != 2:
            raise RuntimeError("boundary-hole diagnostic lost closed-loop evidence")
        hole_preview = summaries["hole_preview"]
        if not bool(hole_preview.get("ok")) or hole_preview.get("state") != "preview":
            raise RuntimeError("boundary hole-fill preview smoke failed")
        if hole_preview.get("selected_edge_indices") != hole_edges:
            raise RuntimeError("boundary hole-fill preview lost the explicit edge loop")
        if int(hole_preview.get("new_faces") or 0) != 1:
            raise RuntimeError("boundary hole-fill preview did not create exactly one local face")
        if int(hole_preview.get("new_edges") or 0) != 0:
            raise RuntimeError("boundary hole-fill preview unexpectedly created internal edges")
        if not hole_preview.get("original_topology_preserved"):
            raise RuntimeError("boundary hole-fill preview did not preserve original topology identities")
        if int(hole_preview.get("boundary_edges_before") or 0) - int(hole_preview.get("boundary_edges_after") or 0) != 4:
            raise RuntimeError("boundary hole-fill preview did not close exactly the four selected edges")
        if int((hole_preview.get("before_mesh") or {}).get("polygons") or 0) != 4:
            raise RuntimeError("boundary hole-fill preview pre-state did not contain four ring faces")
        if int((hole_preview.get("candidate_mesh") or {}).get("polygons") or 0) != 5:
            raise RuntimeError("boundary hole-fill candidate did not contain the expected fifth face")

        hole_cancel = summaries["hole_cancel"]
        if not bool(hole_cancel.get("ok")) or hole_cancel.get("state") != "cancelled":
            raise RuntimeError("boundary hole-fill cancel smoke failed")
        if hole_cancel.get("restored_geometry_sha256") != hole_original_sha:
            raise RuntimeError("boundary hole-fill cancel did not restore the exact original fingerprint")
        if _mesh_repair_preview_backups("hole", smoke_ids["hole_preview"]):
            raise RuntimeError("boundary hole-fill cancel left its hidden backup in the scene")

        hole_invalid = summaries["hole_invalid"]
        if bool(hole_invalid.get("ok")):
            raise RuntimeError("open boundary-edge chain was incorrectly accepted as a hole loop")
        if "closed" not in str(hole_invalid.get("summary") or ""):
            raise RuntimeError("invalid boundary-hole selection did not report closed-loop failure")

        hole_preview_commit = summaries["hole_preview_commit"]
        if not bool(hole_preview_commit.get("ok")):
            raise RuntimeError("second boundary hole-fill preview for commit failed")
        hole_commit = summaries["hole_commit"]
        if not bool(hole_commit.get("ok")) or hole_commit.get("state") != "committed":
            raise RuntimeError("boundary hole-fill commit smoke failed")
        if hole_commit.get("committed_geometry_sha256") != hole_preview_commit.get("candidate_geometry_sha256"):
            raise RuntimeError("boundary hole-fill commit changed the reviewed candidate fingerprint")
        if int((hole_commit.get("committed_mesh") or {}).get("polygons") or 0) != 5:
            raise RuntimeError("boundary hole-fill commit did not preserve the five-face candidate")
        if _mesh_repair_preview_backups("hole", smoke_ids["hole_preview_commit"]):
            raise RuntimeError("boundary hole-fill commit left its hidden backup in the scene")

        hole_stale = summaries["hole_stale"]
        if bool(hole_stale.get("ok")):
            raise RuntimeError("stale boundary hole-fill preview was incorrectly accepted")
        if "fingerprint changed" not in str(hole_stale.get("summary") or ""):
            raise RuntimeError("stale boundary hole-fill preview did not report fingerprint mismatch")

        trajectory_ids = set()
        if TRAJECTORY.is_file():
            for line in TRAJECTORY.read_text(encoding="utf-8-sig").splitlines():
                if not line.strip():
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                identifier = str(entry.get("id") or "")
                if identifier:
                    trajectory_ids.add(identifier)

        missing = sorted(set(smoke_ids.values()) - trajectory_ids)
        if missing:
            raise RuntimeError(
                "modeling smoke dispatcher did not journal command(s): "
                + ", ".join(missing)
            )

        return {
            "transform_positive": True,
            "transform_negative_detected": True,
            "create_positive": True,
            "create_duplicate_detected": True,
            "modifier_positive": True,
            "modifier_duplicate_detected": True,
            "array_positive": True,
            "array_limit_detected": True,
            "scatter_positive": True,
            "scatter_limit_detected": True,
            "scatter_instance_count": int(summaries["scatter"].get("instance_count") or 0),
            "scatter_seed": int((summaries["scatter"].get("scatter_parameters") or {}).get("seed") or 0),
            "cut_preview_positive": True,
            "cut_limit_detected": True,
            "cut_commit_positive": True,
            "cut_cancel_rollback": True,
            "cut_profile_types": sorted(profile_types),
            "cutters_created": len(cut_preview.get("cutters") or []),
            "cleanup_modifier_guard": True,
            "cleanup_diagnostic_positive": True,
            "cleanup_positive": True,
            "cleanup_stale_guard": True,
            "cleanup_removed_vertices": int(summaries["cleanup"].get("removed_vertices") or 0),
            "degenerate_diagnostic_positive": True,
            "degenerate_preview_positive": True,
            "degenerate_cancel_rollback": True,
            "degenerate_commit_positive": True,
            "degenerate_stale_guard": True,
            "degenerate_before_issues": preview_before,
            "degenerate_candidate_issues": preview_after,
            "merge_preview_positive": True,
            "merge_cancel_rollback": True,
            "merge_commit_positive": True,
            "merge_stale_guard": True,
            "merge_noop_guard": True,
            "merge_explicit_selection_preserved": True,
            "merge_unselected_survived_preview": merge_unselected_survived_preview,
            "merge_unselected_survived_commit": merge_unselected_survived_commit,
            "merge_original_vertices": int(merge_original_counts.get("vertices") or 0),
            "merge_candidate_vertices": int((merge_preview.get("candidate_mesh") or {}).get("vertices") or 0),
            "hole_diagnostic_positive": True,
            "hole_preview_positive": True,
            "hole_cancel_rollback": True,
            "hole_commit_positive": True,
            "hole_stale_guard": True,
            "hole_open_chain_guard": True,
            "hole_selected_edge_indices": hole_edges,
            "hole_new_faces": int(hole_preview.get("new_faces") or 0),
            "hole_boundary_edges_before": int(hole_preview.get("boundary_edges_before") or 0),
            "hole_boundary_edges_after": int(hole_preview.get("boundary_edges_after") or 0),
            "dispatcher_journaled": True,
            "object_name": object_name,
            "location": current.get("location"),
            "scale": current.get("scale"),
            "results": {
                key: str(path)
                for key, path in result_paths.items()
            },
        }
    finally:
        scatter_modifier = obj.modifiers.get("SmokeScatter") if obj is not None else None
        scatter_group = scatter_modifier.node_group if scatter_modifier is not None else None
        if scatter_modifier is not None:
            try:
                obj.modifiers.remove(scatter_modifier)
            except Exception:
                pass
        if scatter_group is not None and getattr(scatter_group, "users", 0) == 0:
            try:
                bpy.data.node_groups.remove(scatter_group)
            except Exception:
                pass
        for backup in list(bpy.data.objects):
            if (
                bool(backup.get("ordax_degenerate_backup"))
                and str(backup.get("ordax_degenerate_target") or "") == degenerate_name
            ):
                backup_mesh = backup.data if backup.type == "MESH" else None
                try:
                    _remove_degenerate_backup(backup)
                except Exception:
                    try:
                        bpy.data.objects.remove(backup, do_unlink=True)
                    except Exception:
                        pass
                if backup_mesh is not None and getattr(backup_mesh, "users", 0) == 0:
                    try:
                        bpy.data.meshes.remove(backup_mesh)
                    except Exception:
                        pass
        degenerate_object = bpy.context.scene.objects.get(degenerate_name)
        if degenerate_object is not None:
            mesh = degenerate_object.data if degenerate_object.type == "MESH" else None
            bpy.data.objects.remove(degenerate_object, do_unlink=True)
            if mesh is not None and getattr(mesh, "users", 0) == 0:
                bpy.data.meshes.remove(mesh)
        for backup in list(bpy.data.objects):
            if (
                bool(backup.get("ordax_merge_backup"))
                and str(backup.get("ordax_merge_target") or "") == merge_name
            ):
                backup_mesh = backup.data if backup.type == "MESH" else None
                try:
                    _remove_mesh_repair_backup(backup)
                except Exception:
                    try:
                        bpy.data.objects.remove(backup, do_unlink=True)
                    except Exception:
                        pass
                if backup_mesh is not None and getattr(backup_mesh, "users", 0) == 0:
                    try:
                        bpy.data.meshes.remove(backup_mesh)
                    except Exception:
                        pass
        merge_object = bpy.context.scene.objects.get(merge_name)
        if merge_object is not None:
            mesh = merge_object.data if merge_object.type == "MESH" else None
            bpy.data.objects.remove(merge_object, do_unlink=True)
            if mesh is not None and getattr(mesh, "users", 0) == 0:
                bpy.data.meshes.remove(mesh)
        for backup in list(bpy.data.objects):
            if (
                bool(backup.get("ordax_hole_backup"))
                and str(backup.get("ordax_hole_target") or "") == hole_name
            ):
                backup_mesh = backup.data if backup.type == "MESH" else None
                try:
                    _remove_mesh_repair_backup(backup)
                except Exception:
                    try:
                        bpy.data.objects.remove(backup, do_unlink=True)
                    except Exception:
                        pass
                if backup_mesh is not None and getattr(backup_mesh, "users", 0) == 0:
                    try:
                        bpy.data.meshes.remove(backup_mesh)
                    except Exception:
                        pass
        hole_object = bpy.context.scene.objects.get(hole_name)
        if hole_object is not None:
            mesh = hole_object.data if hole_object.type == "MESH" else None
            bpy.data.objects.remove(hole_object, do_unlink=True)
            if mesh is not None and getattr(mesh, "users", 0) == 0:
                bpy.data.meshes.remove(mesh)
        temporary = bpy.context.scene.objects.get(temporary_name)
        if temporary is not None:
            mesh = temporary.data if temporary.type == "MESH" else None
            bpy.data.objects.remove(temporary, do_unlink=True)
            if mesh is not None and getattr(mesh, "users", 0) == 0:
                bpy.data.meshes.remove(mesh)
            bpy.context.view_layer.update()


for stale in INFLIGHT.glob("*.json"):
    try:
        stale.unlink()
    except OSError:
        pass

if CFG.ordax_smoke_output_dir:
    quality_summary = None
    modeling_summary = None
    if CFG.ordax_smoke_modeling_fixture:
        modeling_summary = _run_modeling_smoke_fixture()
    if CFG.ordax_smoke_quality_fixture:
        valid_quality_id = "smoke-quality-valid"
        _quality_gate(
            {
                "id": valid_quality_id,
                "checks": [
                    {
                        "type": "uv_quality",
                        "object_name": "UVProbe",
                        "max_zero_area_faces": 0,
                        "max_degenerate_uv_triangles": 0,
                        "max_out_of_bounds_loops": 0,
                        "max_overlap_pairs": 0,
                        "max_shape_distortion": 0.000001,
                        "require_unit_tile": True,
                    }
                ],
            }
        )
        valid_path = RESULTS / f"{valid_quality_id}.json"
        if not valid_path.is_file():
            raise RuntimeError("UV quality smoke did not create its positive-control result")
        valid_quality = json.loads(valid_path.read_text(encoding="utf-8-sig"))
        if not bool(valid_quality.get("ok")):
            raise RuntimeError(
                "UV quality positive control failed: "
                + str(valid_quality.get("summary") or "unknown failure")
            )

        invalid_quality_id = "smoke-quality-invalid"
        _quality_gate(
            {
                "id": invalid_quality_id,
                "checks": [
                    {
                        "type": "uv_quality",
                        "object_name": "UVProbeBad",
                        "max_zero_area_faces": 0,
                    }
                ],
            }
        )
        invalid_path = RESULTS / f"{invalid_quality_id}.json"
        if not invalid_path.is_file():
            raise RuntimeError("UV quality smoke did not create its negative-control result")
        invalid_quality = json.loads(invalid_path.read_text(encoding="utf-8-sig"))
        if bool(invalid_quality.get("ok")):
            raise RuntimeError("UV quality negative control was incorrectly accepted")

        quality_summary = {
            "positive_control": True,
            "negative_control_detected": True,
            "valid_metrics": (
                (valid_quality.get("checks") or [{}])[0].get("metrics")
            ),
            "invalid_metrics": (
                (invalid_quality.get("checks") or [{}])[0].get("metrics")
            ),
        }

    smoke_id = "smoke"
    _multiview_capture(
        {
            "id": smoke_id,
            "output_dir": str(Path(CFG.ordax_smoke_output_dir).resolve()),
            "mode": CFG.ordax_smoke_mode,
            "width": CFG.ordax_smoke_width,
            "height": CFG.ordax_smoke_height,
            "views": ["front", "right", "top", "three_quarter"],
        }
    )
    smoke_result_path = RESULTS / f"{smoke_id}.json"
    if not smoke_result_path.is_file():
        raise RuntimeError("multiview smoke did not create a durable result")
    smoke_result = json.loads(
        smoke_result_path.read_text(encoding="utf-8-sig")
    )
    if not bool(smoke_result.get("ok")):
        raise RuntimeError(
            "multiview smoke failed: "
            + str(smoke_result.get("summary") or "unknown failure")
        )
    print(
        json.dumps(
            {
                "ok": True,
                "mode": smoke_result.get("mode"),
                "manifest": smoke_result.get("manifest"),
                "views": [
                    item.get("view")
                    for item in smoke_result.get("artifacts", [])
                ],
                "render_engine": smoke_result.get("render_engine"),
                "quality_fixture": quality_summary,
                "modeling_fixture": modeling_summary,
            },
            indent=2,
        )
    )
else:
    _write_presence(force=True)
    if not bpy.app.timers.is_registered(_tick):
        bpy.app.timers.register(_tick, first_interval=0.10, persistent=True)
