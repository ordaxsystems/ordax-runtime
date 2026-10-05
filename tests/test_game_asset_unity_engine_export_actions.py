import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ordax_dev_agent.actions import ActionRegistry
from ordax_dev_agent.config import AgentConfig
from ordax_dev_agent.game_asset_engine_export_actions import ENGINE_EXPORT_SCHEMA
from ordax_dev_agent.models import ActionResult


class GameAssetUnityEngineExportActionsTests(unittest.TestCase):
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

    def make_export(self, root: Path, *, engine: str = "unity") -> tuple[Path, Path, bytes]:
        project = root / "project"
        source = project / "staging" / "prop_lods.blend"
        source.parent.mkdir(parents=True, exist_ok=True)
        source_body = b"lod-blend"
        source.write_bytes(source_body)
        artifact = project / "exports" / "prop_lods.fbx"
        artifact.parent.mkdir(parents=True, exist_ok=True)
        body = b"fbx-export"
        artifact.write_bytes(body)
        manifest = Path(str(artifact) + ".ordax.json")
        manifest.write_text(
            json.dumps(
                {
                    "schema": ENGINE_EXPORT_SCHEMA,
                    "project": "game",
                    "created_at": "2026-09-25T00:00:00+00:00",
                    "engine": engine,
                    "source": {
                        "path": "staging/prop_lods.blend",
                        "bytes": len(source_body),
                        "sha256": hashlib.sha256(source_body).hexdigest(),
                    },
                    "artifact": {
                        "path": "exports/prop_lods.fbx",
                        "format": "fbx",
                        "bytes": len(body),
                        "sha256": hashlib.sha256(body).hexdigest(),
                    },
                    "profile": {"format": "fbx", "purpose": engine},
                }
            ),
            encoding="utf-8",
        )
        return artifact, manifest, body

    def test_registry_exposes_verified_engine_export_unity_import(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            registry = ActionRegistry(self.make_config(Path(raw)))
            status = registry.execute("agent.status", {})
            self.assertTrue(status.ok)
            self.assertIn("game_assets.unity_import_engine_export", status.data["actions"])

    def test_unity_targeted_export_is_verified_copied_and_refreshed(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            artifact, _, body = self.make_export(root, engine="unity")
            registry = ActionRegistry(self.make_config(root))
            refresh = ActionResult(True, "Unity Editor refreshed", {"presence_fresh": True})
            with patch.object(registry, "unity_refresh_editor", return_value=refresh):
                result = registry.execute(
                    "game_assets.unity_import_engine_export",
                    {
                        "project": "game",
                        "artifact_path": "exports/prop_lods.fbx",
                        "destination_path": "Assets/Models/prop_lods.fbx",
                        "wait_seconds": 45,
                    },
                )
            self.assertTrue(result.ok, result.summary)
            self.assertEqual("unity", result.data["engine"])
            self.assertEqual(ENGINE_EXPORT_SCHEMA, result.data["integrity"]["schema"])
            self.assertEqual(
                "Assets/Models/prop_lods.fbx",
                result.data["asset_path"],
            )
            self.assertEqual(
                body,
                (root / "project" / "Assets" / "Models" / "prop_lods.fbx").read_bytes(),
            )
            self.assertEqual(body, artifact.read_bytes())

    def test_non_unity_engine_export_is_rejected_before_copy(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            self.make_export(root, engine="unreal")
            registry = ActionRegistry(self.make_config(root))
            with patch.object(registry, "unity_refresh_editor") as refresh:
                result = registry.execute(
                    "game_assets.unity_import_engine_export",
                    {
                        "project": "game",
                        "artifact_path": "exports/prop_lods.fbx",
                        "destination_path": "Assets/Models/prop_lods.fbx",
                    },
                )
            self.assertFalse(result.ok)
            self.assertIn("not targeted to Unity", result.summary)
            refresh.assert_not_called()
            self.assertFalse(
                (root / "project" / "Assets" / "Models" / "prop_lods.fbx").exists()
            )

    def test_tampered_engine_export_is_rejected_before_copy(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            artifact, _, _ = self.make_export(root, engine="unity")
            artifact.write_bytes(b"tampered")
            registry = ActionRegistry(self.make_config(root))
            result = registry.execute(
                "game_assets.unity_import_engine_export",
                {"project": "game", "artifact_path": "exports/prop_lods.fbx"},
            )
            self.assertFalse(result.ok)
            self.assertTrue("byte size" in result.summary or "SHA-256" in result.summary)
            self.assertFalse(
                (root / "project" / "Assets" / "OrdaX" / "Generated" / "prop_lods.fbx").exists()
            )


if __name__ == "__main__":
    unittest.main()
