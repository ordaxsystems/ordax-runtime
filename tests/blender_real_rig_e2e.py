"""Real Blender 5.3 regression: the installed companion creates, saves and reopens a rig.

Executed by Blender itself on a disposable CI workspace, never by the
ChatGPT Product MCP. This verifies bpy behavior; it does not attest a
project-device grant or a remote operation.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path

import bpy


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError("ORDAX_BLENDER_REAL_E2E_FAILED: " + message)


def run() -> None:
    root = Path(os.environ["ORDAX_BLENDER_REAL_E2E_ROOT"]).resolve()
    phase = os.environ["ORDAX_BLENDER_REAL_E2E_PHASE"]
    require(phase in {"create", "verify"}, "unknown phase")
    require(tuple(bpy.app.version[:2]) == (5, 3), "wrong Blender version")
    require(root.is_dir(), "disposable CI root missing")
    for directory in (
        root / "control",
        root / "control" / "inbox",
        root / "control" / "responses",
        root / "control" / "results",
        root / "control" / "inflight",
        root / "scripts",
        root / "artifacts",
    ):
        directory.mkdir(parents=True, exist_ok=True)

    companion_path = (
        Path(__file__).resolve().parents[1]
        / "ordax_dev_agent"
        / "assets"
        / "blender_live_companion.py"
    )
    spec = importlib.util.spec_from_file_location(
        "_ordax_real_ci_companion", companion_path
    )
    require(spec is not None and spec.loader is not None, "companion unavailable")
    companion = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(companion)
    require(companion.PROTOCOL_VERSION == 9, "companion protocol mismatch")
    require("rig_create" in companion.CAPABILITIES, "rig capability absent")

    target = root / "ordax-rig-e2e.blend"
    name = "OrdaX_Real_CI_Rig"
    bones = [
        {"name": "Root", "head": [0, 0, 0], "tail": [0, 0, 1]},
        {"name": "Spine", "head": [0, 0, 1], "tail": [0, 0, 2], "parent": "Root"},
        {"name": "Head", "head": [0, 0, 2], "tail": [0, 0, 2.7], "parent": "Spine"},
    ]

    if phase == "create":
        require(not target.exists(), "will not overwrite previous .blend")
        companion._rig_create(
            {
                "id": "ci-positive",
                "operation": "rig_create",
                "name": name,
                "bones": bones,
                "location": [0, 0, 0],
            }
        )
        positive = json.loads(
            (companion.RESULTS / "ci-positive.json").read_text(encoding="utf-8")
        )
        require(positive.get("ok") is True, "rig creation failed: " + str(positive))
        require(positive.get("bone_count") == 3, "unexpected response bone count")
        rig = bpy.data.objects.get(name)
        require(rig is not None and rig.type == "ARMATURE", "armature missing")
        require(len(rig.data.bones) == 3, "armature bones missing")
        require(rig.data.bones["Spine"].parent.name == "Root", "Spine parenting")
        require(rig.data.bones["Head"].parent.name == "Spine", "Head parenting")
        companion._object_inspect({
            "id": "ci-rig-inspect", "operation": "object_inspect", "object_name": name,
        })
        inspection = json.loads(
            (companion.RESULTS / "ci-rig-inspect.json").read_text(encoding="utf-8")
        )
        require(inspection.get("ok") is True, "rig inspection failed")
        details = inspection.get("armature") or {}
        require(details.get("bone_count") == 3, "inspection bone count")
        require(details.get("bones_truncated") is False, "unexpected bone truncation")
        inspected = {bone["name"]: bone for bone in details["bones"]}
        require(inspected["Spine"]["parent"] == "Root", "inspected Spine parent")
        require(inspected["Head"]["parent"] == "Spine", "inspected Head parent")
        require(inspected["Head"]["pose"]["head_world"] == [0.0, 0.0, 2.0], "inspected pose")
        before_count = len(bpy.data.objects)

        companion._rig_create(
            {
                "id": "ci-duplicate",
                "operation": "rig_create",
                "name": name,
                "bones": bones,
                "location": [0, 0, 0],
            }
        )
        duplicate = json.loads(
            (companion.RESULTS / "ci-duplicate.json").read_text(encoding="utf-8")
        )
        require(duplicate.get("ok") is False, "duplicate name accepted")
        require(len(bpy.data.objects) == before_count, "duplicate mutated scene")

        companion._rig_create(
            {
                "id": "ci-invalid",
                "operation": "rig_create",
                "name": "OrdaX_CI_Invalid",
                "bones": [
                    {
                        "name": "Zero",
                        "head": [0, 0, 0],
                        "tail": [0, 0, 0],
                    }
                ],
                "location": [0, 0, 0],
            }
        )
        invalid = json.loads(
            (companion.RESULTS / "ci-invalid.json").read_text(encoding="utf-8")
        )
        require(invalid.get("ok") is False, "zero-length bone accepted")
        require(len(bpy.data.objects) == before_count, "invalid rig mutated scene")
        require(bpy.data.objects.get("OrdaX_CI_Invalid") is None, "partial rig leaked")
        require("FINISHED" in bpy.ops.wm.save_as_mainfile(filepath=str(target)), "save failed")
        require(target.is_file() and target.stat().st_size > 1000, "empty .blend")
        result = {
            "ok": True,
            "phase": phase,
            "blender_version": bpy.app.version_string,
            "companion_protocol": companion.PROTOCOL_VERSION,
            "companion_fingerprint": companion.COMPANION_FINGERPRINT,
            "bone_count": len(rig.data.bones),
            "duplicate_rejected": True,
            "invalid_rejected": True,
            "saved_file": str(target),
            "saved_sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
        }
    else:
        require(target.is_file(), "saved .blend missing")
        require(Path(bpy.data.filepath).resolve() == target, "Blender did not reopen saved file")
        rig = bpy.data.objects.get(name)
        require(rig is not None and rig.type == "ARMATURE", "reopened armature missing")
        require(len(rig.data.bones) == 3, "reopened bone count mismatch")
        require(rig.data.bones["Spine"].parent.name == "Root", "reopened Spine parent")
        require(rig.data.bones["Head"].parent.name == "Spine", "reopened Head parent")
        companion._object_inspect({
            "id": "ci-reopened-rig-inspect", "operation": "object_inspect", "object_name": name,
        })
        reopened_inspection = json.loads(
            (companion.RESULTS / "ci-reopened-rig-inspect.json").read_text(encoding="utf-8")
        )
        require(reopened_inspection.get("ok") is True, "reopened rig inspection failed")
        reopened_bones = {
            bone["name"]: bone
            for bone in reopened_inspection["armature"]["bones"]
        }
        require(reopened_bones["Head"]["parent"] == "Spine", "reopened inspected hierarchy")
        require("pose" in reopened_bones["Head"], "reopened pose missing")
        require(bpy.data.objects.get("OrdaX_CI_Invalid") is None, "invalid rig persisted")
        positive = json.loads(
            (companion.RESULTS / "ci-positive.json").read_text(encoding="utf-8")
        )
        require(positive.get("ok") is True, "durable companion result missing")
        result = {
            "ok": True,
            "phase": phase,
            "blender_version": bpy.app.version_string,
            "companion_protocol": companion.PROTOCOL_VERSION,
            "bone_count": len(rig.data.bones),
            "bone_names": [bone.name for bone in rig.data.bones],
            "parents": {"Spine": "Root", "Head": "Spine"},
            "reopened": True,
            "saved_sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
            "durable_result_present": True,
        }

    evidence = root / ("evidence-" + phase + ".json")
    require(not evidence.exists(), "evidence file already exists")
    evidence.write_text(json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")
    print("ORDAX_REAL_BLENDER_E2E_OK=" + json.dumps(result, sort_keys=True), flush=True)


run()
