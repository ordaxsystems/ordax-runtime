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


class GameAssetEngineExportActionsTests(unittest.TestCase):
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

    def test_registry_exposes_verified_engine_export_actions(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            registry = ActionRegistry(self.make_config(Path(raw)))
            status = registry.execute("agent.status", {})
            self.assertTrue(status.ok)
            self.assertIn("game_assets.blender_export_verified", status.data["actions"])
            self.assertIn("game_assets.engine_export_verify", status.data["actions"])

    def test_verified_export_uses_temporary_artifact_then_writes_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            config = self.make_config(root)
            project = root / "project"
            source = project / "staging" / "prop_lods.blend"
            source.parent.mkdir(parents=True, exist_ok=True)
            source_body = b"blend-source-v1"
            source.write_bytes(source_body)
            registry = ActionRegistry(config)

            def fake_export(payload):
                temporary = project / payload["output_path"]
                self.assertNotEqual(project / "exports" / "prop_lods.fbx", temporary)
                self.assertEqual("unity", payload["engine"])
                self.assertEqual("staging/prop_lods.blend", payload["blend_file"])
                temporary.parent.mkdir(parents=True, exist_ok=True)
                temporary.write_bytes(b"fbx-runtime-output")
                return ActionResult(
                    True,
                    "unity character export completed",
                    {
                        "profile": {"format": "fbx", "purpose": "Unity"},
                        "report": {"quality_ok": True},
                    },
                )

            with patch.object(registry, "game_assets_blender_export", side_effect=fake_export):
                result = registry.execute(
                    "game_assets.blender_export_verified",
                    {
                        "project": "game",
                        "blend_file": "staging/prop_lods.blend",
                        "engine": "unity",
                        "output_path": "exports/prop_lods.fbx",
                    },
                )

            self.assertTrue(result.ok, result.summary)
            output = project / "exports" / "prop_lods.fbx"
            manifest = Path(str(output) + ".ordax.json")
            self.assertEqual(b"fbx-runtime-output", output.read_bytes())
            self.assertTrue(manifest.is_file())
            data = json.loads(manifest.read_text(encoding="utf-8"))
            self.assertEqual(ENGINE_EXPORT_SCHEMA, data["schema"])
            self.assertEqual("game", data["project"])
            self.assertEqual("unity", data["engine"])
            self.assertEqual("staging/prop_lods.blend", data["source"]["path"])
            self.assertEqual(
                hashlib.sha256(source_body).hexdigest(),
                data["source"]["sha256"],
            )
            self.assertEqual("exports/prop_lods.fbx", data["artifact"]["path"])
            self.assertEqual(
                hashlib.sha256(b"fbx-runtime-output").hexdigest(),
                data["artifact"]["sha256"],
            )
            self.assertEqual(source_body, source.read_bytes())

    def test_export_requires_explicit_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            config = self.make_config(root)
            project = root / "project"
            source = project / "source.blend"
            source.write_bytes(b"blend")
            output = project / "asset.fbx"
            output.write_bytes(b"existing")
            registry = ActionRegistry(config)
            with patch.object(registry, "game_assets_blender_export") as export:
                result = registry.execute(
                    "game_assets.blender_export_verified",
                    {
                        "project": "game",
                        "blend_file": "source.blend",
                        "engine": "unity",
                        "output_path": "asset.fbx",
                    },
                )
            self.assertFalse(result.ok)
            self.assertIn("overwrite=true", result.summary)
            export.assert_not_called()
            self.assertEqual(b"existing", output.read_bytes())

    def test_export_verify_rejects_artifact_tampering(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            config = self.make_config(root)
            project = root / "project"
            source = project / "source.blend"
            source.write_bytes(b"blend")
            output = project / "asset.fbx"
            output.write_bytes(b"fbx")
            manifest = Path(str(output) + ".ordax.json")
            manifest.write_text(
                json.dumps(
                    {
                        "schema": ENGINE_EXPORT_SCHEMA,
                        "project": "game",
                        "created_at": "2026-09-25T00:00:00+00:00",
                        "engine": "unity",
                        "source": {
                            "path": "source.blend",
                            "bytes": len(b"blend"),
                            "sha256": hashlib.sha256(b"blend").hexdigest(),
                        },
                        "artifact": {
                            "path": "asset.fbx",
                            "format": "fbx",
                            "bytes": len(b"fbx"),
                            "sha256": hashlib.sha256(b"fbx").hexdigest(),
                        },
                        "profile": {"format": "fbx"},
                    }
                ),
                encoding="utf-8",
            )
            output.write_bytes(b"tampered")
            registry = ActionRegistry(config)
            result = registry.execute(
                "game_assets.engine_export_verify",
                {"project": "game", "artifact_path": "asset.fbx"},
            )
            self.assertFalse(result.ok)
            self.assertTrue("byte size" in result.summary or "SHA-256" in result.summary)

    def test_export_remains_verifiable_after_source_blend_changes(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            config = self.make_config(root)
            project = root / "project"
            source = project / "source.blend"
            original = b"blend-v1"
            source.write_bytes(original)
            output = project / "asset.fbx"
            body = b"fbx-v1"
            output.write_bytes(body)
            manifest = Path(str(output) + ".ordax.json")
            manifest.write_text(
                json.dumps(
                    {
                        "schema": ENGINE_EXPORT_SCHEMA,
                        "project": "game",
                        "created_at": "2026-09-25T00:00:00+00:00",
                        "engine": "unity",
                        "source": {
                            "path": "source.blend",
                            "bytes": len(original),
                            "sha256": hashlib.sha256(original).hexdigest(),
                        },
                        "artifact": {
                            "path": "asset.fbx",
                            "format": "fbx",
                            "bytes": len(body),
                            "sha256": hashlib.sha256(body).hexdigest(),
                        },
                        "profile": {"format": "fbx"},
                    }
                ),
                encoding="utf-8",
            )
            source.write_bytes(b"blend-v2")
            registry = ActionRegistry(config)
            result = registry.execute(
                "game_assets.engine_export_verify",
                {"project": "game", "artifact_path": "asset.fbx"},
            )
            self.assertTrue(result.ok, result.summary)
            self.assertFalse(result.data["source_current_matches"])
            self.assertEqual(hashlib.sha256(original).hexdigest(), result.data["source_sha256"])


if __name__ == "__main__":
    unittest.main()
