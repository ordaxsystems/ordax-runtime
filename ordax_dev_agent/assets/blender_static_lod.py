"""Create a clean static LOD derivative from an existing Blender scene.

This helper intentionally refuses armatures and shape keys. It evaluates the
source meshes first (baking their current modifier result into fresh mesh data),
then applies Decimate as the first/only modifier on each generated LOD mesh.
"""
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
    parser.add_argument("--output", required=True)
    parser.add_argument("--report", required=True)
    parser.add_argument("--ratios", required=True)
    return parser.parse_args(raw)


def _write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def _triangles(mesh: bpy.types.Mesh) -> int:
    mesh.calc_loop_triangles()
    return len(mesh.loop_triangles)


def _parse_ratios(raw: str) -> list[float]:
    result = [float(value) for value in raw.split(",") if value.strip()]
    if not result or len(result) > 5:
        raise RuntimeError("invalid LOD ratio list")
    if any(value < 0.05 or value > 0.95 for value in result):
        raise RuntimeError("LOD ratios must be between 0.05 and 0.95")
    if any(result[index] <= result[index + 1] for index in range(len(result) - 1)):
        raise RuntimeError("LOD ratios must be strictly descending")
    return result


def _link_collection(name: str) -> bpy.types.Collection:
    collection = bpy.data.collections.new(name)
    bpy.context.scene.collection.children.link(collection)
    return collection


def _activate(obj: bpy.types.Object) -> None:
    bpy.ops.object.select_all(action="DESELECT")
    obj.hide_set(False)
    obj.hide_viewport = False
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj


def _decimate(obj: bpy.types.Object, ratio: float) -> None:
    _activate(obj)
    modifier = obj.modifiers.new(name="ORDAX_StaticLOD", type="DECIMATE")
    modifier.decimate_type = "COLLAPSE"
    modifier.ratio = ratio
    modifier.use_collapse_triangulate = True
    bpy.ops.object.modifier_apply(modifier=modifier.name)


def main() -> int:
    args = _args()
    output = Path(args.output).resolve()
    report_path = Path(args.report).resolve()
    source_blend = str(Path(bpy.data.filepath).resolve()) if bpy.data.filepath else ""
    try:
        ratios = _parse_ratios(args.ratios)
        if output.suffix.lower() != ".blend":
            raise RuntimeError("output must be a .blend file")

        source_meshes = [obj for obj in bpy.context.scene.objects if obj.type == "MESH"]
        armatures = [obj for obj in bpy.context.scene.objects if obj.type == "ARMATURE"]
        if not source_meshes:
            raise RuntimeError("static LOD generation requires at least one mesh")
        if armatures:
            raise RuntimeError("static LOD generation refuses scenes containing armatures")
        shaped = [
            obj.name
            for obj in source_meshes
            if obj.data.shape_keys is not None and len(obj.data.shape_keys.key_blocks) > 1
        ]
        if shaped:
            raise RuntimeError(
                "static LOD generation refuses shape-key meshes: " + ", ".join(shaped[:10])
            )

        depsgraph = bpy.context.evaluated_depsgraph_get()
        source_records = []
        for obj in source_meshes:
            evaluated = obj.evaluated_get(depsgraph)
            evaluated_mesh = bpy.data.meshes.new_from_object(
                evaluated,
                preserve_all_data_layers=True,
                depsgraph=depsgraph,
            )
            source_records.append(
                {
                    "name": obj.name,
                    "matrix_world": obj.matrix_world.copy(),
                    "mesh": evaluated_mesh,
                    "triangles": _triangles(evaluated_mesh),
                    "materials": len(evaluated_mesh.materials),
                    "uv_layers": len(evaluated_mesh.uv_layers),
                }
            )

        bpy.ops.object.select_all(action="SELECT")
        bpy.ops.object.delete(use_global=False)
        for collection in list(bpy.data.collections):
            if collection.name != "Collection" and collection.users == 0:
                bpy.data.collections.remove(collection)
        for child in list(bpy.context.scene.collection.children):
            bpy.context.scene.collection.children.unlink(child)

        level_specs = [(0, 1.0), *[(index + 1, ratio) for index, ratio in enumerate(ratios)]]
        level_reports = []
        for level, ratio in level_specs:
            collection = _link_collection(f"ORDAX_LOD{level}")
            level_triangles = 0
            objects = []
            for record in source_records:
                mesh = record["mesh"].copy()
                obj = bpy.data.objects.new(f"{record['name']}_LOD{level}", mesh)
                collection.objects.link(obj)
                obj.matrix_world = record["matrix_world"]
                if level > 0:
                    _decimate(obj, ratio)
                actual = _triangles(obj.data)
                level_triangles += actual
                objects.append(
                    {
                        "name": obj.name,
                        "source": record["name"],
                        "source_triangles": record["triangles"],
                        "triangles": actual,
                        "material_slots": len(obj.material_slots),
                        "uv_layers": len(obj.data.uv_layers),
                    }
                )
            level_reports.append(
                {
                    "level": level,
                    "requested_ratio": ratio,
                    "triangles": level_triangles,
                    "objects": objects,
                }
            )

        for record in source_records:
            source_mesh = record["mesh"]
            if source_mesh.users == 0:
                bpy.data.meshes.remove(source_mesh)

        scene = bpy.context.scene
        source_total = sum(record["triangles"] for record in source_records)
        scene["ordax_lod_schema"] = "ordax.static-lod/1"
        scene["ordax_lod_source_blend"] = source_blend
        scene["ordax_lod_source_triangles"] = source_total
        scene["ordax_lod_ratios"] = json.dumps(ratios)

        output.parent.mkdir(parents=True, exist_ok=True)
        bpy.ops.wm.save_as_mainfile(filepath=str(output), check_existing=False)

        report = {
            "ok": True,
            "schema": "ordax.static-lod/1",
            "source_blend": source_blend,
            "output_blend": str(output),
            "source": {
                "mesh_objects": len(source_records),
                "triangles": source_total,
                "objects": [
                    {
                        "name": record["name"],
                        "triangles": record["triangles"],
                        "materials": record["materials"],
                        "uv_layers": record["uv_layers"],
                    }
                    for record in source_records
                ],
            },
            "levels": level_reports,
            "safety": {
                "armatures": 0,
                "shape_key_meshes": 0,
                "source_file_modified": False,
                "evaluated_source_geometry_used": True,
            },
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
