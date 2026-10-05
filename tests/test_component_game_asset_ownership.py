import unittest

from ordax_dev_agent.component_updates import plan_component_update


class GameAssetComponentOwnershipTests(unittest.TestCase):
    def test_generated_asset_pipeline_has_no_unknown_runtime_paths(self) -> None:
        plan = plan_component_update(
            [
                "ordax_dev_agent/game_asset_image_actions.py",
                "ordax_dev_agent/game_asset_artifact_actions.py",
                "ordax_dev_agent/game_asset_engine_export_actions.py",
                "ordax_dev_agent/game_asset_threejs_actions.py",
                "ordax_dev_agent/game_asset_web_runtime_actions.py",
                "ordax_dev_agent/game_asset_threejs_viewer_runtime_actions.py",
                "ordax_dev_agent/game_asset_godot_actions.py",
                "ordax_dev_agent/game_asset_status_actions.py",
                "ordax_dev_agent/game_asset_runtime_actions.py",
                "ordax_dev_agent/game_asset_lod_actions.py",
                "ordax_dev_agent/game_asset_unity_actions.py",
                "ordax_dev_agent/game_asset_unity_companion.py",
                "ordax_dev_agent/game_asset_unity_semantic_actions.py",
                "ordax_dev_agent/game_asset_unity_import_config_actions.py",
                "ordax_dev_agent/game_asset_unity_animation_actions.py",
                "ordax_dev_agent/game_asset_unreal_actions.py",
                "ordax_dev_agent/game_asset_unreal_semantic_actions.py",
                "ordax_dev_agent/visual_environment.py",
                "ordax_dev_agent/visual_environment_actions.py",
                "ordax_dev_agent/generated_asset_actions.py",
                "ordax_dev_agent/assets/OrdaXGameAssetAgent.cs",
                "ordax_dev_agent/assets/OrdaXGameAssetAnimationAgent.cs",
                "ordax_dev_agent/assets/blender_generated_asset_ingest.py",
                "ordax_dev_agent/assets/blender_runtime_budget.py",
                "ordax_dev_agent/assets/blender_static_lod.py",
            ]
        )
        self.assertEqual(plan["affected_components"], ["adapter-game-assets"])
        self.assertEqual(plan["unknown_paths"], [])
        self.assertTrue(plan["device_agent_restart_required"])


if __name__ == "__main__":
    unittest.main()
