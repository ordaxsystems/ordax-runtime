import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ordax_dev_agent.actions import ActionRegistry
from ordax_dev_agent.config import AgentConfig
from ordax_dev_agent.models import ActionResult


class GameAssetUnitySemanticActionsTests(unittest.TestCase):
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
            "modelImporterPresent": True,
            "modelAnimationType": "Human",
            "modelImportAnimation": True,
            "modelMeshCount": 2,
            "modelAnimationClipCount": 3,
            "modelBoneCount": 64,
            "modelBlendShapeCount": 8,
            "modelLodGroupCount": 1,
        }
        data.update(updates)
        return data

    def advanced(self, **updates):
        data = {
            "protocol": "ordax-game-assets-v1",
            "assetPath": "Assets/OrdaX/Generated/hero.fbx",
            "animationType": "Human",
            "avatarSetup": "CreateFromThisModel",
            "avatarCount": 1,
            "validAvatarCount": 1,
            "humanAvatarCount": 1,
            "humanoidMappedBoneCount": 55,
            "animationClipCount": 3,
            "rootCurveClipCount": 2,
            "motionCurveClipCount": 2,
            "humanMotionClipCount": 3,
            "genericRootTransformClipCount": 0,
            "animatorCount": 1,
            "animatorWithAvatarCount": 1,
            "humanAnimatorCount": 1,
            "meshRendererCount": 0,
            "skinnedMeshRendererCount": 2,
            "colliderCount": 1,
            "lodGroupCount": 1,
            "lodLevelCount": 3,
            "lodRendererCount": 3,
            "lodEmptyLevelCount": 0,
            "lodLevels": [],
        }
        data.update(updates)
        return data

    def model_result(self, evidence=None):
        return ActionResult(
            True,
            "Unity model import audit passed",
            {
                "asset_path": "Assets/OrdaX/Generated/hero.fbx",
                "engine": "unity",
                "audit": evidence or self.evidence(),
            },
        )

    def test_registry_exposes_unity_semantic_audit(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            registry = ActionRegistry(self.make_config(Path(raw)))
            status = registry.execute("agent.status", {})
            self.assertTrue(status.ok)
            self.assertIn("game_assets.unity_semantic_audit", status.data["actions"])

    def test_humanoid_character_requirements_pass(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            registry = ActionRegistry(self.make_config(Path(raw)))
            with patch.object(
                registry,
                "game_assets_unity_model_audit",
                return_value=self.model_result(),
            ):
                result = registry.execute(
                    "game_assets.unity_semantic_audit",
                    {
                        "project": "game",
                        "asset_path": "Assets/OrdaX/Generated/hero.fbx",
                        "expected_animation_type": "humanoid",
                        "require_animation_import": True,
                        "min_meshes": 1,
                        "min_bones": 50,
                        "min_animation_clips": 1,
                        "min_blend_shapes": 1,
                        "min_lod_groups": 1,
                        "max_lod_groups": 1,
                    },
                )

            self.assertTrue(result.ok, result.summary)
            self.assertTrue(result.data["engine_loaded"])
            self.assertTrue(result.data["semantic_requirements_passed"])
            self.assertEqual([], result.data["failures"])
            self.assertIn(
                "advanced_avatar_root_motion_lod_telemetry_not_requested",
                result.data["limitations"],
            )

    def test_advanced_character_requirements_pass_with_real_unity_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            registry = ActionRegistry(self.make_config(Path(raw)))
            with patch.object(
                registry,
                "game_assets_unity_model_audit",
                return_value=self.model_result(),
            ), patch.object(
                registry,
                "_advanced_unity_audit",
                return_value=ActionResult(True, "advanced", self.advanced()),
            ) as advanced:
                result = registry.execute(
                    "game_assets.unity_semantic_audit",
                    {
                        "project": "game",
                        "asset_path": "Assets/OrdaX/Generated/hero.fbx",
                        "require_humanoid_avatar": True,
                        "require_root_motion": True,
                        "require_human_motion": True,
                        "require_skinned_renderer": True,
                        "require_all_lod_levels_have_renderers": True,
                        "min_humanoid_mapped_bones": 50,
                        "min_root_motion_clips": 1,
                        "min_human_motion_clips": 1,
                        "min_lod_levels": 3,
                        "min_lod_renderers": 3,
                        "min_skinned_renderers": 1,
                    },
                )

            self.assertTrue(result.ok, result.summary)
            self.assertTrue(result.data["semantic_requirements_passed"])
            self.assertEqual(55, result.data["advanced_audit"]["humanoidMappedBoneCount"])
            self.assertEqual(2, result.data["advanced_audit"]["motionCurveClipCount"])
            self.assertNotIn(
                "advanced_avatar_root_motion_lod_telemetry_not_requested",
                result.data["limitations"],
            )
            advanced.assert_called_once()

    def test_advanced_semantics_fail_without_avatar_root_motion_or_complete_lods(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            registry = ActionRegistry(self.make_config(Path(raw)))
            broken = self.advanced(
                avatarCount=0,
                validAvatarCount=0,
                humanAvatarCount=0,
                animatorWithAvatarCount=0,
                humanAnimatorCount=0,
                humanoidMappedBoneCount=0,
                motionCurveClipCount=0,
                humanMotionClipCount=0,
                skinnedMeshRendererCount=0,
                lodEmptyLevelCount=1,
            )
            with patch.object(
                registry,
                "game_assets_unity_model_audit",
                return_value=self.model_result(),
            ), patch.object(
                registry,
                "_advanced_unity_audit",
                return_value=ActionResult(True, "advanced", broken),
            ):
                result = registry.execute(
                    "game_assets.unity_semantic_audit",
                    {
                        "project": "game",
                        "asset_path": "Assets/OrdaX/Generated/hero.fbx",
                        "require_avatar": True,
                        "require_valid_avatar": True,
                        "require_humanoid_avatar": True,
                        "require_root_motion": True,
                        "require_human_motion": True,
                        "require_skinned_renderer": True,
                        "require_all_lod_levels_have_renderers": True,
                        "min_humanoid_mapped_bones": 1,
                    },
                )

            self.assertFalse(result.ok)
            self.assertTrue(result.data["engine_loaded"])
            self.assertFalse(result.data["semantic_requirements_passed"])
            self.assertGreaterEqual(len(result.data["failures"]), 8)

    def test_loaded_asset_can_fail_semantic_requirements(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            registry = ActionRegistry(self.make_config(Path(raw)))
            with patch.object(
                registry,
                "game_assets_unity_model_audit",
                return_value=self.model_result(
                    self.evidence(
                        modelAnimationType="Generic",
                        modelAnimationClipCount=0,
                        modelLodGroupCount=0,
                    )
                ),
            ):
                result = registry.execute(
                    "game_assets.unity_semantic_audit",
                    {
                        "project": "game",
                        "asset_path": "Assets/OrdaX/Generated/hero.fbx",
                        "expected_animation_type": "human",
                        "min_animation_clips": 1,
                        "min_lod_groups": 1,
                    },
                )

            self.assertFalse(result.ok)
            self.assertTrue(result.data["engine_loaded"])
            self.assertFalse(result.data["semantic_requirements_passed"])
            self.assertEqual(3, len(result.data["failures"]))

    def test_old_companion_is_retryable_not_false_success(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            registry = ActionRegistry(self.make_config(Path(raw)))
            with patch.object(
                registry,
                "game_assets_unity_model_audit",
                return_value=self.model_result({"modelImporterPresent": True}),
            ):
                result = registry.execute(
                    "game_assets.unity_semantic_audit",
                    {"project": "game", "asset_path": "Assets/OrdaX/Generated/hero.fbx"},
                )

            self.assertFalse(result.ok)
            self.assertTrue(result.data["retryable"])
            self.assertIn("modelBoneCount", result.data["missing_fields"])

    def test_invalid_requirement_is_rejected_before_unity_call(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            registry = ActionRegistry(self.make_config(Path(raw)))
            with patch.object(registry, "game_assets_unity_model_audit") as audit:
                result = registry.execute(
                    "game_assets.unity_semantic_audit",
                    {
                        "project": "game",
                        "asset_path": "Assets/OrdaX/Generated/hero.fbx",
                        "min_lod_groups": 3,
                        "max_lod_groups": 1,
                    },
                )
            self.assertFalse(result.ok)
            self.assertIn("min_lod_groups cannot exceed", result.summary)
            audit.assert_not_called()

    def test_unmanaged_game_asset_companion_is_not_overwritten(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            registry = ActionRegistry(self.make_config(root))
            target = root / "project" / "Assets" / "OrdaX" / "Editor" / "OrdaXGameAssetAgent.cs"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("// user-owned file\n", encoding="utf-8")
            project = registry.projects["game"]
            with self.assertRaisesRegex(ValueError, "not OrdaX-managed"):
                registry._install_game_asset_companion(project)
            self.assertEqual("// user-owned file\n", target.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
