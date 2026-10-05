"""Headless Blender character/game-asset inspection and export helper.

Executed by OrdaX Device Agent through Blender's --background --python path.
This file intentionally has no dependency on the host package at runtime.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

import bpy
import bmesh
from mathutils import Vector


def _args() -> argparse.Namespace:
    raw = sys.argv
    raw = raw[raw.index("--") + 1 :] if "--" in raw else []
    parser = argparse.ArgumentParser()
    parser.add_argument("operation", choices=("inspect", "export"))
    parser.add_argument("--profile", choices=("mixamo", "unity", "unreal", "godot", "web"))
    parser.add_argument("--output")
    parser.add_argument("--report", required=True)
    return parser.parse_args(raw)


def _world_bounds(obj: bpy.types.Object) -> list[Vector]:
    if obj.type != "MESH" or not obj.bound_box:
        return []
    return [obj.matrix_world @ Vector(corner) for corner in obj.bound_box]


def _mesh_report(obj: bpy.types.Object) -> dict:
    mesh = obj.data
    boundary_edges = 0
    non_manifold_edges = 0
    try:
        bm = bmesh.new()
        bm.from_mesh(mesh)
        boundary_edges = sum(1 for edge in bm.edges if edge.is_boundary)
        non_manifold_edges = sum(1 for edge in bm.edges if not edge.is_manifold)
        bm.free()
    except Exception:
        boundary_edges = -1
        non_manifold_edges = -1

    max_influences = 0
    weighted_vertices = 0
    for vertex in mesh.vertices:
        count = sum(1 for group in vertex.groups if group.weight > 0.000001)
        if count:
            weighted_vertices += 1
            max_influences = max(max_influences, count)

    return {
        "name": obj.name,
        "vertices": len(mesh.vertices),
        "edges": len(mesh.edges),
        "polygons": len(mesh.polygons),
        "materials": len(mesh.materials),
        "uv_layers": len(mesh.uv_layers),
        "shape_keys": (
            len(mesh.shape_keys.key_blocks)
            if getattr(mesh, "shape_keys", None) is not None
            else 0
        ),
        "vertex_groups": len(obj.vertex_groups),
        "weighted_vertices": weighted_vertices,
        "max_vertex_influences": max_influences,
        "boundary_edges": boundary_edges,
        "non_manifold_edges": non_manifold_edges,
        "location": [round(float(value), 6) for value in obj.location],
        "scale": [round(float(value), 6) for value in obj.scale],
    }


def _armature_report(obj: bpy.types.Object) -> dict:
    bones = list(obj.data.bones)
    roots = [bone.name for bone in bones if bone.parent is None]
    deform = [bone.name for bone in bones if bone.use_deform]
    mixamo_bones = [
        bone.name
        for bone in bones
        if bone.name.lower().startswith("mixamorig:")
        or bone.name.lower().startswith("mixamorig_")
    ]
    return {
        "name": obj.name,
        "bones": len(bones),
        "deform_bones": len(deform),
        "root_bones": roots,
        "mixamo_named_bones": len(mixamo_bones),
        "location": [round(float(value), 6) for value in obj.location],
        "scale": [round(float(value), 6) for value in obj.scale],
    }


def _action_report() -> list[dict]:
    actions = []
    for action in bpy.data.actions:
        start, end = action.frame_range
        actions.append(
            {
                "name": action.name,
                "frame_start": round(float(start), 4),
                "frame_end": round(float(end), 4),
            }
        )
    return actions


def _scene_bounds(meshes: list[bpy.types.Object]) -> dict | None:
    corners: list[Vector] = []
    for obj in meshes:
        corners.extend(_world_bounds(obj))
    if not corners:
        return None
    mins = Vector(
        (
            min(point.x for point in corners),
            min(point.y for point in corners),
            min(point.z for point in corners),
        )
    )
    maxs = Vector(
        (
            max(point.x for point in corners),
            max(point.y for point in corners),
            max(point.z for point in corners),
        )
    )
    center = (mins + maxs) * 0.5
    size = maxs - mins
    return {
        "min": [round(float(value), 6) for value in mins],
        "max": [round(float(value), 6) for value in maxs],
        "center": [round(float(value), 6) for value in center],
        "size": [round(float(value), 6) for value in size],
        "height": round(float(size.z), 6),
    }


def inspect_scene() -> dict:
    meshes = [obj for obj in bpy.context.scene.objects if obj.type == "MESH"]
    armatures = [obj for obj in bpy.context.scene.objects if obj.type == "ARMATURE"]
    cameras = [obj for obj in bpy.context.scene.objects if obj.type == "CAMERA"]
    lights = [obj for obj in bpy.context.scene.objects if obj.type == "LIGHT"]
    empties = [obj for obj in bpy.context.scene.objects if obj.type == "EMPTY"]

    mesh_reports = [_mesh_report(obj) for obj in meshes]
    armature_reports = [_armature_report(obj) for obj in armatures]
    bounds = _scene_bounds(meshes)

    issues: list[dict] = []
    warnings: list[dict] = []

    if not meshes:
        issues.append({"code": "no_mesh", "message": "No mesh objects found."})
    if len(armatures) > 1:
        warnings.append(
            {
                "code": "multiple_armatures",
                "message": "Multiple armatures found; game-character exports are usually cleaner with one.",
            }
        )

    for report in mesh_reports:
        if report["uv_layers"] == 0:
            warnings.append(
                {
                    "code": "missing_uv",
                    "object": report["name"],
                    "message": "Mesh has no UV layer.",
                }
            )
        if report["non_manifold_edges"] > 0:
            warnings.append(
                {
                    "code": "non_manifold",
                    "object": report["name"],
                    "count": report["non_manifold_edges"],
                    "message": "Mesh contains non-manifold edges.",
                }
            )
        if report["max_vertex_influences"] > 4:
            warnings.append(
                {
                    "code": "skin_influences_gt4",
                    "object": report["name"],
                    "count": report["max_vertex_influences"],
                    "message": "More than four skin influences were found on at least one vertex.",
                }
            )
        if any(abs(value - 1.0) > 0.0001 for value in report["scale"]):
            warnings.append(
                {
                    "code": "unapplied_scale",
                    "object": report["name"],
                    "message": "Object scale is not applied.",
                }
            )

    centered = None
    if bounds is not None:
        cx, cy, _ = bounds["center"]
        centered = math.hypot(cx, cy) <= max(0.01, bounds["height"] * 0.02)
        if not centered:
            warnings.append(
                {
                    "code": "off_origin",
                    "message": "Character bounds are noticeably offset from world origin on X/Y.",
                }
            )

    has_mixamo_rig = any(report["mixamo_named_bones"] >= 10 for report in armature_reports)
    mixamo = {
        "has_armature": bool(armatures),
        "has_mixamo_named_rig": has_mixamo_rig,
        "centered_xy": centered,
        "auto_rig_candidate": bool(meshes) and not armatures and centered is not False,
        "mapped_rig_candidate": bool(meshes) and len(armatures) == 1,
        "manual_checks": [
            "confirm humanoid/biped proportions",
            "confirm neutral pose before auto-rigging",
            "confirm no large extra appendages/props",
            "visually inspect hands/feet and clothing intersections",
        ],
    }

    return {
        "schema": 1,
        "blender_version": ".".join(str(value) for value in bpy.app.version),
        "blend_file": bpy.data.filepath,
        "scene": bpy.context.scene.name,
        "counts": {
            "meshes": len(meshes),
            "armatures": len(armatures),
            "cameras": len(cameras),
            "lights": len(lights),
            "empties": len(empties),
            "actions": len(bpy.data.actions),
        },
        "bounds": bounds,
        "meshes": mesh_reports,
        "armatures": armature_reports,
        "actions": _action_report(),
        "mixamo": mixamo,
        "issues": issues,
        "warnings": warnings,
        "quality_ok": not issues,
    }


def _select_character_objects() -> tuple[list[bpy.types.Object], list[bpy.types.Object]]:
    bpy.ops.object.select_all(action="DESELECT")
    meshes = [obj for obj in bpy.context.scene.objects if obj.type == "MESH"]
    armatures = [obj for obj in bpy.context.scene.objects if obj.type == "ARMATURE"]
    for obj in [*meshes, *armatures]:
        obj.hide_set(False)
        obj.hide_viewport = False
        obj.select_set(True)
    if armatures:
        bpy.context.view_layer.objects.active = armatures[0]
    elif meshes:
        bpy.context.view_layer.objects.active = meshes[0]
    return meshes, armatures


def _export_fbx(output: Path, profile: str) -> None:
    embed = profile == "mixamo"
    bake_anim = profile != "mixamo" and bool(bpy.data.actions)
    bpy.ops.export_scene.fbx(
        filepath=str(output),
        use_selection=True,
        object_types={"MESH", "ARMATURE"},
        apply_unit_scale=True,
        bake_space_transform=False,
        axis_forward="-Z",
        axis_up="Y",
        add_leaf_bones=False,
        use_armature_deform_only=False,
        bake_anim=bake_anim,
        bake_anim_use_all_bones=True,
        bake_anim_use_nla_strips=True,
        bake_anim_use_all_actions=True,
        path_mode="COPY" if embed else "AUTO",
        embed_textures=embed,
    )


def _export_glb(output: Path) -> None:
    bpy.ops.export_scene.gltf(
        filepath=str(output),
        export_format="GLB",
        use_selection=True,
        export_animations=True,
        export_skins=True,
        export_morph=True,
    )


def export_scene(profile: str, output: Path) -> dict:
    meshes, armatures = _select_character_objects()
    if not meshes:
        raise RuntimeError("No mesh objects available for character export.")
    if profile == "mixamo" and len(armatures) > 1:
        raise RuntimeError("Mixamo export requires at most one armature.")

    output.parent.mkdir(parents=True, exist_ok=True)
    if profile in {"mixamo", "unity", "unreal"}:
        if output.suffix.lower() != ".fbx":
            raise RuntimeError(f"{profile} export requires .fbx output")
        _export_fbx(output, profile)
    else:
        if output.suffix.lower() != ".glb":
            raise RuntimeError(f"{profile} export requires .glb output")
        _export_glb(output)

    return {
        "profile": profile,
        "output_path": str(output),
        "bytes": output.stat().st_size if output.exists() else None,
        "selected_meshes": [obj.name for obj in meshes],
        "selected_armatures": [obj.name for obj in armatures],
    }


def _write_report(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def main() -> int:
    args = _args()
    report_path = Path(args.report).resolve()
    try:
        inspection = inspect_scene()
        payload = {"ok": True, "inspection": inspection}
        if args.operation == "export":
            if not args.profile or not args.output:
                raise RuntimeError("export requires --profile and --output")
            payload["export"] = export_scene(args.profile, Path(args.output).resolve())
        _write_report(report_path, payload)
        print(json.dumps({"ok": True, "report": str(report_path)}))
        return 0
    except Exception as error:
        payload = {
            "ok": False,
            "error_type": type(error).__name__,
            "error": str(error),
        }
        try:
            _write_report(report_path, payload)
        except Exception:
            pass
        print(json.dumps(payload), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
