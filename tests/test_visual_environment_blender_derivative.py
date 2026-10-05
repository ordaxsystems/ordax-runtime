import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ordax_dev_agent.actions import ActionRegistry
from ordax_dev_agent.config import AgentConfig
from ordax_dev_agent.models import ActionResult


class VisualEnvironmentBlenderDerivativeTests(unittest.TestCase):
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
                "salvador": {
                    "path": str(project),
                    "apps": ["blender", "unity"],
                    "allowed_branches": [],
                    "blender": {"blend_file": "salvador.blend"},
                }
            },
            default_project="salvador",
        )

    def test_write_can_materialize_source_preserving_blender_derivative(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            project = root / "project"
            project.mkdir(parents=True, exist_ok=True)
            source = project / "salvador.blend"
            source.write_bytes(b"BLENDER-SOURCE")
            source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
            registry = ActionRegistry(self.make_config(root))

            def fake_run(command, *, cwd=None, timeout=0, env=None):
                self.assertIn("--disable-autoexec", command)
                request_path = Path(command[-1])
                request = json.loads(request_path.read_text(encoding="utf-8"))
                environment = request["environment"]
                output = Path(request["output_path"])
                report = Path(request["report_path"])
                output.write_bytes(b"BLENDER-DERIVATIVE")
                report.write_text(
                    json.dumps(
                        {
                            "ok": True,
                            "schema": "ordax.blender-environment-materialization/2",
                            "audit": {
                                "environment_metadata_valid": True,
                                "ocean": {
                                    "present": True,
                                    "modifier_present": True,
                                    "profile": environment["ocean"]["spectrum"]["profile"],
                                    "spectrum": environment["ocean"]["spectrum"],
                                },
                            },
                            "applied": {"ocean": {"enabled": True}},
                        }
                    ),
                    encoding="utf-8",
                )
                return ActionResult(True, "command completed", {"command": command})

            with patch(
                "ordax_dev_agent.visual_environment_actions.find_blender",
                return_value=Path("C:/Blender/blender.exe"),
            ), patch(
                "ordax_dev_agent.visual_environment_actions._run",
                side_effect=fake_run,
            ):
                result = registry.execute(
                    "visual.environment_write",
                    {
                        "project": "salvador",
                        "environment": {
                            "ocean": {
                                "spectrum": {
                                    "profile": "bay",
                                    "wind_wave_height_m": 0.31,
                                    "short_wave_strength": 0.66,
                                }
                            }
                        },
                        "output_path": "ordax/salvador-environment.json",
                        "blender": {
                            "output_path": "blender/salvador_environment_preview.blend",
                            "report_path": "ordax/salvador-blender-environment-report.json",
                        },
                    },
                )

            self.assertTrue(result.ok, result.summary)
            self.assertEqual(source_hash, hashlib.sha256(source.read_bytes()).hexdigest())
            self.assertTrue(result.data["blender"]["source_preserved"])
            self.assertTrue((project / "blender" / "salvador_environment_preview.blend").is_file())
            self.assertTrue((project / "ordax" / "salvador-blender-environment-report.json").is_file())
            self.assertEqual("bay", result.data["blender"]["audit"]["ocean"]["profile"])
            self.assertEqual(
                0.31,
                result.data["environment"]["ocean"]["spectrum"]["wind_wave_height_m"],
            )

    def test_blender_block_rejects_unknown_fields(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            project = root / "project"
            project.mkdir(parents=True, exist_ok=True)
            (project / "salvador.blend").write_bytes(b"BLENDER-SOURCE")
            registry = ActionRegistry(self.make_config(root))
            with patch(
                "ordax_dev_agent.visual_environment_actions.find_blender",
                return_value=Path("C:/Blender/blender.exe"),
            ):
                result = registry.execute(
                    "visual.environment_write",
                    {
                        "project": "salvador",
                        "blender": {"magic_ocean": True},
                    },
                )
            self.assertFalse(result.ok)
            self.assertIn("unsupported blender field", result.summary)

    def test_refuses_to_overwrite_source_blend(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            project = root / "project"
            project.mkdir(parents=True, exist_ok=True)
            (project / "salvador.blend").write_bytes(b"BLENDER-SOURCE")
            registry = ActionRegistry(self.make_config(root))
            with patch(
                "ordax_dev_agent.visual_environment_actions.find_blender",
                return_value=Path("C:/Blender/blender.exe"),
            ):
                result = registry.execute(
                    "visual.environment_write",
                    {
                        "project": "salvador",
                        "blender": {
                            "blend_file": "salvador.blend",
                            "output_path": "salvador.blend",
                        },
                    },
                )
            self.assertFalse(result.ok)
            self.assertIn("must differ", result.summary)


if __name__ == "__main__":
    unittest.main()
