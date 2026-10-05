import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ordax_dev_agent.actions import ActionRegistry
from ordax_dev_agent.config import AgentConfig
from ordax_dev_agent.models import ActionResult


class GameAssetUnityImportConfigActionsTests(unittest.TestCase):
    def make_config(self, root: Path) -> AgentConfig:
        project = root / "project"
        generated = project / "Assets" / "OrdaX" / "Generated"
        generated.mkdir(parents=True, exist_ok=True)
        (generated / "hero.fbx").write_bytes(b"hero")
        (generated / "source-avatar.fbx").write_bytes(b"avatar")
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

    def model_audit(self) -> ActionResult:
        return ActionResult(
            True,
            "Unity model import audit passed",
            {
                "asset_path": "Assets/OrdaX/Generated/hero.fbx",
                "audit": {
                    "modelImporterPresent": True,
                    "modelAnimationType": "Human",
                    "modelImportAnimation": True,
                },
            },
        )

    def advanced_audit(self) -> ActionResult:
        return ActionResult(
            True,
            "advanced",
            {
                "protocol": "ordax-game-assets-v1",
                "validAvatarCount": 1,
                "humanAvatarCount": 1,
                "humanoidMappedBoneCount": 55,
            },
        )

    def test_registry_exposes_character_import_configure(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            registry = ActionRegistry(self.make_config(Path(raw)))
            status = registry.execute("agent.status", {})
            self.assertTrue(status.ok)
            self.assertIn("game_assets.unity_character_import_configure", status.data["actions"])

    def test_confirmation_is_required_before_any_unity_request(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            registry = ActionRegistry(self.make_config(Path(raw)))
            with patch(
                "ordax_dev_agent.game_asset_unity_import_config_actions.request_game_asset_companion"
            ) as request:
                result = registry.execute(
                    "game_assets.unity_character_import_configure",
                    {
                        "project": "game",
                        "asset_path": "Assets/OrdaX/Generated/hero.fbx",
                        "animation_type": "human",
                    },
                )
            self.assertFalse(result.ok)
            self.assertIn("confirm=true", result.summary)
            request.assert_not_called()

    def test_humanoid_create_from_this_model_is_configured_and_verified(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            registry = ActionRegistry(self.make_config(Path(raw)))

            def fake_request(**kwargs):
                self.assertEqual("asset_character_import_configure", kwargs["action"])
                command = kwargs["payload"]
                self.assertEqual("Human", command["animationType"])
                self.assertEqual("CreateFromThisModel", command["avatarSetup"])
                self.assertTrue(command["setImportAnimation"])
                self.assertTrue(command["importAnimation"])
                self.assertTrue(command["setOptimizeGameObjects"])
                self.assertFalse(command["optimizeGameObjects"])
                return ActionResult(
                    True,
                    "configured",
                    {
                        "reimported": True,
                        "beforeAnimationType": "Generic",
                        "afterAnimationType": "Human",
                    },
                )

            with patch(
                "ordax_dev_agent.game_asset_unity_import_config_actions.request_game_asset_companion",
                side_effect=fake_request,
            ), patch.object(
                registry,
                "game_assets_unity_model_audit",
                return_value=self.model_audit(),
            ), patch.object(
                registry,
                "_advanced_unity_audit",
                return_value=self.advanced_audit(),
            ):
                result = registry.execute(
                    "game_assets.unity_character_import_configure",
                    {
                        "project": "game",
                        "asset_path": "Assets/OrdaX/Generated/hero.fbx",
                        "confirm": True,
                        "animation_type": "humanoid",
                        "avatar_setup": "create_from_this_model",
                        "import_animation": True,
                        "optimize_game_objects": False,
                    },
                )

            self.assertTrue(result.ok, result.summary)
            self.assertTrue(result.data["configuration_applied"])
            self.assertTrue(result.data["transactional_rollback_supported"])
            self.assertFalse(result.data["model_source_file_written"])
            self.assertEqual(55, result.data["advanced_audit"]["humanoidMappedBoneCount"])

    def test_copy_from_other_requires_explicit_source_avatar(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            registry = ActionRegistry(self.make_config(Path(raw)))
            with patch(
                "ordax_dev_agent.game_asset_unity_import_config_actions.request_game_asset_companion"
            ) as request:
                result = registry.execute(
                    "game_assets.unity_character_import_configure",
                    {
                        "project": "game",
                        "asset_path": "Assets/OrdaX/Generated/hero.fbx",
                        "confirm": True,
                        "animation_type": "human",
                        "avatar_setup": "copy_from_other",
                    },
                )
            self.assertFalse(result.ok)
            self.assertIn("requires source_avatar_path", result.summary)
            request.assert_not_called()

    def test_copy_from_other_sends_project_local_avatar_source(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            registry = ActionRegistry(self.make_config(Path(raw)))

            def fake_request(**kwargs):
                command = kwargs["payload"]
                self.assertEqual("CopyFromOther", command["avatarSetup"])
                self.assertEqual(
                    "Assets/OrdaX/Generated/source-avatar.fbx",
                    command["sourceAvatarPath"],
                )
                return ActionResult(True, "configured", {"reimported": True})

            with patch(
                "ordax_dev_agent.game_asset_unity_import_config_actions.request_game_asset_companion",
                side_effect=fake_request,
            ), patch.object(
                registry,
                "game_assets_unity_model_audit",
                return_value=self.model_audit(),
            ), patch.object(
                registry,
                "_advanced_unity_audit",
                return_value=self.advanced_audit(),
            ):
                result = registry.execute(
                    "game_assets.unity_character_import_configure",
                    {
                        "project": "game",
                        "asset_path": "Assets/OrdaX/Generated/hero.fbx",
                        "confirm": True,
                        "animation_type": "human",
                        "avatar_setup": "copy_from_other",
                        "source_avatar_path": "Assets/OrdaX/Generated/source-avatar.fbx",
                    },
                )
            self.assertTrue(result.ok, result.summary)

    def test_source_avatar_cannot_point_to_target_model(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            registry = ActionRegistry(self.make_config(Path(raw)))
            result = registry.execute(
                "game_assets.unity_character_import_configure",
                {
                    "project": "game",
                    "asset_path": "Assets/OrdaX/Generated/hero.fbx",
                    "confirm": True,
                    "animation_type": "human",
                    "avatar_setup": "copy_from_other",
                    "source_avatar_path": "Assets/OrdaX/Generated/hero.fbx",
                },
            )
            self.assertFalse(result.ok)
            self.assertIn("different Unity asset", result.summary)

    def test_post_reimport_audit_failure_is_not_reported_as_success(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            registry = ActionRegistry(self.make_config(Path(raw)))
            with patch(
                "ordax_dev_agent.game_asset_unity_import_config_actions.request_game_asset_companion",
                return_value=ActionResult(True, "configured", {"reimported": True}),
            ), patch.object(
                registry,
                "game_assets_unity_model_audit",
                return_value=ActionResult(False, "editor audit failed", {"retryable": True}),
            ), patch.object(
                registry,
                "_advanced_unity_audit",
                return_value=self.advanced_audit(),
            ):
                result = registry.execute(
                    "game_assets.unity_character_import_configure",
                    {
                        "project": "game",
                        "asset_path": "Assets/OrdaX/Generated/hero.fbx",
                        "confirm": True,
                        "resample_curves": False,
                    },
                )
            self.assertFalse(result.ok)
            self.assertTrue(result.data["configuration_applied"])
            self.assertTrue(result.data["retryable"])


if __name__ == "__main__":
    unittest.main()
