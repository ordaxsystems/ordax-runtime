import base64
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

from ordax_dev_agent.actions import ActionRegistry
from ordax_dev_agent.config import AgentConfig


PNG = b"\x89PNG\r\n\x1a\n" + b"x" * 32
JPEG = b"\xff\xd8\xff\xe0" + b"x" * 32


class GameAssetImageActionsTests(unittest.TestCase):
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

    def write_image(self, root: Path, name: str, body: bytes = PNG) -> Path:
        path = root / "project" / "refs" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)
        return path

    def test_registry_exposes_image_generation_actions(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            registry = ActionRegistry(self.make_config(Path(raw)))
            status = registry.execute("agent.status", {})
            self.assertTrue(status.ok)
            self.assertIn("game_assets.meshy_submit_images", status.data["actions"])
            self.assertIn("game_assets.tripo_submit_images", status.data["actions"])
            self.assertIn("game_assets.rodin_submit_images", status.data["actions"])

    def test_meshy_single_image_uses_project_file_as_data_uri(self) -> None:
        response = httpx.Response(202, json={"result": "meshy-task-1"})
        with tempfile.TemporaryDirectory() as raw, patch.dict(
            os.environ, {"MESHY_API_KEY": "meshy-secret"}, clear=False
        ), patch("ordax_dev_agent.game_asset_image_actions.httpx.post", return_value=response) as post:
            root = Path(raw)
            self.write_image(root, "front.png")
            registry = ActionRegistry(self.make_config(root))
            result = registry.execute(
                "game_assets.meshy_submit_images",
                {
                    "project": "game",
                    "image_paths": ["refs/front.png"],
                    "pose_mode": "a-pose",
                    "target_polycount": 24000,
                },
            )
            self.assertTrue(result.ok, result.summary)
            args, kwargs = post.call_args
            self.assertEqual("https://api.meshy.ai/openapi/v1/image-to-3d", args[0])
            request = kwargs["json"]
            self.assertTrue(request["image_url"].startswith("data:image/png;base64,"))
            encoded = request["image_url"].split(",", 1)[1]
            self.assertEqual(PNG, base64.b64decode(encoded))
            self.assertEqual("a-pose", request["pose_mode"])
            self.assertEqual(24000, request["target_polycount"])
            self.assertNotIn("meshy-secret", repr(result.data))

    def test_meshy_multiview_uses_four_local_images(self) -> None:
        response = httpx.Response(202, json={"result": "meshy-task-4"})
        with tempfile.TemporaryDirectory() as raw, patch.dict(
            os.environ, {"MESHY_API_KEY": "meshy-secret"}, clear=False
        ), patch("ordax_dev_agent.game_asset_image_actions.httpx.post", return_value=response) as post:
            root = Path(raw)
            names = ["front.png", "left.jpg", "back.png", "right.jpg"]
            for name in names:
                self.write_image(root, name, JPEG if name.endswith(".jpg") else PNG)
            registry = ActionRegistry(self.make_config(root))
            result = registry.execute(
                "game_assets.meshy_submit_images",
                {
                    "project": "game",
                    "image_paths": [f"refs/{name}" for name in names],
                    "geometry_resolution": "2k",
                },
            )
            self.assertTrue(result.ok, result.summary)
            args, kwargs = post.call_args
            self.assertEqual("https://api.meshy.ai/openapi/v1/multi-image-to-3d", args[0])
            self.assertEqual(4, len(kwargs["json"]["image_urls"]))
            self.assertEqual("2k", kwargs["json"]["geometry_resolution"])
            self.assertEqual("multi_image_to_3d", result.data["operation"])

    def test_meshy_rejects_single_only_options_for_multiview(self) -> None:
        with tempfile.TemporaryDirectory() as raw, patch.dict(
            os.environ, {"MESHY_API_KEY": "meshy-secret"}, clear=False
        ):
            root = Path(raw)
            self.write_image(root, "a.png")
            self.write_image(root, "b.png")
            registry = ActionRegistry(self.make_config(root))
            result = registry.execute(
                "game_assets.meshy_submit_images",
                {
                    "project": "game",
                    "image_paths": ["refs/a.png", "refs/b.png"],
                    "pose_mode": "t-pose",
                },
            )
            self.assertFalse(result.ok)
            self.assertIn("single-image options", result.summary)

    def test_tripo_multiview_uploads_in_front_left_back_right_order_without_returning_tokens(self) -> None:
        upload_responses = [
            httpx.Response(200, json={"data": {"image_token": f"token-{index}"}})
            for index in range(4)
        ]
        task_response = httpx.Response(200, json={"data": {"task_id": "tripo-task-4"}})
        with tempfile.TemporaryDirectory() as raw, patch.dict(
            os.environ, {"TRIPO_API_KEY": "tripo-secret"}, clear=False
        ), patch(
            "ordax_dev_agent.game_asset_image_actions.httpx.post",
            side_effect=[*upload_responses, task_response],
        ) as post:
            root = Path(raw)
            names = ["front.png", "left.png", "back.png", "right.png"]
            for name in names:
                self.write_image(root, name)
            registry = ActionRegistry(self.make_config(root))
            result = registry.execute(
                "game_assets.tripo_submit_images",
                {
                    "project": "game",
                    "image_paths": [f"refs/{name}" for name in names],
                    "face_limit": 18000,
                },
            )
            self.assertTrue(result.ok, result.summary)
            self.assertEqual(["front", "left", "back", "right"], result.data["view_order"])
            self.assertFalse(result.data["upload_tokens_returned"])
            self.assertNotIn("token-", repr(result.data))
            calls = post.call_args_list
            self.assertEqual(5, len(calls))
            for index, call in enumerate(calls[:4]):
                self.assertEqual("https://api.tripo3d.ai/v2/openapi/upload", call.args[0])
                upload_tuple = call.kwargs["files"]["file"]
                self.assertEqual(names[index], upload_tuple[0])
            task_call = calls[4]
            self.assertEqual("https://api.tripo3d.ai/v2/openapi/task", task_call.args[0])
            request = task_call.kwargs["json"]
            self.assertEqual("multiview_to_model", request["type"])
            self.assertEqual(
                ["token-0", "token-1", "token-2", "token-3"],
                [entry["file_token"] for entry in request["files"]],
            )
            self.assertEqual(18000, request["face_limit"])

    def test_tripo_requires_one_or_four_images(self) -> None:
        with tempfile.TemporaryDirectory() as raw, patch.dict(
            os.environ, {"TRIPO_API_KEY": "tripo-secret"}, clear=False
        ):
            root = Path(raw)
            self.write_image(root, "a.png")
            self.write_image(root, "b.png")
            registry = ActionRegistry(self.make_config(root))
            result = registry.execute(
                "game_assets.tripo_submit_images",
                {"project": "game", "image_paths": ["refs/a.png", "refs/b.png"]},
            )
            self.assertFalse(result.ok)
            self.assertIn("exactly 1 image or 4", result.summary)

    def test_rodin_repeats_images_and_orientation_labels(self) -> None:
        response = httpx.Response(
            201,
            json={
                "uuid": "123e4567-e89b-12d3-a456-426614174000",
                "jobs": {"subscription_key": "sub-123", "uuids": []},
                "consumed": 0.5,
            },
        )
        with tempfile.TemporaryDirectory() as raw, patch.dict(
            os.environ, {"RODIN_API_KEY": "rodin-secret"}, clear=False
        ), patch("ordax_dev_agent.game_asset_image_actions.httpx.post", return_value=response) as post:
            root = Path(raw)
            self.write_image(root, "front.png")
            self.write_image(root, "back.png")
            registry = ActionRegistry(self.make_config(root))
            result = registry.execute(
                "game_assets.rodin_submit_images",
                {
                    "project": "game",
                    "image_paths": ["refs/front.png", "refs/back.png"],
                    "image_labels": ["F", "B"],
                    "t_a_pose": True,
                    "quality_override": 20000,
                },
            )
            self.assertTrue(result.ok, result.summary)
            args, kwargs = post.call_args
            self.assertEqual("https://api.hyper3d.com/api/v2/rodin", args[0])
            self.assertEqual(2, len(kwargs["files"]))
            self.assertEqual(["images", "images"], [entry[0] for entry in kwargs["files"]])
            data = kwargs["data"]
            self.assertEqual([("image_label", "F"), ("image_label", "B")], [x for x in data if x[0] == "image_label"])
            self.assertIn(("TAPose", "true"), data)
            self.assertIn(("quality_override", "20000"), data)
            self.assertNotIn("rodin-secret", repr(result.data))

    def test_source_must_be_inside_registered_project(self) -> None:
        with tempfile.TemporaryDirectory() as raw, patch.dict(
            os.environ, {"MESHY_API_KEY": "meshy-secret"}, clear=False
        ):
            root = Path(raw)
            outside = root / "outside.png"
            outside.write_bytes(PNG)
            registry = ActionRegistry(self.make_config(root))
            result = registry.execute(
                "game_assets.meshy_submit_images",
                {"project": "game", "image_paths": [str(outside)]},
            )
            self.assertFalse(result.ok)
            self.assertIn("outside registered project", result.summary)

    def test_magic_bytes_must_match_extension(self) -> None:
        with tempfile.TemporaryDirectory() as raw, patch.dict(
            os.environ, {"MESHY_API_KEY": "meshy-secret"}, clear=False
        ):
            root = Path(raw)
            self.write_image(root, "fake.png", JPEG)
            registry = ActionRegistry(self.make_config(root))
            result = registry.execute(
                "game_assets.meshy_submit_images",
                {"project": "game", "image_paths": ["refs/fake.png"]},
            )
            self.assertFalse(result.ok)
            self.assertIn("does not match PNG", result.summary)


if __name__ == "__main__":
    unittest.main()
