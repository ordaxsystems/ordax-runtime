import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

from ordax_dev_agent.actions import ActionRegistry
from ordax_dev_agent.config import AgentConfig
from ordax_dev_agent.game_asset_actions import _meshy_request, _tripo_request


class GameAssetActionsTests(unittest.TestCase):
    def make_config(self, root: Path) -> AgentConfig:
        project = root / "project"
        project.mkdir(parents=True, exist_ok=True)
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

    def test_tripo_rig_defaults_to_mixamo_spec(self) -> None:
        request = _tripo_request(
            "animate_rig",
            {"original_model_task_id": "task-123"},
        )
        self.assertEqual("animate_rig", request["type"])
        self.assertEqual("mixamo", request["spec"])
        self.assertEqual("biped", request["rig_type"])
        self.assertEqual("glb", request["out_format"])

    def test_tripo_lowpoly_enforces_face_limit(self) -> None:
        with self.assertRaises(ValueError):
            _tripo_request(
                "highpoly_to_lowpoly",
                {"original_model_task_id": "task-123", "face_limit": 50000},
            )

    def test_meshy_preview_can_request_game_ready_pose_and_poly_budget(self) -> None:
        path, request = _meshy_request(
            "text_to_3d_preview",
            {
                "prompt": "stylized humanoid scout",
                "pose_mode": "a-pose",
                "target_polycount": 24000,
                "ai_model": "latest",
            },
        )
        self.assertEqual("/openapi/v2/text-to-3d", path)
        self.assertEqual("preview", request["mode"])
        self.assertEqual("a-pose", request["pose_mode"])
        self.assertEqual(24000, request["target_polycount"])
        self.assertEqual(["glb"], request["target_formats"])

    def test_meshy_animation_requires_exactly_one_animation_source(self) -> None:
        with self.assertRaises(ValueError):
            _meshy_request("animation", {"rig_task_id": "rig-123"})
        with self.assertRaises(ValueError):
            _meshy_request(
                "animation",
                {"rig_task_id": "rig-123", "motion_task_id": "motion-1", "action_id": 2},
            )

    def test_registry_exposes_game_asset_actions(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            registry = ActionRegistry(self.make_config(Path(raw)))
            status = registry.execute("agent.status", {})
            self.assertTrue(status.ok)
            self.assertIn("game_assets.providers", status.data["actions"])
            self.assertIn("game_assets.provider_submit", status.data["actions"])
            self.assertIn("game_assets.blender_character_preflight", status.data["actions"])
            self.assertIn("game_assets.blender_export", status.data["actions"])

    def test_provider_catalog_reports_configuration_without_secret(self) -> None:
        with tempfile.TemporaryDirectory() as raw, patch.dict(
            os.environ,
            {"TRIPO_API_KEY": "tsk_secret", "MESHY_API_KEY": "msy_secret"},
            clear=False,
        ):
            registry = ActionRegistry(self.make_config(Path(raw)))
            result = registry.execute("game_assets.providers", {"project": "game"})
            self.assertTrue(result.ok)
            self.assertTrue(result.data["providers"]["tripo"]["configured"])
            self.assertTrue(result.data["providers"]["meshy"]["configured"])
            self.assertNotIn("tsk_secret", repr(result.data))
            self.assertNotIn("msy_secret", repr(result.data))

    def test_mixamo_handoff_requires_fbx_when_already_rigged(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            config = self.make_config(root)
            source = root / "project" / "character.obj"
            source.write_text("o character", encoding="utf-8")
            registry = ActionRegistry(config)
            result = registry.execute(
                "game_assets.mixamo_handoff",
                {"project": "game", "source_path": "character.obj", "rigged": True},
            )
            self.assertFalse(result.ok)
            self.assertIn("FBX", result.summary)

    def test_export_profiles_choose_fbx_for_unreal_and_glb_for_godot(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            registry = ActionRegistry(self.make_config(Path(raw)))
            unreal = registry.execute(
                "game_assets.export_profiles", {"project": "game", "engine": "unreal"}
            )
            godot = registry.execute(
                "game_assets.export_profiles", {"project": "game", "engine": "godot"}
            )
            self.assertEqual("fbx", unreal.data["profile"]["format"])
            self.assertEqual("glb", godot.data["profile"]["format"])

    def test_tripo_submit_uses_fixed_api_endpoint(self) -> None:
        response = httpx.Response(200, json={"task_id": "task-456"})
        with tempfile.TemporaryDirectory() as raw, patch.dict(
            os.environ, {"TRIPO_API_KEY": "tsk_secret"}, clear=False
        ), patch(
            "ordax_dev_agent.game_asset_actions.httpx.post", return_value=response
        ) as post:
            registry = ActionRegistry(self.make_config(Path(raw)))
            result = registry.execute(
                "game_assets.provider_submit",
                {
                    "project": "game",
                    "provider": "tripo",
                    "operation": "animate_rig",
                    "original_model_task_id": "task-123",
                    "spec": "mixamo",
                    "out_format": "fbx",
                },
            )
            self.assertTrue(result.ok, result.summary)
            self.assertEqual("task-456", result.data["task_id"])
            args, kwargs = post.call_args
            self.assertEqual("https://api.tripo3d.ai/v2/openapi/task", args[0])
            self.assertEqual("Bearer tsk_secret", kwargs["headers"]["Authorization"])
            self.assertEqual("mixamo", kwargs["json"]["spec"])

    def test_meshy_status_route_is_operation_scoped(self) -> None:
        response = httpx.Response(200, json={"id": "rig-123", "status": "SUCCEEDED"})
        with tempfile.TemporaryDirectory() as raw, patch.dict(
            os.environ, {"MESHY_API_KEY": "msy_secret"}, clear=False
        ), patch(
            "ordax_dev_agent.game_asset_status_actions.httpx.get", return_value=response
        ) as get:
            registry = ActionRegistry(self.make_config(Path(raw)))
            result = registry.execute(
                "game_assets.provider_status",
                {
                    "project": "game",
                    "provider": "meshy",
                    "operation": "rigging",
                    "task_id": "rig-123",
                },
            )
            self.assertTrue(result.ok, result.summary)
            args, _ = get.call_args
            self.assertEqual(
                "https://api.meshy.ai/openapi/v1/rigging/rig-123",
                args[0],
            )
            self.assertFalse(result.data["signed_result_urls_returned"])


if __name__ == "__main__":
    unittest.main()
