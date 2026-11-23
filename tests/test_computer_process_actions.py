from __future__ import annotations

import tempfile
import os
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

    def test_listing_preserves_old_live_process_and_bounds_history(self):
        registry = self._device_registry()
        with patch(
            "ordax_dev_agent.computer_filesystem_actions.load_computer_access_policy",
            return_value=SimpleNamespace(enabled=True, full_access=True),
        ):
            scope = registry._device_process_scope()
            registry._process_root(scope).mkdir(parents=True)
            for index in range(42):
                process_id = f"00000000-0000-4000-8000-{index + 1:012x}"
                live = index == 0
                state = {
                    "process_id": process_id,
                    "scope_kind": "device",
                    "device_id": scope.device_id,
                    "state": "running" if live else "exited",
                    "manager_pid": 1337 if live else 0,
                    "started_at_unix": index + 1,
                    "task_title": "Old active task" if live else f"Done {index}",
                    "argv": ["python", "-c", "sensitive command"],
                    "token": "secret-token",
                    "mission": {"id": "batch-check", "project_label": "runtime"} if index < 30 else None,
                }
                registry._process_state_path(scope, process_id).write_text(
                    json.dumps(state), encoding="utf-8"
                )
            with patch.object(registry, "_pid_running", side_effect=lambda pid: pid == 1337), patch.object(
                registry, "_owned", side_effect=lambda state: state.get("manager_pid") == 1337
            ):
                listed = registry.execute("computer.process_list", {})
            self.assertTrue(listed.ok, listed.summary)
            self.assertEqual(listed.data["total_registered"], 42)
            self.assertEqual(len(listed.data["processes"]), 24)
            self.assertTrue(listed.data["truncated"])
            self.assertEqual(listed.data["processes"][0]["title"], "Old active task")
            self.assertTrue(listed.data["processes"][0]["running"])
            self.assertNotIn("secret-token", repr(listed.data))
            self.assertNotIn("sensitive command", repr(listed.data))
            first = registry.execute("computer.process_list", {"mission_id": "batch-check", "mission_project": "runtime"})
            second = registry.execute("computer.process_list", {"mission_id": "batch-check", "offset": 24})
            self.assertTrue(first.ok, first.summary)
            self.assertTrue(second.ok, second.summary)
            self.assertEqual((first.data["total_registered"], first.data["total_matching"]), (42, 30))
            self.assertEqual((len(first.data["processes"]), first.data["next_offset"]), (24, 24))
            self.assertEqual((len(second.data["processes"]), second.data["next_offset"]), (6, None))
            self.assertTrue({p["process_id"] for p in first.data["processes"]}.isdisjoint(
                {p["process_id"] for p in second.data["processes"]}))
            self.assertFalse(registry.execute("computer.process_list", {"mission_id": "../bad"}).ok)
            self.assertFalse(registry.execute("computer.process_list", {"mission_project": "runtime"}).ok)
            self.assertFalse(registry.execute("computer.process_list", {"offset": True}).ok)
            self.assertFalse(registry.execute("computer.process_list", {"offset": -1}).ok)
            self.assertEqual(registry.execute("computer.process_list", {"mission_id": "missing"}).data["total_matching"], 0)

    def test_mission_checkpoint_is_visible_without_id_and_advisory(self):
        registry = self._device_registry()
        with patch(
            "ordax_dev_agent.computer_filesystem_actions.load_computer_access_policy",
            return_value=SimpleNamespace(enabled=True, full_access=True),
        ):
            scope = registry._device_process_scope()
            registry._process_root(scope).mkdir(parents=True)
            process_id = "88888888-8888-4888-8888-888888888888"
            state = {
                "process_id": process_id, "scope_kind": "device",
                "device_id": scope.device_id, "state": "exited", "returncode": 0,
                "started_at_unix": 500,
                "argv": ["python", "-c", "hidden-command"],
                "token": "secret-token",
                "mission": {"id": "runtime-qa", "project_label": "ordax-runtime"},
            }
            registry._process_state_path(scope, process_id).write_text(json.dumps(state), encoding="utf-8")
            marker = "ORDAX_MISSION_CHECKPOINT|"
            log = "\n".join([
                "prefix " + marker + '{"step":20,"total":20,"stage":"fake","status":"completed"}',
                marker + json.dumps({"step": 1, "total": 3, "stage": "setup", "status": "running",
                                     "summary": "started", "resume_ref": "commit:abc"}),
                marker + '{"step":999,"total":3,"stage":"invalid","status":"completed"}',
                marker + json.dumps({"step": 2, "total": 3, "stage": "tests", "status": "blocked",
                                     "summary": "needs review", "resume_ref": "commit:def"}),
                marker + "not-json",
            ]) + "\n"
            registry._process_log_path(scope, process_id).write_text(log, encoding="utf-8")
            detail = registry.execute("computer.process_status", {"process_id": process_id})
            listed = registry.execute("computer.process_list", {})
        self.assertTrue(detail.ok)
        self.assertEqual(detail.data["returncode"], 0)
        checkpoint = detail.data["latest_checkpoint"]
        self.assertEqual((checkpoint["step"], checkpoint["total"]), (2, 3))
        self.assertEqual(checkpoint["status"], "blocked")
        self.assertEqual(checkpoint["source"], "unverified_child_stdout")
        item = listed.data["processes"][0]
        self.assertEqual(item["mission"], {"id": "runtime-qa", "project_label": "ordax-runtime"})
        self.assertEqual(item["latest_checkpoint"], checkpoint)
        self.assertNotIn("secret-token", repr(listed.data))
        self.assertNotIn("hidden-command", repr(listed.data))

    def test_mission_checkpoint_only_reads_complete_bounded_log_tail(self):
        registry = self._device_registry()
        with patch(
            "ordax_dev_agent.computer_filesystem_actions.load_computer_access_policy",
            return_value=SimpleNamespace(enabled=True, full_access=True),
        ):
            scope = registry._device_process_scope()
            registry._process_root(scope).mkdir(parents=True)
            process_id = "99999999-9999-4999-8999-999999999999"
            registry._process_state_path(scope, process_id).write_text(json.dumps({
                "process_id": process_id, "scope_kind": "device",
                "device_id": scope.device_id, "state": "exited",
                "started_at_unix": 123, "mission": {"id": "big-log", "project_label": "runtime"},
            }), encoding="utf-8")
            checkpoint = 'ORDAX_MISSION_CHECKPOINT|{"step":1,"total":2,"stage":"first","status":"running"}\\n'
            path = registry._process_log_path(scope, process_id)
            path.write_text(checkpoint.replace("\\n", "\n") + ("noise\\n".replace("\\n", "\n") * 6000),
                            encoding="utf-8")
            detail = registry.execute("computer.process_status", {"process_id": process_id})
            self.assertNotIn("latest_checkpoint", detail.data)
            # Incomplete final JSON must not be reported as a checkpoint.
            with path.open("a", encoding="utf-8") as handle:
                handle.write('ORDAX_MISSION_CHECKPOINT|{"step":2,"total":2,"stage":"done","status":"completed"}')
            self.assertNotIn("latest_checkpoint", registry.execute(
                "computer.process_status", {"process_id": process_id},
            ).data)
            with path.open("a", encoding="utf-8") as handle:
                handle.write("\n")
            self.assertEqual(registry.execute(
                "computer.process_status", {"process_id": process_id},
            ).data["latest_checkpoint"]["step"], 2)

    def test_mission_metadata_requires_safe_labels_and_id(self):
        registry = self._device_registry()
        with patch(
            "ordax_dev_agent.computer_filesystem_actions.load_computer_access_policy",
            return_value=SimpleNamespace(enabled=True, full_access=True),
        ), patch("ordax_dev_agent.persistent_process_actions.subprocess.Popen") as spawn:
            for env in (
                {"ORDAX_MISSION_ID": "../escape"},
                {"ORDAX_MISSION_PROJECT": "ordax-runtime"},
                {"ORDAX_MISSION_ID": "safe", "ORDAX_MISSION_PROJECT": "bad name"},
                {"ORDAX_MISSION_ID": "x" * 81},
            ):
                with self.subTest(env=env):
                    result = registry.execute("computer.process_start", {
                        "argv": ["cmd.exe", "/d", "/c", "echo never"],
                        "cwd": self.temp.name, "env": env,
                    })
                    self.assertFalse(result.ok)
            spawn.assert_not_called()

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
                    "env": {"ORDAX_MISSION_ID": "dev-smoke", "ORDAX_MISSION_PROJECT": "ordax-runtime"},
                })
                self.assertTrue(started.ok, started.summary)
                process_id = started.data["process_id"]
                self.assertEqual(started.data["mission"]["id"], "dev-smoke")
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

    def test_quick_device_process_exit_preserves_supervisor_receipt(self):
        # A detached worker can run, exit and persist its final journal before
        # process_start checks readiness. This is not a launch failure.
        class FinishedManager:
            pid = 4242

            def poll(self):
                return 0

        registry = self._device_registry()
        with patch(
            "ordax_dev_agent.computer_filesystem_actions.load_computer_access_policy",
            return_value=SimpleNamespace(enabled=True, full_access=True),
        ):
            for state, returncode, trusted, expected_ok, expected_summary in (
                ("exited", 0, True, True, "persistent process completed"),
                ("failed", 12, True, False, "persistent process exited with failure"),
                ("exited", 0, False, False, "persistent process failed to start"),
            ):
                with self.subTest(state=state, exit=returncode, trusted=trusted):
                    def persisted_terminal(scope, process_id):
                        raw = json.loads(registry._process_state_path(
                            scope, process_id,
                        ).read_text(encoding="utf-8"))
                        raw.update(
                            state=state, returncode=returncode,
                            manager_pid=4242, manager_ready_at_unix=1.0,
                            child_pid=4343, running_at_unix=1.0,
                            ended_at_unix=2.0,
                        )
                        if not trusted:
                            raw["token"] = "untrusted-other-launch"
                        return raw

                    with patch(
                        "ordax_dev_agent.persistent_process_actions.subprocess.Popen",
                        return_value=FinishedManager(),
                    ) as launcher, patch.object(
                        registry, "_load_process_state",
                        side_effect=persisted_terminal,
                    ), patch.object(
                        registry, "_pid_running", return_value=False,
                    ), patch(
                        "ordax_dev_agent.persistent_process_actions.subprocess.run",
                    ) as terminator:
                        result = registry.computer_process_start({
                            "argv": ["cmd.exe", "/d", "/c", "echo fast"],
                            "cwd": self.temp.name,
                            "wait_seconds": 0.1,
                        })
                    self.assertEqual(result.ok, expected_ok, result.summary)
                    self.assertEqual(result.summary, expected_summary)
                    self.assertEqual(result.data["returncode"], returncode)
                    self.assertFalse(result.data["running"])
                    self.assertIn("process_id", result.data)
                    launcher.assert_called_once()
                    terminator.assert_not_called()

    def test_detached_manager_exits_before_handshake_with_private_diagnostic(self):
        # This was previously indistinguishable from a slow manager because
        # Popen discarded stderr and the return value contained no failure.
        class ExitedManager:
            pid = 4242
            def poll(self):
                return 3

        registry = self._device_registry()
        with patch(
            "ordax_dev_agent.computer_filesystem_actions.load_computer_access_policy",
            return_value=SimpleNamespace(enabled=True, full_access=True),
        ), patch(
            "ordax_dev_agent.persistent_process_actions.subprocess.Popen",
            return_value=ExitedManager(),
        ) as launcher, patch.object(
            registry, "_pid_running", return_value=False,
        ):
            result = registry.computer_process_start({
                "argv": ["cmd.exe", "/d", "/c", "exit 3"],
                "cwd": self.temp.name, "wait_seconds": 0.1,
            })
        self.assertFalse(result.ok)
        self.assertEqual(result.data["startup_failure"], "manager_exited_before_handshake")
        self.assertFalse(result.data["ownership_valid"])
        self.assertNotIn("token", result.data)
        self.assertIn("stderr", launcher.call_args.kwargs)
        self.assertNotEqual(launcher.call_args.kwargs["stderr"], __import__("subprocess").DEVNULL)

    def test_frozen_desktop_device_process_reuses_product_executable(self):
        # A frozen Desktop component cannot interpret an unpacked Python path.
        # The current OS-owned Product executable must re-enter its private
        # worker mode, with a clean PyInstaller process environment.
        class ExitedManager:
            pid = 4242
            def poll(self):
                return 3

        registry = ActionRegistry(replace(
            self._device_registry().config,
            computer_access_source="native-windows-dev",
        ))
        with (
            patch(
                "ordax_dev_agent.computer_filesystem_actions.load_computer_access_policy",
                return_value=SimpleNamespace(enabled=True, full_access=True),
            ),
            patch.dict(os.environ, {"ORDAX_RUNTIME_HOST_PROFILE": "ordax-desktop"}),
            patch.object(sys, "frozen", True, create=True),
            patch.object(sys, "platform", "win32"),
            patch(
                "ordax_dev_agent.persistent_process_actions.subprocess.Popen",
                return_value=ExitedManager(),
            ) as launched,
            patch.object(registry, "_pid_running", return_value=False),
        ):
            result = registry.computer_process_start({
                "argv": ["powershell.exe", "-NoProfile", "-Command", "exit 3"],
                "cwd": self.temp.name,
                "wait_seconds": 0.1,
            })
        self.assertFalse(result.ok, "Fake manager must not claim a started process")
        launched.assert_called_once()
        command = launched.call_args.args[0]
        self.assertEqual(command[0], sys.executable)
        self.assertEqual(command[1], "--persistent-process-worker")
        self.assertNotIn("persistent_process_runtime.py", " ".join(command))
        self.assertEqual(
            launched.call_args.kwargs["env"]["PYINSTALLER_RESET_ENVIRONMENT"], "1",
        )

    def test_delayed_manager_is_not_killed_before_actual_windows_handshake(self):
        # A delayed Windows supervisor cannot be accepted without journal
        # proof; it gets a bounded opportunity to publish that proof.
        registry = self._device_registry()
        class LiveManager:
            pid = 4141
            def poll(self):
                return None
            def wait(self, timeout):
                return 0
        manager = LiveManager()
        reads = 0
        def journal(scope, process_id):
            nonlocal reads
            reads += 1
            raw = json.loads(registry._process_state_path(
                scope, process_id,
            ).read_text(encoding="utf-8"))
            if reads >= 4:
                raw.update(
                    manager_pid=4141, manager_ready_at_unix=1.0,
                    child_pid=4242, running_at_unix=1.0, state="running",
                )
            return raw
        with patch(
            "ordax_dev_agent.computer_filesystem_actions.load_computer_access_policy",
            return_value=SimpleNamespace(enabled=True, full_access=True),
        ), patch(
            "ordax_dev_agent.persistent_process_actions.subprocess.Popen",
            return_value=manager,
        ), patch.object(
            registry, "_load_process_state", side_effect=journal,
        ), patch.object(
            registry, "_pid_running", return_value=True,
        ), patch(
            "ordax_dev_agent.persistent_process_actions.subprocess.run",
        ) as stop_tree:
            result = registry.computer_process_start({
                "argv": ["cmd.exe", "/d", "/c", "echo ready"],
                "cwd": self.temp.name, "wait_seconds": 0.1,
            })
        self.assertTrue(result.ok, result.summary)
        self.assertTrue(result.data["ownership_valid"])
        self.assertTrue(result.data["running"])
        stop_tree.assert_not_called()

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
