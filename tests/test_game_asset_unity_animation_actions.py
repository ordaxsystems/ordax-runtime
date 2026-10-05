import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ordax_dev_agent.actions import ActionRegistry
from ordax_dev_agent.config import AgentConfig
from ordax_dev_agent.models import ActionResult


class GameAssetUnityAnimationActionsTests(unittest.TestCase):
    def make_config(self, root: Path) -> AgentConfig:
        project = root / "project"
        asset = project / "Assets" / "OrdaX" / "Generated" / "hero.fbx"
        asset.parent.mkdir(parents=True, exist_ok=True)
        asset.write_bytes(b"fake-fbx")
        return AgentConfig(
            agent_name="test-agent",
            poll_seconds=1.0,
            state_dir=root / "state",
            agent_repo_path=root / "agent",
            hordax_path=project,
            bridge_path=root / "bridge",
            projects={
                "game": {
                    "path": str(project),
                    "apps": ["blender", "unity"],
                    "allowed_branches": [],
                }
            },
            default_project="game",
        )

    def evidence(self, **updates):
        data = {
            "protocol": "ordax-game-assets-animation-v1",
            "assetPath": "Assets/OrdaX/Generated/hero.fbx",
            "clipName": "Walk",
            "availableClipNames": ["Walk"],
            "clipLengthSeconds": 1.2,
            "normalizedSampleTime": 0.5,
            "sampleTimeSeconds": 0.6,
            "humanMotion": True,
            "hasRootCurves": True,
            "hasMotionCurves": True,
            "hasGenericRootTransform": False,
            "transformCount": 65,
            "changedTransformCount": 42,
            "skinnedRendererCount": 1,
            "blendShapeChannelCount": 8,
            "changedBlendShapeCount": 2,
            "maxPositionDelta": 0.2,
            "maxRotationAngleDegrees": 34.0,
            "maxScaleDelta": 0.0,
            "maxBlendShapeWeightDelta": 15.0,
            "rootPositionDelta": 0.1,
            "rootRotationAngleDegrees": 5.0,
            "poseChanged": True,
            "previewSceneUsed": True,
            "animationModeUsed": True,
        }
        data.update(updates)
        return data

    def test_registry_exposes_animation_sample_audit(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            registry = ActionRegistry(self.make_config(Path(raw)))
            status = registry.execute("agent.status", {})
            self.assertTrue(status.ok)
            self.assertIn("game_assets.unity_animation_sample_audit", status.data["actions"])

    def test_pose_sample_passes_with_transform_and_blend_shape_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            registry = ActionRegistry(self.make_config(Path(raw)))

            def fake_request(**kwargs):
                self.assertEqual("asset_animation_sample_audit", kwargs["action"])
                self.assertEqual("Assets/OrdaX/Generated/hero.fbx", kwargs["payload"]["assetPath"])
                self.assertEqual("Walk", kwargs["payload"]["clipName"])
                self.assertEqual(0.75, kwargs["payload"]["normalizedSampleTime"])
                return ActionResult(True, "sampled", self.evidence(normalizedSampleTime=0.75))

            with patch(
                "ordax_dev_agent.game_asset_unity_animation_actions.request_game_asset_animation_companion",
                side_effect=fake_request,
            ):
                result = registry.execute(
                    "game_assets.unity_animation_sample_audit",
                    {
                        "project": "game",
                        "asset_path": "Assets/OrdaX/Generated/hero.fbx",
                        "clip_name": "Walk",
                        "normalized_time": 0.75,
                        "require_human_motion": True,
                        "require_root_or_motion_curves": True,
                        "min_changed_transforms": 20,
                        "min_changed_blend_shapes": 1,
                    },
                )

            self.assertTrue(result.ok, result.summary)
            self.assertTrue(result.data["editor_sample_validated"])
            self.assertTrue(result.data["pose_change_validated"])
            self.assertTrue(result.data["scene_isolated"])
            self.assertFalse(result.data["play_mode_validated"])
            self.assertFalse(result.data["root_motion_application_validated"])

    def test_default_gate_fails_when_clip_does_not_change_pose(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            registry = ActionRegistry(self.make_config(Path(raw)))
            evidence = self.evidence(
                poseChanged=False,
                changedTransformCount=0,
                changedBlendShapeCount=0,
                maxPositionDelta=0.0,
                maxRotationAngleDegrees=0.0,
                maxBlendShapeWeightDelta=0.0,
            )
            with patch(
                "ordax_dev_agent.game_asset_unity_animation_actions.request_game_asset_animation_companion",
                return_value=ActionResult(True, "sampled", evidence),
            ):
                result = registry.execute(
                    "game_assets.unity_animation_sample_audit",
                    {
                        "project": "game",
                        "asset_path": "Assets/OrdaX/Generated/hero.fbx",
                        "clip_name": "Walk",
                    },
                )
            self.assertFalse(result.ok)
            self.assertTrue(result.data["editor_sample_validated"])
            self.assertFalse(result.data["semantic_requirements_passed"])
            self.assertIn("did not change", result.data["failures"][0])

    def test_root_curve_requirement_is_distinct_from_pose_change(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            registry = ActionRegistry(self.make_config(Path(raw)))
            evidence = self.evidence(hasRootCurves=False, hasMotionCurves=False)
            with patch(
                "ordax_dev_agent.game_asset_unity_animation_actions.request_game_asset_animation_companion",
                return_value=ActionResult(True, "sampled", evidence),
            ):
                result = registry.execute(
                    "game_assets.unity_animation_sample_audit",
                    {
                        "project": "game",
                        "asset_path": "Assets/OrdaX/Generated/hero.fbx",
                        "clip_name": "Walk",
                        "require_root_or_motion_curves": True,
                    },
                )
            self.assertFalse(result.ok)
            self.assertTrue(result.data["pose_change_validated"])
            self.assertIn("neither root nor motion curves", result.data["failures"][0])

    def test_invalid_normalized_time_is_rejected_before_companion(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            registry = ActionRegistry(self.make_config(Path(raw)))
            with patch(
                "ordax_dev_agent.game_asset_unity_animation_actions.request_game_asset_animation_companion"
            ) as request:
                result = registry.execute(
                    "game_assets.unity_animation_sample_audit",
                    {
                        "project": "game",
                        "asset_path": "Assets/OrdaX/Generated/hero.fbx",
                        "normalized_time": 0,
                    },
                )
            self.assertFalse(result.ok)
            self.assertIn("normalized_time", result.summary)
            request.assert_not_called()

    def test_companion_failure_is_propagated_without_false_validation(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            registry = ActionRegistry(self.make_config(Path(raw)))
            with patch(
                "ordax_dev_agent.game_asset_unity_animation_actions.request_game_asset_animation_companion",
                return_value=ActionResult(False, "clipName is required", {"retryable": False}),
            ):
                result = registry.execute(
                    "game_assets.unity_animation_sample_audit",
                    {
                        "project": "game",
                        "asset_path": "Assets/OrdaX/Generated/hero.fbx",
                    },
                )
            self.assertFalse(result.ok)
            self.assertEqual("clipName is required", result.summary)


if __name__ == "__main__":
    unittest.main()
