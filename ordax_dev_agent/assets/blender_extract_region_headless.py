from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

import bpy
from mathutils import Vector


def _parse_args() -> argparse.Namespace:
    argv = sys.argv
    raw = argv[argv.index("--") + 1 :] if "--" in argv else []
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    parser.add_argument("--report", required=True)
    parser.add_argument("--axis", choices=("X", "Y", "Z"), required=True)
    parser.add_argument("--minimum", type=float, required=True)
    parser.add_argument("--maximum", type=float, required=True)
    parser.add_argument("--translate-x", type=float, default=0.0)
    parser.add_argument("--translate-y", type=float, default=0.0)
    parser.add_argument("--translate-z", type=float, default=0.0)
    parser.add_argument("--cleanup-orphans", action="store_true")
    return parser.parse_args(raw)


def _atomic_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    temp.replace(path)


def _coordinate(obj, axis_index: int) -> float:
    world_coordinate = float(obj.matrix_world.translation[axis_index])
    local_coordinate = float(obj.location[axis_index])
    coordinate = world_coordinate
    try:
        corners = [obj.matrix_world @ Vector(corner) for corner in obj.bound_box]
        values = [float(corner[axis_index]) for corner in corners]
        coordinate = (min(values) + max(values)) / 2.0
    except Exception:
        coordinate = world_coordinate

    if obj.parent is None:
        local_world_delta = abs(local_coordinate - world_coordinate)
        bounds_world_delta = abs(coordinate - world_coordinate)
        if local_world_delta > 1e-5 and bounds_world_delta <= 1e-4:
            coordinate = local_coordinate
    return coordinate


def _cleanup_orphans() -> dict[str, int]:
    removed: dict[str, int] = {}
    collections = (
        "meshes",
        "curves",
        "materials",
        "images",
        "textures",
        "cameras",
        "lights",
        "node_groups",
    )
    for name in collections:
        datablocks = getattr(bpy.data, name, None)
        if datablocks is None:
            continue
        count = 0
        for datablock in list(datablocks):
            if getattr(datablock, "users", 1) != 0:
                continue
            try:
                datablocks.remove(datablock)
                count += 1
            except Exception:
                pass
        removed[name] = count
    return removed


def main() -> None:
    args = _parse_args()
    if not math.isfinite(args.minimum) or not math.isfinite(args.maximum):
        raise ValueError("minimum and maximum must be finite")
    if args.maximum <= args.minimum:
        raise ValueError("maximum must be greater than minimum")

    output = Path(args.output).expanduser().resolve()
    report = Path(args.report).expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)

    source_file = str(Path(bpy.data.filepath).resolve()) if bpy.data.filepath else ""
    if source_file and Path(source_file) == output:
        raise ValueError("output must differ from input blend file")

    # Establish the destination file while the source scene is still intact.
    # Subsequent mutations are then persisted with save_mainfile, avoiding a
    # fragile Save As after large object/data removals.
    bpy.ops.wm.save_as_mainfile(filepath=str(output))

    bpy.context.view_layer.update()
    axis_index = {"X": 0, "Y": 1, "Z": 2}[args.axis]
    scene_objects = list(bpy.context.scene.objects)
    original_count = len(scene_objects)

    keep_objects = []
    remove_objects = []
    for obj in scene_objects:
        coordinate = _coordinate(obj, axis_index)
        if args.minimum <= coordinate <= args.maximum:
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
        bpy.data.objects.remove(obj, do_unlink=True)

    delta = Vector((args.translate_x, args.translate_y, args.translate_z))
    if delta.length > 0.0:
        remaining = list(bpy.context.scene.objects)
        remaining_set = set(remaining)
        for obj in remaining:
            if obj.parent is None:
                obj.location = obj.location + delta
            elif obj.parent not in remaining_set:
                matrix = obj.matrix_world.copy()
                matrix.translation = matrix.translation + delta
                obj.matrix_world = matrix

    scene = bpy.context.scene
    if scene.camera is None or scene.camera.name not in bpy.data.objects:
        cameras = [obj for obj in scene.objects if obj.type == "CAMERA"]
        scene.camera = cameras[0] if cameras else None

    bpy.context.view_layer.update()
    orphan_removed = _cleanup_orphans() if args.cleanup_orphans else {}

    bpy.ops.wm.save_mainfile()

    kept_names = [obj.name for obj in bpy.context.scene.objects]
    data = {
        "ok": True,
        "source_file": source_file,
        "output_file": str(output),
        "axis": args.axis,
        "minimum": args.minimum,
        "maximum": args.maximum,
        "translate": [args.translate_x, args.translate_y, args.translate_z],
        "original_object_count": original_count,
        "kept_count": len(kept_names),
        "removed_count": len(removed_names),
        "kept_names": kept_names[:500],
        "removed_names": removed_names[:500],
        "kept_names_truncated": len(kept_names) > 500,
        "removed_names_truncated": len(removed_names) > 500,
        "orphan_removed": orphan_removed,
    }
    _atomic_json(report, data)


if __name__ == "__main__":
    main()
