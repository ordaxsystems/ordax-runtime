import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image, PngImagePlugin

from ordax_dev_agent.actions import ActionRegistry
from ordax_dev_agent.config import AgentConfig
from ordax_dev_agent.models import ActionResult
from ordax_dev_agent.references import _image_pixel_digest


PNG_BYTES = b"\x89PNG\r\n\x1a\nreference-fixture"


class ReferenceContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.project = self.root / "project"
        self.project.mkdir()
        (self.project / "references").mkdir()
        self.image = self.project / "references" / "boat-front.png"
        self.image.write_bytes(PNG_BYTES)

        manifest = {
            "version": 1,
            "assets": {
                "boat": {
                    "requirements": ["Preserve the hull silhouette."],
                    "components": ["hull", "rope"],
                    "dimensions_world_m": {"x": 4.0, "y": 2.0},
                    "tolerance_percent": 5,
                    "references": [
                        {
                            "id": "front",
                            "path": "references/boat-front.png",
                            "view": "front",
                            "projection": "orthographic",
                            "sha256": hashlib.sha256(PNG_BYTES).hexdigest(),
                        }
                    ],
                }
            },
        }
        self.manifest_path = self.project / "references" / "manifest.json"
        self.manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

        self.config = AgentConfig(
            "test",
            
            
            5,
            self.root / "state",
            self.root / "agent",
            self.root / "hordax",
            self.root / "bridge",
            projects={
                "model": {
                    "path": str(self.project),
                    "apps": ["blender"],
                    "blender": {},
                }
            },
            default_project="model",
        )
        self.registry = ActionRegistry(self.config)

    def test_reference_catalog_and_brief_are_versioned(self) -> None:
        catalog = self.registry.execute("project.references", {"project": "model"})
        self.assertTrue(catalog.ok)
        self.assertEqual("boat", catalog.data["assets"][0]["asset"])
        self.assertEqual(1, catalog.data["assets"][0]["reference_count"])

        brief = self.registry.execute(
            "project.references",
            {"project": "model", "asset": "boat"},
        )
        self.assertTrue(brief.ok)
        self.assertEqual(["hull", "rope"], brief.data["brief"]["components"])
        self.assertEqual(4.0, brief.data["brief"]["dimensions_world_m"]["x"])
        self.assertRegex(brief.data["manifest_sha256"], r"^[0-9a-f]{64}$")

    def test_reference_images_require_stable_manifest_digest(self) -> None:
        first = self.registry.execute(
            "project.references",
            {"project": "model", "asset": "boat"},
        )
        digest = first.data["manifest_sha256"]
        manifest = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        manifest["assets"]["boat"]["requirements"].append("Changed")
        self.manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

        result = self.registry.execute(
            "project.reference_images",
            {
                "project": "model",
                "asset": "boat",
                "manifest_sha256": digest,
                "reference_ids": ["front"],
            },
        )
        self.assertFalse(result.ok)
        self.assertIn("manifest changed", result.summary)

    def test_reference_path_cannot_escape_registered_project(self) -> None:
        manifest = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        manifest["assets"]["boat"]["references"][0]["path"] = "../outside.png"
        manifest["assets"]["boat"]["references"][0].pop("sha256")
        self.manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        (self.root / "outside.png").write_bytes(PNG_BYTES)

        result = self.registry.execute(
            "project.reference_images",
            {
                "project": "model",
                "asset": "boat",
                "reference_ids": ["front"],
            },
        )
        self.assertFalse(result.ok)
        self.assertIn("outside registered project", result.summary)

    def test_reference_images_are_copied_to_managed_evidence(self) -> None:
        result = self.registry.execute(
            "project.reference_images",
            {
                "project": "model",
                "asset": "boat",
                "reference_ids": ["front"],
            },
        )
        self.assertTrue(result.ok)
        copied = Path(result.data["references"][0]["artifact"])
        self.assertTrue(copied.is_file())
        self.assertTrue(
            copied.is_relative_to(self.config.state_dir / "artifacts" / "model")
        )
        self.assertEqual(PNG_BYTES, copied.read_bytes())
        self.assertTrue(Path(result.data["contract_snapshot"]).is_file())
        self.assertEqual(
            {"reference-image", "reference-contract"},
            {item["kind"] for item in result.data["artifacts"]},
        )

    def test_reference_review_pairs_multiview_and_checks_physical_scale(self) -> None:
        capture = ActionResult(
            True,
            "captured",
            {
                "artifacts": [
                    {
                        "view": "front",
                        "artifact": str(self.root / "front-model.png"),
                        "sha256": "0" * 64,
                    }
                ],
                "manifest": str(self.root / "multiview.json"),
                "bounds": {
                    "dimensions": [2.0, 1.0, 0.5],
                    "min": [-1.0, -0.5, -0.25],
                    "max": [1.0, 0.5, 0.25],
                },
                "unit_system": "METRIC",
                "unit_scale_m": 2.0,
            },
        )
        (self.root / "front-model.png").write_bytes(PNG_BYTES)
        (self.root / "multiview.json").write_text("{}", encoding="utf-8")

        with patch.object(
            self.registry,
            "blender_live_multiview_capture",
            return_value=capture,
        ) as multiview:
            result = self.registry.execute(
                "blender.reference_review",
                {
                    "project": "model",
                    "asset": "boat",
                    "reference_ids": ["front"],
                    "object_names": ["Hull", "Rope"],
                },
            )

        self.assertTrue(result.ok)
        multiview.assert_called_once()
        self.assertEqual(["front"], multiview.call_args.args[0]["views"])
        self.assertEqual(["Hull", "Rope"], multiview.call_args.args[0]["object_names"])
        self.assertFalse(result.data["pairs"][0]["camera_correspondence_proven"])
        self.assertFalse(result.data["pairs"][0]["pixel_alignment_verified"])

        checks = {item["axis"]: item for item in result.data["dimension_checks"]}
        self.assertEqual(4.0, checks["x"]["actual_m"])
        self.assertEqual(2.0, checks["y"]["actual_m"])
        self.assertTrue(checks["x"]["within_tolerance"])
        self.assertTrue(checks["y"]["within_tolerance"])
        self.assertIn("no arbitrary similarity score", result.data["visual_assessment"])

    def test_reference_review_does_not_guess_physical_scale(self) -> None:
        capture = ActionResult(
            True,
            "captured",
            {
                "artifacts": [],
                "bounds": {"dimensions": [4.0, 2.0, 1.0]},
                "unit_system": "NONE",
                "unit_scale_m": None,
            },
        )
        with patch.object(
            self.registry,
            "blender_live_multiview_capture",
            return_value=capture,
        ):
            result = self.registry.execute(
                "blender.reference_review",
                {
                    "project": "model",
                    "asset": "boat",
                    "reference_ids": ["front"],
                    "object_names": ["Hull"],
                },
            )

        self.assertTrue(result.ok)
        self.assertTrue(
            all(item["status"] == "unknown" for item in result.data["dimension_checks"])
        )


    def test_reference_generation_rolls_back_failed_declared_dimension(self) -> None:
        generation = ActionResult(
            True,
            "generated",
            {"checkpoint_id": "checkpoint-1", "artifacts": []},
        )
        review = ActionResult(
            True,
            "reviewed",
            {
                "dimension_checks": [
                    {
                        "axis": "x",
                        "target_m": 4.0,
                        "actual_m": 5.0,
                        "within_tolerance": False,
                    }
                ],
                "artifacts": [],
            },
        )
        rollback = ActionResult(True, "restored", {"checkpoint_id": "checkpoint-1"})

        with (
            patch.object(
                self.registry,
                "blender_live_generation_pass",
                return_value=generation,
            ) as generation_pass,
            patch.object(
                self.registry,
                "_blender_reference_review_from_materialized",
                return_value=review,
            ),
            patch.object(
                self.registry,
                "blender_live_checkpoint_restore",
                return_value=rollback,
            ) as restore,
            patch.object(
                self.registry,
                "blender_live_save",
            ) as save,
        ):
            result = self.registry.execute(
                "blender.reference_generation_pass",
                {
                    "project": "model",
                    "asset": "boat",
                    "reference_ids": ["front"],
                    "object_names": ["Hull"],
                    "script_path": "automation/blender/boat.py",
                    "save_target_path": "boat.blend",
                },
            )

        self.assertFalse(result.ok)
        self.assertIn("outside tolerance", result.summary)
        self.assertEqual("checkpoint-1", result.data["checkpoint_id"])
        self.assertEqual("x", result.data["failed_dimensions"][0]["axis"])
        self.assertNotIn(
            "save_target_path",
            generation_pass.call_args.args[0],
        )
        restore.assert_called_once()
        self.assertEqual(
            "checkpoint-1",
            restore.call_args.args[0]["checkpoint_id"],
        )
        self.assertTrue(restore.call_args.args[0]["discard_unsaved"])
        save.assert_not_called()

    def test_reference_generation_defers_save_until_visual_decision(self) -> None:
        generation = ActionResult(
            True,
            "generated",
            {"checkpoint_id": "checkpoint-2", "artifacts": []},
        )
        review_dir = (
            self.config.state_dir
            / "artifacts"
            / "model"
            / "reference-review-test"
        )
        review_dir.mkdir(parents=True)
        review_path = review_dir / "reference-review.json"
        review_path.write_text('{"review": true}', encoding="utf-8")
        review = ActionResult(
            True,
            "reviewed",
            {
                "dimension_checks": [
                    {
                        "axis": "x",
                        "target_m": 4.0,
                        "actual_m": 4.0,
                        "within_tolerance": True,
                    }
                ],
                "review_path": str(review_path),
                "artifacts": [],
            },
        )
        fingerprints = ActionResult(
            True,
            "fingerprinted",
            {
                "fingerprints": [
                    {
                        "selector_key": "name:Hull",
                        "object_name": "Hull",
                        "transform_sha256": "a" * 64,
                        "geometry_sha256": "b" * 64,
                        "combined_sha256": "c" * 64,
                    }
                ]
            },
        )

        with (
            patch.object(
                self.registry,
                "blender_live_generation_pass",
                return_value=generation,
            ) as generation_pass,
            patch.object(
                self.registry,
                "_blender_reference_review_from_materialized",
                return_value=review,
            ),
            patch.object(
                self.registry,
                "_reference_candidate_fingerprints",
                return_value=fingerprints,
            ),
            patch.object(
                self.registry,
                "blender_live_checkpoint_restore",
            ) as restore,
            patch.object(
                self.registry,
                "blender_live_save",
            ) as save,
        ):
            result = self.registry.execute(
                "blender.reference_generation_pass",
                {
                    "project": "model",
                    "asset": "boat",
                    "reference_ids": ["front"],
                    "object_names": ["Hull"],
                    "script_path": "automation/blender/boat.py",
                    "save_target_path": "boat.blend",
                },
            )

        self.assertTrue(result.ok)
        self.assertTrue(result.data["visual_review_pending"])
        self.assertTrue(result.data["decision_required"])
        self.assertIn("visual decision required", result.summary)
        self.assertNotIn(
            "save_target_path",
            generation_pass.call_args.args[0],
        )
        save.assert_not_called()
        restore.assert_not_called()

        pass_path = Path(result.data["reference_pass_path"])
        self.assertTrue(pass_path.is_file())
        state = json.loads(pass_path.read_text(encoding="utf-8"))
        self.assertEqual("pending_visual_review", state["status"])
        self.assertEqual("checkpoint-2", state["checkpoint_id"])
        self.assertEqual(
            str((self.project / "boat.blend").resolve()),
            state["proposed_save_target_path"],
        )
        self.assertEqual(
            "c" * 64,
            state["candidate_fingerprints"][0]["combined_sha256"],
        )

    def _write_reference_pass_fixture(
        self,
        *,
        combined_sha256: str = "c" * 64,
        save_target: str | None = None,
    ) -> Path:
        evidence = self.config.state_dir / "artifacts" / "model" / "decision-test"
        evidence.mkdir(parents=True, exist_ok=True)
        review = evidence / "reference-review.json"
        review.write_text('{"review": true}', encoding="utf-8")
        state = {
            "version": 1,
            "status": "pending_visual_review",
            "project": "model",
            "asset": "boat",
            "manifest_sha256": "d" * 64,
            "checkpoint_id": "checkpoint-decision",
            "review_path": str(review),
            "review_sha256": hashlib.sha256(review.read_bytes()).hexdigest(),
            "object_names": ["Hull"],
            "candidate_fingerprints": [
                {
                    "selector_key": "name:Hull",
                    "object_name": "Hull",
                    "transform_sha256": "a" * 64,
                    "geometry_sha256": "b" * 64,
                    "combined_sha256": combined_sha256,
                }
            ],
            "proposed_save_target_path": save_target,
            "script_path": "automation/blender/boat.py",
        }
        path = evidence / "reference-pass.json"
        path.write_text(json.dumps(state), encoding="utf-8")
        return path

    def test_reference_decision_accepts_unchanged_candidate_then_saves(self) -> None:
        target = str((self.project / "boat.blend").resolve())
        pass_path = self._write_reference_pass_fixture(save_target=target)
        current = ActionResult(
            True,
            "fingerprinted",
            {
                "fingerprints": [
                    {
                        "selector_key": "name:Hull",
                        "object_name": "Hull",
                        "combined_sha256": "c" * 64,
                    }
                ]
            },
        )
        saved = ActionResult(True, "saved", {"target_path": target})

        with (
            patch.object(
                self.registry,
                "_reference_candidate_fingerprints",
                return_value=current,
            ),
            patch.object(
                self.registry,
                "_reference_candidate_visual_hashes",
                return_value=ActionResult(
                    True,
                    "visuals unchanged",
                    {"verified_views": ["front"]},
                ),
            ),
            patch.object(
                self.registry,
                "blender_live_save",
                return_value=saved,
            ) as save,
            patch.object(
                self.registry,
                "blender_live_checkpoint_restore",
            ) as restore,
        ):
            result = self.registry.execute(
                "blender.reference_decision",
                {
                    "project": "model",
                    "reference_pass_path": str(pass_path),
                    "decision": "accept",
                    "assessment": "Hull silhouette and rope route match the reviewed references.",
                },
            )

        self.assertTrue(result.ok)
        self.assertIn("accepted and saved", result.summary)
        save.assert_called_once()
        restore.assert_not_called()
        state = json.loads(pass_path.read_text(encoding="utf-8"))
        self.assertEqual("accepted", state["status"])
        self.assertEqual(target, state["saved_target_path"])

    def test_reference_decision_rejects_and_restores_checkpoint(self) -> None:
        pass_path = self._write_reference_pass_fixture(
            save_target=str((self.project / "boat.blend").resolve())
        )
        restored = ActionResult(
            True,
            "restored",
            {"checkpoint_id": "checkpoint-decision"},
        )

        with (
            patch.object(
                self.registry,
                "blender_live_checkpoint_restore",
                return_value=restored,
            ) as restore,
            patch.object(
                self.registry,
                "_reference_candidate_fingerprints",
            ) as fingerprints,
            patch.object(
                self.registry,
                "blender_live_save",
            ) as save,
        ):
            result = self.registry.execute(
                "blender.reference_decision",
                {
                    "project": "model",
                    "reference_pass_path": str(pass_path),
                    "decision": "reject",
                    "assessment": "Rope no longer follows the outer gunwale.",
                },
            )

        self.assertTrue(result.ok)
        restore.assert_called_once()
        fingerprints.assert_not_called()
        save.assert_not_called()
        state = json.loads(pass_path.read_text(encoding="utf-8"))
        self.assertEqual("rejected", state["status"])

    def _write_real_png(
        self,
        path: Path,
        *,
        rgba: tuple[int, int, int, int],
        note: str,
        compress_level: int = 6,
    ) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        image = Image.new("RGBA", (4, 3), rgba)
        metadata = PngImagePlugin.PngInfo()
        metadata.add_text("ordax_test_note", note)
        image.save(
            path,
            format="PNG",
            pnginfo=metadata,
            compress_level=compress_level,
        )

    def test_pixel_digest_ignores_png_metadata_and_compression(self) -> None:
        root = self.config.state_dir / "artifacts" / "model" / "pixel-digest"
        first = root / "first.png"
        second = root / "second.png"
        self._write_real_png(
            first,
            rgba=(42, 84, 126, 255),
            note="reviewed",
            compress_level=0,
        )
        self._write_real_png(
            second,
            rgba=(42, 84, 126, 255),
            note="recaptured",
            compress_level=9,
        )

        self.assertNotEqual(
            hashlib.sha256(first.read_bytes()).hexdigest(),
            hashlib.sha256(second.read_bytes()).hexdigest(),
        )
        self.assertEqual(
            _image_pixel_digest(first)["sha256"],
            _image_pixel_digest(second)["sha256"],
        )

    def test_visual_hash_guard_recaptures_same_pixels_and_parameters(self) -> None:
        root = self.config.state_dir / "artifacts" / "model" / "pixel-guard"
        reviewed_path = root / "reviewed-front.png"
        fresh_path = root / "fresh-front.png"
        self._write_real_png(
            reviewed_path,
            rgba=(20, 40, 60, 255),
            note="reviewed-container",
            compress_level=0,
        )
        self._write_real_png(
            fresh_path,
            rgba=(20, 40, 60, 255),
            note="fresh-container",
            compress_level=9,
        )
        reviewed_file_sha = hashlib.sha256(reviewed_path.read_bytes()).hexdigest()
        fresh_file_sha = hashlib.sha256(fresh_path.read_bytes()).hexdigest()
        self.assertNotEqual(reviewed_file_sha, fresh_file_sha)

        reviewed = {
            "capture": {
                "artifacts": [
                    {
                        "view": "front",
                        "artifact": str(reviewed_path),
                        "sha256": reviewed_file_sha,
                    }
                ],
                "resolution": [640, 480],
                "margin": 1.25,
                "mode": "material",
            }
        }
        state = {
            "project": "model",
            "object_names": ["Hull"],
        }
        recaptured = ActionResult(
            True,
            "captured",
            {
                "artifacts": [
                    {
                        "view": "front",
                        "artifact": str(fresh_path),
                        "sha256": fresh_file_sha,
                    }
                ]
            },
        )

        with patch.object(
            self.registry,
            "blender_live_multiview_capture",
            return_value=recaptured,
        ) as capture:
            result = self.registry._reference_candidate_visual_hashes(
                {"project": "model"},
                state,
                reviewed,
            )

        self.assertTrue(result.ok)
        request = capture.call_args.args[0]
        self.assertEqual(["front"], request["views"])
        self.assertEqual(["Hull"], request["object_names"])
        self.assertEqual(640, request["width"])
        self.assertEqual(480, request["height"])
        self.assertEqual(1.25, request["margin"])
        self.assertEqual("material", request["mode"])
        self.assertEqual(["front"], result.data["verified_views"])
        self.assertEqual(
            _image_pixel_digest(reviewed_path)["sha256"],
            result.data["pixel_sha256"]["front"],
        )
        self.assertEqual(
            reviewed_file_sha,
            result.data["reviewed_file_sha256"]["front"],
        )
        self.assertEqual(
            fresh_file_sha,
            result.data["fresh_file_sha256"]["front"],
        )

    def test_visual_hash_guard_rejects_real_pixel_change(self) -> None:
        root = self.config.state_dir / "artifacts" / "model" / "pixel-change"
        reviewed_path = root / "reviewed-front.png"
        fresh_path = root / "fresh-front.png"
        self._write_real_png(
            reviewed_path,
            rgba=(20, 40, 60, 255),
            note="reviewed",
        )
        self._write_real_png(
            fresh_path,
            rgba=(21, 40, 60, 255),
            note="fresh",
        )
        reviewed_file_sha = hashlib.sha256(reviewed_path.read_bytes()).hexdigest()
        fresh_file_sha = hashlib.sha256(fresh_path.read_bytes()).hexdigest()

        reviewed = {
            "capture": {
                "artifacts": [
                    {
                        "view": "front",
                        "artifact": str(reviewed_path),
                        "sha256": reviewed_file_sha,
                    }
                ],
                "resolution": [320, 240],
                "margin": 1.15,
                "mode": "material",
            }
        }
        state = {"project": "model", "object_names": ["Hull"]}
        recaptured = ActionResult(
            True,
            "captured",
            {
                "artifacts": [
                    {
                        "view": "front",
                        "artifact": str(fresh_path),
                        "sha256": fresh_file_sha,
                    }
                ]
            },
        )

        with patch.object(
            self.registry,
            "blender_live_multiview_capture",
            return_value=recaptured,
        ):
            result = self.registry._reference_candidate_visual_hashes(
                {"project": "model"},
                state,
                reviewed,
            )

        self.assertFalse(result.ok)
        self.assertIn("pixels changed", result.summary)
        self.assertEqual(["front"], result.data["changed_views"])
        self.assertNotEqual(
            result.data["expected_pixel_sha256"]["front"],
            result.data["current_pixel_sha256"]["front"],
        )

    def test_reference_decision_blocks_accept_after_pixels_changed(self) -> None:
        target = str((self.project / "boat.blend").resolve())
        pass_path = self._write_reference_pass_fixture(save_target=target)
        current = ActionResult(
            True,
            "fingerprinted",
            {
                "fingerprints": [
                    {
                        "selector_key": "name:Hull",
                        "object_name": "Hull",
                        "combined_sha256": "c" * 64,
                    }
                ]
            },
        )
        visual = ActionResult(
            False,
            "Rendered candidate changed after visual evidence was reviewed",
            {
                "changed_views": ["front"],
                "expected_sha256": {"front": "1" * 64},
                "current_sha256": {"front": "2" * 64},
            },
        )

        with (
            patch.object(
                self.registry,
                "_reference_candidate_fingerprints",
                return_value=current,
            ),
            patch.object(
                self.registry,
                "_reference_candidate_visual_hashes",
                return_value=visual,
            ),
            patch.object(
                self.registry,
                "blender_live_save",
            ) as save,
        ):
            result = self.registry.execute(
                "blender.reference_decision",
                {
                    "project": "model",
                    "reference_pass_path": str(pass_path),
                    "decision": "accept",
                    "assessment": "The earlier visual evidence looked acceptable.",
                },
            )

        self.assertFalse(result.ok)
        self.assertIn("visual state changed after review", result.summary)
        self.assertEqual(
            ["front"],
            result.data["visual_consistency"]["changed_views"],
        )
        save.assert_not_called()
        state = json.loads(pass_path.read_text(encoding="utf-8"))
        self.assertEqual("pending_visual_review", state["status"])

    def test_reference_decision_blocks_accept_after_scene_changed(self) -> None:
        target = str((self.project / "boat.blend").resolve())
        pass_path = self._write_reference_pass_fixture(
            combined_sha256="c" * 64,
            save_target=target,
        )
        current = ActionResult(
            True,
            "fingerprinted",
            {
                "fingerprints": [
                    {
                        "selector_key": "name:Hull",
                        "object_name": "Hull",
                        "combined_sha256": "e" * 64,
                    }
                ]
            },
        )

        with (
            patch.object(
                self.registry,
                "_reference_candidate_fingerprints",
                return_value=current,
            ),
            patch.object(
                self.registry,
                "blender_live_save",
            ) as save,
        ):
            result = self.registry.execute(
                "blender.reference_decision",
                {
                    "project": "model",
                    "reference_pass_path": str(pass_path),
                    "decision": "accept",
                    "assessment": "The captured candidate looked acceptable.",
                },
            )

        self.assertFalse(result.ok)
        self.assertIn("changed after visual evidence", result.summary)
        self.assertEqual(["name:Hull"], result.data["changed_selectors"])
        save.assert_not_called()
        state = json.loads(pass_path.read_text(encoding="utf-8"))
        self.assertEqual("pending_visual_review", state["status"])

    def test_reference_generation_can_require_declared_physical_scale(self) -> None:
        generation = ActionResult(
            True,
            "generated",
            {"checkpoint_id": "checkpoint-3", "artifacts": []},
        )
        review = ActionResult(
            True,
            "reviewed",
            {
                "dimension_checks": [
                    {
                        "axis": "x",
                        "target_m": 4.0,
                        "status": "unknown",
                        "reason": "Blender scene does not declare a physical unit scale",
                    }
                ],
                "artifacts": [],
            },
        )
        rollback = ActionResult(True, "restored", {})

        with (
            patch.object(
                self.registry,
                "blender_live_generation_pass",
                return_value=generation,
            ),
            patch.object(
                self.registry,
                "_blender_reference_review_from_materialized",
                return_value=review,
            ),
            patch.object(
                self.registry,
                "blender_live_checkpoint_restore",
                return_value=rollback,
            ) as restore,
        ):
            result = self.registry.execute(
                "blender.reference_generation_pass",
                {
                    "project": "model",
                    "asset": "boat",
                    "reference_ids": ["front"],
                    "object_names": ["Hull"],
                    "script_path": "automation/blender/boat.py",
                    "require_reference_physical_scale": True,
                },
            )

        self.assertFalse(result.ok)
        self.assertIn("physical scale is required", result.summary)
        restore.assert_called_once()

    def test_reference_generation_preflight_failure_never_mutates_blender(self) -> None:
        self.image.write_bytes(b"changed-after-contract")

        with patch.object(
            self.registry,
            "blender_live_generation_pass",
        ) as generation_pass:
            result = self.registry.execute(
                "blender.reference_generation_pass",
                {
                    "project": "model",
                    "asset": "boat",
                    "reference_ids": ["front"],
                    "object_names": ["Hull"],
                    "script_path": "automation/blender/boat.py",
                },
            )

        self.assertFalse(result.ok)
        self.assertIn("preflight failed", result.summary)
        self.assertEqual("reference_preflight", result.data["phase"])
        generation_pass.assert_not_called()


if __name__ == "__main__":
    unittest.main()
