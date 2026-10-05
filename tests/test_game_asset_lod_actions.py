import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ordax_dev_agent.actions import ActionRegistry
from ordax_dev_agent.config import AgentConfig
from ordax_dev_agent.game_asset_lod_actions import _ratios
from ordax_dev_agent.models import ActionResult


class GameAssetLodActionsTests(unittest.TestCase):
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

    def test_registry_exposes_static_lod_action(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            registry = ActionRegistry(self.make_config(Path(raw)))
            status = registry.execute("agent.status", {})
            self.assertTrue(status.ok)
            self.assertIn("game_assets.blender_generate_static_lods", status.data["actions"])

    def test_default_lod_ratios_are_conservative_and_descending(self) -> None:
        self.assertEqual([0.5, 0.25, 0.1], _ratios(None))

    def test_lod_ratios_reject_increasing_or_extreme_values(self) -> None:
        with self.assertRaises(ValueError):
            _ratios([0.25, 0.5])
        with self.assertRaises(ValueError):
            _ratios([0.01])
        with self.assertRaises(ValueError):
            _ratios([1.0])

    def test_lod_generation_requires_distinct_output_and_explicit_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            config = self.make_config(root)
            source = root / "project" / "asset.blend"
            source.write_bytes(b"blend")
            registry = ActionRegistry(config)
            same = registry.execute(
                "game_assets.blender_generate_static_lods",
                {
                    "project": "game",
                    "blend_file": "asset.blend",
                    "output_blend": "asset.blend",
                },
            )
            self.assertFalse(same.ok)
            self.assertIn("different", same.summary)

            output = root / "project" / "asset_lods.blend"
            output.write_bytes(b"existing")
            existing = registry.execute(
                "game_assets.blender_generate_static_lods",
                {
                    "project": "game",
                    "blend_file": "asset.blend",
                    "output_blend": "asset_lods.blend",
                },
            )
            self.assertFalse(existing.ok)
            self.assertIn("overwrite=true", existing.summary)

    def test_lod_generation_invokes_headless_helper_with_bounded_ratios(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            config = self.make_config(root)
            project = root / "project"
            source = project / "asset.blend"
            source.write_bytes(b"blend")
            output = project / "generated" / "asset_lods.blend"

            def fake_run(command, *, cwd=None, timeout=0, env=None):
                self.assertTrue(os.path.samefile(project, cwd))
                self.assertEqual(777, timeout)
                self.assertTrue(os.path.samefile(source, command[2]))
                self.assertEqual("--background", command[1])
                ratio_arg = command[command.index("--ratios") + 1]
                self.assertEqual("0.600000,0.300000,0.120000", ratio_arg)
                output_arg = Path(command[command.index("--output") + 1])
                self.assertEqual(output.name, output_arg.name)
                self.assertTrue(os.path.samefile(output.parent, output_arg.parent))
                output.write_bytes(b"lod-blend")
                report = Path(command[command.index("--report") + 1])
                report.write_text(
                    json.dumps(
                        {
                            "ok": True,
                            "schema": "ordax.static-lod/1",
                            "source": {"triangles": 100000},
                            "levels": [
                                {"level": 0, "requested_ratio": 1.0, "triangles": 100000},
                                {"level": 1, "requested_ratio": 0.6, "triangles": 60000},
                                {"level": 2, "requested_ratio": 0.3, "triangles": 30000},
                                {"level": 3, "requested_ratio": 0.12, "triangles": 12000},
                            ],
                            "safety": {
                                "armatures": 0,
                                "shape_key_meshes": 0,
                                "source_file_modified": False,
                            },
                        }
                    ),
                    encoding="utf-8",
                )
                return ActionResult(True, "command completed", {})

            registry = ActionRegistry(config)
            with patch(
                "ordax_dev_agent.game_asset_lod_actions.find_blender",
                return_value=Path("blender"),
            ), patch(
                "ordax_dev_agent.game_asset_lod_actions._run",
                side_effect=fake_run,
            ):
                result = registry.execute(
                    "game_assets.blender_generate_static_lods",
                    {
                        "project": "game",
                        "blend_file": "asset.blend",
                        "output_blend": "generated/asset_lods.blend",
                        "ratios": [0.6, 0.3, 0.12],
                        "timeout_seconds": 777,
                    },
                )
            self.assertTrue(result.ok, result.summary)
            self.assertEqual([0.6, 0.3, 0.12], result.data["ratios"])
            self.assertFalse(result.data["report"]["safety"]["source_file_modified"])

    def test_helper_safety_failure_is_returned_as_action_failure(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            config = self.make_config(root)
            project = root / "project"
            (project / "character.blend").write_bytes(b"blend")

            def fake_run(command, *, cwd=None, timeout=0, env=None):
                report = Path(command[command.index("--report") + 1])
                report.parent.mkdir(parents=True, exist_ok=True)
                report.write_text(
                    json.dumps(
                        {
                            "ok": False,
                            "error_type": "RuntimeError",
                            "error": "static LOD generation refuses scenes containing armatures",
                        }
                    ),
                    encoding="utf-8",
                )
                return ActionResult(False, "Blender command failed", {})

            registry = ActionRegistry(config)
            with patch(
                "ordax_dev_agent.game_asset_lod_actions.find_blender",
                return_value=Path("blender"),
            ), patch(
                "ordax_dev_agent.game_asset_lod_actions._run",
                side_effect=fake_run,
            ):
                result = registry.execute(
                    "game_assets.blender_generate_static_lods",
                    {
                        "project": "game",
                        "blend_file": "character.blend",
                        "output_blend": "character_lods.blend",
                    },
                )
            self.assertFalse(result.ok)
            self.assertIn("armatures", result.summary)


if __name__ == "__main__":
    unittest.main()
