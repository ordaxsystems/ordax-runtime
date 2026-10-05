"""Collect runtime-oriented metrics from a Blender scene without mutating it."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import bpy


def _args() -> argparse.Namespace:
    raw = sys.argv
    raw = raw[raw.index("--") + 1 :] if "--" in raw else []
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", required=True)
    return parser.parse_args(raw)


def _write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def main() -> int:
    args = _args()
    report_path = Path(args.report).resolve()
    try:
        scene = bpy.context.scene
        depsgraph = bpy.context.evaluated_depsgraph_get()
        mesh_objects = [obj for obj in scene.objects if obj.type == "MESH"]
        armatures = [obj for obj in scene.objects if obj.type == "ARMATURE"]

        triangles = 0
        vertices = 0
        material_slots = 0
        max_vertex_influences = 0
        shape_keys = 0
        mesh_details = []
        for obj in mesh_objects:
            evaluated = obj.evaluated_get(depsgraph)
            mesh = evaluated.to_mesh()
            try:
                mesh.calc_loop_triangles()
                object_triangles = len(mesh.loop_triangles)
                object_vertices = len(mesh.vertices)
            finally:
                evaluated.to_mesh_clear()
            source_mesh = obj.data
            material_slots += len(obj.material_slots)
            if source_mesh.shape_keys is not None:
                shape_keys += max(0, len(source_mesh.shape_keys.key_blocks) - 1)
            object_max_influences = 0
            for vertex in source_mesh.vertices:
                influences = sum(1 for item in vertex.groups if item.weight > 0.000001)
                object_max_influences = max(object_max_influences, influences)
            max_vertex_influences = max(max_vertex_influences, object_max_influences)
            triangles += object_triangles
            vertices += object_vertices
            mesh_details.append(
                {
                    "name": obj.name,
                    "triangles": object_triangles,
                    "vertices": object_vertices,
                    "material_slots": len(obj.material_slots),
                    "max_vertex_influences": object_max_influences,
                    "shape_keys": (
                        max(0, len(source_mesh.shape_keys.key_blocks) - 1)
                        if source_mesh.shape_keys is not None
                        else 0
                    ),
                }
            )

        bones = sum(len(obj.data.bones) for obj in armatures)
        actions = len(bpy.data.actions)
        images = []
        max_texture_dimension = 0
        texture_pixels = 0
        for image in bpy.data.images:
            if image.name in {"Render Result", "Viewer Node"}:
                continue
            width = int(image.size[0]) if len(image.size) > 0 else 0
            height = int(image.size[1]) if len(image.size) > 1 else 0
            if width <= 0 or height <= 0:
                continue
            max_texture_dimension = max(max_texture_dimension, width, height)
            texture_pixels += width * height
            images.append(
                {
                    "name": image.name,
                    "width": width,
                    "height": height,
                    "packed": image.packed_file is not None,
                    "source": str(image.source),
                }
            )

        metrics = {
            "mesh_objects": len(mesh_objects),
            "triangles": triangles,
            "vertices": vertices,
            "material_slots": material_slots,
            "materials": len(bpy.data.materials),
            "textures": len(images),
            "max_texture_dimension": max_texture_dimension,
            "texture_pixels": texture_pixels,
            "armatures": len(armatures),
            "bones": bones,
            "actions": actions,
            "shape_keys": shape_keys,
            "max_vertex_influences": max_vertex_influences,
            "lod_safe_static_candidate": len(armatures) == 0 and shape_keys == 0,
        }
        report = {
            "ok": True,
            "schema": 1,
            "blend_file": bpy.data.filepath,
            "blender_version": ".".join(str(value) for value in bpy.app.version),
            "metrics": metrics,
            "meshes": mesh_details,
            "images": images,
            "armatures": [
                {"name": obj.name, "bones": len(obj.data.bones)}
                for obj in armatures
            ],
            "actions": [
                {
                    "name": action.name,
                    "frame_start": round(float(action.frame_range[0]), 4),
                    "frame_end": round(float(action.frame_range[1]), 4),
                }
                for action in bpy.data.actions
            ],
        }
        _write(report_path, report)
        print(json.dumps({"ok": True, "report": str(report_path)}))
        return 0
    except Exception as error:
        payload = {"ok": False, "error_type": type(error).__name__, "error": str(error)}
        try:
            _write(report_path, payload)
        except Exception:
            pass
        print(json.dumps(payload), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
