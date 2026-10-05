import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ordax_dev_agent.actions import ActionRegistry
from ordax_dev_agent.config import AgentConfig
from ordax_dev_agent.models import ActionResult


class MixamoRoundTripTests(unittest.TestCase):
    def make_config(self, root: Path) -> AgentConfig:
        project = root / "project"
        project.mkdir(parents=True, exist_ok=True)
        state = root / "state"
        return AgentConfig(
            agent_name="test-agent",
            poll_seconds=1.0,
            state_dir=state,
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

    def test_registry_exposes_fbx_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            registry = ActionRegistry(self.make_config(Path(raw)))
            result = registry.execute("agent.status", {})
            self.assertTrue(result.ok)
            self.assertIn("game_assets.blender_import_fbx", result.data["actions"])

    def test_fbx_roundtrip_rejects_non_fbx(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            config = self.make_config(root)
            source = root / "project" / "character.obj"
            source.write_text("o character", encoding="utf-8")
            registry = ActionRegistry(config)
            result = registry.execute(
                "game_assets.blender_import_fbx",
                {
                    "project": "game",
                    "source_path": "character.obj",
                    "output_blend": "generated/character.blend",
                },
            )
            self.assertFalse(result.ok)
            self.assertIn("FBX", result.summary)

    def test_fbx_roundtrip_is_project_scoped(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            config = self.make_config(root)
            outside = root / "outside.fbx"
            outside.write_bytes(b"fbx")
            registry = ActionRegistry(config)
            result = registry.execute(
                "game_assets.blender_import_fbx",
                {
                    "project": "game",
                    "source_path": str(outside),
                    "output_blend": "generated/character.blend",
                },
            )
            self.assertFalse(result.ok)
            self.assertIn("outside registered project", result.summary)

    def test_fbx_roundtrip_requires_explicit_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            config = self.make_config(root)
            project = root / "project"
            (project / "character.fbx").write_bytes(b"fbx")
            output = project / "generated" / "character.blend"
            output.parent.mkdir(parents=True)
            output.write_bytes(b"existing")
            registry = ActionRegistry(config)
            result = registry.execute(
                "game_assets.blender_import_fbx",
                {
                    "project": "game",
                    "source_path": "character.fbx",
                    "output_blend": "generated/character.blend",
                },
            )
            self.assertFalse(result.ok)
            self.assertIn("overwrite=true", result.summary)

    def test_fbx_roundtrip_invokes_bounded_blender_helper(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            config = self.make_config(root)
            project = root / "project"
            source = project / "character.fbx"
            source.write_bytes(b"fbx")
            output = project / "generated" / "character.blend"

            def fake_run(command, *, cwd=None, timeout=0, env=None):
                self.assertIn("--factory-startup", command)
                input_arg = Path(command[command.index("--input") + 1])
                output_arg = Path(command[command.index("--output") + 1])
                self.assertTrue(os.path.samefile(input_arg, source))
                self.assertTrue(os.path.samefile(cwd, project))
                self.assertEqual(output.name, output_arg.name)
                self.assertTrue(os.path.samefile(output_arg.parent, output.parent))
                self.assertEqual(321, timeout)
                output.write_bytes(b"blend")
                artifact_dir = config.state_dir / "artifacts" / "game" / "game-assets"
                reports = list(artifact_dir.glob("mixamo-import-*.json"))
                self.assertEqual([], reports)
                report_arg = Path(command[command.index("--report") + 1])
                report_arg.write_text(
                    json.dumps(
                        {
                            "ok": True,
                            "counts": {"meshes": 1, "armatures": 1, "actions": 2},
                            "mixamo_rig_detected": True,
                        }
                    ),
                    encoding="utf-8",
                )
                return ActionResult(True, "command completed", {})

            registry = ActionRegistry(config)
            with patch("ordax_dev_agent.mixamo_actions.find_blender", return_value=Path("blender")), patch(
                "ordax_dev_agent.mixamo_actions._run", side_effect=fake_run
            ):
                result = registry.execute(
                    "game_assets.blender_import_fbx",
                    {
                        "project": "game",
                        "source_path": "character.fbx",
                        "output_blend": "generated/character.blend",
                        "timeout_seconds": 321,
                    },
                )

            self.assertTrue(result.ok, result.summary)
            self.assertTrue(os.path.samefile(result.data["output_blend"], output))
            self.assertTrue(result.data["report"]["mixamo_rig_detected"])


if __name__ == "__main__":
    unittest.main()
