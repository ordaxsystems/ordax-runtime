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
from ordax_dev_agent.process_environment import native_private_worker_command


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


    def test_native_private_worker_reuses_exact_bundled_loader(self):
        from ordax_dev_agent import process_environment
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            binary = root / "ordax-product-agent"
            binary.write_bytes(b"test-executable")
            sidecar = root / "portable-lib"
            sidecar.mkdir()
            for name in ("ld-musl-x86_64.so.1", "libc.musl-x86_64.so.1",
                         "libz.so.1"):
                (sidecar / name).write_bytes(b"test-library")
            loader = sidecar / "ld-musl-x86_64.so.1"
            loader.chmod(0o755)
            argv = ["--persistent-process-worker", "--state", "/tmp/process.json"]
            with (patch.object(process_environment.sys, "platform", "linux"),
                  patch.object(process_environment.sys, "frozen", True, create=True),
                  patch.object(process_environment.sys, "_ordax_native_runtime", True, create=True)):
                self.assertEqual(native_private_worker_command(str(binary), argv), [
                    str(loader), "--library-path", str(sidecar), str(binary), *argv,
                ])
                # The native worker never silently falls back from a partially
                # staged sidecar to the OS /lib or another host interpreter.
                (sidecar / "libz.so.1").unlink()
                with self.assertRaisesRegex(RuntimeError, "loader_unavailable"):
                    native_private_worker_command(str(binary), argv)

    def test_private_worker_command_preserves_unmodified_non_native_execution(self):
        from ordax_dev_agent import process_environment
        executable = "/usr/bin/python3"
        argv = ["--persistent-process-worker"]
        with (patch.object(process_environment.sys, "platform", "linux"),
              patch.object(process_environment.sys, "frozen", False, create=True),
              patch.object(process_environment.sys, "_ordax_native_runtime", True, create=True)):
            self.assertEqual(native_private_worker_command(executable, argv),
                             [executable, *argv])
        with (patch.object(process_environment.sys, "platform", "win32"),
              patch.object(process_environment.sys, "frozen", True, create=True),
              patch.object(process_environment.sys, "_ordax_native_runtime", True, create=True)):
            self.assertEqual(native_private_worker_command(executable, argv),
                             [executable, *argv])

    def test_windows_supervisor_grace_covers_child_shutdown_contract(self):
        from ordax_dev_agent.persistent_process_runtime import (
            WINDOWS_CHILD_TREE_STOP_TIMEOUT_SECONDS,
        )
        from ordax_dev_agent import persistent_process_actions

        self.assertEqual(WINDOWS_CHILD_TREE_STOP_TIMEOUT_SECONDS, 15)
        source = Path(persistent_process_actions.__file__).read_text(encoding="utf-8")
        self.assertIn(
            'WINDOWS_CHILD_TREE_STOP_TIMEOUT_SECONDS + 3.0 if os.name == "nt"',
            source,
        )
        self.assertNotIn('return ActionResult(False, "persistent process stop failed", {', source)
        self.assertIn('ORDAX_PERSISTENT_BREAKAWAY_ALLOWED', source)
        self.assertIn('CREATE_BREAKAWAY_FROM_JOB', source)

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

    def test_failed_start_keeps_ownership_token_internal(self):
        with patch(
            "ordax_dev_agent.persistent_process_actions.subprocess.Popen",
            side_effect=OSError("launch denied"),
        ):
            result = self.registry.execute(
                "process.start",
                {"project": "demo", "cwd": ".", "argv": [sys.executable, "-c", "pass"]},
            )

        self.assertFalse(result.ok)
        self.assertEqual("failed", result.data["state"])
        self.assertNotIn("token", result.data)
        project = self.registry._project({"project": "demo"})
        persisted = json.loads(
            self.registry._process_state_path(project, result.data["process_id"])
            .read_text(encoding="utf-8")
        )
        self.assertTrue(persisted["token"])

    def test_list_terminal_history_never_rechecks_recycled_pids(self):
        project = self.registry._project({"project": "demo"})
        root = self.registry._process_root(project)
        root.mkdir(parents=True, exist_ok=True)
        for index in range(36):
            process_id = f"00000000-0000-4000-8000-{index:012d}"
            (root / f"{process_id}.json").write_text(json.dumps({
                "process_id": process_id,
                "project": "demo",
                "state": "exited",
                "manager_pid": os.getpid(),
                "token": "private-ownership-token",
                "started_at_unix": time.time() - index,
                "argv": ["python", "-c", "pass"],
            }), encoding="utf-8")

        # Reused PIDs must not prompt a Windows CIM command-line lookup for
        # terminal receipts; that made large historical lists very slow.
        with patch.object(self.registry, "_pid_running", side_effect=AssertionError("unnecessary PID lookup")), patch.object(
            self.registry, "_commandline", side_effect=AssertionError("unnecessary CIM lookup")
        ):
            result = self.registry.execute("process.list", {"project": "demo"})

        self.assertTrue(result.ok, result.summary)
        self.assertEqual(36, result.data["total_registered"])
        self.assertEqual(24, result.data["returned"])
        self.assertEqual(24, result.data["next_offset"])
        self.assertTrue(all(not item["running"] for item in result.data["processes"]))
        self.assertTrue(all(not item["ownership_valid"] for item in result.data["processes"]))
        self.assertTrue(all("token" not in item for item in result.data["processes"]))

    def test_log_byte_cursor_reads_once_without_splitting_utf8(self):
        project = self.registry._project({"project": "demo"})
        process_id = "12345678-1234-4234-8234-123456789012"
        state_path = self.registry._process_state_path(project, process_id)
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(json.dumps({
            "process_id": process_id, "project": "demo",
            "state": "exited", "started_at_unix": time.time() - 30,
            "manager_pid": 0, "token": "", "argv": ["python"],
        }), encoding="utf-8")
        log_path = self.registry._process_log_path(project, process_id)
        source = b"a" * 1023 + "é".encode("utf-8") + b"\n" + b"b" * 1500
        log_path.write_bytes(source)

        received = []
        offset = 0
        for _ in range(10):
            page = self.registry.execute("process.logs", {
                "project": "demo", "process_id": process_id,
                "offset": offset, "max_bytes": 1024,
            })
            self.assertTrue(page.ok, page.summary)
            self.assertEqual("cursor", page.data["read_mode"])
            self.assertEqual(offset, page.data["start_offset"])
            self.assertGreaterEqual(page.data["next_offset"], offset)
            received.append(page.data["tail"])
            if not page.data["has_more"]:
                break
            offset = page.data["next_offset"]
        else:
            self.fail("cursor did not reach EOF")
        self.assertEqual(source.decode("utf-8"), "".join(received))
        self.assertEqual(len(source), page.data["next_offset"])
        self.assertFalse(page.data["has_more"])
        self.assertNotIn("token", page.data["process"])

        # Existing callers keep the same bounded tail semantics.
        tail = self.registry.execute("process.logs", {
            "project": "demo", "process_id": process_id, "max_bytes": 1024,
        })
        self.assertTrue(tail.ok, tail.summary)
        self.assertEqual("b" * 1024, tail.data["tail"])
        self.assertTrue(tail.data["truncated"])
        self.assertNotIn("next_offset", tail.data)

        stale = self.registry.execute("process.logs", {
            "project": "demo", "process_id": process_id,
            "offset": len(source) + 1,
        })
        self.assertFalse(stale.ok)
        self.assertEqual("process_log_cursor_stale", stale.data["error_code"])
        for bad in (True, -1, "0"):
            rejected = self.registry.execute("process.logs", {
                "project": "demo", "process_id": process_id, "offset": bad,
            })
            self.assertFalse(rejected.ok)

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
        self.assertNotIn("token", started.data)

        status = self.registry.execute(
            "process.status",
            {"project": "demo", "process_id": self.process_id},
        )
        self.assertTrue(status.ok)
        self.assertTrue(status.data["running"])
        self.assertTrue(status.data["ownership_valid"])
        self.assertNotIn("token", status.data)

        listing = self.registry.execute("process.list", {"project": "demo"})
        self.assertTrue(listing.ok)
        self.assertIn(
            self.process_id,
            [item["process_id"] for item in listing.data["processes"]],
        )
        self.assertTrue(all("token" not in item for item in listing.data["processes"]))

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
