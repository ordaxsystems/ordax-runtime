"""OrdaX BlenderBench: isolated end-to-end geometry/perception regression.

Creates two temporary Blender scenes, runs the real live companion multiview
implementation in background Blender, then verifies that:
- a baseline compared with itself is identical;
- a controlled geometry mutation is detected by silhouette IoU and bounds;
- source .blend files are not modified by observation.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mcp_blender_unity.config import find_blender
from mcp_blender_unity.process import run_process
from ordax_dev_agent.actions import ActionRegistry
from ordax_dev_agent.config import AgentConfig


def _create_scene(blender: Path, scene: Path, dimensions: tuple[float, float, float]) -> None:
    script = f"""
import bpy

bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.mesh.primitive_cube_add(size=2, location=(0, 0, 0))
body = bpy.context.object
body.name = "BenchmarkBody"
body.dimensions = {dimensions!r}
bpy.context.view_layer.objects.active = body
bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)

def uv_probe(name, invalid=False):
    mesh = bpy.data.meshes.new(name + "Mesh")
    mesh.from_pydata(
        [(-1.0, -1.0, 0.0), (1.0, -1.0, 0.0), (1.0, 1.0, 0.0), (-1.0, 1.0, 0.0)],
        [],
        [(0, 1, 2, 3)],
    )
    mesh.update()
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(obj)
    obj.hide_render = True
    layer = mesh.uv_layers.new(name="UVMap")
    coordinates = (
        [(0.0, 0.0)] * 4
        if invalid
        else [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)]
    )
    for polygon in mesh.polygons:
        for loop_index in polygon.loop_indices:
            vertex_index = mesh.loops[loop_index].vertex_index
            layer.uv[loop_index].vector = coordinates[vertex_index]
    return obj

