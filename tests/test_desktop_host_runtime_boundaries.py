from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from ordax_dev_agent.actions import ActionRegistry
from ordax_dev_agent.config import AgentConfig
from ordax_dev_agent.desktop_host_main import DESKTOP_OS_ACTIONS, main


class DesktopOsRuntimeBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "OrdaX OS Desktop Dev"
        self.root.mkdir()
        (self.root / "native-state").mkdir()
        self.state = self.root / "native-state" / "product-runtime"
        self.state.mkdir()
        (self.state / "agent-settings.json").write_text(
            '{"device_id":"11111111-1111-4111-8111-111111111111",'
            '"control_plane_protocol":"cloudflare-v3",'
            '"product_mcp_url":"https://mcp.ordax.com.br"}', encoding="utf-8")
        (self.state / "device-token.cloudflare-v3.txt").write_text("b" * 64, encoding="ascii")
        self.config = AgentConfig(
            agent_name="OrdaX Desktop", poll_seconds=60,
            state_dir=self.state, agent_repo_path=self.root / "package",
            hordax_path=self.root / "Files", bridge_path=self.root / "package",
            device_id="11111111-1111-4111-8111-111111111111",
            product_mcp_url="https://mcp.ordax.com.br", projects={},
        )

    def test_only_explicit_os_status_capability_is_advertised(self):
        registry = ActionRegistry(self.config, computer_adapter={},
                                  allowed_action_names=DESKTOP_OS_ACTIONS)
        self.assertEqual(registry.names, ["agent.status"])
        for forbidden in (
            "terminal.exec", "computer.terminal_exec", "computer.text_read",
            "computer.click", "computer.windows", "workspace.text_read",
            "workspace.text_write", "git.command", "process.start",
            "browser.navigate", "memory.remember",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertFalse(registry.execute(forbidden, {}).ok)

    def test_no_host_policy_may_accept_bad_allowlist(self):
        for bad in (None if False else set(["agent.status"]),
                    frozenset(), frozenset({"terminalx.exec"}),
                    frozenset({"agent.status", 5}),
                    ["agent.status"]):
            with self.subTest(value=str(bad)):
                with self.assertRaises(ValueError):
                    ActionRegistry(self.config, computer_adapter={},
                                   allowed_action_names=bad)

    def test_existing_generic_runtime_keeps_its_default_registry(self):
        normal = ActionRegistry(self.config)
        self.assertIn("agent.status", normal.names)
        self.assertIn("terminal.exec", normal.names)

    def run_host(self, *, config=None, env=None):
        variables = {
            "ORDAX_DESKTOP_USER_DATA": str(self.root),
            "ORDAX_AGENT_STATE_DIR": str(self.state),
        }
        if env:
            variables.update(env)
        with (
            patch.dict(os.environ, variables, clear=True),
            patch("ordax_dev_agent.desktop_host_main.sys.platform", "win32"),
            patch("ordax_dev_agent.desktop_host_main.AgentConfig.from_env",
                  return_value=config if config is not None else self.config),
            patch("ordax_dev_agent.desktop_host_main.run", return_value=0) as dispatch,
        ):
            result = main()
        return result, dispatch

    def test_separate_enrolled_device_starts_single_existing_dispatcher(self):
        result, dispatch = self.run_host()
        self.assertEqual(result, 0)
        dispatch.assert_called_once()
        args, kwargs = dispatch.call_args
        self.assertEqual(args[0].device_id, self.config.device_id)
        self.assertEqual(args[0].state_dir, self.state)
        self.assertEqual(args[0].agent_name, "OrdaX Desktop")
        self.assertEqual(args[0].workspace_root, self.root / "Files")
        self.assertEqual(kwargs, {"computer_adapter": {},
                                  "allowed_action_names": frozenset({"agent.status"}),
                                  "status_port": 0})

    def test_windows_auxiliary_device_path_cannot_be_reused(self):
        with self.assertRaisesRegex(RuntimeError, "another device identity"):
            self.run_host(env={"ORDAX_AGENT_STATE_DIR":
                               str(self.root / "Windows Agent" / "DevAgent")})

    def test_enrollment_is_mandatory_and_cannot_auto_mint(self):
        from dataclasses import replace
        with self.assertRaisesRegex(RuntimeError, "has not been enrolled"):
            self.run_host(config=replace(self.config, device_id=None))
        self.assertEqual((self.state / "device-token.cloudflare-v3.txt").read_text(), "b" * 64)

    def test_credentials_missing_or_from_another_device_are_rejected(self):
        token = self.state / "device-token.cloudflare-v3.txt"
        token.unlink()
        with self.assertRaisesRegex(RuntimeError, "not been enrolled"):
            self.run_host()
        token.write_text("b" * 64, encoding="ascii")
        settings = self.state / "agent-settings.json"
        settings.write_text('{"device_id":"22222222-2222-4222-8222-222222222222",'
                            '"control_plane_protocol":"cloudflare-v3",'
                            '"product_mcp_url":"https://mcp.ordax.com.br"}', encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError, "identity is invalid"):
            self.run_host()

    def test_host_adapters_and_project_overrides_are_refused(self):
        from dataclasses import replace
        for bad in (replace(self.config, adapters=("windows",)),
                    replace(self.config, projects={"windows": {}})):
            with self.assertRaisesRegex(RuntimeError, "host-side adapters"):
                self.run_host(config=bad)

    def test_non_windows_host_is_not_this_profile(self):
        with patch.dict(os.environ, {}, clear=True), patch(
            "ordax_dev_agent.desktop_host_main.sys.platform", "linux"
        ):
            with self.assertRaisesRegex(RuntimeError, "requires Windows"):
                main()

    def test_symlink_owned_identity_root_fails_closed(self):
        import shutil
        shutil.rmtree(self.state)
        target = self.root / "outsider"
        target.mkdir()
        try:
            self.state.symlink_to(target, target_is_directory=True)
        except OSError as exc:
            self.skipTest("Windows symlink privilege unavailable: " + str(exc))
        with self.assertRaisesRegex(RuntimeError, "linked profile roots"):
            self.run_host()

    def test_runtime_entrypoint_requires_canonical_profile_path(self):
        for value in ("", "relative", str(self.root / "wrong-profile")):
            with self.subTest(value=value), self.assertRaises(RuntimeError):
                self.run_host(env={"ORDAX_DESKTOP_USER_DATA":value})

    def test_canonical_windows_runtime_dispatches_trusted_desktop_profile(self):
        from ordax_dev_agent import main as runtime_main
        with patch.dict(os.environ, {"ORDAX_RUNTIME_HOST_PROFILE": "ordax-desktop"}), patch.object(
            runtime_main, "_startup_log", side_effect=AssertionError("Windows agent state touched")
        ), patch("ordax_dev_agent.desktop_host_main.main", return_value=0) as desktop:
            self.assertEqual(runtime_main.main(), 0)
            desktop.assert_called_once_with()

    def test_unknown_runtime_profile_never_uses_windows_agent_identity(self):
        from ordax_dev_agent import main as runtime_main
        with patch.dict(os.environ, {"ORDAX_RUNTIME_HOST_PROFILE": "unknown"}), patch.object(
            runtime_main, "_startup_log", side_effect=AssertionError("Windows agent state touched")
        ), self.assertRaisesRegex(RuntimeError, "Unknown Product Runtime host profile"):
            runtime_main.main()


if __name__ == "__main__":
    unittest.main()
