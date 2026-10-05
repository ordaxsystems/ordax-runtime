from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from ordax_dev_agent.actions import ActionRegistry
from ordax_dev_agent.config import AgentConfig
from ordax_dev_agent.persistent_process_runtime import atomic_json


class PersistentProcessActionsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.project = self.root / "project"
        self.project.mkdir()
        self.registry = ActionRegistry(
            AgentConfig(
                agent_name="test",
                poll_seconds=0.25,
                state_dir=self.root / "state",
                agent_repo_path=self.root / "agent",
                hordax_path=self.root / "hordax",
                bridge_path=self.root / "bridge",
                projects={"demo": {"path": str(self.project), "apps": []}},
                default_project="demo",
            )
        )
        self.process_id = None

    def tearDown(self):
        if self.process_id:
            try:
                self.registry.execute(
                    "process.stop",
                    {"project": "demo", "process_id": self.process_id},
                )
            except Exception:
                pass

    def test_atomic_json_retries_transient_replace_error(self):
        path = self.root / "atomic-state.json"
        original_replace = os.replace
        attempts = 0

        def flaky_replace(source, destination):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise PermissionError(32, "sharing violation")
            return original_replace(source, destination)

        with patch(
            "ordax_dev_agent.persistent_process_runtime.os.replace",
            side_effect=flaky_replace,
        ):
            atomic_json(path, {"state": "running"})

        self.assertEqual({"state": "running"}, json.loads(path.read_text(encoding="utf-8")))
        self.assertEqual(2, attempts)

    def test_process_state_read_retries_atomic_replace_race(self):
        project = self.registry._project({"project": "demo"})
        process_id = "66666666-6666-4666-8666-666666666666"
        state_path = self.registry._process_state_path(project, process_id)
        state_path.parent.mkdir(parents=True, exist_ok=True)
        expected = {
            "process_id": process_id,
            "project": "demo",
            "state": "running",
            "token": "owned-token",
        }
        state_path.write_text(json.dumps(expected), encoding="utf-8")

        original_read_text = Path.read_text
        attempts = 0

        def flaky_read_text(path, *args, **kwargs):
            nonlocal attempts
            if path == state_path and attempts == 0:
                attempts += 1
                raise PermissionError(32, "sharing violation")
            attempts += 1
            return original_read_text(path, *args, **kwargs)

        with patch.object(Path, "read_text", new=flaky_read_text):
            loaded = self.registry._load_process_state(project, process_id)

        self.assertEqual(expected, loaded)
        self.assertGreaterEqual(attempts, 2)

    def test_start_status_logs_list_and_stop(self):
        started = self.registry.execute(
            "process.start",
            {
                "project": "demo",
                "cwd": ".",
                "argv": [
                    sys.executable,
                    "-u",
                    "-c",
                    "import time; print('ORDAX_PROCESS_READY', flush=True); time.sleep(30)",
                ],
                "env": {"ORDAX_TEST_VALUE": "1"},
                "wait_seconds": 1.5,
            },
        )
        self.assertTrue(started.ok, started.summary)
        self.process_id = started.data["process_id"]
        self.assertTrue(started.data["ownership_valid"])
        self.assertTrue(started.data["running"])

        status = self.registry.execute(
            "process.status",
            {"project": "demo", "process_id": self.process_id},
        )
        self.assertTrue(status.ok)
        self.assertTrue(status.data["running"])
        self.assertTrue(status.data["ownership_valid"])

        listing = self.registry.execute("process.list", {"project": "demo"})
        self.assertTrue(listing.ok)
        self.assertIn(
            self.process_id,
            [item["process_id"] for item in listing.data["processes"]],
        )

        tail = ""
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            logs = self.registry.execute(
                "process.logs",
                {
                    "project": "demo",
                    "process_id": self.process_id,
                    "max_bytes": 32768,
                },
            )
            self.assertTrue(logs.ok)
            tail = logs.data["tail"]
            if "ORDAX_PROCESS_READY" in tail:
                break
            time.sleep(0.1)
        self.assertIn("ORDAX_PROCESS_READY", tail)

        stopped = self.registry.execute(
            "process.stop",
            {"project": "demo", "process_id": self.process_id},
        )
        self.assertTrue(stopped.ok, stopped.summary)
        self.process_id = None

        status = self.registry.execute(
            "process.status",
            {"project": "demo", "process_id": started.data["process_id"]},
        )
        self.assertTrue(status.ok)
        self.assertFalse(status.data["running"])
        self.assertFalse(status.data["ownership_valid"])

    def test_write_stdin_reaches_managed_child(self):
        started = self.registry.execute(
            "process.start",
            {
                "project": "demo",
                "cwd": ".",
                "argv": [
                    sys.executable,
                    "-u",
                    "-c",
                    (
                        "import sys,time; "
                        "print('ORDAX_STDIN_READY', flush=True); "
                        "value=sys.stdin.readline().rstrip('\\r\\n'); "
                        "print('ORDAX_STDIN_GOT:'+value, flush=True); "
                        "time.sleep(5)"
                    ),
                ],
                "wait_seconds": 1.5,
            },
        )
        self.assertTrue(started.ok, started.summary)
        self.process_id = started.data["process_id"]

        written = self.registry.execute(
            "process.write_stdin",
            {
                "project": "demo",
                "process_id": self.process_id,
                "text": "hello-ordax",
                "newline": True,
            },
        )
        self.assertTrue(written.ok, written.summary)

        tail = ""
        deadline = time.monotonic() + 6.0
        while time.monotonic() < deadline:
            logs = self.registry.execute(
                "process.logs",
                {
                    "project": "demo",
                    "process_id": self.process_id,
                    "max_bytes": 32768,
                },
            )
            self.assertTrue(logs.ok, logs.summary)
            tail = logs.data["tail"]
            if "ORDAX_STDIN_GOT:hello-ordax" in tail:
                break
            time.sleep(0.1)
        self.assertIn("ORDAX_STDIN_GOT:hello-ordax", tail)

    def test_live_managed_handle_is_authoritative_before_windows_pid_probe(self):
        process_id = "11111111-1111-4111-8111-111111111111"
        handle = SimpleNamespace(pid=4242, poll=lambda: None)
        self.registry._persistent_process_handles = {process_id: handle}
        state = {
            "process_id": process_id,
            "project": "demo",
            "manager_pid": 4242,
            "token": "owned-token",
            "state": "starting",
        }

        with patch.object(self.registry, "_pid_running", return_value=False):
            public = self.registry._public_process_state(state)

        self.assertTrue(public["running"])
        self.assertTrue(public["ownership_valid"])
        self.assertEqual("running", public["state"])

    def test_live_managed_handle_is_authoritative_before_manager_pid_is_persisted(self):
        process_id = "22222222-2222-4222-8222-222222222222"
        handle = SimpleNamespace(pid=5252, poll=lambda: None)
        self.registry._persistent_process_handles = {process_id: handle}
        state = {
            "process_id": process_id,
            "project": "demo",
            "token": "owned-token",
            "state": "starting",
        }

        with patch.object(self.registry, "_pid_running", return_value=False):
            public = self.registry._public_process_state(state)

        self.assertTrue(public["running"])
        self.assertTrue(public["ownership_valid"])
        self.assertEqual("running", public["state"])
        self.assertEqual(5252, public["manager_pid"])

    def test_persisted_manager_pid_must_match_live_managed_handle(self):
        process_id = "33333333-3333-4333-8333-333333333333"
        handle = SimpleNamespace(pid=6262, poll=lambda: None)
        self.registry._persistent_process_handles = {process_id: handle}
        state = {
            "process_id": process_id,
            "project": "demo",
            "manager_pid": 7272,
            "token": "owned-token",
            "state": "starting",
        }

        with patch.object(self.registry, "_pid_running", return_value=False):
            public = self.registry._public_process_state(state)

        self.assertFalse(public["running"])
        self.assertFalse(public["ownership_valid"])
        self.assertEqual("stopped", public["state"])
        self.assertEqual(7272, public["manager_pid"])

    def test_live_windows_launcher_mismatch_uses_ephemeral_launch_token(self):
        process_id = "44444444-4444-4444-8444-444444444444"
        handle = SimpleNamespace(pid=8181, poll=lambda: None)
        self.registry._persistent_process_handles = {process_id: handle}
        self.registry._persistent_process_launch_tokens = {process_id: "owned-token"}
        state = {
            "process_id": process_id,
            "project": "demo",
            "manager_pid": 9191,
            "manager_ready_at_unix": 123.0,
            "token": "owned-token",
            "state": "running",
        }

        with (
            patch.object(self.registry, "_pid_running", return_value=True),
            patch.object(self.registry, "_commandline") as commandline,
        ):
            public = self.registry._public_process_state(state)

        self.assertTrue(public["running"])
        self.assertTrue(public["ownership_valid"])
        commandline.assert_not_called()

    def test_exited_windows_launcher_yields_to_runtime_owned_pid(self):
        process_id = "55555555-5555-4555-8555-555555555555"
        handle = SimpleNamespace(pid=8181, poll=lambda: 0)
        self.registry._persistent_process_handles = {process_id: handle}
        state = {
            "process_id": process_id,
            "project": "demo",
            "manager_pid": 9191,
            "token": "owned-token",
            "state": "running",
        }

        with (
            patch.object(self.registry, "_pid_running", return_value=True),
            patch.object(
                self.registry,
                "_commandline",
                return_value="python persistent_process_runtime.py --token owned-token",
            ),
        ):
            public = self.registry._public_process_state(state)

        self.assertTrue(public["running"])
        self.assertTrue(public["ownership_valid"])
        self.assertEqual("running", public["state"])
        self.assertNotIn(process_id, self.registry._persistent_process_handles)

    def test_process_id_is_project_scoped(self):
        result = self.registry.execute(
            "process.status",
            {
                "project": "demo",
                "process_id": "not-a-uuid",
            },
        )
        self.assertFalse(result.ok)
        self.assertIn("UUID", result.summary)


if __name__ == "__main__":
    unittest.main()
