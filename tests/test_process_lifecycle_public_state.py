"""Regression: a finished supervised child must not be reported running."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

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

    def test_terminal_receipt_skips_pid_queries_and_releases_ephemeral_proof(self):
        supervisor = PersistentProcessActions()
        process_id = "finished-process"
        handle = Mock()
        handle.poll.return_value = 0
        supervisor._persistent_process_handles = {process_id: handle}
        supervisor._persistent_process_launch_tokens = {process_id: "old-token"}
        with patch.object(supervisor, "_owned", side_effect=AssertionError("historical ownership query")), patch.object(
            supervisor, "_pid_running", side_effect=AssertionError("historical PID query")
        ):
            result = supervisor._public_process_state({
                "process_id": process_id, "manager_pid": 1234,
                "state": "exited", "token": "old-token", "returncode": 0,
            })
        self.assertFalse(result["running"])
        self.assertFalse(result["ownership_valid"])
        self.assertNotIn("token", result)
        self.assertNotIn(process_id, supervisor._persistent_process_handles)
        self.assertNotIn(process_id, supervisor._persistent_process_launch_tokens)

    def test_dead_manager_cannot_be_owned_by_stale_launch_token(self):
        supervisor = PersistentProcessActions()
        process_id = "dead-manager"
        supervisor._persistent_process_launch_tokens = {process_id: "old-token"}
        state = {"process_id": process_id, "manager_pid": 1234,
                 "manager_ready_at_unix": 1002.0, "started_at_unix": 1000.0,
                 "state": "running", "token": "old-token"}
        with patch.object(supervisor, "_pid_running", return_value=False), patch.object(
            supervisor, "_commandline", side_effect=AssertionError("dead PID probe")
        ):
            result = supervisor._public_process_state(state)
        self.assertEqual(result["state"], "stopped")
        self.assertFalse(result["running"])
        self.assertFalse(result["ownership_valid"])
        self.assertNotIn(process_id, supervisor._persistent_process_launch_tokens)

    def test_recycled_manager_cannot_be_owned_by_stale_launch_token(self):
        supervisor = PersistentProcessActions()
        process_id = "recycled-manager"
        supervisor._persistent_process_launch_tokens = {process_id: "old-token"}
        state = {"process_id": process_id, "manager_pid": 1234,
                 "manager_ready_at_unix": 1002.0, "started_at_unix": 1000.0,
                 "state": "running", "token": "old-token"}
        with patch.object(supervisor, "_pid_running", return_value=True), patch.object(
            supervisor, "_pid_created_at_unix", return_value=2200.0
        ), patch.object(supervisor, "_commandline", side_effect=AssertionError("recycled PID probe")):
            result = supervisor._public_process_state(state)
        self.assertEqual(result["state"], "stale")
        self.assertFalse(result["running"])
        self.assertFalse(result["ownership_valid"])

    def test_running_child_remains_running_when_owned(self):
        supervisor = PersistentProcessActions()
        state = {"process_id": "test-process", "manager_pid": 1234, "state": "running"}
        with patch.object(supervisor, "_owned", return_value=True), patch.object(
            supervisor, "_pid_running", return_value=True
        ):
            result = supervisor._public_process_state(state)
        self.assertEqual(result["state"], "running")
        self.assertTrue(result["running"])

    def test_public_process_state_does_not_expose_supervisor_token(self):
        supervisor = PersistentProcessActions()
        state = {
            "process_id": "test-process",
            "manager_pid": 1234,
            "token": "internal-ownership-proof",
            "state": "running",
        }
        with patch.object(supervisor, "_owned", return_value=True), patch.object(
            supervisor, "_pid_running", return_value=True
        ):
            result = supervisor._public_process_state(state)

        self.assertNotIn("token", result)
        self.assertEqual("internal-ownership-proof", state["token"])

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
