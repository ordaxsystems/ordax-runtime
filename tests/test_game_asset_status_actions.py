import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

from ordax_dev_agent.actions import ActionRegistry
from ordax_dev_agent.config import AgentConfig
from ordax_dev_agent.game_asset_status_actions import GameAssetStatusActions


class GameAssetStatusActionsTests(unittest.TestCase):
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

    def test_registry_mro_uses_sanitized_status(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            registry = ActionRegistry(self.make_config(Path(raw)))
            self.assertEqual(
                GameAssetStatusActions.game_assets_provider_status,
                registry.game_assets_provider_status.__func__,
            )

    def test_meshy_image_status_omits_signed_model_urls(self) -> None:
        response = httpx.Response(
            200,
            json={
                "id": "task-1",
                "status": "SUCCEEDED",
                "progress": 100,
                "consumed_credits": 20,
                "model_urls": {
                    "glb": "https://assets.meshy.ai/model.glb?Signature=SECRET"
                },
                "thumbnail_url": "https://assets.meshy.ai/thumb.png?Signature=SECRET",
            },
        )
        with tempfile.TemporaryDirectory() as raw, patch.dict(
            os.environ, {"MESHY_API_KEY": "meshy-secret"}, clear=False
        ), patch("ordax_dev_agent.game_asset_status_actions.httpx.get", return_value=response) as get:
            registry = ActionRegistry(self.make_config(Path(raw)))
            result = registry.execute(
                "game_assets.provider_status",
                {
                    "project": "game",
                    "provider": "meshy",
                    "operation": "image_to_3d",
                    "task_id": "task-1",
                },
            )
            self.assertTrue(result.ok, result.summary)
            self.assertEqual("SUCCEEDED", result.data["status"]["status"])
            self.assertFalse(result.data["signed_result_urls_returned"])
            self.assertNotIn("SECRET", repr(result.data))
            self.assertNotIn("model_urls", result.data["status"])
            args, _ = get.call_args
            self.assertEqual("https://api.meshy.ai/openapi/v1/image-to-3d/task-1", args[0])

    def test_tripo_status_omits_output_urls(self) -> None:
        response = httpx.Response(
            200,
            json={
                "data": {
                    "task_id": "task-2",
                    "type": "image_to_model",
                    "status": "success",
                    "progress": 100,
                    "output": {
                        "model": "https://cdn.tripo3d.ai/model.glb?token=SECRET"
                    },
                }
            },
        )
        with tempfile.TemporaryDirectory() as raw, patch.dict(
            os.environ, {"TRIPO_API_KEY": "tripo-secret"}, clear=False
        ), patch("ordax_dev_agent.game_asset_status_actions.httpx.get", return_value=response):
            registry = ActionRegistry(self.make_config(Path(raw)))
            result = registry.execute(
                "game_assets.provider_status",
                {"project": "game", "provider": "tripo", "task_id": "task-2"},
            )
            self.assertTrue(result.ok, result.summary)
            self.assertEqual("success", result.data["status"]["status"])
            self.assertNotIn("SECRET", repr(result.data))
            self.assertNotIn("output", result.data["status"])

    def test_rodin_status_returns_only_job_control_fields(self) -> None:
        response = httpx.Response(
            200,
            json={
                "jobs": [
                    {
                        "uuid": "job-1",
                        "status": "Done",
                        "progress": 100,
                        "url": "https://cdn.hyper3d.ai/model.glb?sig=SECRET",
                    }
                ]
            },
        )
        with tempfile.TemporaryDirectory() as raw, patch.dict(
            os.environ, {"RODIN_API_KEY": "rodin-secret"}, clear=False
        ), patch("ordax_dev_agent.game_asset_status_actions.httpx.post", return_value=response) as post:
            registry = ActionRegistry(self.make_config(Path(raw)))
            result = registry.execute(
                "game_assets.provider_status",
                {
                    "project": "game",
                    "provider": "rodin",
                    "subscription_key": "sub-key-123",
                },
            )
            self.assertTrue(result.ok, result.summary)
            self.assertEqual("Done", result.data["status"]["jobs"][0]["status"])
            self.assertNotIn("SECRET", repr(result.data))
            self.assertNotIn("url", result.data["status"]["jobs"][0])
            args, kwargs = post.call_args
            self.assertEqual("https://api.hyper3d.com/api/v2/status", args[0])
            self.assertEqual({"subscription_key": "sub-key-123"}, kwargs["json"])


if __name__ == "__main__":
    unittest.main()
