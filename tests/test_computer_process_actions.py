from __future__ import annotations

import tempfile
import unittest
import sys
import json
from dataclasses import replace
from types import SimpleNamespace
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

    def _device_registry(self):
        return ActionRegistry(replace(
            self.registry.config, device_id="24630b00-e9c0-4d93-b38a-2367048cd9d1",
        ))

    def test_device_process_namespace_is_separate_and_owner_scoped(self):
        registry = self._device_registry()
        with patch(
            "ordax_dev_agent.computer_filesystem_actions.load_computer_access_policy",
            return_value=SimpleNamespace(enabled=True, full_access=True),
        ):
            scope = registry._device_process_scope()
            path = registry._process_state_path(scope, "66666666-6666-4666-8666-666666666666")
            project = registry._project({"project": "demo"})
            self.assertNotEqual(path.parent, registry._process_root(project))
            path.parent.mkdir(parents=True, exist_ok=True)
            state = {
                "process_id": "66666666-6666-4666-8666-666666666666",
                "scope_kind": "device",
                "device_id": scope.device_id,
                "state": "stopped",
                "token": "test-token",
            }
            path.write_text(json.dumps(state), encoding="utf-8")
            listing = registry.execute("computer.process_list", {})
            self.assertTrue(listing.ok, listing.summary)
            self.assertEqual(listing.data["device_id"], scope.device_id)
            self.assertEqual(len(listing.data["processes"]), 1)
            with self.assertRaises(ValueError):
                registry._load_process_state(project, state["process_id"])
            state["device_id"] = "77777777-7777-4777-8777-777777777777"
            path.write_text(json.dumps(state), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "identity mismatch"):
                registry._load_process_state(scope, state["process_id"])

    def test_device_process_requires_full_access_and_valid_device_identity(self):
        registry = self._device_registry()
        for policy in (SimpleNamespace(enabled=False, full_access=True),
                       SimpleNamespace(enabled=True, full_access=False)):
            with patch("ordax_dev_agent.computer_filesystem_actions.load_computer_access_policy", return_value=policy):
                denied = registry.execute("computer.process_list", {})
                self.assertFalse(denied.ok)
        with patch("ordax_dev_agent.computer_filesystem_actions.load_computer_access_policy",
                   return_value=SimpleNamespace(enabled=True, full_access=True)):
            invalid = ActionRegistry(replace(registry.config, device_id=None))
            result = invalid.execute("computer.process_list", {})
            self.assertFalse(result.ok)

    def test_supervised_device_process_start_status_logs_and_stop(self):
        registry = self._device_registry()
        process_id = None
        with patch("ordax_dev_agent.computer_filesystem_actions.load_computer_access_policy",
                   return_value=SimpleNamespace(enabled=True, full_access=True)):
            try:
                started = registry.execute("computer.process_start", {
                    "argv": [sys.executable, "-u", "-c",
                             "import time; print('ORDAX_DEVICE_READY', flush=True); time.sleep(25)"],
                    "wait_seconds": 1.0,
                })
                self.assertTrue(started.ok, started.summary)
                process_id = started.data["process_id"]
                self.assertEqual(started.data["device_id"], registry.config.device_id)
                self.assertNotIn("project", started.data)
                status = registry.execute("computer.process_status", {"process_id": process_id})
                self.assertTrue(status.ok, status.summary)
                self.assertTrue(status.data["ownership_valid"])
                logs = registry.execute("computer.process_logs", {"process_id": process_id})
                self.assertTrue(logs.ok, logs.summary)
                self.assertIn("ORDAX_DEVICE_READY", logs.data["tail"])
                self.assertFalse(registry.execute("process.status", {
                    "project": "demo", "process_id": process_id,
                }).ok)
            finally:
                if process_id:
                    stopped = registry.execute("computer.process_stop", {"process_id": process_id})
                    self.assertTrue(stopped.ok, stopped.summary)

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
