import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

from ordax_dev_agent.blender_live_bridge import (
    blender_companion_bundle_fingerprint,
)
from ordax_dev_agent.assets.blender_modeling_contracts import (
    evaluate_boolean_cut_runtime_budget,
    evaluate_modifier_runtime_budget,
    evaluate_surface_scatter_runtime_budget,
    modeling_schemas,
    normalize_transform_fields,
    normalize_transform_request,
    plan_modeling_operation,
)
from ordax_dev_agent.assets.blender_material_contracts import normalize_material_request


def load_spatial_math():
    root = Path(__file__).resolve().parents[1]
    path = root / "ordax_dev_agent" / "assets" / "blender_spatial_math.py"
    spec = importlib.util.spec_from_file_location(
        "_test_ordax_blender_spatial_math",
        path,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("could not load Blender spatial math helper")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_quality_rules():
    root = Path(__file__).resolve().parents[1]
    path = root / "ordax_dev_agent" / "assets" / "blender_quality_rules.py"
    spec = importlib.util.spec_from_file_location(
        "_test_ordax_blender_quality_rules",
        path,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("could not load Blender quality rules helper")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_uv_math():
    root = Path(__file__).resolve().parents[1]
    path = root / "ordax_dev_agent" / "assets" / "blender_uv_math.py"
    spec = importlib.util.spec_from_file_location("_test_ordax_blender_uv_math", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("could not load Blender UV math helper")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class BlenderCompanionBundleTests(unittest.TestCase):
    def test_bundle_fingerprint_changes_when_helper_changes(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            (root / "blender_live_companion.py").write_text(
                "print('companion')\n",
                encoding="utf-8",
            )
            helper = root / "blender_uv_math.py"
            helper.write_text("VALUE = 1\n", encoding="utf-8")
            (root / "blender_companion_bundle.json").write_text(
                json.dumps(
                    {
                        "version": 1,
                        "files": [
                            "blender_live_companion.py",
                            "blender_uv_math.py",
                        ],
                    }
                ),
                encoding="utf-8",
            )

            before = blender_companion_bundle_fingerprint(root)
            helper.write_text("VALUE = 2\n", encoding="utf-8")
            after = blender_companion_bundle_fingerprint(root)

        self.assertNotEqual(before, after)

    def test_bundle_fingerprint_is_independent_of_manifest_file_order(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            (root / "a.py").write_text("A = 1\n", encoding="utf-8")
            (root / "b.py").write_text("B = 2\n", encoding="utf-8")
            manifest = root / "blender_companion_bundle.json"
            manifest.write_text(
                json.dumps({"version": 1, "files": ["a.py", "b.py"]}),
                encoding="utf-8",
            )
            first = blender_companion_bundle_fingerprint(root)
            manifest.write_text(
                json.dumps({"version": 1, "files": ["b.py", "a.py"]}),
                encoding="utf-8",
            )
            second = blender_companion_bundle_fingerprint(root)

        self.assertEqual(first, second)

    def test_bundle_rejects_path_escape(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            outside = root.parent / "outside-companion-helper.py"
            outside.write_text("VALUE = 1\n", encoding="utf-8")
            self.addCleanup(outside.unlink, missing_ok=True)
            (root / "blender_companion_bundle.json").write_text(
                json.dumps({"version": 1, "files": ["../outside-companion-helper.py"]}),
                encoding="utf-8",
            )

            with self.assertRaises(ValueError):
                blender_companion_bundle_fingerprint(root)

    def test_repository_bundle_fingerprint_is_valid(self) -> None:
        root = (
            Path(__file__).resolve().parents[1]
            / "ordax_dev_agent"
            / "assets"
        )
        digest = blender_companion_bundle_fingerprint(root)
        self.assertEqual(64, len(digest))
        manifest = json.loads(
            (root / "blender_companion_bundle.json").read_text(encoding="utf-8")
        )
        self.assertEqual(
            [
                "blender_live_companion.py",
                "blender_uv_math.py",
                "blender_spatial_math.py",
                "blender_quality_rules.py",
                "blender_modeling_contracts.py",
                "blender_material_contracts.py",
            ],
            manifest["files"],
        )


class BlenderMaterialContractTests(unittest.TestCase):
    def test_transport_fields_are_allowed_only_when_explicit(self) -> None:
        payload = {
            "id": "abc",
            "operation": "material_apply",
            "object_name": "Acrylic",
            "material_name": "Crystal",
            "transmission": 1.0,
        }
        with self.assertRaisesRegex(ValueError, "unsupported field"):
            normalize_material_request(payload)

        normalized = normalize_material_request(
            payload,
            transport_fields={"id", "operation"},
        )
        self.assertEqual("Acrylic", normalized["object_name"])
        self.assertEqual("Crystal", normalized["material_name"])
        self.assertEqual(1.0, normalized["transmission"])


class BlenderModelingContractTests(unittest.TestCase):
    def test_create_primitive_plan_applies_typed_defaults(self) -> None:
        plan = plan_modeling_operation(
            "create_primitive",
            {"name": "Body", "primitive": "sphere"},
        )
        self.assertTrue(plan["executable"])
        self.assertEqual("available", plan["status"])
        self.assertEqual("blender.live_create_primitive", plan["action"])
        self.assertEqual(
            {
                "name": "Body",
                "primitive": "sphere",
                "location": [0.0, 0.0, 0.0],
                "radius": 1.0,
                "segments": 32,
            },
            plan["arguments"],
        )

    def test_create_plan_rejects_primitive_specific_mismatch(self) -> None:
        with self.assertRaisesRegex(ValueError, "unsupported field.*cube"):
            plan_modeling_operation(
                "create_primitive",
                {
                    "name": "Body",
                    "primitive": "cube",
                    "radius": 1.0,
                },
            )

    def test_modifier_plan_applies_type_specific_defaults(self) -> None:
        plan = plan_modeling_operation(
            "add_modifier",
            {
                "object_name": "Body",
                "name": "SoftEdges",
                "type": "bevel",
            },
        )
        self.assertTrue(plan["executable"])
        self.assertEqual("available", plan["status"])
        self.assertEqual("blender.live_add_modifier", plan["action"])
        self.assertEqual(
            {
                "object_name": "Body",
                "name": "SoftEdges",
                "type": "BEVEL",
                "width": 0.05,
                "segments": 2,
            },
            plan["arguments"],
        )

    def test_array_modifier_plan_is_executable_after_blenderbench_promotion(self) -> None:
        plan = plan_modeling_operation(
            "add_modifier",
            {
                "object_name": "Body",
                "name": "Repeat",
                "type": "ARRAY",
                "count": 4,
                "relative_offset": [1.5, 0, 0],
            },
        )
        self.assertTrue(plan["executable"])
        self.assertEqual("available", plan["status"])
        self.assertEqual("blender.live_add_modifier", plan["action"])
        self.assertEqual(4, plan["arguments"]["count"])
        self.assertEqual([1.5, 0.0, 0.0], plan["arguments"]["relative_offset"])
        self.assertFalse(plan["requires_real_blender_smoke"])
        self.assertEqual(64, plan["runtime_guards"]["max_array_count"])
        self.assertEqual(500000, plan["runtime_guards"]["max_projected_array_faces"])

    def test_array_modifier_plan_rejects_zero_offsets(self) -> None:
        with self.assertRaisesRegex(ValueError, "non-zero relative_offset or constant_offset"):
            plan_modeling_operation(
                "add_modifier",
                {
                    "object_name": "Body",
                    "name": "Repeat",
                    "type": "ARRAY",
                    "relative_offset": [0, 0, 0],
                    "constant_offset": [0, 0, 0],
                },
            )

    def test_surface_scatter_plan_is_executable_after_blenderbench_promotion(self) -> None:
        plan = plan_modeling_operation(
            "surface_scatter",
            {
                "object_name": "Ground",
                "source_object_name": "Rock",
                "name": "RockScatter",
                "density": 2.5,
                "seed": 17,
                "max_instances": 400,
                "scale_min": 0.75,
                "scale_max": 1.25,
            },
        )
        self.assertTrue(plan["executable"])
        self.assertEqual("available", plan["status"])
        self.assertEqual("blender.live_surface_scatter", plan["action"])
        self.assertFalse(plan["requires_real_blender_smoke"])
        self.assertEqual(17, plan["arguments"]["seed"])
        self.assertEqual(400, plan["arguments"]["max_instances"])
        self.assertEqual(5000, plan["runtime_guards"]["max_instances"])
        self.assertEqual(2000000, plan["runtime_guards"]["max_projected_instance_faces"])

    def test_surface_scatter_plan_rejects_unsafe_inputs(self) -> None:
        with self.assertRaisesRegex(ValueError, "source and target"):
            plan_modeling_operation(
                "surface_scatter",
                {
                    "object_name": "Ground",
                    "source_object_name": "Ground",
                    "name": "BadScatter",
                },
            )
        with self.assertRaisesRegex(ValueError, "scale_max"):
            plan_modeling_operation(
                "surface_scatter",
                {
                    "object_name": "Ground",
                    "source_object_name": "Rock",
                    "name": "BadScale",
                    "scale_min": 2.0,
                    "scale_max": 1.0,
                },
            )

    def test_surface_scatter_budget_bounds_projected_geometry(self) -> None:
        accepted = evaluate_surface_scatter_runtime_budget(
            modifier_count=1, target_faces=1000, source_faces=100, max_instances=5000
        )
        self.assertTrue(accepted["allowed"])
        self.assertEqual(500000, accepted["projected_instance_faces"])
        rejected = evaluate_surface_scatter_runtime_budget(
            modifier_count=1, target_faces=1000, source_faces=1000, max_instances=5000
        )
        self.assertFalse(rejected["allowed"])
        self.assertIn(
            "projected scatter geometry exceeds interactive face budget",
            rejected["reasons"],
        )

    def test_boolean_cut_preview_plan_is_executable_after_blenderbench_promotion(self) -> None:
        plan = plan_modeling_operation(
            "boolean_cut_preview",
            {
                "object_name": "Panel",
                "name": "VentPass",
                "profiles": [
                    {"type": "circle", "radius": 0.5, "depth": 2.0},
                    {"type": "slot", "length": 2.0, "width": 0.5, "depth": 2.0},
                    {"type": "polygon", "points": [[-1,-1],[1,-1],[1,1],[-1,1]], "depth": 2.0},
                    {"type": "vent", "length": 2.0, "width": 0.4, "depth": 2.0, "count": 3, "spacing": 0.75},
                ],
            },
        )
        self.assertTrue(plan["executable"])
        self.assertEqual("available", plan["status"])
        self.assertFalse(plan["requires_real_blender_smoke"])
        self.assertEqual("blender.live_boolean_cut_preview", plan["action"])
        self.assertEqual(6, plan["arguments"]["expanded_cutters"])
        self.assertEqual("blender.live_boolean_cut_commit", plan["workflow_actions"]["commit"])
        self.assertEqual("blender.live_boolean_cut_cancel", plan["workflow_actions"]["cancel"])

    def test_boolean_cut_preview_rejects_nonconvex_and_expansion_overflow(self) -> None:
        with self.assertRaisesRegex(ValueError, "convex polygon"):
            plan_modeling_operation(
                "boolean_cut_preview",
                {
                    "object_name": "Panel",
                    "name": "BadPolygon",
                    "profiles": [{"type": "polygon", "points": [[0,0],[2,0],[1,0.5],[2,2],[0,2]], "depth": 1.0}],
                },
            )
        with self.assertRaisesRegex(ValueError, "expand to more than"):
            plan_modeling_operation(
                "boolean_cut_preview",
                {
                    "object_name": "Panel",
                    "name": "TooMany",
                    "profiles": [
                        {"type": "vent", "length": 2.0, "width": 0.4, "depth": 1.0, "count": 8, "spacing": 0.5},
                        {"type": "circle", "radius": 0.25, "depth": 1.0},
                    ],
                },
            )

    def test_boolean_cut_budget_accounts_for_existing_modifier_stack(self) -> None:
        accepted = evaluate_boolean_cut_runtime_budget(
            modifier_count=2, target_faces=1000, expanded_cutters=6, generated_cutter_faces=500
        )
        self.assertTrue(accepted["allowed"])
        rejected = evaluate_boolean_cut_runtime_budget(
            modifier_count=3, target_faces=1000, expanded_cutters=6, generated_cutter_faces=500
        )
        self.assertFalse(rejected["allowed"])
        self.assertIn("boolean preview would exceed modifier stack limit", rejected["reasons"])

    def test_mesh_cleanup_plan_is_executable_after_blenderbench_promotion(self) -> None:
        digest = "a" * 64
        plan = plan_modeling_operation(
            "mesh_cleanup",
            {
                "object_name": "Body",
                "repair": "remove_loose_vertices",
                "expected_base_geometry_sha256": digest,
                "expected_loose_vertices": 3,
            },
        )
        self.assertTrue(plan["executable"])
        self.assertEqual("available", plan["status"])
        self.assertEqual("blender.live_mesh_cleanup", plan["action"])
        self.assertFalse(plan["requires_real_blender_smoke"])
        self.assertEqual(digest, plan["arguments"]["expected_base_geometry_sha256"])
        self.assertEqual(3, plan["arguments"]["expected_loose_vertices"])
        self.assertIn("single_user_mesh_data", plan["runtime_requirements"])
        self.assertIn("no_shape_keys", plan["runtime_requirements"])
        self.assertIn("no_modifiers", plan["runtime_requirements"])
        self.assertIn("mutate_working_mesh_copy_only", plan["failure_policy"])

    def test_mesh_cleanup_plan_rejects_unbounded_or_stale_contract_inputs(self) -> None:
        with self.assertRaisesRegex(ValueError, "64-character SHA-256"):
            plan_modeling_operation(
                "mesh_cleanup",
                {
                    "object_name": "Body",
                    "repair": "remove_loose_vertices",
                    "expected_base_geometry_sha256": "not-a-hash",
                },
            )
        with self.assertRaisesRegex(ValueError, "repair must be remove_loose_vertices"):
            plan_modeling_operation(
                "mesh_cleanup",
                {
                    "object_name": "Body",
                    "repair": "merge_by_distance",
                    "expected_base_geometry_sha256": "b" * 64,
                },
            )

        with self.assertRaisesRegex(ValueError, "expected_loose_vertices must be between 1"):
            plan_modeling_operation(
                "mesh_cleanup",
                {
                    "object_name": "Body",
                    "repair": "remove_loose_vertices",
                    "expected_base_geometry_sha256": "d" * 64,
                    "expected_loose_vertices": 0,
                },
            )

    def test_degenerate_repair_preview_plan_is_executable_after_blenderbench_promotion(self) -> None:
        digest = "e" * 64
        plan = plan_modeling_operation(
            "degenerate_repair_preview",
            {
                "object_name": "Body",
                "expected_base_geometry_sha256": digest,
                "expected_zero_length_edges": 2,
                "expected_degenerate_faces": 1,
                "threshold": 1e-8,
            },
        )
        self.assertTrue(plan["executable"])
        self.assertEqual("available", plan["status"])
        self.assertEqual("blender.live_degenerate_repair_preview", plan["action"])
        self.assertFalse(plan["requires_real_blender_smoke"])
        self.assertEqual(2, plan["arguments"]["expected_zero_length_edges"])
        self.assertEqual(1, plan["arguments"]["expected_degenerate_faces"])
        self.assertEqual(1e-8, plan["arguments"]["threshold"])
        self.assertEqual(
            "blender.live_degenerate_repair_commit",
            plan["workflow_actions"]["commit"],
        )
        self.assertEqual(
            "blender.live_degenerate_repair_cancel",
            plan["workflow_actions"]["cancel"],
        )
        self.assertEqual(0.001, plan["runtime_guards"]["max_repair_distance"])

    def test_degenerate_repair_preview_rejects_empty_or_unbounded_diagnostics(self) -> None:
        base = {
            "object_name": "Body",
            "expected_base_geometry_sha256": "f" * 64,
            "expected_zero_length_edges": 0,
            "expected_degenerate_faces": 0,
        }
        with self.assertRaisesRegex(ValueError, "at least one diagnosed"):
            plan_modeling_operation("degenerate_repair_preview", base)
        with self.assertRaisesRegex(ValueError, "threshold must be between"):
            plan_modeling_operation(
                "degenerate_repair_preview",
                {
                    **base,
                    "expected_zero_length_edges": 1,
                    "threshold": 0.01,
                },
            )
        with self.assertRaisesRegex(ValueError, "diagnosed degenerate elements"):
            plan_modeling_operation(
                "degenerate_repair_preview",
                {
                    **base,
                    "expected_zero_length_edges": 6000,
                    "expected_degenerate_faces": 5000,
                },
            )

    def test_merge_by_distance_preview_plan_is_executable_after_blenderbench_promotion(self) -> None:
        digest = "9" * 64
        plan = plan_modeling_operation(
            "merge_by_distance_preview",
            {
                "object_name": "Body",
                "expected_base_geometry_sha256": digest,
                "vertex_indices": [7, 2, 5],
                "distance": 0.0001,
            },
        )
        self.assertTrue(plan["executable"])
        self.assertEqual("available", plan["status"])
        self.assertEqual("blender.live_merge_by_distance_preview", plan["action"])
        self.assertFalse(plan["requires_real_blender_smoke"])
        self.assertEqual([2, 5, 7], plan["arguments"]["vertex_indices"])
        self.assertEqual(0.0001, plan["arguments"]["distance"])
        self.assertEqual(64, plan["runtime_guards"]["max_selected_vertices"])
        self.assertEqual(0.001, plan["runtime_guards"]["max_distance"])
        self.assertEqual(
            "blender.live_merge_by_distance_commit",
            plan["workflow_actions"]["commit"],
        )
        self.assertEqual(
            "blender.live_merge_by_distance_cancel",
            plan["workflow_actions"]["cancel"],
        )

    def test_merge_by_distance_preview_rejects_unsafe_selection_contracts(self) -> None:
        base = {
            "object_name": "Body",
            "expected_base_geometry_sha256": "8" * 64,
            "distance": 1e-5,
        }
        with self.assertRaisesRegex(ValueError, "between 2 and 64"):
            plan_modeling_operation(
                "merge_by_distance_preview",
                {**base, "vertex_indices": [1]},
            )
        with self.assertRaisesRegex(ValueError, "duplicate vertex indices"):
            plan_modeling_operation(
                "merge_by_distance_preview",
                {**base, "vertex_indices": [1, 1]},
            )
        with self.assertRaisesRegex(ValueError, "distance must be between"):
            plan_modeling_operation(
                "merge_by_distance_preview",
                {**base, "vertex_indices": [1, 2], "distance": 0.01},
            )
        with self.assertRaisesRegex(ValueError, "between 2 and 64"):
            plan_modeling_operation(
                "merge_by_distance_preview",
                {**base, "vertex_indices": list(range(65))},
            )

    def test_boundary_hole_fill_preview_plan_is_executable_after_blenderbench_promotion(self) -> None:
        digest = "7" * 64
        plan = plan_modeling_operation(
            "boundary_hole_fill_preview",
            {
                "object_name": "Panel",
                "expected_base_geometry_sha256": digest,
                "edge_indices": [9, 3, 6, 11],
            },
        )
        self.assertTrue(plan["executable"])
        self.assertEqual("available", plan["status"])
        self.assertEqual("blender.live_boundary_hole_fill_preview", plan["action"])
        self.assertFalse(plan["requires_real_blender_smoke"])
        self.assertEqual([3, 6, 9, 11], plan["arguments"]["edge_indices"])
        self.assertEqual(32, plan["runtime_guards"]["max_boundary_edges"])
        self.assertEqual(32, plan["runtime_guards"]["max_new_faces"])
        self.assertEqual(
            "blender.live_boundary_hole_fill_commit",
            plan["workflow_actions"]["commit"],
        )
        self.assertEqual(
            "blender.live_boundary_hole_fill_cancel",
            plan["workflow_actions"]["cancel"],
        )

    def test_boundary_hole_fill_preview_rejects_unsafe_edge_contracts(self) -> None:
        base = {
            "object_name": "Panel",
            "expected_base_geometry_sha256": "6" * 64,
        }
        with self.assertRaisesRegex(ValueError, "between 3 and 32"):
            plan_modeling_operation(
                "boundary_hole_fill_preview",
                {**base, "edge_indices": [1, 2]},
            )
        with self.assertRaisesRegex(ValueError, "duplicate vertex indices"):
            plan_modeling_operation(
                "boundary_hole_fill_preview",
                {**base, "edge_indices": [1, 2, 2]},
            )
        with self.assertRaisesRegex(ValueError, "between 3 and 32"):
            plan_modeling_operation(
                "boundary_hole_fill_preview",
                {**base, "edge_indices": list(range(33))},
            )

    def test_transform_plan_is_executable_and_closed_to_unknown_fields(self) -> None:
        plan = plan_modeling_operation(
            "object_transform",
            {
                "ordax_object_id": "hull.main",
                "location": [1, 2, 3],
            },
        )
        self.assertTrue(plan["executable"])
        self.assertEqual("blender.live_object_transform", plan["action"])
        self.assertEqual(
            {
                "ordax_object_id": "hull.main",
                "location": [1.0, 2.0, 3.0],
            },
            plan["arguments"],
        )
        with self.assertRaisesRegex(ValueError, "unsupported field"):
            plan_modeling_operation(
                "object_transform",
                {
                    "object_name": "Body",
                    "location": [0, 0, 0],
                    "anything": True,
                },
            )

    def test_selector_rejects_non_string_values(self) -> None:
        with self.assertRaisesRegex(ValueError, "object_name must be a string"):
            plan_modeling_operation(
                "object_transform",
                {
                    "object_name": 123,
                    "location": [0, 0, 0],
                },
            )

    def test_modifier_plan_exposes_runtime_guard_limits(self) -> None:
        plan = plan_modeling_operation(
            "add_modifier",
            {
                "object_name": "Body",
                "name": "Subsurf",
                "type": "SUBSURF",
                "levels": 2,
            },
        )
        self.assertEqual(
            {
                "max_modifier_stack": 8,
                "max_evaluated_faces": 200000,
                "max_projected_subsurf_faces": 500000,
            },
            plan["runtime_guards"],
        )

    def test_create_plan_exposes_runtime_and_failure_contracts(self) -> None:
        plan = plan_modeling_operation(
            "create_primitive",
            {"name": "Body", "primitive": "cube"},
        )
        self.assertEqual(
            ["object_mode", "no_render_job", "unique_object_name"],
            plan["runtime_requirements"],
        )
        self.assertEqual(
            [
                "remove_partial_object_on_failure",
                "remove_partial_mesh_on_failure",
            ],
            plan["failure_policy"],
        )

    def test_modifier_plan_exposes_runtime_and_failure_contracts(self) -> None:
        plan = plan_modeling_operation(
            "add_modifier",
            {
                "object_name": "Body",
                "name": "Edges",
                "type": "BEVEL",
            },
        )
        self.assertIn(
            "local_nonlinked_mesh_target",
            plan["runtime_requirements"],
        )
        self.assertIn(
            "unique_modifier_name",
            plan["runtime_requirements"],
        )
        self.assertIn(
            "animated_or_constrained_target_requires_dedicated_workflow",
            plan["runtime_requirements"],
        )
        self.assertEqual(
            [
                "remove_new_modifier_on_failure",
                "preserve_existing_modifier_stack",
            ],
            plan["failure_policy"],
        )

    def test_transform_plan_does_not_inherit_legacy_mesh_only_runtime_guards(self) -> None:
        plan = plan_modeling_operation(
            "object_transform",
            {
                "object_name": "Camera",
                "location": [1, 2, 3],
            },
        )
        self.assertTrue(plan["executable"])
        self.assertNotIn("runtime_requirements", plan)
        self.assertNotIn("failure_policy", plan)

    def test_modifier_budget_rejects_full_stack(self) -> None:
        result = evaluate_modifier_runtime_budget(
            modifier_type="BEVEL",
            modifier_count=8,
            evaluated_faces=1000,
        )
        self.assertFalse(result["allowed"])
        self.assertIn("modifier stack limit reached", result["reasons"])

    def test_modifier_budget_rejects_mesh_above_evaluated_face_limit(self) -> None:
        result = evaluate_modifier_runtime_budget(
            modifier_type="SOLIDIFY",
            modifier_count=2,
            evaluated_faces=200001,
        )
        self.assertFalse(result["allowed"])
        self.assertIn(
            "evaluated mesh exceeds interactive face budget",
            result["reasons"],
        )

    def test_subsurf_budget_uses_projected_face_count(self) -> None:
        accepted = evaluate_modifier_runtime_budget(
            modifier_type="SUBSURF",
            modifier_count=2,
            evaluated_faces=31250,
            levels=2,
        )
        self.assertTrue(accepted["allowed"])
        self.assertEqual(500000, accepted["projected_faces"])

        rejected = evaluate_modifier_runtime_budget(
            modifier_type="SUBSURF",
            modifier_count=2,
            evaluated_faces=31251,
            levels=2,
        )
        self.assertFalse(rejected["allowed"])
        self.assertEqual(500016, rejected["projected_faces"])
        self.assertIn(
            "projected SUBSURF mesh exceeds interactive face budget",
            rejected["reasons"],
        )

    def test_modifier_budget_rejects_boolean_or_invalid_metrics(self) -> None:
        with self.assertRaises(ValueError):
            evaluate_modifier_runtime_budget(
                modifier_type="BEVEL",
                modifier_count=True,
                evaluated_faces=100,
            )
        with self.assertRaises(ValueError):
            evaluate_modifier_runtime_budget(
                modifier_type="SUBSURF",
                modifier_count=1,
                evaluated_faces=100,
                levels=3,
            )

    def test_schema_marks_validated_modeling_mutations_available(self) -> None:
        schemas = modeling_schemas()
        self.assertEqual("available", schemas["object_transform"]["status"])
        self.assertEqual("available", schemas["create_primitive"]["status"])
        self.assertEqual(
            "blender.live_create_primitive",
            schemas["create_primitive"]["action"],
        )
        self.assertEqual("available", schemas["add_modifier"]["status"])
        self.assertEqual(
            "blender.live_add_modifier",
            schemas["add_modifier"]["action"],
        )

    def test_schema_copy_cannot_mutate_global_contract(self) -> None:
        first = modeling_schemas()
        first["object_transform"]["status"] = "changed"
        self.assertEqual(
            "available",
            modeling_schemas()["object_transform"]["status"],
        )

    def test_transform_contract_rejects_nonfinite_and_boolean_values(self) -> None:
        for payload in (
            {"location": [0, float("nan"), 0]},
            {"rotation_euler": [0, True, 0]},
            {"scale": [1, float("inf"), 1]},
        ):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                normalize_transform_fields(payload)

    def test_transform_contract_enforces_positive_scale_and_dimensions(self) -> None:
        with self.assertRaises(ValueError):
            normalize_transform_fields({"scale": [1, 0, 1]})
        with self.assertRaises(ValueError):
            normalize_transform_fields({"dimensions": [1, -1, 1]})

    def test_transform_contract_normalizes_valid_values(self) -> None:
        self.assertEqual(
            {
                "location": [1.0, 2.5, -3.0],
                "rotation_euler": [0.0, 1.25, 0.0],
                "scale": [1.0, 2.0, 1.0],
            },
            normalize_transform_fields(
                {
                    "location": [1, 2.5, -3],
                    "rotation_euler": [0, 1.25, 0],
                    "scale": [1, 2, 1],
                }
            ),
        )

    def test_companion_transform_validation_accepts_only_transport_metadata(self) -> None:
        normalized = normalize_transform_request(
            {
                "id": "command-1",
                "operation": "object_transform",
                "object_name": "Hull",
                "location": [1, 2, 3],
                "scale": [1, 1.5, 1],
            },
            transport_fields={"id", "operation"},
        )
        self.assertEqual(
            {
                "object_name": "Hull",
                "location": [1.0, 2.0, 3.0],
                "scale": [1.0, 1.5, 1.0],
            },
            normalized,
        )

        with self.assertRaisesRegex(ValueError, "unsupported field"):
            normalize_transform_request(
                {
                    "id": "command-1",
                    "operation": "object_transform",
                    "project": "model",
                    "object_name": "Hull",
                    "location": [0, 0, 0],
                },
                transport_fields={"id", "operation"},
            )

    def test_companion_transform_validation_rejects_boolean_and_zero_scale(self) -> None:
        with self.assertRaises(ValueError):
            normalize_transform_request(
                {
                    "id": "command-1",
                    "operation": "object_transform",
                    "object_name": "Hull",
                    "rotation_euler": [0, True, 0],
                },
                transport_fields={"id", "operation"},
            )
        with self.assertRaises(ValueError):
            normalize_transform_request(
                {
                    "id": "command-1",
                    "operation": "object_transform",
                    "object_name": "Hull",
                    "scale": [1, 0, 1],
                },
                transport_fields={"id", "operation"},
            )


class BlenderSpatialMathTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.spatial = load_spatial_math()

    def test_aabb_overlap_accepts_touching_faces(self) -> None:
        left = {"aabb_min": [0, 0, 0], "aabb_max": [1, 1, 1]}
        right = {"aabb_min": [1, 0, 0], "aabb_max": [2, 1, 1]}
        self.assertTrue(self.spatial.aabb_overlap(left, right))

    def test_aabb_overlap_rejects_separated_boxes(self) -> None:
        left = {"aabb_min": [0, 0, 0], "aabb_max": [1, 1, 1]}
        right = {"aabb_min": [1.01, 0, 0], "aabb_max": [2, 1, 1]}
        self.assertFalse(self.spatial.aabb_overlap(left, right))

    def test_aabb_contains_respects_tolerance(self) -> None:
        outer = {"aabb_min": [0, 0, 0], "aabb_max": [1, 1, 1]}
        inner = {
            "aabb_min": [-5e-7, 0.1, 0.1],
            "aabb_max": [1.0000005, 0.9, 0.9],
        }
        self.assertTrue(self.spatial.aabb_contains(outer, inner, 1e-6))
        self.assertFalse(self.spatial.aabb_contains(outer, inner, 1e-8))

    def test_aabb_helpers_reject_missing_bounds(self) -> None:
        complete = {"aabb_min": [0, 0, 0], "aabb_max": [1, 1, 1]}
        self.assertFalse(self.spatial.aabb_overlap({}, complete))
        self.assertFalse(self.spatial.aabb_contains(complete, {}))


class BlenderQualityRuleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.rules = load_quality_rules()

    def test_quality_axis_normalizes_case_and_whitespace(self) -> None:
        self.assertEqual(("x", 0), self.rules.quality_axis(" X ", "axis"))
        self.assertEqual(("z", 2), self.rules.quality_axis("z", "axis"))

    def test_quality_axis_rejects_unknown_axis(self) -> None:
        with self.assertRaisesRegex(ValueError, "must be one of x, y, z"):
            self.rules.quality_axis("w", "axis")

    def test_quality_tolerance_uses_default(self) -> None:
        self.assertEqual(0.02, self.rules.quality_tolerance({}, default=0.02))

    def test_quality_tolerance_accepts_numeric_strings(self) -> None:
        self.assertEqual(
            0.125,
            self.rules.quality_tolerance({"tolerance": "0.125"}),
        )

    def test_quality_tolerance_rejects_invalid_or_out_of_range_values(self) -> None:
        with self.assertRaisesRegex(ValueError, "must be a number"):
            self.rules.quality_tolerance({"tolerance": "bad"})
        with self.assertRaisesRegex(ValueError, "between 0 and 1000000"):
            self.rules.quality_tolerance({"tolerance": -0.1})
        with self.assertRaisesRegex(ValueError, "between 0 and 1000000"):
            self.rules.quality_tolerance({"tolerance": 1000000.1})


class BlenderUvMathTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.uv = load_uv_math()

    def test_triangle_area(self) -> None:
        self.assertEqual(
            0.5,
            self.uv.triangle_area_2d([(0.0, 0.0), (1.0, 0.0), (0.0, 1.0)]),
        )

    def test_overlap_area_matches_identical_triangle(self) -> None:
        triangle = [(0.0, 0.0), (1.0, 0.0), (0.0, 1.0)]
        self.assertAlmostEqual(
            0.5,
            self.uv.triangle_overlap_area_2d(triangle, triangle),
            places=12,
        )

    def test_overlap_area_is_zero_for_disjoint_triangles(self) -> None:
        first = [(0.0, 0.0), (1.0, 0.0), (0.0, 1.0)]
        second = [(2.0, 2.0), (3.0, 2.0), (2.0, 3.0)]
        self.assertAlmostEqual(
            0.0,
            self.uv.triangle_overlap_area_2d(first, second),
            places=12,
        )

    def test_overlap_is_orientation_independent(self) -> None:
        subject = [(0.0, 0.0), (1.0, 0.0), (0.0, 1.0)]
        clip_clockwise = [(0.0, 1.0), (1.0, 0.0), (0.0, 0.0)]
        self.assertAlmostEqual(
            0.5,
            self.uv.triangle_overlap_area_2d(subject, clip_clockwise),
            places=12,
        )


    def test_shape_distortion_is_zero_for_uniform_uv_scale(self) -> None:
        geometry = [(0.0, 0.0, 0.0), (2.0, 0.0, 0.0), (0.0, 2.0, 0.0)]
        uv = [(0.0, 0.0), (5.0, 0.0), (0.0, 5.0)]
        self.assertAlmostEqual(
            0.0,
            self.uv.triangle_shape_distortion(geometry, uv, 1e-12),
            places=12,
        )

    def test_shape_distortion_detects_changed_edge_proportions(self) -> None:
        geometry = [(0.0, 0.0, 0.0), (2.0, 0.0, 0.0), (0.0, 2.0, 0.0)]
        uv = [(0.0, 0.0), (4.0, 0.0), (0.0, 1.0)]
        distortion = self.uv.triangle_shape_distortion(
            geometry,
            uv,
            1e-12,
        )
        self.assertIsNotNone(distortion)
        self.assertGreater(distortion, 0.1)

    def test_shape_distortion_returns_none_for_degenerate_triangle(self) -> None:
        geometry = [(0.0, 0.0, 0.0)] * 3
        uv = [(0.0, 0.0), (1.0, 0.0), (0.0, 1.0)]
        self.assertIsNone(
            self.uv.triangle_shape_distortion(geometry, uv, 1e-12)
        )


if __name__ == "__main__":
    unittest.main()