uv_probe("UVProbe", invalid=False)
uv_probe("UVProbeBad", invalid=True)
bpy.ops.wm.save_as_mainfile(filepath={str(scene)!r})
"""
    scene.parent.mkdir(parents=True, exist_ok=True)
    fixture_script = scene.with_suffix(".fixture.py")
    fixture_script.write_text(script, encoding="utf-8")
    try:
        result = run_process(
            [
                str(blender),
                "--background",
                "--factory-startup",
                "--disable-autoexec",
                "--python-exit-code",
                "1",
                "--python",
                str(fixture_script),
            ],
            timeout_seconds=120,
        )
    finally:
        try:
            fixture_script.unlink()
        except OSError:
            pass

    if not result["ok"]:
        raise RuntimeError(json.dumps(result, indent=2))


def _run_companion_smoke(
    blender: Path,
    companion: Path,
    scene: Path,
    root: Path,
    label: str,
) -> dict:
    state_root = root / "state"
    project_artifacts = state_root / "artifacts" / "bench"
    control_root = root / "control" / label
    output_dir = project_artifacts / label
    scripts_root = root / "automation" / "blender"
    scripts_root.mkdir(parents=True, exist_ok=True)

    before = hashlib.sha256(scene.read_bytes()).hexdigest()
    result = run_process(
        [
            str(blender),
            "--background",
            str(scene),
            "--python-exit-code",
            "1",
            "--python",
            str(companion),
            "--",
            "--ordax-control-root",
            str(control_root),
            "--ordax-project-root",
            str(root),
            "--ordax-scripts-root",
            str(scripts_root),
            "--ordax-artifacts-root",
            str(project_artifacts),
            "--ordax-project-slug",
            "bench",
            "--ordax-smoke-output-dir",
            str(output_dir),
            "--ordax-smoke-mode",
            "silhouette",
            "--ordax-smoke-width",
            "384",
            "--ordax-smoke-height",
            "384",
            "--ordax-smoke-quality-fixture",
            "--ordax-smoke-modeling-fixture",
        ],
        timeout_seconds=180,
    )
    if not result["ok"]:
        raise RuntimeError(json.dumps(result, indent=2))

    result_path = control_root / "results" / "smoke.json"
    if not result_path.is_file():
        raise RuntimeError(f"missing durable companion result: {result_path}")
    data = json.loads(result_path.read_text(encoding="utf-8-sig"))
    if not data.get("ok"):
        raise RuntimeError(json.dumps(data, indent=2))
    valid_quality_path = control_root / "results" / "smoke-quality-valid.json"
    invalid_quality_path = control_root / "results" / "smoke-quality-invalid.json"
    if not valid_quality_path.is_file() or not invalid_quality_path.is_file():
        raise RuntimeError("UV quality fixture did not produce both durable results")
    valid_quality = json.loads(valid_quality_path.read_text(encoding="utf-8-sig"))
    invalid_quality = json.loads(invalid_quality_path.read_text(encoding="utf-8-sig"))
    if not valid_quality.get("ok"):
        raise RuntimeError(
            "UV positive control failed: " + json.dumps(valid_quality, indent=2)
        )
    if invalid_quality.get("ok"):
        raise RuntimeError("UV negative control was incorrectly accepted")
    data["uv_quality"] = {
        "positive_control": True,
        "negative_control_detected": True,
        "valid": valid_quality,
        "invalid": invalid_quality,
    }

    modeling_valid_path = (
        control_root / "results" / "smoke-model-transform.json"
    )
    modeling_invalid_path = (
        control_root / "results" / "smoke-model-invalid.json"
    )
    modeling_create_path = (
        control_root / "results" / "smoke-model-create.json"
    )
    modeling_create_duplicate_path = (
        control_root / "results" / "smoke-model-create-duplicate.json"
    )
    modeling_modifier_path = (
        control_root / "results" / "smoke-model-modifier.json"
    )
    modeling_modifier_duplicate_path = (
        control_root / "results" / "smoke-model-modifier-duplicate.json"
    )
    modeling_array_path = control_root / "results" / "smoke-model-array.json"
    modeling_array_invalid_path = control_root / "results" / "smoke-model-array-invalid.json"
    modeling_scatter_path = control_root / "results" / "smoke-model-scatter.json"
    modeling_scatter_invalid_path = control_root / "results" / "smoke-model-scatter-invalid.json"
    modeling_cut_preview_path = control_root / "results" / "smoke-model-cut-preview.json"
    modeling_cut_invalid_path = control_root / "results" / "smoke-model-cut-invalid.json"
    modeling_cut_commit_path = control_root / "results" / "smoke-model-cut-commit.json"
    modeling_cut_cancel_path = control_root / "results" / "smoke-model-cut-cancel.json"
    modeling_cleanup_blocked_path = control_root / "results" / "smoke-model-cleanup-blocked.json"
    modeling_cleanup_diag_path = control_root / "results" / "smoke-model-cleanup-diagnostic.json"
    modeling_cleanup_path = control_root / "results" / "smoke-model-cleanup.json"
    modeling_cleanup_stale_path = control_root / "results" / "smoke-model-cleanup-stale.json"
    modeling_degenerate_diag_path = control_root / "results" / "smoke-model-degenerate-diagnostic.json"
    modeling_degenerate_preview_path = control_root / "results" / "smoke-model-degenerate-preview.json"
    modeling_degenerate_cancel_path = control_root / "results" / "smoke-model-degenerate-cancel.json"
    modeling_degenerate_preview_commit_path = control_root / "results" / "smoke-model-degenerate-preview-commit.json"
    modeling_degenerate_commit_path = control_root / "results" / "smoke-model-degenerate-commit.json"
    modeling_degenerate_stale_path = control_root / "results" / "smoke-model-degenerate-stale.json"
    modeling_merge_preview_path = control_root / "results" / "smoke-model-merge-preview.json"
    modeling_merge_cancel_path = control_root / "results" / "smoke-model-merge-cancel.json"
    modeling_merge_preview_commit_path = control_root / "results" / "smoke-model-merge-preview-commit.json"
    modeling_merge_commit_path = control_root / "results" / "smoke-model-merge-commit.json"
    modeling_merge_stale_path = control_root / "results" / "smoke-model-merge-stale.json"
    modeling_merge_noop_path = control_root / "results" / "smoke-model-merge-noop.json"
    modeling_hole_diag_path = control_root / "results" / "smoke-model-hole-diagnostic.json"
    modeling_hole_preview_path = control_root / "results" / "smoke-model-hole-preview.json"
    modeling_hole_cancel_path = control_root / "results" / "smoke-model-hole-cancel.json"
    modeling_hole_invalid_path = control_root / "results" / "smoke-model-hole-invalid.json"
    modeling_hole_preview_commit_path = control_root / "results" / "smoke-model-hole-preview-commit.json"
    modeling_hole_commit_path = control_root / "results" / "smoke-model-hole-commit.json"
    modeling_hole_stale_path = control_root / "results" / "smoke-model-hole-stale.json"
    if not all(
        path.is_file()
        for path in (
            modeling_valid_path,
            modeling_invalid_path,
            modeling_create_path,
            modeling_create_duplicate_path,
            modeling_modifier_path,
            modeling_modifier_duplicate_path,
            modeling_array_path,
            modeling_array_invalid_path,
            modeling_scatter_path,
            modeling_scatter_invalid_path,
            modeling_cut_preview_path,
            modeling_cut_invalid_path,
            modeling_cut_commit_path,
            modeling_cut_cancel_path,
            modeling_cleanup_blocked_path,
            modeling_cleanup_diag_path,
            modeling_cleanup_path,
            modeling_cleanup_stale_path,
            modeling_degenerate_diag_path,
            modeling_degenerate_preview_path,
            modeling_degenerate_cancel_path,
            modeling_degenerate_preview_commit_path,
            modeling_degenerate_commit_path,
            modeling_degenerate_stale_path,
            modeling_merge_preview_path,
            modeling_merge_cancel_path,
            modeling_merge_preview_commit_path,
            modeling_merge_commit_path,
            modeling_merge_stale_path,
            modeling_merge_noop_path,
            modeling_hole_diag_path,
            modeling_hole_preview_path,
            modeling_hole_cancel_path,
            modeling_hole_invalid_path,
            modeling_hole_preview_commit_path,
            modeling_hole_commit_path,
            modeling_hole_stale_path,
        )
    ):
        raise RuntimeError(
            "modeling fixture did not produce all durable results"
        )
    modeling_valid = json.loads(
        modeling_valid_path.read_text(encoding="utf-8-sig")
    )
    modeling_invalid = json.loads(
        modeling_invalid_path.read_text(encoding="utf-8-sig")
    )
    modeling_create = json.loads(
        modeling_create_path.read_text(encoding="utf-8-sig")
    )
    modeling_create_duplicate = json.loads(
        modeling_create_duplicate_path.read_text(encoding="utf-8-sig")
    )
    modeling_modifier = json.loads(
        modeling_modifier_path.read_text(encoding="utf-8-sig")
    )
    modeling_modifier_duplicate = json.loads(
        modeling_modifier_duplicate_path.read_text(encoding="utf-8-sig")
    )
    modeling_array = json.loads(
        modeling_array_path.read_text(encoding="utf-8-sig")
    )
    modeling_array_invalid = json.loads(
        modeling_array_invalid_path.read_text(encoding="utf-8-sig")
    )
    modeling_scatter = json.loads(
        modeling_scatter_path.read_text(encoding="utf-8-sig")
    )
    modeling_scatter_invalid = json.loads(
        modeling_scatter_invalid_path.read_text(encoding="utf-8-sig")
    )
    modeling_cut_preview = json.loads(modeling_cut_preview_path.read_text(encoding="utf-8-sig"))
    modeling_cut_invalid = json.loads(modeling_cut_invalid_path.read_text(encoding="utf-8-sig"))
    modeling_cut_commit = json.loads(modeling_cut_commit_path.read_text(encoding="utf-8-sig"))
    modeling_cut_cancel = json.loads(modeling_cut_cancel_path.read_text(encoding="utf-8-sig"))
    modeling_cleanup_blocked = json.loads(modeling_cleanup_blocked_path.read_text(encoding="utf-8-sig"))
    modeling_cleanup_diag = json.loads(modeling_cleanup_diag_path.read_text(encoding="utf-8-sig"))
    modeling_cleanup = json.loads(modeling_cleanup_path.read_text(encoding="utf-8-sig"))
    modeling_cleanup_stale = json.loads(modeling_cleanup_stale_path.read_text(encoding="utf-8-sig"))
    modeling_degenerate_diag = json.loads(modeling_degenerate_diag_path.read_text(encoding="utf-8-sig"))
    modeling_degenerate_preview = json.loads(modeling_degenerate_preview_path.read_text(encoding="utf-8-sig"))
    modeling_degenerate_cancel = json.loads(modeling_degenerate_cancel_path.read_text(encoding="utf-8-sig"))
    modeling_degenerate_preview_commit = json.loads(modeling_degenerate_preview_commit_path.read_text(encoding="utf-8-sig"))
    modeling_degenerate_commit = json.loads(modeling_degenerate_commit_path.read_text(encoding="utf-8-sig"))
    modeling_degenerate_stale = json.loads(modeling_degenerate_stale_path.read_text(encoding="utf-8-sig"))
    modeling_merge_preview = json.loads(modeling_merge_preview_path.read_text(encoding="utf-8-sig"))
    modeling_merge_cancel = json.loads(modeling_merge_cancel_path.read_text(encoding="utf-8-sig"))
    modeling_merge_preview_commit = json.loads(modeling_merge_preview_commit_path.read_text(encoding="utf-8-sig"))
    modeling_merge_commit = json.loads(modeling_merge_commit_path.read_text(encoding="utf-8-sig"))
    modeling_merge_stale = json.loads(modeling_merge_stale_path.read_text(encoding="utf-8-sig"))
    modeling_merge_noop = json.loads(modeling_merge_noop_path.read_text(encoding="utf-8-sig"))
    modeling_hole_diag = json.loads(modeling_hole_diag_path.read_text(encoding="utf-8-sig"))
    modeling_hole_preview = json.loads(modeling_hole_preview_path.read_text(encoding="utf-8-sig"))
    modeling_hole_cancel = json.loads(modeling_hole_cancel_path.read_text(encoding="utf-8-sig"))
    modeling_hole_invalid = json.loads(modeling_hole_invalid_path.read_text(encoding="utf-8-sig"))
    modeling_hole_preview_commit = json.loads(modeling_hole_preview_commit_path.read_text(encoding="utf-8-sig"))
    modeling_hole_commit = json.loads(modeling_hole_commit_path.read_text(encoding="utf-8-sig"))
    modeling_hole_stale = json.loads(modeling_hole_stale_path.read_text(encoding="utf-8-sig"))
    if not modeling_valid.get("ok"):
        raise RuntimeError(
            "modeling positive control failed: "
            + json.dumps(modeling_valid, indent=2)
        )
    if modeling_invalid.get("ok"):
        raise RuntimeError(
            "modeling negative control was incorrectly accepted"
        )

    if not modeling_create.get("ok"):
        raise RuntimeError(
            "primitive creation smoke failed: "
            + json.dumps(modeling_create, indent=2)
        )
    if modeling_create_duplicate.get("ok"):
        raise RuntimeError(
            "duplicate primitive name was incorrectly accepted"
        )
    if not modeling_modifier.get("ok"):
        raise RuntimeError(
            "modifier insertion smoke failed: "
            + json.dumps(modeling_modifier, indent=2)
        )
    if modeling_modifier_duplicate.get("ok"):
        raise RuntimeError(
            "duplicate modifier name was incorrectly accepted"
        )
    if not modeling_array.get("ok"):
        raise RuntimeError(
            "ARRAY modifier smoke failed: " + json.dumps(modeling_array, indent=2)
        )
    if modeling_array_invalid.get("ok"):
        raise RuntimeError(
            "ARRAY count above the contract limit was incorrectly accepted"
        )
    if not modeling_scatter.get("ok"):
        raise RuntimeError(
            "surface scatter smoke failed: " + json.dumps(modeling_scatter, indent=2)
        )
    if modeling_scatter_invalid.get("ok"):
        raise RuntimeError(
            "surface scatter instance cap overflow was incorrectly accepted"
        )
    if not modeling_cut_preview.get("ok"):
        raise RuntimeError("boolean cutter preview smoke failed: " + json.dumps(modeling_cut_preview, indent=2))
    if modeling_cut_invalid.get("ok"):
        raise RuntimeError("boolean cutter expansion overflow was incorrectly accepted")
    if not modeling_cut_commit.get("ok") or modeling_cut_commit.get("state") != "committed":
        raise RuntimeError("boolean cutter commit smoke failed")
    if not modeling_cut_cancel.get("ok") or modeling_cut_cancel.get("state") != "cancelled":
        raise RuntimeError("boolean cutter cancel smoke failed")
    if modeling_cleanup_blocked.get("ok"):
        raise RuntimeError("modifier-blocked mesh cleanup diagnostic unexpectedly passed")
    cleanup_blocked_check = (modeling_cleanup_blocked.get("checks") or [{}])[0]
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
    if modeling_cleanup_diag.get("ok"):
        raise RuntimeError("mesh cleanup diagnostic unexpectedly passed")
    cleanup_check = (modeling_cleanup_diag.get("checks") or [{}])[0]
    if int((cleanup_check.get("metrics") or {}).get("loose_vertices") or 0) != 1:
        raise RuntimeError("mesh cleanup diagnostic did not measure one loose vertex")
    cleanup_hint = next((hint for hint in (cleanup_check.get("repair_hints") or []) if hint.get("code") == "remove_isolated_vertices"), None)
    if not cleanup_hint or not cleanup_hint.get("automatic"):
        raise RuntimeError("mesh cleanup diagnostic did not expose the bounded auto-fix")
    if not modeling_cleanup.get("ok") or int(modeling_cleanup.get("removed_vertices") or 0) != 1:
        raise RuntimeError("mesh cleanup smoke did not remove exactly one isolated vertex")
    if int((modeling_cleanup.get("before_mesh") or {}).get("vertices") or 0) != 9:
        raise RuntimeError("mesh cleanup pre-state did not contain 9 vertices")
    if int((modeling_cleanup.get("after_mesh") or {}).get("vertices") or 0) != 8:
        raise RuntimeError("mesh cleanup post-state did not restore 8 vertices")
    if modeling_cleanup_stale.get("ok"):
        raise RuntimeError("stale mesh cleanup fingerprint was incorrectly accepted")
    if "fingerprint changed" not in str(modeling_cleanup_stale.get("summary") or ""):
        raise RuntimeError("stale mesh cleanup rejection did not expose fingerprint mismatch")

    if modeling_degenerate_diag.get("ok"):
        raise RuntimeError("degenerate repair diagnostic unexpectedly passed")
    degenerate_check = (modeling_degenerate_diag.get("checks") or [{}])[0]
    degenerate_metrics = degenerate_check.get("metrics") or {}
    if int(degenerate_metrics.get("zero_length_edges") or 0) != 1:
        raise RuntimeError("degenerate repair diagnostic did not measure one zero-length edge")
    if int(degenerate_metrics.get("degenerate_faces") or 0) != 1:
        raise RuntimeError("degenerate repair diagnostic did not measure one degenerate face")
    degenerate_hint = next(
        (
            hint for hint in (degenerate_check.get("repair_hints") or [])
            if hint.get("code") == "preview_degenerate_dissolve"
        ),
        None,
    )
    if not degenerate_hint or not degenerate_hint.get("preview_available"):
        raise RuntimeError("degenerate repair diagnostic did not expose a preview fix")
    if not modeling_degenerate_preview.get("ok") or modeling_degenerate_preview.get("state") != "preview":
        raise RuntimeError("degenerate repair preview smoke failed")
    degenerate_before = modeling_degenerate_preview.get("before_issues") or {}
    degenerate_after = modeling_degenerate_preview.get("candidate_issues") or {}
    if int(degenerate_before.get("zero_length_edges") or 0) != 1 or int(degenerate_before.get("degenerate_faces") or 0) != 1:
        raise RuntimeError("degenerate repair preview lost diagnosed pre-state")
    if int(degenerate_after.get("zero_length_edges") or 0) != 0 or int(degenerate_after.get("degenerate_faces") or 0) != 0:
        raise RuntimeError("degenerate repair candidate did not clear both diagnosed categories")
    if not modeling_degenerate_cancel.get("ok") or modeling_degenerate_cancel.get("state") != "cancelled":
        raise RuntimeError("degenerate repair cancel smoke failed")
    if modeling_degenerate_cancel.get("restored_geometry_sha256") != modeling_degenerate_preview.get("before_geometry_sha256"):
        raise RuntimeError("degenerate repair cancel did not restore the original geometry fingerprint")
    if not modeling_degenerate_preview_commit.get("ok"):
        raise RuntimeError("degenerate repair second preview failed")
    if not modeling_degenerate_commit.get("ok") or modeling_degenerate_commit.get("state") != "committed":
        raise RuntimeError("degenerate repair commit smoke failed")
    if modeling_degenerate_stale.get("ok"):
        raise RuntimeError("stale degenerate repair preview was incorrectly accepted")
    if "fingerprint changed" not in str(modeling_degenerate_stale.get("summary") or ""):
        raise RuntimeError("stale degenerate repair rejection did not expose fingerprint mismatch")

    if not modeling_merge_preview.get("ok") or modeling_merge_preview.get("state") != "preview":
        raise RuntimeError("merge-by-distance preview smoke failed")
    if modeling_merge_preview.get("selected_vertex_indices") != [0, 1]:
        raise RuntimeError("merge-by-distance preview lost explicit selection")
    if int(modeling_merge_preview.get("merged_vertices") or 0) != 1:
        raise RuntimeError("merge-by-distance preview did not merge exactly one selected vertex")
    if int((modeling_merge_preview.get("before_mesh") or {}).get("vertices") or 0) != 6:
        raise RuntimeError("merge-by-distance preview pre-state did not contain six vertices")
    if int((modeling_merge_preview.get("candidate_mesh") or {}).get("vertices") or 0) != 5:
        raise RuntimeError("merge-by-distance candidate did not contain five vertices")
    if not modeling_merge_cancel.get("ok") or modeling_merge_cancel.get("state") != "cancelled":
        raise RuntimeError("merge-by-distance cancel smoke failed")
    if modeling_merge_cancel.get("restored_geometry_sha256") != modeling_merge_preview.get("before_geometry_sha256"):
        raise RuntimeError("merge-by-distance cancel did not restore the original fingerprint")
    if modeling_merge_noop.get("ok"):
        raise RuntimeError("merge-by-distance no-op selection was incorrectly accepted")
    if "produced no merge" not in str(modeling_merge_noop.get("summary") or ""):
        raise RuntimeError("merge-by-distance no-op rejection did not report no merge")
    if not modeling_merge_preview.get("unselected_vertices_preserved"):
        raise RuntimeError("merge-by-distance preview touched an unselected vertex")
    if int(modeling_merge_preview.get("unselected_vertices_verified") or 0) != 4:
        raise RuntimeError("merge-by-distance preview did not verify all four unselected vertices")
    if not modeling_merge_preview_commit.get("ok"):
        raise RuntimeError("merge-by-distance second preview failed")
    if not modeling_merge_preview_commit.get("unselected_vertices_preserved"):
        raise RuntimeError("merge-by-distance second preview touched an unselected vertex")
    if int(modeling_merge_preview_commit.get("unselected_vertices_verified") or 0) != 4:
        raise RuntimeError("merge-by-distance second preview did not verify all four unselected vertices")
    if not modeling_merge_commit.get("ok") or modeling_merge_commit.get("state") != "committed":
        raise RuntimeError("merge-by-distance commit smoke failed")
    if modeling_merge_commit.get("committed_geometry_sha256") != modeling_merge_preview_commit.get("candidate_geometry_sha256"):
        raise RuntimeError("merge-by-distance commit changed the reviewed candidate fingerprint")
    if modeling_merge_stale.get("ok"):
        raise RuntimeError("stale merge-by-distance preview was incorrectly accepted")
    if "fingerprint changed" not in str(modeling_merge_stale.get("summary") or ""):
        raise RuntimeError("stale merge-by-distance rejection did not expose fingerprint mismatch")

    if modeling_hole_diag.get("ok"):
        raise RuntimeError("boundary-hole diagnostic unexpectedly passed")
    hole_check = (modeling_hole_diag.get("checks") or [{}])[0]
    if int((hole_check.get("metrics") or {}).get("boundary_loops") or 0) != 2:
        raise RuntimeError("boundary-hole diagnostic did not measure two closed loops")
    hole_hint = next(
        (hint for hint in (hole_check.get("repair_hints") or []) if hint.get("code") == "preview_boundary_hole_fill"),
        None,
    )
    if not hole_hint or len(hole_hint.get("preview_candidates") or []) < 2:
        raise RuntimeError("boundary-hole diagnostic did not expose explicit preview candidates")
    if not modeling_hole_preview.get("ok") or modeling_hole_preview.get("state") != "preview":
        raise RuntimeError("boundary hole-fill preview smoke failed")
    if len(modeling_hole_preview.get("selected_edge_indices") or []) != 4:
        raise RuntimeError("boundary hole-fill preview lost the four-edge selection")
    if int(modeling_hole_preview.get("new_faces") or 0) != 1:
        raise RuntimeError("boundary hole-fill preview did not create exactly one face")
    if int(modeling_hole_preview.get("new_edges") or 0) != 0:
        raise RuntimeError("boundary hole-fill preview unexpectedly created an edge")
    if not modeling_hole_preview.get("original_topology_preserved"):
        raise RuntimeError("boundary hole-fill preview did not prove original topology preservation")
    if int(modeling_hole_preview.get("boundary_edges_before") or 0) != 8 or int(modeling_hole_preview.get("boundary_edges_after") or 0) != 4:
        raise RuntimeError("boundary hole-fill preview did not close exactly one four-edge loop")
    if int((modeling_hole_preview.get("before_mesh") or {}).get("polygons") or 0) != 4:
        raise RuntimeError("boundary hole-fill pre-state did not contain four faces")
    if int((modeling_hole_preview.get("candidate_mesh") or {}).get("polygons") or 0) != 5:
        raise RuntimeError("boundary hole-fill candidate did not contain five faces")
    if not modeling_hole_cancel.get("ok") or modeling_hole_cancel.get("state") != "cancelled":
        raise RuntimeError("boundary hole-fill cancel smoke failed")
    if modeling_hole_cancel.get("restored_geometry_sha256") != modeling_hole_preview.get("before_geometry_sha256"):
        raise RuntimeError("boundary hole-fill cancel did not restore the original fingerprint")
    if modeling_hole_invalid.get("ok"):
        raise RuntimeError("open boundary chain was incorrectly accepted as a hole loop")
    if "closed" not in str(modeling_hole_invalid.get("summary") or ""):
        raise RuntimeError("open boundary-chain rejection did not expose closed-loop failure")
    if not modeling_hole_preview_commit.get("ok"):
        raise RuntimeError("boundary hole-fill second preview failed")
    if not modeling_hole_commit.get("ok") or modeling_hole_commit.get("state") != "committed":
        raise RuntimeError("boundary hole-fill commit smoke failed")
    if modeling_hole_commit.get("committed_geometry_sha256") != modeling_hole_preview_commit.get("candidate_geometry_sha256"):
        raise RuntimeError("boundary hole-fill commit changed the reviewed candidate fingerprint")
    if modeling_hole_stale.get("ok"):
        raise RuntimeError("stale boundary hole-fill preview was incorrectly accepted")
    if "fingerprint changed" not in str(modeling_hole_stale.get("summary") or ""):
        raise RuntimeError("stale boundary hole-fill rejection did not expose fingerprint mismatch")

    created_object = modeling_create.get("object") or {}
    if (created_object.get("mesh") or {}).get("vertices") != 8:
        raise RuntimeError(
            "smoke cube did not expose 8 vertices"
        )

    modifier_object = modeling_modifier.get("object") or {}
    if not any(
        item.get("name") == "SmokeBevel" and item.get("type") == "BEVEL"
        for item in (modifier_object.get("modifiers") or [])
    ):
        raise RuntimeError("smoke BEVEL modifier is missing")
    if not (modeling_modifier.get("runtime_budget") or {}).get("allowed"):
        raise RuntimeError("smoke modifier exceeded runtime budget")

    array_object = modeling_array.get("object") or {}
    if not any(
        item.get("name") == "SmokeArray" and item.get("type") == "ARRAY"
        for item in (array_object.get("modifiers") or [])
    ):
        raise RuntimeError("smoke ARRAY modifier is missing")
    array_budget = modeling_array.get("runtime_budget") or {}
    if not array_budget.get("allowed") or int(array_budget.get("count") or 0) != 4:
        raise RuntimeError("smoke ARRAY runtime budget/count mismatch")

    scatter_object = modeling_scatter.get("object") or {}
    if not any(
        item.get("name") == "SmokeScatter" and item.get("type") == "NODES"
        for item in (scatter_object.get("modifiers") or [])
    ):
        raise RuntimeError("smoke surface scatter modifier is missing")
    scatter_budget = modeling_scatter.get("runtime_budget") or {}
    if not scatter_budget.get("allowed"):
        raise RuntimeError("smoke surface scatter exceeded runtime budget")
    if int(modeling_scatter.get("instance_count") or 0) != 25:
        raise RuntimeError("smoke surface scatter did not enforce max_instances=25")
    if int((modeling_scatter.get("scatter_parameters") or {}).get("seed") or -1) != 37:
        raise RuntimeError("smoke surface scatter did not preserve seed=37")

    if modeling_cut_preview.get("state") != "preview":
        raise RuntimeError("smoke boolean cutter preview did not report preview state")
    if len(modeling_cut_preview.get("cutters") or []) != 7:
        raise RuntimeError("smoke boolean cutter preview did not create 7 cutters")
    if not (modeling_cut_preview.get("runtime_budget") or {}).get("allowed"):
        raise RuntimeError("smoke boolean cutter preview exceeded runtime budget")
    cut_types = {item.get("type") for item in (modeling_cut_preview.get("profiles") or [])}
    if cut_types != {"box", "circle", "slot", "polygon", "vent"}:
        raise RuntimeError("smoke boolean cutter preview lost typed profile variants")
    if len(modeling_cut_cancel.get("removed_modifiers") or []) != 7:
        raise RuntimeError("smoke boolean cutter cancel did not remove 7 modifiers")
    if len(modeling_cut_cancel.get("removed_cutters") or []) != 7:
        raise RuntimeError("smoke boolean cutter cancel did not remove 7 cutters")
    before_modifier_names = [item.get("name") for item in ((modeling_cut_preview.get("before") or {}).get("modifiers") or [])]
    after_modifier_names = [item.get("name") for item in ((modeling_cut_cancel.get("object") or {}).get("modifiers") or [])]
    if before_modifier_names != after_modifier_names:
        raise RuntimeError("smoke boolean cutter cancel did not restore modifier stack")

    modeled_object = modeling_valid.get("object") or {}
    if modeled_object.get("location") != [1.25, -0.5, 0.75]:
        raise RuntimeError(
            "modeling fixture location mismatch: "
            + repr(modeled_object.get("location"))
        )
    if modeled_object.get("scale") != [1.0, 1.25, 0.75]:
        raise RuntimeError(
            "modeling fixture scale mismatch: "
            + repr(modeled_object.get("scale"))
        )

    trajectory_path = control_root / "trajectory.jsonl"
    if not trajectory_path.is_file():
        raise RuntimeError("modeling fixture did not create trajectory evidence")
    trajectory_ids = set()
    for line in trajectory_path.read_text(
        encoding="utf-8-sig"
    ).splitlines():
        if not line.strip():
            continue
        entry = json.loads(line)
        identifier = str(entry.get("id") or "")
        if identifier:
            trajectory_ids.add(identifier)
    if not {
        "smoke-model-transform",
        "smoke-model-invalid",
        "smoke-model-create",
        "smoke-model-create-duplicate",
        "smoke-model-modifier",
        "smoke-model-modifier-duplicate",
        "smoke-model-array",
        "smoke-model-array-invalid",
        "smoke-model-scatter",
        "smoke-model-scatter-invalid",
        "smoke-model-cut-preview",
        "smoke-model-cut-invalid",
        "smoke-model-cut-commit",
        "smoke-model-cut-cancel",
        "smoke-model-cleanup-blocked",
        "smoke-model-cleanup-diagnostic",
        "smoke-model-cleanup",
        "smoke-model-cleanup-stale",
        "smoke-model-degenerate-diagnostic",
        "smoke-model-degenerate-preview",
        "smoke-model-degenerate-cancel",
        "smoke-model-degenerate-preview-commit",
        "smoke-model-degenerate-commit",
        "smoke-model-degenerate-stale",
        "smoke-model-merge-preview",
        "smoke-model-merge-cancel",
        "smoke-model-merge-preview-commit",
        "smoke-model-merge-commit",
        "smoke-model-merge-stale",
        "smoke-model-merge-noop",
        "smoke-model-hole-diagnostic",
        "smoke-model-hole-preview",
        "smoke-model-hole-cancel",
        "smoke-model-hole-invalid",
        "smoke-model-hole-preview-commit",
        "smoke-model-hole-commit",
        "smoke-model-hole-stale",
    }.issubset(trajectory_ids):
        raise RuntimeError(
            "modeling fixture commands are missing from trajectory evidence"
        )

    data["modeling"] = {
        "positive_control": True,
        "negative_control_detected": True,
        "create_positive": True,
        "create_duplicate_detected": True,
        "modifier_positive": True,
        "modifier_duplicate_detected": True,
        "array_positive": True,
        "array_limit_detected": True,
        "scatter_positive": True,
        "scatter_limit_detected": True,
        "cut_preview_positive": True,
        "cut_limit_detected": True,
        "cut_commit_positive": True,
        "cut_cancel_rollback": True,
        "cleanup_modifier_guard": True,
        "cleanup_diagnostic_positive": True,
        "cleanup_positive": True,
        "cleanup_stale_guard": True,
        "degenerate_diagnostic_positive": True,
        "degenerate_preview_positive": True,
        "degenerate_cancel_rollback": True,
        "degenerate_commit_positive": True,
        "degenerate_stale_guard": True,
        "merge_preview_positive": True,
        "merge_cancel_rollback": True,
        "merge_commit_positive": True,
        "merge_stale_guard": True,
        "merge_noop_guard": True,
        "merge_explicit_selection_preserved": True,
        "merge_unselected_survived_preview": bool(modeling_merge_preview.get("unselected_vertices_preserved")),
        "hole_diagnostic_positive": True,
        "hole_preview_positive": True,
        "hole_cancel_rollback": True,
        "hole_commit_positive": True,
        "hole_stale_guard": True,
        "hole_open_chain_guard": True,
        "merge_unselected_survived_commit": bool(modeling_merge_preview_commit.get("unselected_vertices_preserved")),
        "dispatcher_journaled": True,
        "valid": modeling_valid,
        "invalid": modeling_invalid,
        "create": modeling_create,
        "create_duplicate": modeling_create_duplicate,
        "modifier": modeling_modifier,
        "modifier_duplicate": modeling_modifier_duplicate,
        "array": modeling_array,
        "array_invalid": modeling_array_invalid,
        "scatter": modeling_scatter,
        "scatter_invalid": modeling_scatter_invalid,
        "cut_preview": modeling_cut_preview,
        "cut_invalid": modeling_cut_invalid,
        "cut_commit": modeling_cut_commit,
        "cut_cancel": modeling_cut_cancel,
        "cleanup_blocked": modeling_cleanup_blocked,
        "cleanup_diag": modeling_cleanup_diag,
        "cleanup": modeling_cleanup,
        "cleanup_stale": modeling_cleanup_stale,
        "degenerate_diag": modeling_degenerate_diag,
        "degenerate_preview": modeling_degenerate_preview,
        "degenerate_cancel": modeling_degenerate_cancel,
        "degenerate_preview_commit": modeling_degenerate_preview_commit,
        "degenerate_commit": modeling_degenerate_commit,
        "degenerate_stale": modeling_degenerate_stale,
        "merge_preview": modeling_merge_preview,
        "merge_cancel": modeling_merge_cancel,
        "merge_preview_commit": modeling_merge_preview_commit,
        "merge_commit": modeling_merge_commit,
        "merge_stale": modeling_merge_stale,
        "merge_noop": modeling_merge_noop,
        "hole_diag": modeling_hole_diag,
        "hole_preview": modeling_hole_preview,
        "hole_cancel": modeling_hole_cancel,
        "hole_invalid": modeling_hole_invalid,
        "hole_preview_commit": modeling_hole_preview_commit,
        "hole_commit": modeling_hole_commit,
        "hole_stale": modeling_hole_stale,
    }

    after = hashlib.sha256(scene.read_bytes()).hexdigest()
    if before != after:
        raise AssertionError(f"companion modified source scene: {scene}")

    manifest = Path(data["manifest"])
    if not manifest.is_file():
        raise AssertionError(f"multiview manifest missing: {manifest}")
    for artifact in data.get("artifacts", []):
        path = Path(artifact["artifact"])
        if not path.is_file() or not path.read_bytes().startswith(b"\x89PNG\r\n\x1a\n"):
            raise AssertionError(f"invalid PNG artifact: {path}")
    return data


def _registry(root: Path) -> ActionRegistry:
    config = AgentConfig(
        agent_name="blenderbench",
        poll_seconds=5.0,
        state_dir=root / "state",
        agent_repo_path=root,
        hordax_path=root,
        bridge_path=root,
        projects={
            "bench": {
                "path": str(root),
                "apps": ["blender"],
                "blender": {"scripts_dir": "automation/blender"},
            }
        },
        default_project="bench",
    )
    return ActionRegistry(config)


def run(root: Path) -> dict:
    blender = find_blender()
    if not blender:
        raise RuntimeError("Blender not installed")
    blender = Path(blender)

    companion = (
        Path(__file__).resolve().parents[1]
        / "ordax_dev_agent"
        / "assets"
        / "blender_live_companion.py"
    )
    baseline_scene = root / "baseline.blend"
    candidate_scene = root / "candidate.blend"

    _create_scene(blender, baseline_scene, (2.0, 2.0, 2.0))
    _create_scene(blender, candidate_scene, (3.0, 2.0, 2.0))

    baseline = _run_companion_smoke(
        blender, companion, baseline_scene, root, "baseline"
    )
    candidate = _run_companion_smoke(
        blender, companion, candidate_scene, root, "candidate"
    )

    agent = _registry(root)
    identical = agent.execute(
        "blender.multiview_compare",
        {
            "project": "bench",
            "baseline_manifest_path": baseline["manifest"],
            "candidate_manifest_path": baseline["manifest"],
            "min_silhouette_iou": 1.0,
            "max_mae": 0.0,
            "max_changed_ratio": 0.0,
        },
    )
    if not identical.ok:
        raise AssertionError(
            "baseline self-comparison failed: "
            + json.dumps(identical.data, indent=2)
        )

    mutation = agent.execute(
        "blender.multiview_compare",
        {
            "project": "bench",
            "baseline_manifest_path": baseline["manifest"],
            "candidate_manifest_path": candidate["manifest"],
            "min_silhouette_iou": 0.99,
            "write_diff_images": True,
        },
    )
    if mutation.ok:
        raise AssertionError("controlled geometry mutation was not detected")
    if not mutation.data.get("failed_views"):
        raise AssertionError("mutation comparison reported no failed views")

    dimension_error = (
        mutation.data.get("bounds", {}).get("absolute_dimension_error") or []
    )
    if not dimension_error or max(dimension_error) < 0.9:
        raise AssertionError(
            "controlled dimension change was not reflected in bounds evidence"
        )

    return {
        "ok": True,
        "blender": str(blender),
        "baseline_manifest": baseline["manifest"],
        "candidate_manifest": candidate["manifest"],
        "baseline_views": [item["view"] for item in baseline["artifacts"]],
        "uv_quality": {
            "positive_control": baseline["uv_quality"]["positive_control"],
            "negative_control_detected": baseline["uv_quality"]["negative_control_detected"],
            "valid_metrics": (
                (baseline["uv_quality"]["valid"].get("checks") or [{}])[0].get("metrics")
            ),
            "invalid_metrics": (
                (baseline["uv_quality"]["invalid"].get("checks") or [{}])[0].get("metrics")
            ),
        },
        "modeling_dispatch": {
            "transform_positive": baseline["modeling"]["positive_control"],
            "transform_negative_detected": baseline["modeling"]["negative_control_detected"],
            "create_positive": baseline["modeling"]["create_positive"],
            "create_duplicate_detected": baseline["modeling"]["create_duplicate_detected"],
            "modifier_positive": baseline["modeling"]["modifier_positive"],
            "modifier_duplicate_detected": baseline["modeling"]["modifier_duplicate_detected"],
            "array_positive": baseline["modeling"]["array_positive"],
            "array_limit_detected": baseline["modeling"]["array_limit_detected"],
            "scatter_positive": baseline["modeling"]["scatter_positive"],
            "scatter_limit_detected": baseline["modeling"]["scatter_limit_detected"],
            "scatter_instance_count": baseline["modeling"]["scatter"].get("instance_count"),
            "scatter_seed": (baseline["modeling"]["scatter"].get("scatter_parameters") or {}).get("seed"),
            "cut_preview_positive": baseline["modeling"]["cut_preview_positive"],
            "cut_limit_detected": baseline["modeling"]["cut_limit_detected"],
            "cut_commit_positive": baseline["modeling"]["cut_commit_positive"],
            "cut_cancel_rollback": baseline["modeling"]["cut_cancel_rollback"],
            "cut_profile_types": sorted(item.get("type") for item in (baseline["modeling"]["cut_preview"].get("profiles") or [])),
            "cutters_created": len(baseline["modeling"]["cut_preview"].get("cutters") or []),
            "cleanup_modifier_guard": baseline["modeling"]["cleanup_modifier_guard"],
            "cleanup_blocked_repair_hints": (baseline["modeling"]["cleanup_blocked"].get("checks") or [{}])[0].get("repair_hints"),
            "cleanup_diagnostic_positive": baseline["modeling"]["cleanup_diagnostic_positive"],
            "cleanup_positive": baseline["modeling"]["cleanup_positive"],
            "cleanup_stale_guard": baseline["modeling"]["cleanup_stale_guard"],
            "cleanup_removed_vertices": baseline["modeling"]["cleanup"].get("removed_vertices"),
            "cleanup_repair_hints": (baseline["modeling"]["cleanup_diag"].get("checks") or [{}])[0].get("repair_hints"),
            "degenerate_diagnostic_positive": baseline["modeling"]["degenerate_diagnostic_positive"],
            "degenerate_preview_positive": baseline["modeling"]["degenerate_preview_positive"],
            "degenerate_cancel_rollback": baseline["modeling"]["degenerate_cancel_rollback"],
            "degenerate_commit_positive": baseline["modeling"]["degenerate_commit_positive"],
            "degenerate_stale_guard": baseline["modeling"]["degenerate_stale_guard"],
            "degenerate_before_issues": baseline["modeling"]["degenerate_preview"].get("before_issues"),
            "degenerate_candidate_issues": baseline["modeling"]["degenerate_preview"].get("candidate_issues"),
            "degenerate_repair_hints": (baseline["modeling"]["degenerate_diag"].get("checks") or [{}])[0].get("repair_hints"),
            "merge_preview_positive": baseline["modeling"]["merge_preview_positive"],
            "merge_cancel_rollback": baseline["modeling"]["merge_cancel_rollback"],
            "merge_commit_positive": baseline["modeling"]["merge_commit_positive"],
            "merge_stale_guard": baseline["modeling"]["merge_stale_guard"],
            "merge_noop_guard": baseline["modeling"]["merge_noop_guard"],
            "merge_explicit_selection_preserved": baseline["modeling"]["merge_explicit_selection_preserved"],
            "merge_unselected_survived_preview": baseline["modeling"]["merge_unselected_survived_preview"],
            "merge_unselected_survived_commit": baseline["modeling"]["merge_unselected_survived_commit"],
            "merge_selected_vertex_indices": baseline["modeling"]["merge_preview"].get("selected_vertex_indices"),
            "merge_merged_vertices": baseline["modeling"]["merge_preview"].get("merged_vertices"),
            "merge_before_mesh": baseline["modeling"]["merge_preview"].get("before_mesh"),
            "merge_candidate_mesh": baseline["modeling"]["merge_preview"].get("candidate_mesh"),
            "hole_diagnostic_positive": baseline["modeling"]["hole_diagnostic_positive"],
            "hole_preview_positive": baseline["modeling"]["hole_preview_positive"],
            "hole_cancel_rollback": baseline["modeling"]["hole_cancel_rollback"],
            "hole_commit_positive": baseline["modeling"]["hole_commit_positive"],
            "hole_stale_guard": baseline["modeling"]["hole_stale_guard"],
            "hole_open_chain_guard": baseline["modeling"]["hole_open_chain_guard"],
            "hole_boundary_loops": ((baseline["modeling"]["hole_diag"].get("checks") or [{}])[0].get("metrics") or {}).get("boundary_loops"),
            "hole_selected_edge_indices": baseline["modeling"]["hole_preview"].get("selected_edge_indices"),
            "hole_new_faces": baseline["modeling"]["hole_preview"].get("new_faces"),
            "hole_new_edges": baseline["modeling"]["hole_preview"].get("new_edges"),
            "hole_boundary_edges_before": baseline["modeling"]["hole_preview"].get("boundary_edges_before"),
            "hole_boundary_edges_after": baseline["modeling"]["hole_preview"].get("boundary_edges_after"),
            "hole_before_mesh": baseline["modeling"]["hole_preview"].get("before_mesh"),
            "hole_candidate_mesh": baseline["modeling"]["hole_preview"].get("candidate_mesh"),
            "hole_repair_hints": (baseline["modeling"]["hole_diag"].get("checks") or [{}])[0].get("repair_hints"),
            "dispatcher_journaled": baseline["modeling"]["dispatcher_journaled"],
            "location": (
                (baseline["modeling"]["valid"].get("object") or {}).get("location")
            ),
            "scale": (
                (baseline["modeling"]["valid"].get("object") or {}).get("scale")
            ),
            "created_mesh_vertices": (
                ((baseline["modeling"]["create"].get("object") or {}).get("mesh") or {}).get("vertices")
            ),
            "modifier_runtime_budget": baseline["modeling"]["modifier"].get("runtime_budget"),
            "array_runtime_budget": baseline["modeling"]["array"].get("runtime_budget"),
            "scatter_runtime_budget": baseline["modeling"]["scatter"].get("runtime_budget"),
            "cut_runtime_budget": baseline["modeling"]["cut_preview"].get("runtime_budget"),
        },
        "self_comparison": {
            "passed": identical.data.get("comparison_passed"),
            "views": identical.data.get("views"),
        },
        "mutation_detection": {
            "detected": not mutation.ok,
            "failed_views": mutation.data.get("failed_views"),
            "bounds": mutation.data.get("bounds"),
            "report": mutation.data.get("report"),
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output-dir",
        help="Keep benchmark files in this directory instead of a temporary directory.",
    )
    parser.add_argument(
        "--report-file",
        help="Write the final structured benchmark report to this JSON file.",
    )
    args = parser.parse_args()

    if args.output_dir:
        root = Path(args.output_dir).expanduser().resolve()
        root.mkdir(parents=True, exist_ok=True)
        report = run(root)
    else:
        with tempfile.TemporaryDirectory(prefix="ordax-blenderbench-") as raw:
            report = run(Path(raw))

    rendered = json.dumps(report, indent=2)
    if args.report_file:
        report_file = Path(args.report_file).expanduser().resolve()
        report_file.parent.mkdir(parents=True, exist_ok=True)
        report_file.write_text(rendered, encoding="utf-8")
    print(rendered)


if __name__ == "__main__":
    main()
