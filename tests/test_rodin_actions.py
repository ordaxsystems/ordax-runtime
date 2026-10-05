import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

from ordax_dev_agent.actions import ActionRegistry
from ordax_dev_agent.config import AgentConfig


class RodinActionsTests(unittest.TestCase):
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

    def test_registry_exposes_rodin_actions(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            registry = ActionRegistry(self.make_config(Path(raw)))
            result = registry.execute("agent.status", {})
            self.assertTrue(result.ok)
            self.assertIn("game_assets.rodin_submit_text", result.data["actions"])
            self.assertIn("game_assets.rodin_status", result.data["actions"])
            self.assertIn("game_assets.rodin_download_manifest", result.data["actions"])

    def test_submit_text_uses_fixed_endpoint_and_animation_pose(self) -> None:
        response = httpx.Response(
            201,
            json={
                "message": "Submitted.",
                "uuid": "123e4567-e89b-12d3-a456-426614174000",
                "jobs": {
                    "uuids": ["223e4567-e89b-12d3-a456-426614174000"],
                    "subscription_key": "sub-key-123",
                },
                "consumed": 0.5,
            },
        )
        with tempfile.TemporaryDirectory() as raw, patch.dict(
            os.environ, {"RODIN_API_KEY": "rodin_secret"}, clear=False
        ), patch("ordax_dev_agent.rodin_actions.httpx.post", return_value=response) as post:
            registry = ActionRegistry(self.make_config(Path(raw)))
            result = registry.execute(
                "game_assets.rodin_submit_text",
                {
                    "project": "game",
                    "prompt": "stylized humanoid ranger for a realtime game",
                    "tier": "Gen-2.5-Medium",
                    "mesh_mode": "Raw",
                    "geometry_file_format": "glb",
                    "quality_override": 20000,
                    "t_a_pose": True,
                    "is_symmetric": "balanced",
                },
            )
            self.assertTrue(result.ok, result.summary)
            args, kwargs = post.call_args
            self.assertEqual("https://api.hyper3d.com/api/v2/rodin", args[0])
            self.assertEqual("Bearer rodin_secret", kwargs["headers"]["Authorization"])
            self.assertEqual("true", kwargs["data"]["TAPose"])
            self.assertEqual("20000", kwargs["data"]["quality_override"])
            self.assertEqual("balanced", kwargs["data"]["is_symmetric"])
            self.assertNotIn("rodin_secret", repr(result.data))

    def test_submit_rejects_body_level_error_even_on_201(self) -> None:
        response = httpx.Response(
            201,
            json={
                "error": "API_INSUFFICIENT_FUNDS",
                "message": "Insufficient balance",
            },
        )
        with tempfile.TemporaryDirectory() as raw, patch.dict(
            os.environ, {"RODIN_API_KEY": "rodin_secret"}, clear=False
        ), patch("ordax_dev_agent.rodin_actions.httpx.post", return_value=response):
            registry = ActionRegistry(self.make_config(Path(raw)))
            result = registry.execute(
                "game_assets.rodin_submit_text",
                {"project": "game", "prompt": "small wooden crate"},
            )
            self.assertFalse(result.ok)
            self.assertIn("API_INSUFFICIENT_FUNDS", result.summary)

    def test_quad_quality_override_is_bounded(self) -> None:
        with tempfile.TemporaryDirectory() as raw, patch.dict(
            os.environ, {"RODIN_API_KEY": "rodin_secret"}, clear=False
        ):
            registry = ActionRegistry(self.make_config(Path(raw)))
            result = registry.execute(
                "game_assets.rodin_submit_text",
                {
                    "project": "game",
                    "prompt": "small wooden crate",
                    "mesh_mode": "Quad",
                    "quality_override": 300000,
                },
            )
            self.assertFalse(result.ok)
            self.assertIn("200000", result.summary)

    def test_status_uses_subscription_key_endpoint(self) -> None:
        response = httpx.Response(
            200,
            json={"jobs": [{"uuid": "job-1", "status": "Done"}]},
        )
        with tempfile.TemporaryDirectory() as raw, patch.dict(
            os.environ, {"RODIN_API_KEY": "rodin_secret"}, clear=False
        ), patch("ordax_dev_agent.rodin_actions.httpx.post", return_value=response) as post:
            registry = ActionRegistry(self.make_config(Path(raw)))
            result = registry.execute(
                "game_assets.rodin_status",
                {"project": "game", "subscription_key": "sub-key-123"},
            )
            self.assertTrue(result.ok, result.summary)
            args, kwargs = post.call_args
            self.assertEqual("https://api.hyper3d.com/api/v2/status", args[0])
            self.assertEqual({"subscription_key": "sub-key-123"}, kwargs["json"])

    def test_download_manifest_uses_top_level_task_uuid(self) -> None:
        response = httpx.Response(
            201,
            json={
                "list": [
                    {"name": "model.glb", "url": "https://cdn.example/model.glb"},
                    {"name": "unsafe", "url": "http://example.invalid/file"},
                ]
            },
        )
        task_uuid = "123e4567-e89b-12d3-a456-426614174000"
        with tempfile.TemporaryDirectory() as raw, patch.dict(
            os.environ, {"RODIN_API_KEY": "rodin_secret"}, clear=False
        ), patch("ordax_dev_agent.rodin_actions.httpx.post", return_value=response) as post:
            registry = ActionRegistry(self.make_config(Path(raw)))
            result = registry.execute(
                "game_assets.rodin_download_manifest",
                {"project": "game", "task_uuid": task_uuid},
            )
            self.assertTrue(result.ok, result.summary)
            args, kwargs = post.call_args
            self.assertEqual("https://api.hyper3d.com/api/v2/download", args[0])
            self.assertEqual({"task_uuid": task_uuid}, kwargs["json"])
            self.assertEqual(
                [{"name": "model.glb", "url": "https://cdn.example/model.glb"}],
                result.data["files"],
            )


if __name__ == "__main__":
    unittest.main()
