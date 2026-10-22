from __future__ import annotations

import io
import json
import os
import signal
import tempfile
import unittest
from contextlib import redirect_stdout
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock, patch

from ordax_dev_agent.config import AgentConfig
from ordax_dev_agent.control_plane import build_control_plane
from ordax_dev_agent.main import run, _wait_for_native_access
from ordax_dev_agent.models import AgentJob
from ordax_dev_agent.models import ActionResult
from ordax_dev_agent.actions import ActionRegistry, COMPUTER_GRAPHICAL_ACTIONS


DEVICE = "22222222-2222-4222-8222-222222222222"


class NativeRuntimeCompositionTests(unittest.TestCase):
    def setUp(self):
        environment = patch.dict(os.environ, {}, clear=True)
        environment.start()
        self.addCleanup(environment.stop)
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.state = self.root / "state"
        self.state.mkdir()
        self.config = AgentConfig(
            agent_name="native-test", poll_seconds=8,
            state_dir=self.state, agent_repo_path=self.root / "runtime-source",
            hordax_path=self.root / "unused", bridge_path=self.root / "unused",
            projects={}, device_id=DEVICE,
            product_mcp_url="https://mcp.ordax.com.br",
        )
        self.provider = Mock(return_value={"device_id": DEVICE, "device_token": "b" * 64})

    def test_factory_uses_native_identity_without_creating_token_file(self):
        plane = build_control_plane(self.config, device_credential_provider=self.provider)
        self.addCleanup(plane.http.close)
        self.addCleanup(plane.product_central_session.close)
        self.assertEqual(plane.runtime_kind, "ordax-os")
        self.assertEqual(plane.http.headers["X-Ordax-Device-Id"], DEVICE)
        self.provider.assert_called_once_with()
        self.assertFalse((self.state / "device-token.cloudflare-v3.txt").exists())

    def test_native_denial_cannot_fall_back_to_desktop_credential(self):
        (self.state / "device-token.cloudflare-v3.txt").write_text("a" * 64)
        with self.assertRaisesRegex(RuntimeError, "identity mismatch"):
            build_control_plane(self.config, device_credential_provider=lambda: None)

    def test_incomplete_native_configuration_fails_before_starting_services(self):
        for config in (
            replace(self.config, device_id=None),
            replace(self.config, product_mcp_url=None),
            replace(self.config, control_plane_protocol="legacy"),
        ):
            with self.subTest(config=config), patch("ordax_dev_agent.main.start_status_server") as server:
                with self.assertRaises(ValueError):
                    run(config, device_credential_provider=self.provider)
                server.assert_not_called()

    def test_native_switch_deny_by_default_and_fails_closed(self):
        from ordax_dev_agent.main import _native_local_access_granted
        self.assertTrue(_native_local_access_granted(None))
        self.assertTrue(_native_local_access_granted(lambda: True))
        for unsafe in (False, None, 1, "true"):
            self.assertFalse(_native_local_access_granted(lambda unsafe=unsafe: unsafe))
        self.assertFalse(_native_local_access_granted(lambda: 1 / 0))

        with patch("ordax_dev_agent.main.start_status_server") as server:
            with self.assertRaisesRegex(TypeError, "OS-owned local Connector policy"):
                run(self.config, device_credential_provider=self.provider)
            with self.assertRaisesRegex(RuntimeError, "native_local_connector_disabled"):
                run(self.config, device_credential_provider=self.provider,
                    native_access_enabled=lambda: False)
            server.assert_not_called()

    def test_native_idle_wait_rechecks_existing_local_switch_without_new_scheduler(self):
        from threading import Event

        self.assertTrue(_wait_for_native_access(0, Event(), lambda: True))
        self.assertFalse(_wait_for_native_access(0, Event(), lambda: False))
        self.assertFalse(_wait_for_native_access(0, Event(), lambda: 1 / 0))
        stopped = Event()
        stopped.set()
        self.assertFalse(_wait_for_native_access(10, stopped, lambda: True))

        enabled = [True]

        class RevokingWait:
            def is_set(self):
                return False

            def wait(self, seconds):
                self_seconds = seconds
                self_owner = enabled
                self_owner[0] = False
                self.test_case.assertLessEqual(self_seconds, 1.0)
                return False

        wait = RevokingWait()
        wait.test_case = self
        self.assertFalse(_wait_for_native_access(8, wait, lambda: enabled[0]))
        self.assertFalse(enabled[0])

    def test_native_switch_revoked_after_claim_refuses_file_mutation(self):
        target = self.root / "revoked.txt"
        (self.state / "agent-settings.json").write_text(json.dumps({
            "computer_access": {"enabled": True, "full_access": True,
                                "allowed_roots": [str(self.root)]},
        }))
        job = AgentJob("revoked-local-job", "computer.text_write", {
            "path": str(target), "content": "should never be written", "create": True,
        })
        plane = Mock()
        plane.recover_pending_reports.return_value = 0
        plane.pair_if_needed.return_value = False
        plane.claim_next_job.return_value = job
        handlers = {}
        results = []

        def complete(completed, result):
            self.assertIs(completed, job)
            results.append(result)
            handlers[signal.SIGTERM]()

        plane.complete.side_effect = complete
        # Startup, pairing and claim succeed; local setting changes before dispatch.
        switch = Mock(side_effect=[True, True, True, True, False])
        with (
            patch("ordax_dev_agent.control_plane.build_control_plane", return_value=plane),
            patch("ordax_dev_agent.main.start_status_server") as server,
            patch("ordax_dev_agent.main._start_local_watchdog", return_value=None),
            patch("ordax_dev_agent.main.signal.signal",
                  side_effect=lambda kind, handler: handlers.__setitem__(kind, handler)),
            redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(run(self.config, device_credential_provider=self.provider,
                                 native_access_enabled=switch, status_port=0), 0)
            server.return_value.shutdown.assert_called_once_with()
        self.assertEqual(len(results), 1)
        self.assertFalse(results[0].ok)
        self.assertEqual(results[0].summary, "native_local_connector_disabled")
        self.assertFalse(target.exists())
        plane.complete.assert_called_once()

    def test_native_graphics_register_only_supplied_handlers_under_shared_policy(self):
        (self.state / "agent-settings.json").write_text(json.dumps({"computer_access": {"enabled": False}}))
        handler = Mock(return_value=ActionResult(True, "native screen ready", {"width": 1280}))
        registry = ActionRegistry(self.config, computer_adapter={"computer.screen_info": handler})
        self.assertEqual(COMPUTER_GRAPHICAL_ACTIONS.intersection(registry.names), {"computer.screen_info"})
        self.assertFalse(registry.execute("computer.screen_info", {}).ok)
        handler.assert_not_called()
        (self.state / "agent-settings.json").write_text(json.dumps({"computer_access": {"enabled": True}}))
        self.assertTrue(registry.execute("computer.screen_info", {}).ok)
        handler.assert_called_once_with({})
        self.assertFalse(registry.execute("computer.click", {"x": 1, "y": 1}).ok)
        for adapter in ({"computer.text_write": handler}, {"computer.screen_info": None}):
            with self.assertRaises(ValueError):
                ActionRegistry(self.config, computer_adapter=adapter)

    def test_denied_native_identity_stops_status_service_without_claiming_work(self):
        with (
            patch("ordax_dev_agent.main.start_status_server") as server,
            patch("ordax_dev_agent.main._start_local_watchdog", return_value=None),
            patch("ordax_dev_agent.main.signal.signal"),
            redirect_stdout(io.StringIO()),
        ):
            with self.assertRaisesRegex(RuntimeError, "identity mismatch"):
                run(self.config, device_credential_provider=lambda: None,
                    native_access_enabled=lambda: True)
            server.return_value.shutdown.assert_called_once_with()

    def test_existing_loop_executes_native_file_action_and_respects_policy(self):
        # Real registry, filesystem action and local policy; transport is isolated.
        # This is source integration evidence, not a credentialled device E2E.
        for enabled in (True, False):
            with self.subTest(enabled=enabled):
                path = self.root / ("allowed.txt" if enabled else "denied.txt")
                (self.state / "agent-settings.json").write_text(json.dumps({
                    "computer_access": {"enabled": enabled, "allowed_roots": [str(self.root)]},
                }))
                job = AgentJob("native-file-job", "computer.text_write", {
                    "path": str(path), "content": "native runtime integration\n", "create": True,
                })
                plane = Mock()
                plane.recover_pending_reports.return_value = 0
                plane.pair_if_needed.return_value = False
                plane.claim_next_job.return_value = job
                handlers = {}
                results = []

                def complete(completed_job, result):
                    self.assertIs(completed_job, job)
                    results.append(result)
                    handlers[signal.SIGTERM]()

                plane.complete.side_effect = complete
                with (
                    patch("ordax_dev_agent.config.AgentConfig.from_env", side_effect=AssertionError("host config required")),
                    patch("ordax_dev_agent.control_plane.build_control_plane", return_value=plane) as factory,
                    patch("ordax_dev_agent.main.start_status_server") as server,
                    patch("ordax_dev_agent.main._start_local_watchdog", return_value=None),
                    patch("ordax_dev_agent.main.signal.signal", side_effect=lambda kind, handler: handlers.__setitem__(kind, handler)),
                    redirect_stdout(io.StringIO()),
                ):
                    self.assertEqual(run(self.config, device_credential_provider=self.provider,
                                         native_access_enabled=lambda: True, status_port=0), 0)
                    factory.assert_called_once_with(self.config, device_credential_provider=self.provider)
                    self.assertEqual(server.call_args.kwargs["port"], 0)
                    server.return_value.shutdown.assert_called_once_with()
                self.assertEqual(len(results), 1)
                self.assertEqual(results[0].ok, enabled, results[0].summary)
                self.assertEqual(path.exists(), enabled)
                if enabled:
                    self.assertEqual(path.read_text(), "native runtime integration\n")
                self.assertEqual(plane.heartbeat.call_args.kwargs["status"], "offline")
                self.assertFalse(COMPUTER_GRAPHICAL_ACTIONS.intersection(plane.heartbeat.call_args.args[0]))
                self.assertFalse((self.state / "device-token.cloudflare-v3.txt").exists())


if __name__ == "__main__":
    unittest.main()
