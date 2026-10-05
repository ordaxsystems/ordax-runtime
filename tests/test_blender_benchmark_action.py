import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ordax_dev_agent.actions import ActionRegistry
from ordax_dev_agent.config import AgentConfig
from ordax_dev_agent.models import ActionResult


class BlenderBenchmarkActionTests(unittest.TestCase):
    def make_config(self, root: Path) -> AgentConfig:
        project_root = (root / "project").resolve()
        project_root.mkdir(parents=True, exist_ok=True)
        return AgentConfig(
            agent_name="benchmark-test",
            poll_seconds=5.0,
            state_dir=root / "state",
            agent_repo_path=root / "managed",
            hordax_path=root / "hordax",
            bridge_path=root / "bridge",
            projects={
                "bench": {
                    "path": str(project_root),
                    "apps": ["blender"],
                    "blender": {},
                }
            },
            default_project="bench",
        )

    def valid_report(self) -> dict:
        return {
            "modeling_dispatch": {
                "transform_positive": True,
                "transform_negative_detected": True,
                "create_positive": True,
                "create_duplicate_detected": True,
                "modifier_positive": True,
                "modifier_duplicate_detected": True,
                "dispatcher_journaled": True,
            }
        }

    def test_action_is_registered(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            registry = ActionRegistry(self.make_config(Path(raw)))
            self.assertIn("blender.benchmark", registry.names)

    def test_rejects_unknown_fields_before_execution(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            registry = ActionRegistry(self.make_config(Path(raw)))
            with patch("ordax_dev_agent.blender_actions._run") as run:
                result = registry.execute(
                    "blender.benchmark",
                    {"script": "anything.py"},
                )
            self.assertFalse(result.ok)
            self.assertIn("unsupported field", result.summary)
            run.assert_not_called()

    def test_rejects_invalid_timeout_before_execution(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            registry = ActionRegistry(self.make_config(Path(raw)))
            with patch("ordax_dev_agent.blender_actions._run") as run:
                result = registry.execute(
                    "blender.benchmark",
                    {"timeout_seconds": 59},
                )
            self.assertFalse(result.ok)
            self.assertIn("between 60 and 3600", result.summary)
            run.assert_not_called()

    def test_runs_fixed_benchmark_and_returns_durable_report(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            registry = ActionRegistry(self.make_config(root))
            report = self.valid_report()

            def fake_run(command, *, cwd=None, timeout=1800):
                report_index = command.index("--report-file") + 1
                report_path = Path(command[report_index])
                report_path.parent.mkdir(parents=True, exist_ok=True)
                report_path.write_text(
                    json.dumps(report),
                    encoding="utf-8",
                )
                self.assertIn("blender_benchmark.py", command[1])
                self.assertEqual(600, timeout)
                self.assertEqual(command[1], str(Path(command[1]).resolve()))
                return ActionResult(
                    True,
                    "command completed",
                    {
                        "returncode": 0,
                        "stdout": "",
                        "stderr": "",
                        "command": command,
                        "timed_out": False,
                    },
                )

            with (
                patch(
                    "ordax_dev_agent.blender_actions.find_blender",
                    return_value=Path("C:/Program Files/Blender/blender.exe"),
                ),
                patch(
                    "ordax_dev_agent.blender_actions._run",
                    side_effect=fake_run,
                ) as run,
            ):
                result = registry.execute(
                    "blender.benchmark",
                    {"timeout_seconds": 600},
                )

            self.assertTrue(result.ok)
            self.assertEqual("OrdaX BlenderBench passed", result.summary)
            self.assertEqual(report, result.data["benchmark"])
            self.assertTrue(Path(result.data["artifact"]).is_file())
            self.assertTrue(
                Path(result.data["artifact"]).is_relative_to(
                    (root / "state").resolve()
                )
            )
            run.assert_called_once()

    def test_missing_required_modeling_evidence_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            registry = ActionRegistry(self.make_config(root))
            report = self.valid_report()
            report["modeling_dispatch"]["modifier_positive"] = False

            def fake_run(command, *, cwd=None, timeout=1800):
                report_path = Path(
                    command[command.index("--report-file") + 1]
                )
                report_path.parent.mkdir(parents=True, exist_ok=True)
                report_path.write_text(
                    json.dumps(report),
                    encoding="utf-8",
                )
                return ActionResult(
                    True,
                    "command completed",
                    {
                        "returncode": 0,
                        "stdout": "",
                        "stderr": "",
                        "command": command,
                        "timed_out": False,
                    },
                )

            with (
                patch(
                    "ordax_dev_agent.blender_actions.find_blender",
                    return_value=Path("C:/Program Files/Blender/blender.exe"),
                ),
                patch(
                    "ordax_dev_agent.blender_actions._run",
                    side_effect=fake_run,
                ),
            ):
                result = registry.execute("blender.benchmark", {})

            self.assertFalse(result.ok)
            self.assertIn("missing required staged-modeling evidence", result.summary)
            self.assertEqual(
                ["modifier_positive"],
                result.data["missing_evidence"],
            )


if __name__ == "__main__":
    unittest.main()
