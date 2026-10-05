import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ordax_dev_agent.actions import ActionRegistry
from ordax_dev_agent.config import AgentConfig
from ordax_dev_agent.models import ActionResult


class GameAssetUnityActionsTests(unittest.TestCase):
    def make_config(self, root: Path, *, unity: bool = True) -> AgentConfig:
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
                    "apps": ["blender", "unity"] if unity else ["blender"],
                    "allowed_branches": [],
                }
            },
            default_project="game",
        )

    def make_artifact(self, root: Path) -> tuple[Path, Path, bytes]:
        project = root / "project"
        artifact = project / "generated" / "scout.glb"
        artifact.parent.mkdir(parents=True, exist_ok=True)
        body = b"generated-unity-model"
        artifact.write_bytes(body)
        manifest = Path(str(artifact) + ".ordax.json")
        manifest.write_text(
            json.dumps(
                {
                    "schema": "ordax.generated-asset/1",
                    "project": "game",
                    "artifact": {
                        "path": artifact.relative_to(project).as_posix(),
                        "format": "glb",
                        "bytes": len(body),
                        "sha256": hashlib.sha256(body).hexdigest(),
                    },
                    "provenance": {
                        "provider": "tripo",
                        "task_id": "task-123",
                        "operation": "image_to_model",
                    },
                }
            ),
            encoding="utf-8",
        )
        return artifact, manifest, body

    def test_registry_exposes_verified_unity_import_and_model_audit(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            registry = ActionRegistry(self.make_config(Path(raw)))
            status = registry.execute("agent.status", {})
            self.assertTrue(status.ok)
            self.assertIn("game_assets.unity_import_generated", status.data["actions"])
            self.assertIn("game_assets.unity_model_audit", status.data["actions"])

    def test_verified_asset_is_copied_then_refreshed_in_unity(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            artifact, _, body = self.make_artifact(root)
            registry = ActionRegistry(self.make_config(root))
            refresh = ActionResult(True, "Unity Editor refreshed", {"presence_fresh": True})
            with patch.object(registry, "unity_refresh_editor", return_value=refresh) as refresh_call:
                result = registry.execute(
                    "game_assets.unity_import_generated",
                    {
                        "project": "game",
                        "artifact_path": "generated/scout.glb",
                        "wait_seconds": 30,
                    },
                )

            self.assertTrue(result.ok, result.summary)
            self.assertEqual("Assets/OrdaX/Generated/scout.glb", result.data["asset_path"])
            self.assertTrue(result.data["import_confirmed"])
            self.assertEqual(hashlib.sha256(body).hexdigest(), result.data["integrity"]["sha256"])
            destination = root / "project" / "Assets" / "OrdaX" / "Generated" / "scout.glb"
            self.assertEqual(body, destination.read_bytes())
            refresh_call.assert_called_once_with(
                {"project": "game", "force": True, "wait_seconds": 30.0}
            )
            self.assertEqual(body, artifact.read_bytes())

    def test_tampered_artifact_is_rejected_before_unity_copy(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            artifact, _, _ = self.make_artifact(root)
            artifact.write_bytes(b"tampered")
            registry = ActionRegistry(self.make_config(root))
            with patch.object(registry, "unity_refresh_editor") as refresh_call:
                result = registry.execute(
                    "game_assets.unity_import_generated",
                    {"project": "game", "artifact_path": "generated/scout.glb"},
                )
            self.assertFalse(result.ok)
            self.assertTrue("byte size" in result.summary or "SHA-256" in result.summary)
            refresh_call.assert_not_called()
            self.assertFalse(
                (root / "project" / "Assets" / "OrdaX" / "Generated" / "scout.glb").exists()
            )

    def test_refresh_failure_preserves_canonical_source_and_reports_retryable(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            artifact, _, body = self.make_artifact(root)
            registry = ActionRegistry(self.make_config(root))
            refresh = ActionResult(False, "Unity companion unavailable", {})
            with patch.object(registry, "unity_refresh_editor", return_value=refresh):
                result = registry.execute(
                    "game_assets.unity_import_generated",
                    {"project": "game", "artifact_path": "generated/scout.glb"},
                )
            self.assertFalse(result.ok)
            self.assertTrue(result.data["retryable"])
            self.assertTrue(result.data["source_preserved"])
            self.assertEqual(body, artifact.read_bytes())
            self.assertEqual(
                body,
                (root / "project" / "Assets" / "OrdaX" / "Generated" / "scout.glb").read_bytes(),
            )

    def test_model_audit_uses_editor_companion_on_existing_assets_model(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            project = root / "project"
            asset = project / "Assets" / "Models" / "scout.fbx"
            asset.parent.mkdir(parents=True, exist_ok=True)
            asset.write_bytes(b"fbx")
            registry = ActionRegistry(self.make_config(root))

            class FakeEditor:
                def request(self, action, payload, *, timeout_seconds):
                    self.action = action
                    self.payload = payload
                    self.timeout_seconds = timeout_seconds
                    return ActionResult(
                        True,
                        "Unity model import audit passed",
                        {
                            "assetPath": payload["assetPath"],
                            "assetImporterType": "UnityEditor.ModelImporter",
                            "modelMeshCount": 2,
                            "modelVertexCount": 1200,
                            "modelTriangleCount": 800,
                            "modelMaterialCount": 3,
                            "modelAnimationClipCount": 1,
                            "modelBoneCount": 24,
                            "modelLodGroupCount": 0,
                        },
                    )

            editor = FakeEditor()
            with patch.object(registry, "_editor", return_value=editor):
                result = registry.execute(
                    "game_assets.unity_model_audit",
                    {
                        "project": "game",
                        "asset_path": "Assets/Models/scout.fbx",
                        "timeout_seconds": 90,
                    },
                )

            self.assertTrue(result.ok, result.summary)
            self.assertEqual("asset_model_audit", editor.action)
            self.assertEqual({"assetPath": "Assets/Models/scout.fbx"}, editor.payload)
            self.assertEqual(90.0, editor.timeout_seconds)
            self.assertEqual(800, result.data["audit"]["modelTriangleCount"])

    def test_model_audit_rejects_paths_outside_assets(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            registry = ActionRegistry(self.make_config(root))
            (root / "project" / "outside.fbx").write_bytes(b"fbx")
            result = registry.execute(
                "game_assets.unity_model_audit",
                {"project": "game", "asset_path": "outside.fbx"},
            )
            self.assertFalse(result.ok)
            self.assertIn("Assets", result.summary)

    def test_unity_must_be_enabled_for_project(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            self.make_artifact(root)
            registry = ActionRegistry(self.make_config(root, unity=False))
            result = registry.execute(
                "game_assets.unity_import_generated",
                {"project": "game", "artifact_path": "generated/scout.glb", "refresh": False},
            )
            self.assertFalse(result.ok)
            self.assertIn("not enabled", result.summary)


if __name__ == "__main__":
    unittest.main()
