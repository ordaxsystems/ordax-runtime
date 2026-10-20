"""Device-scoped terminal, sharing project executor without synthetic project."""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from ordax_dev_agent.developer_actions import DeveloperActions
from ordax_dev_agent.models import ActionResult


class Harness(DeveloperActions):
    def __init__(self):
        self.config = object()


class DeviceTerminalExecTests(unittest.TestCase):
    def setUp(self):
        self.actions = Harness()
        self.full = patch(
            "ordax_dev_agent.computer_filesystem_actions.load_computer_access_policy",
            return_value=SimpleNamespace(enabled=True, full_access=True),
        )
        self.full.start()
        self.addCleanup(self.full.stop)

    def test_device_terminal_uses_shared_runner_without_project(self):
        with tempfile.TemporaryDirectory() as root, patch(
            "ordax_dev_agent.developer_actions._run",
            return_value=ActionResult(True, "command completed", {
                "stdout": "OK", "stderr": "", "returncode": 0,
                "command": ["python", "--version"], "timed_out": False,
            }),
        ) as runner:
            result = self.actions.computer_terminal_exec({
                "argv": ["python", "--version"], "cwd": root, "timeout_seconds": 15,
            })
        self.assertTrue(result.ok)
        self.assertNotIn("project", result.data)
        self.assertNotIn("command", result.data)
        self.assertEqual(result.data["stdout"], "OK")
        self.assertEqual(runner.call_args.kwargs["timeout"], 15)

    def test_local_owner_policy_fails_closed(self):
        with patch(
            "ordax_dev_agent.computer_filesystem_actions.load_computer_access_policy",
            return_value=SimpleNamespace(enabled=True, full_access=False),
        ), patch("ordax_dev_agent.developer_actions._run") as runner:
            result = self.actions.computer_terminal_exec({"argv": ["echo", "NEVER"]})
            self.assertFalse(result.ok)
            self.assertEqual(result.data["error_code"], "local_full_access_required")
            runner.assert_not_called()

    def test_reject_relative_cwd_before_exec(self):
        with patch("ordax_dev_agent.developer_actions._run") as runner:
            result = self.actions.computer_terminal_exec({"argv": ["echo", "NEVER"], "cwd": "relative"})
            self.assertFalse(result.ok)
            runner.assert_not_called()

    def test_shell_command_and_argv_are_mutually_exclusive(self):
        with patch("ordax_dev_agent.developer_actions._run") as runner:
            result = self.actions.computer_terminal_exec({"argv": ["echo"], "command": "test", "shell": True})
            self.assertFalse(result.ok)
            runner.assert_not_called()

    def test_runner_launch_error_has_sanitized_receipt(self):
        with patch("ordax_dev_agent.developer_actions._run", side_effect=OSError("secret-path-in-error")):
            result = self.actions.computer_terminal_exec({"argv": ["missing-command"]})
        self.assertFalse(result.ok)
        self.assertEqual(result.data["error_code"], "terminal_launch_failed")
        self.assertNotIn("secret-path-in-error", str(result.data))


if __name__ == "__main__":
    unittest.main()
