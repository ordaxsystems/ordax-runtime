import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ordax_dev_agent.actions import ActionRegistry
from ordax_dev_agent.config import AgentConfig
from ordax_dev_agent.models import ActionResult


class GeneratedAssetActionsTests(unittest.TestCase):
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

    def make_artifact(self, root: Path, name="generated/scout.glb") -> tuple[Path, Path, bytes]:
        project = root / "project"
        artifact = project / name
        artifact.parent.mkdir(parents=True, exist_ok=True)
        body = b"generated-model-payload"
        artifact.write_bytes(body)
        manifest = Path(str(artifact) + ".ordax.json")
        manifest.write_text(
            json.dumps(
                {
                    "schema": "ordax.generated-asset/1",
                    "project": "game",
                    "artifact": {
                        "path": artifact.relative_to(project).as_posix(),
                        "format": artifact.suffix.lstrip("."),
                        "bytes": len(body),
                        "sha256": hashlib.sha256(body).hexdigest(),
                    },
                    "provenance": {
                        "provider": "meshy",
                        "task_id": "task-123",
                        "operation": "image_to_3d",
                    },
                }
            ),
            encoding="utf-8",
        )
        return artifact, manifest, body

    def test_registry_exposes_integrity_and_ingest_actions(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            registry = ActionRegistry(self.make_config(Path(raw)))
            status = registry.execute("agent.status", {})
            self.assertTrue(status.ok)
            self.assertIn("game_assets.artifact_verify", status.data["actions"])
            self.assertIn("game_assets.blender_ingest_generated", status.data["actions"])

    def test_artifact_verify_accepts_matching_hash_and_size(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            artifact, manifest, body = self.make_artifact(root)
            registry = ActionRegistry(self.make_config(root))
            result = registry.execute(
                "game_assets.artifact_verify",
                {"project": "game", "artifact_path": "generated/scout.glb"},
            )
            self.assertTrue(result.ok, result.summary)
            self.assertEqual(hashlib.sha256(body).hexdigest(), result.data["sha256"])
            self.assertEqual("meshy", result.data["provenance"]["provider"])
            self.assertTrue(os.path.samefile(result.data["manifest_path"], manifest))

    def test_artifact_verify_rejects_tampering(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            artifact, _, _ = self.make_artifact(root)
            artifact.write_bytes(b"tampered")
            registry = ActionRegistry(self.make_config(root))
            result = registry.execute(
                "game_assets.artifact_verify",
                {"project": "game", "artifact_path": "generated/scout.glb"},
            )
            self.assertFalse(result.ok)
            self.assertTrue("byte size" in result.summary or "SHA-256" in result.summary)

    def test_artifact_verify_rejects_manifest_for_different_file(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            _, manifest, _ = self.make_artifact(root)
            other = root / "project" / "generated" / "other.glb"
            other.write_bytes(b"generated-model-payload")
            registry = ActionRegistry(self.make_config(root))
            result = registry.execute(
                "game_assets.artifact_verify",
                {
                    "project": "game",
                    "artifact_path": "generated/other.glb",
                    "manifest_path": "generated/scout.glb.ordax.json",
                },
            )
            self.assertFalse(result.ok)
            self.assertIn("does not match requested artifact", result.summary)

    def test_ingest_requires_provenance_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            project = root / "project"
            project.mkdir(parents=True)
            (project / "model.glb").write_bytes(b"glb")
            registry = ActionRegistry(self.make_config(root))
            result = registry.execute(
                "game_assets.blender_ingest_generated",
                {
                    "project": "game",
                    "artifact_path": "model.glb",
                    "output_blend": "staging/model.blend",
                },
            )
            self.assertFalse(result.ok)
            self.assertIn("provenance manifest is required", result.summary)

    def test_ingest_verifies_before_invoking_blender_and_embeds_manifest_path(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            config = self.make_config(root)
            artifact, manifest, body = self.make_artifact(root)
            project = root / "project"
            output = project / "staging" / "scout.blend"

            def fake_run(command, *, cwd=None, timeout=0, env=None):
                self.assertIn("--factory-startup", command)
                self.assertTrue(os.path.samefile(cwd, project))
                self.assertEqual(900, timeout)
                input_arg = Path(command[command.index("--input") + 1])
                output_arg = Path(command[command.index("--output") + 1])
                provenance_arg = Path(command[command.index("--provenance") + 1])
                self.assertTrue(os.path.samefile(input_arg, artifact))
                self.assertEqual(output.name, output_arg.name)
                self.assertTrue(os.path.samefile(output_arg.parent, output.parent))
                self.assertTrue(os.path.samefile(provenance_arg, manifest))
                output.write_bytes(b"blend")
                report = Path(command[command.index("--report") + 1])
                report.write_text(
                    json.dumps(
                        {
                            "ok": True,
                            "counts": {"meshes": 1, "armatures": 0, "actions": 0},
                            "provenance_embedded": True,
                        }
                    ),
                    encoding="utf-8",
                )
                return ActionResult(True, "command completed", {})

            registry = ActionRegistry(config)
            with patch(
                "ordax_dev_agent.generated_asset_actions.find_blender",
                return_value=Path("blender"),
            ), patch(
                "ordax_dev_agent.generated_asset_actions._run",
                side_effect=fake_run,
            ):
                result = registry.execute(
                    "game_assets.blender_ingest_generated",
                    {
                        "project": "game",
                        "artifact_path": "generated/scout.glb",
                        "output_blend": "staging/scout.blend",
                        "timeout_seconds": 900,
                    },
                )
            self.assertTrue(result.ok, result.summary)
            self.assertEqual(hashlib.sha256(body).hexdigest(), result.data["integrity"]["sha256"])
            self.assertTrue(result.data["report"]["provenance_embedded"])
            self.assertTrue(os.path.samefile(result.data["output_blend"], output))

    def test_ingest_requires_explicit_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            self.make_artifact(root)
            output = root / "project" / "staging" / "scout.blend"
            output.parent.mkdir(parents=True)
            output.write_bytes(b"existing")
            registry = ActionRegistry(self.make_config(root))
            result = registry.execute(
                "game_assets.blender_ingest_generated",
                {
                    "project": "game",
                    "artifact_path": "generated/scout.glb",
                    "output_blend": "staging/scout.blend",
                },
            )
            self.assertFalse(result.ok)
            self.assertIn("overwrite=true", result.summary)
            self.assertEqual(b"existing", output.read_bytes())


if __name__ == "__main__":
    unittest.main()
