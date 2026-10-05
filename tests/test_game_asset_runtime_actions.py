import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ordax_dev_agent.actions import ActionRegistry
from ordax_dev_agent.config import AgentConfig
from ordax_dev_agent.models import ActionResult


class GameAssetRuntimeActionsTests(unittest.TestCase):
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

    def test_registry_exposes_runtime_audit(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            registry = ActionRegistry(self.make_config(Path(raw)))
            status = registry.execute("agent.status", {})
            self.assertTrue(status.ok)
            self.assertIn("game_assets.blender_runtime_audit", status.data["actions"])

    def test_runtime_audit_passes_when_metrics_fit_limits(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            config = self.make_config(root)
            blend = root / "project" / "asset.blend"
            blend.write_bytes(b"blend")

            def fake_run(command, *, cwd=None, timeout=0, env=None):
                report = Path(command[command.index("--report") + 1])
                report.parent.mkdir(parents=True, exist_ok=True)
                report.write_text(
                    json.dumps(
                        {
                            "ok": True,
                            "metrics": {
                                "triangles": 18000,
                                "material_slots": 3,
                                "max_texture_dimension": 2048,
                                "max_vertex_influences": 4,
                                "bones": 62,
                                "actions": 8,
                                "lod_safe_static_candidate": False,
                            },
                            "meshes": [],
                            "images": [],
                        }
                    ),
                    encoding="utf-8",
                )
                return ActionResult(True, "command completed", {})

            registry = ActionRegistry(config)
            with patch(
                "ordax_dev_agent.game_asset_runtime_actions.find_blender",
                return_value=Path("blender"),
            ), patch(
                "ordax_dev_agent.game_asset_runtime_actions._run",
                side_effect=fake_run,
            ):
                result = registry.execute(
                    "game_assets.blender_runtime_audit",
                    {
                        "project": "game",
                        "blend_file": "asset.blend",
                        "max_triangles": 25000,
                        "max_material_slots": 4,
                        "max_texture_dimension": 2048,
                        "max_vertex_influences": 4,
                        "max_bones": 100,
                        "max_actions": 10,
                    },
                )
            self.assertTrue(result.ok, result.summary)
            self.assertEqual([], result.data["violations"])

    def test_runtime_audit_returns_all_budget_violations(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            config = self.make_config(root)
            blend = root / "project" / "asset.blend"
            blend.write_bytes(b"blend")

            def fake_run(command, *, cwd=None, timeout=0, env=None):
                report = Path(command[command.index("--report") + 1])
                report.parent.mkdir(parents=True, exist_ok=True)
                report.write_text(
                    json.dumps(
                        {
                            "ok": True,
                            "metrics": {
                                "triangles": 125000,
                                "material_slots": 12,
                                "max_texture_dimension": 8192,
                                "max_vertex_influences": 8,
                                "bones": 240,
                                "actions": 40,
                                "lod_safe_static_candidate": False,
                            },
                        }
                    ),
                    encoding="utf-8",
                )
                return ActionResult(True, "command completed", {})

            registry = ActionRegistry(config)
            with patch(
                "ordax_dev_agent.game_asset_runtime_actions.find_blender",
                return_value=Path("blender"),
            ), patch(
                "ordax_dev_agent.game_asset_runtime_actions._run",
                side_effect=fake_run,
            ):
                result = registry.execute(
                    "game_assets.blender_runtime_audit",
                    {
                        "project": "game",
                        "blend_file": "asset.blend",
                        "max_triangles": 50000,
                        "max_material_slots": 4,
                        "max_texture_dimension": 4096,
                        "max_vertex_influences": 4,
                        "max_bones": 128,
                        "max_actions": 16,
                    },
                )
            self.assertFalse(result.ok)
            self.assertEqual(6, len(result.data["violations"]))
            self.assertEqual(
                {
                    "triangles",
                    "material_slots",
                    "max_texture_dimension",
                    "max_vertex_influences",
                    "bones",
                    "actions",
                },
                {item["metric"] for item in result.data["violations"]},
            )


if __name__ == "__main__":
    unittest.main()
