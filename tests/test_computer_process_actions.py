from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ordax_dev_agent.actions import ActionRegistry
from ordax_dev_agent.config import AgentConfig


class ComputerProcessActionsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        project = root / "project"
        project.mkdir()
        self.registry = ActionRegistry(
            AgentConfig(
                agent_name="test",
                poll_seconds=0.25,
                state_dir=root / "state",
                agent_repo_path=root / "agent",
                hordax_path=root / "hordax",
                bridge_path=root / "bridge",
                projects={"demo": {"path": str(project), "apps": []}},
                default_project="demo",
            )
        )

    def test_process_list_is_device_scoped_filtered_and_bounded(self):
        snapshot = [
            {"pid": 10, "parent_pid": 1, "name": "python.exe", "executable": "C:/Python/python.exe"},
            {"pid": 11, "parent_pid": 1, "name": "node.exe", "executable": "C:/Node/node.exe"},
        ]
        with patch.object(self.registry, "_system_process_snapshot", return_value=snapshot):
            result = self.registry.execute(
                "computer.processes",
                {"query": "python", "max_items": 1},
            )

        self.assertTrue(result.ok, result.summary)
        self.assertEqual(1, result.data["total_matches"])
        self.assertEqual("python.exe", result.data["processes"][0]["name"])

    def test_process_list_does_not_collect_or_return_command_lines(self):
        normalized = self.registry._normalize_process_item({
            "ProcessId": 10,
            "ParentProcessId": 1,
            "Name": "python.exe",
            "ExecutablePath": "C:/Python/python.exe",
            "CommandLine": "python secret-or-expensive-arguments.py",
        })
        self.assertNotIn("command_line", normalized)

        source = (Path(__file__).resolve().parents[1] / "ordax_dev_agent" / "computer_control_actions.py").read_text(encoding="utf-8")
        self.assertIn("CreateToolhelp32Snapshot", source)
        self.assertIn("QueryFullProcessImageNameW", source)
        self.assertNotIn("Get-CimInstance Win32_Process", source)
        self.assertNotIn("CommandLine", source)

    def test_terminate_requires_current_name_to_match_pid(self):
        with patch.object(
            self.registry,
            "_system_process_by_pid",
            return_value={"pid": 424242, "name": "node.exe"},
        ):
            result = self.registry.execute(
                "computer.terminate_process",
                {"pid": 424242, "expected_name": "python.exe"},
            )

        self.assertFalse(result.ok)
        self.assertIn("identity changed", result.summary)

    def test_terminate_refuses_protected_process(self):
        with patch.object(
            self.registry,
            "_system_process_by_pid",
            return_value={"pid": 424242, "name": "system"},
        ):
            result = self.registry.execute(
                "computer.terminate_process",
                {"pid": 424242, "expected_name": "system"},
            )

        self.assertFalse(result.ok)
        self.assertIn("protected process", result.summary)

    def test_terminate_posix_requests_signal_after_identity_check(self):
        if __import__("os").name == "nt":
            self.skipTest("POSIX-only termination transport assertion")
        with (
            patch.object(
                self.registry,
                "_system_process_by_pid",
                return_value={"pid": 424242, "name": "python"},
            ),
            patch("ordax_dev_agent.computer_control_actions.os.kill") as kill,
        ):
            result = self.registry.execute(
                "computer.terminate_process",
                {"pid": 424242, "expected_name": "python", "force": False},
            )

        self.assertTrue(result.ok, result.summary)
        kill.assert_called_once()


if __name__ == "__main__":
    unittest.main()
