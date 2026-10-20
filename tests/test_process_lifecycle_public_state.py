"""Regression: a finished supervised child must not be reported running."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ordax_dev_agent.persistent_process_actions import PersistentProcessActions


class ProcessPublicStateTests(unittest.TestCase):
    def test_exited_child_is_not_running_even_when_manager_owned(self):
        supervisor = PersistentProcessActions()
        state = {"process_id": "test-process", "manager_pid": 1234, "state": "exited", "returncode": 0}
        with patch.object(supervisor, "_owned", return_value=True), patch.object(
            supervisor, "_pid_running", return_value=True
        ):
            result = supervisor._public_process_state(state)
        self.assertEqual(result["state"], "exited")
        self.assertFalse(result["running"])
        self.assertEqual(result["returncode"], 0)

    def test_running_child_remains_running_when_owned(self):
        supervisor = PersistentProcessActions()
        state = {"process_id": "test-process", "manager_pid": 1234, "state": "running"}
        with patch.object(supervisor, "_owned", return_value=True), patch.object(
            supervisor, "_pid_running", return_value=True
        ):
            result = supervisor._public_process_state(state)
        self.assertEqual(result["state"], "running")
        self.assertTrue(result["running"])

    def test_failed_child_is_not_running(self):
        supervisor = PersistentProcessActions()
        state = {"process_id": "test-process", "manager_pid": 1234, "state": "failed"}
        with patch.object(supervisor, "_owned", return_value=True), patch.object(
            supervisor, "_pid_running", return_value=True
        ):
            result = supervisor._public_process_state(state)
        self.assertFalse(result["running"])


if __name__ == "__main__":
    unittest.main()
