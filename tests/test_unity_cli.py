import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from ordax_dev_agent.unity_cli import pipeline_command, run_unity_cli


class UnityCliTests(unittest.TestCase):
    @patch("ordax_dev_agent.unity_cli.find_unity_cli")
    @patch("ordax_dev_agent.unity_cli.subprocess.run")
    def test_run_uses_structured_argv_and_parses_json(self, run, find_cli):
        find_cli.return_value = Path("C:/tools/unity.exe")
        run.return_value = SimpleNamespace(
            returncode=0,
            stdout=json.dumps({"success": True, "data": {"ok": 1}}),
            stderr="",
        )

        result = run_unity_cli(
            ["status", "--format", "json"],
            cwd=Path("C:/project"),
        )

        self.assertTrue(result.ok)
        self.assertEqual({"success": True, "data": {"ok": 1}}, result.data["json"])
        self.assertFalse(run.call_args.kwargs["shell"])
        self.assertEqual(str(Path("C:/project")), run.call_args.kwargs["cwd"])
        self.assertEqual(
            "1",
            run.call_args.kwargs["env"]["UNITY_NO_UPDATE_CHECK"],
        )

    def test_pipeline_command_rejects_project_override(self):
        with tempfile.TemporaryDirectory() as raw:
            result = pipeline_command(
                Path(raw),
                "scene_summary",
                ["--project-path", "C:/other"],
            )
        self.assertFalse(result.ok)
        self.assertIn("managed by OrdaX", result.summary)

    def test_pipeline_command_rejects_equals_project_override(self):
        with tempfile.TemporaryDirectory() as raw:
            result = pipeline_command(
                Path(raw),
                "scene_summary",
                ["--project-path=C:/other"],
            )
        self.assertFalse(result.ok)
        self.assertIn("managed by OrdaX", result.summary)

    def test_pipeline_command_rejects_control_characters(self):
        with tempfile.TemporaryDirectory() as raw:
            result = pipeline_command(
                Path(raw),
                "scene_summary",
                ["bad\nargument"],
            )
        self.assertFalse(result.ok)
        self.assertIn("control characters", result.summary)

    def test_pipeline_command_rejects_invalid_name(self):
        with tempfile.TemporaryDirectory() as raw:
            result = pipeline_command(
                Path(raw),
                "scene summary && whoami",
                [],
            )
        self.assertFalse(result.ok)
        self.assertIn("Invalid", result.summary)


if __name__ == "__main__":
    unittest.main()
