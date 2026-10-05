"""Headless Blender export helper for OrdaX Dev Agent.

Runs inside a separate Blender process so a slow exporter cannot block the
visible Blender companion or the single-threaded control-plane worker.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import bpy


def _parse_args() -> argparse.Namespace:
    import sys

    argv = sys.argv
    if "--" in argv:
        argv = argv[argv.index("--") + 1 :]
    else:
        argv = []

    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    parser.add_argument("--format", choices=("glb", "fbx"), required=True)
    parser.add_argument("--selected-only", action="store_true")
    parser.add_argument("--animations", action="store_true")
    parser.add_argument("--apply-modifiers", action="store_true")
    parser.add_argument("--object-name", action="append", default=[])
    return parser.parse_args(argv)


def _select_objects(names: list[str]) -> list[str]:
    bpy.ops.object.select_all(action="DESELECT")
    selected: list[str] = []
    missing: list[str] = []

    for raw in names:
        name = str(raw).strip()
        if not name:
            continue
        obj = bpy.context.scene.objects.get(name)
        if obj is None:
            missing.append(name)
            continue
        obj.select_set(True)
        selected.append(obj.name)

    if missing:
        raise RuntimeError("missing export objects: " + ", ".join(missing[:50]))
    if selected:
        bpy.context.view_layer.objects.active = bpy.context.scene.objects.get(selected[0])
    return selected


def main() -> None:
    args = _parse_args()
    output = Path(args.output).expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)

    selected_names = _select_objects(args.object_name) if args.object_name else [
        obj.name for obj in bpy.context.selected_objects
    ]
    use_selection = bool(args.object_name) or bool(args.selected_only)

    if use_selection and not selected_names:
        raise RuntimeError("selected-only export requested but no objects are selected")

    if args.format == "glb":
        bpy.ops.export_scene.gltf(
            filepath=str(output),
            check_existing=False,
            export_format="GLB",
            use_selection=use_selection,
            export_extras=True,
            export_apply=bool(args.apply_modifiers),
            export_animations=bool(args.animations),
        )
    else:
        bpy.ops.export_scene.fbx(
            filepath=str(output),
            check_existing=False,
            use_selection=use_selection,
            use_custom_props=True,
            use_mesh_modifiers=bool(args.apply_modifiers),
            bake_anim=bool(args.animations),
            add_leaf_bones=False,
        )

    if not output.is_file() or output.stat().st_size <= 0:
        raise RuntimeError("exporter returned without creating a non-empty file")

    report = {
        "ok": True,
        "artifact": str(output),
        "format": args.format,
        "size_bytes": output.stat().st_size,
        "sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
        "selection_only": use_selection,
        "exported_objects": selected_names,
    }
    print("ORDAX_HEADLESS_EXPORT " + json.dumps(report, sort_keys=True))


if __name__ == "__main__":
    main()
