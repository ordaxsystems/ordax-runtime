import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

from ordax_dev_agent.actions import ActionRegistry
from ordax_dev_agent.config import AgentConfig
from ordax_dev_agent.game_asset_artifact_actions import _redacted_url, _safe_url


class _StreamResponse:
    def __init__(self, body: bytes, *, status_code: int = 200, headers=None):
        self.body = body
        self.status_code = status_code
        self.headers = headers or {}

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def iter_bytes(self, chunk_size=1024 * 1024):
        for offset in range(0, len(self.body), chunk_size):
            yield self.body[offset : offset + chunk_size]


class GameAssetArtifactTests(unittest.TestCase):
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

    def test_registry_exposes_provider_download(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            registry = ActionRegistry(self.make_config(Path(raw)))
            status = registry.execute("agent.status", {})
            self.assertTrue(status.ok)
            self.assertIn("game_assets.provider_download", status.data["actions"])

    def test_safe_url_refuses_non_https_and_private_ip(self) -> None:
        with self.assertRaises(ValueError):
            _safe_url("http://assets.meshy.ai/model.glb")
        with self.assertRaises(ValueError):
            _safe_url("https://127.0.0.1/model.glb")
        with self.assertRaises(ValueError):
            _safe_url("https://10.0.0.5/model.glb")
        self.assertEqual(
            "https://assets.meshy.ai/a/model.glb?Expires=123",
            _safe_url("https://assets.meshy.ai/a/model.glb?Expires=123"),
        )

    def test_redacted_url_drops_signed_query_and_fragment(self) -> None:
        self.assertEqual(
            "https://assets.meshy.ai/a/model.glb",
            _redacted_url("https://assets.meshy.ai/a/model.glb?Expires=123&Signature=secret#x"),
        )

    def test_meshy_download_writes_hash_and_redacted_provenance(self) -> None:
        body = b"glTF" + b"asset-bytes" * 10
        status_response = httpx.Response(
            200,
            json={
                "id": "task-123",
                "status": "SUCCEEDED",
                "created_at": 1,
                "finished_at": 2,
                "consumed_credits": 20,
                "model_urls": {
                    "glb": "https://assets.meshy.ai/tasks/task-123/model.glb?Expires=999&Signature=SECRET"
                },
            },
        )
        stream = _StreamResponse(
            body,
            headers={"content-length": str(len(body)), "content-type": "model/gltf-binary"},
        )
        with tempfile.TemporaryDirectory() as raw, patch.dict(
            os.environ, {"MESHY_API_KEY": "meshy-secret"}, clear=False
        ), patch(
            "ordax_dev_agent.game_asset_artifact_actions.httpx.get",
            return_value=status_response,
        ) as get, patch(
            "ordax_dev_agent.game_asset_artifact_actions.httpx.stream",
            return_value=stream,
        ) as stream_call:
            root = Path(raw)
            registry = ActionRegistry(self.make_config(root))
            result = registry.execute(
                "game_assets.provider_download",
                {
                    "project": "game",
                    "provider": "meshy",
                    "operation": "text_to_3d_preview",
                    "task_id": "task-123",
                    "format": "glb",
                    "output_path": "generated/assets/scout.glb",
                },
            )
            self.assertTrue(result.ok, result.summary)
            output = root / "project" / "generated" / "assets" / "scout.glb"
            manifest_path = Path(str(output) + ".ordax.json")
            self.assertEqual(body, output.read_bytes())
            self.assertEqual(hashlib.sha256(body).hexdigest(), result.data["sha256"])
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertEqual("ordax.generated-asset/1", manifest["schema"])
            self.assertEqual("meshy", manifest["provenance"]["provider"])
            self.assertEqual(
                "https://assets.meshy.ai/tasks/task-123/model.glb",
                manifest["provenance"]["source_url"],
            )
            self.assertNotIn("SECRET", manifest_path.read_text(encoding="utf-8"))
            self.assertNotIn("meshy-secret", manifest_path.read_text(encoding="utf-8"))
            args, kwargs = get.call_args
            self.assertEqual(
                "https://api.meshy.ai/openapi/v2/text-to-3d/task-123",
                args[0],
            )
            self.assertEqual("Bearer meshy-secret", kwargs["headers"]["Authorization"])
            stream_args, stream_kwargs = stream_call.call_args
            self.assertIn("Signature=SECRET", stream_args[1])
            self.assertFalse(stream_kwargs["follow_redirects"])

    def test_download_refuses_existing_output_without_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            config = self.make_config(root)
            output = root / "project" / "generated" / "asset.glb"
            output.parent.mkdir(parents=True)
            output.write_bytes(b"original")
            registry = ActionRegistry(config)
            result = registry.execute(
                "game_assets.provider_download",
                {
                    "project": "game",
                    "provider": "meshy",
                    "operation": "text_to_3d_preview",
                    "task_id": "task-123",
                    "format": "glb",
                    "output_path": "generated/asset.glb",
                },
            )
            self.assertFalse(result.ok)
            self.assertIn("overwrite=true", result.summary)
            self.assertEqual(b"original", output.read_bytes())

    def test_download_enforces_max_bytes_before_writing(self) -> None:
        body = b"x" * 4096
        status_response = httpx.Response(
            200,
            json={
                "status": "SUCCEEDED",
                "model_urls": {
                    "glb": "https://assets.meshy.ai/tasks/task-123/model.glb?Expires=999"
                },
            },
        )
        stream = _StreamResponse(
            body,
            headers={"content-length": str(len(body)), "content-type": "application/octet-stream"},
        )
        with tempfile.TemporaryDirectory() as raw, patch.dict(
            os.environ, {"MESHY_API_KEY": "meshy-secret"}, clear=False
        ), patch(
            "ordax_dev_agent.game_asset_artifact_actions.httpx.get",
            return_value=status_response,
        ), patch(
            "ordax_dev_agent.game_asset_artifact_actions.httpx.stream",
            return_value=stream,
        ):
            root = Path(raw)
            registry = ActionRegistry(self.make_config(root))
            result = registry.execute(
                "game_assets.provider_download",
                {
                    "project": "game",
                    "provider": "meshy",
                    "operation": "text_to_3d_preview",
                    "task_id": "task-123",
                    "format": "glb",
                    "output_path": "generated/asset.glb",
                    "max_bytes": 1024,
                },
            )
            self.assertFalse(result.ok)
            self.assertIn("max_bytes", result.summary)
            self.assertFalse((root / "project" / "generated" / "asset.glb").exists())
            self.assertFalse((root / "project" / "generated" / "asset.glb.part").exists())

    def test_tripo_download_extracts_nested_documented_output(self) -> None:
        body = b"FBX-bytes"
        status_response = httpx.Response(
            200,
            json={
                "code": 0,
                "data": {
                    "status": "success",
                    "type": "animate_rig",
                    "progress": 100,
                    "output": {
                        "model": "https://cdn.tripo3d.ai/tasks/task-456/model.fbx?token=SECRET"
                    },
                },
            },
        )
        with tempfile.TemporaryDirectory() as raw, patch.dict(
            os.environ, {"TRIPO_API_KEY": "tripo-secret"}, clear=False
        ), patch(
            "ordax_dev_agent.game_asset_artifact_actions.httpx.get",
            return_value=status_response,
        ), patch(
            "ordax_dev_agent.game_asset_artifact_actions.httpx.stream",
            return_value=_StreamResponse(body),
        ):
            root = Path(raw)
            registry = ActionRegistry(self.make_config(root))
            result = registry.execute(
                "game_assets.provider_download",
                {
                    "project": "game",
                    "provider": "tripo",
                    "task_id": "task-456",
                    "format": "fbx",
                    "output_path": "generated/rigged.fbx",
                },
            )
            self.assertTrue(result.ok, result.summary)
            manifest = json.loads(
                (root / "project" / "generated" / "rigged.fbx.ordax.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual("tripo", manifest["provenance"]["provider"])
            self.assertNotIn("SECRET", repr(manifest))

    def test_rodin_download_selects_requested_format(self) -> None:
        body = b"rodin-glb"
        manifest_response = httpx.Response(
            201,
            json={
                "list": [
                    {"name": "model.glb", "url": "https://cdn.hyper3d.ai/a/model.glb?sig=SECRET"},
                    {"name": "model.fbx", "url": "https://cdn.hyper3d.ai/a/model.fbx?sig=SECRET"},
                ]
            },
        )
        task_uuid = "123e4567-e89b-12d3-a456-426614174000"
        with tempfile.TemporaryDirectory() as raw, patch.dict(
            os.environ, {"RODIN_API_KEY": "rodin-secret"}, clear=False
        ), patch(
            "ordax_dev_agent.game_asset_artifact_actions.httpx.post",
            return_value=manifest_response,
        ), patch(
            "ordax_dev_agent.game_asset_artifact_actions.httpx.stream",
            return_value=_StreamResponse(body),
        ):
            root = Path(raw)
            registry = ActionRegistry(self.make_config(root))
            result = registry.execute(
                "game_assets.provider_download",
                {
                    "project": "game",
                    "provider": "rodin",
                    "task_id": task_uuid,
                    "format": "glb",
                    "output_path": "generated/rodin.glb",
                },
            )
            self.assertTrue(result.ok, result.summary)
            self.assertEqual(body, (root / "project" / "generated" / "rodin.glb").read_bytes())


if __name__ == "__main__":
    unittest.main()
