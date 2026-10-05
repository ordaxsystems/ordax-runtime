"""Headless FBX -> Blender ingestion for generated/Mixamo characters."""
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
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--report", required=True)
    return parser.parse_args(raw)


def _write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def _armature_summary(obj: bpy.types.Object) -> dict:
    bones = list(obj.data.bones)
    mixamo = [
        bone.name
        for bone in bones
        if bone.name.lower().startswith("mixamorig:")
        or bone.name.lower().startswith("mixamorig_")
    ]
    return {
        "name": obj.name,
        "bones": len(bones),
        "root_bones": [bone.name for bone in bones if bone.parent is None],
        "mixamo_named_bones": len(mixamo),
    }


def main() -> int:
    args = _args()
    source = Path(args.input).resolve()
    output = Path(args.output).resolve()
    report_path = Path(args.report).resolve()
    try:
        if source.suffix.lower() != ".fbx" or not source.is_file():
            raise RuntimeError("input must be an existing FBX file")
        if output.suffix.lower() != ".blend":
            raise RuntimeError("output must be a .blend file")

        bpy.ops.object.select_all(action="SELECT")
        bpy.ops.object.delete(use_global=False)
        for datablocks in (
            bpy.data.meshes,
            bpy.data.armatures,
            bpy.data.materials,
            bpy.data.cameras,
            bpy.data.lights,
        ):
            for block in list(datablocks):
                if block.users == 0:
                    datablocks.remove(block)

        bpy.ops.import_scene.fbx(filepath=str(source), use_anim=True)

        meshes = [obj for obj in bpy.context.scene.objects if obj.type == "MESH"]
        armatures = [obj for obj in bpy.context.scene.objects if obj.type == "ARMATURE"]
        if not meshes:
            raise RuntimeError("FBX imported without mesh objects")

        output.parent.mkdir(parents=True, exist_ok=True)
        bpy.ops.wm.save_as_mainfile(filepath=str(output), check_existing=False)

        report = {
            "ok": True,
            "schema": 1,
            "source_path": str(source),
            "output_blend": str(output),
            "blender_version": ".".join(str(value) for value in bpy.app.version),
            "meshes": [obj.name for obj in meshes],
            "armatures": [_armature_summary(obj) for obj in armatures],
            "actions": [
                {
                    "name": action.name,
                    "frame_start": round(float(action.frame_range[0]), 4),
                    "frame_end": round(float(action.frame_range[1]), 4),
                }
                for action in bpy.data.actions
            ],
            "counts": {
                "meshes": len(meshes),
                "armatures": len(armatures),
                "actions": len(bpy.data.actions),
            },
            "mixamo_rig_detected": any(
                item["mixamo_named_bones"] >= 10
                for item in [_armature_summary(obj) for obj in armatures]
            ),
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
