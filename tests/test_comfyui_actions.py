import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

from ordax_dev_agent.actions import ActionRegistry
from ordax_dev_agent.comfyui_actions import _base_url
from ordax_dev_agent.config import AgentConfig


class ComfyUIActionsTests(unittest.TestCase):
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

    def test_registry_exposes_comfyui_actions(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            registry = ActionRegistry(self.make_config(Path(raw)))
            result = registry.execute("agent.status", {})
            self.assertTrue(result.ok)
            self.assertIn("game_assets.comfyui_status", result.data["actions"])
            self.assertIn("game_assets.comfyui_node_info", result.data["actions"])
            self.assertIn("game_assets.comfyui_run_workflow", result.data["actions"])
            self.assertIn("game_assets.comfyui_history", result.data["actions"])

    def test_comfyui_url_is_loopback_only(self) -> None:
        with patch.dict(os.environ, {"ORDAX_COMFYUI_URL": "https://example.com:8188"}, clear=False):
            with self.assertRaises(ValueError):
                _base_url()
        with patch.dict(os.environ, {"ORDAX_COMFYUI_URL": "http://localhost:8188"}, clear=False):
            self.assertEqual("http://localhost:8188", _base_url())

    def test_status_filters_launch_argv(self) -> None:
        response = httpx.Response(
            200,
            json={
                "system": {
                    "os": "win32",
                    "comfyui_version": "1.2.3",
                    "ram_total": 64,
                    "ram_free": 32,
                    "argv": ["python", "main.py", "--token", "secret"],
                },
                "devices": [
                    {
                        "name": "GPU",
                        "type": "cuda",
                        "index": 0,
                        "vram_total": 24,
                        "vram_free": 20,
                    }
                ],
            },
        )
        with tempfile.TemporaryDirectory() as raw, patch(
            "ordax_dev_agent.comfyui_actions.httpx.get", return_value=response
        ):
            registry = ActionRegistry(self.make_config(Path(raw)))
            result = registry.execute("game_assets.comfyui_status", {"project": "game"})
            self.assertTrue(result.ok, result.summary)
            self.assertNotIn("argv", result.data["system"])
            self.assertNotIn("secret", repr(result.data))
            self.assertEqual("GPU", result.data["devices"][0]["name"])

    def test_run_project_workflow_posts_api_prompt_format(self) -> None:
        response = httpx.Response(
            200,
            json={"prompt_id": "prompt-123", "number": 4, "node_errors": {}},
        )
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            config = self.make_config(root)
            workflow = root / "project" / "workflows" / "asset.json"
            workflow.parent.mkdir(parents=True)
            workflow.write_text(
                json.dumps(
                    {
                        "1": {
                            "class_type": "LoadImage",
                            "inputs": {"image": "reference.png"},
                        },
                        "2": {
                            "class_type": "SaveGLB",
                            "inputs": {"filename_prefix": "3d/generated"},
                        },
                    }
                ),
                encoding="utf-8",
            )
            with patch(
                "ordax_dev_agent.comfyui_actions.httpx.post", return_value=response
            ) as post:
                registry = ActionRegistry(config)
                result = registry.execute(
                    "game_assets.comfyui_run_workflow",
                    {"project": "game", "workflow_path": "workflows/asset.json"},
                )
            self.assertTrue(result.ok, result.summary)
            args, kwargs = post.call_args
            self.assertEqual("http://127.0.0.1:8188/prompt", args[0])
            self.assertEqual(2, len(kwargs["json"]["prompt"]))
            self.assertTrue(kwargs["json"]["client_id"].startswith("ordax-"))
            self.assertEqual("prompt-123", result.data["prompt_id"])

    def test_run_workflow_rejects_ui_only_json(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            config = self.make_config(root)
            workflow = root / "project" / "workflow.json"
            workflow.write_text(json.dumps({"nodes": [], "links": []}), encoding="utf-8")
            registry = ActionRegistry(config)
            result = registry.execute(
                "game_assets.comfyui_run_workflow",
                {"project": "game", "workflow_path": "workflow.json"},
            )
            self.assertFalse(result.ok)
            self.assertIn("API prompt format", result.summary)

    def test_run_workflow_returns_node_validation_errors(self) -> None:
        response = httpx.Response(
            200,
            json={
                "prompt_id": "prompt-123",
                "node_errors": {"2": {"errors": [{"message": "missing model"}]}},
            },
        )
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            config = self.make_config(root)
            workflow = root / "project" / "workflow.json"
            workflow.write_text(
                json.dumps({"2": {"class_type": "Missing3DNode", "inputs": {}}}),
                encoding="utf-8",
            )
            with patch("ordax_dev_agent.comfyui_actions.httpx.post", return_value=response):
                registry = ActionRegistry(config)
                result = registry.execute(
                    "game_assets.comfyui_run_workflow",
                    {"project": "game", "workflow_path": "workflow.json"},
                )
            self.assertFalse(result.ok)
            self.assertIn("rejected workflow nodes", result.summary)

    def test_history_uses_fixed_prompt_route(self) -> None:
        response = httpx.Response(200, json={"prompt-123": {"outputs": {}}})
        with tempfile.TemporaryDirectory() as raw, patch(
            "ordax_dev_agent.comfyui_actions.httpx.get", return_value=response
        ) as get:
            registry = ActionRegistry(self.make_config(Path(raw)))
            result = registry.execute(
                "game_assets.comfyui_history",
                {"project": "game", "prompt_id": "prompt-123"},
            )
            self.assertTrue(result.ok, result.summary)
            args, _ = get.call_args
            self.assertEqual("http://127.0.0.1:8188/history/prompt-123", args[0])


if __name__ == "__main__":
    unittest.main()
