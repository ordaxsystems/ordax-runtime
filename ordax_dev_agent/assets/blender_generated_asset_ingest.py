"""Headless generated-asset ingestion into a clean Blender staging file."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import bpy


_SUPPORTED = {".glb", ".gltf", ".fbx", ".obj"}


def _args() -> argparse.Namespace:
    raw = sys.argv
    raw = raw[raw.index("--") + 1 :] if "--" in raw else []
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--report", required=True)
    parser.add_argument("--provenance")
    return parser.parse_args(raw)


def _write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def _import_asset(source: Path) -> None:
    suffix = source.suffix.lower()
    if suffix in {".glb", ".gltf"}:
        bpy.ops.import_scene.gltf(filepath=str(source))
        return
    if suffix == ".fbx":
        bpy.ops.import_scene.fbx(filepath=str(source), use_anim=True)
        return
    if suffix == ".obj":
        if hasattr(bpy.ops.wm, "obj_import"):
            bpy.ops.wm.obj_import(filepath=str(source))
        else:
            bpy.ops.import_scene.obj(filepath=str(source))
        return
    raise RuntimeError(f"unsupported source format: {suffix}")


def _safe_custom(value):
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return json.dumps(value, ensure_ascii=False, sort_keys=True)[:32000]


def main() -> int:
    args = _args()
    source = Path(args.input).resolve()
    output = Path(args.output).resolve()
    report_path = Path(args.report).resolve()
    provenance_path = Path(args.provenance).resolve() if args.provenance else None
    try:
        if not source.is_file() or source.suffix.lower() not in _SUPPORTED:
            raise RuntimeError("input must be an existing GLB, glTF, FBX, or OBJ file")
        if output.suffix.lower() != ".blend":
            raise RuntimeError("output must be a .blend file")

        _import_asset(source)
        meshes = [obj for obj in bpy.context.scene.objects if obj.type == "MESH"]
        armatures = [obj for obj in bpy.context.scene.objects if obj.type == "ARMATURE"]
        cameras = [obj for obj in bpy.context.scene.objects if obj.type == "CAMERA"]
        lights = [obj for obj in bpy.context.scene.objects if obj.type == "LIGHT"]
        if not meshes:
            raise RuntimeError("generated asset imported without mesh objects")

        provenance = None
        if provenance_path is not None:
            try:
                provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as error:
                raise RuntimeError(f"cannot read provenance manifest: {error}") from error
            if not isinstance(provenance, dict) or provenance.get("schema") != "ordax.generated-asset/1":
                raise RuntimeError("provenance is not ordax.generated-asset/1")
            scene = bpy.context.scene
            artifact = provenance.get("artifact") if isinstance(provenance.get("artifact"), dict) else {}
            source_info = provenance.get("provenance") if isinstance(provenance.get("provenance"), dict) else {}
            scene["ordax_provenance_schema"] = provenance.get("schema")
            scene["ordax_source_sha256"] = str(artifact.get("sha256") or "")
            scene["ordax_source_format"] = str(artifact.get("format") or source.suffix.lstrip("."))
            scene["ordax_source_provider"] = str(source_info.get("provider") or "")
            scene["ordax_source_task_id"] = str(source_info.get("task_id") or "")
            scene["ordax_source_operation"] = str(source_info.get("operation") or "")
            scene["ordax_source_artifact_path"] = str(artifact.get("path") or source.name)
            scene["ordax_provenance_json"] = _safe_custom(provenance)

        output.parent.mkdir(parents=True, exist_ok=True)
        bpy.ops.wm.save_as_mainfile(filepath=str(output), check_existing=False)

        report = {
            "ok": True,
            "schema": 1,
            "input": str(source),
            "output_blend": str(output),
            "blender_version": ".".join(str(value) for value in bpy.app.version),
            "counts": {
                "meshes": len(meshes),
                "armatures": len(armatures),
                "actions": len(bpy.data.actions),
                "materials": len(bpy.data.materials),
                "images": len(bpy.data.images),
                "cameras": len(cameras),
                "lights": len(lights),
            },
            "meshes": [obj.name for obj in meshes],
            "armatures": [obj.name for obj in armatures],
            "actions": [action.name for action in bpy.data.actions],
            "provenance_embedded": provenance is not None,
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
