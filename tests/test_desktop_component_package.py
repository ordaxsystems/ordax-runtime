from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("desktop_component", ROOT / "packaging/windows/desktop_entrypoint.py")
component = importlib.util.module_from_spec(spec)
spec.loader.exec_module(component)


class DesktopComponentPackageTests(unittest.TestCase):
    def test_offline_probe_uses_only_canonical_status_and_never_claims_remote_execution(self):
        proof = component.self_test()
        self.assertTrue(proof["ok"])
        self.assertEqual(proof["actions"], ["agent.status"])
        self.assertFalse(proof["remote_action_proven"])
        self.assertFalse(proof["network_used"])
        self.assertFalse(proof["device_credential_used"])

    def test_component_cannot_fall_back_to_auxiliary_windows_agent(self):
        for profile in (None, "windows-agent", ""):
            with self.subTest(profile=profile), patch.dict(os.environ, {}, clear=True), patch.object(component.sys, "platform", "win32"), patch.object(component, "desktop_main") as dispatch:
                if profile is not None:
                    os.environ["ORDAX_RUNTIME_HOST_PROFILE"] = profile
                with self.assertRaisesRegex(RuntimeError, "Supervisor host profile"):
                    component.main([])
                dispatch.assert_not_called()

    def test_only_supervisor_profile_reaches_existing_desktop_dispatcher(self):
        with patch.dict(os.environ, {"ORDAX_RUNTIME_HOST_PROFILE": "ordax-desktop"}, clear=True), patch.object(component.sys, "platform", "win32"), patch.object(component, "desktop_main", return_value=7) as dispatch:
            self.assertEqual(component.main([]), 7)
            dispatch.assert_called_once_with()

    def test_invalid_cli_does_not_start_runtime(self):
        with patch.object(component, "desktop_main") as dispatch:
            with self.assertRaises(SystemExit):
                component.main(["--run-windows-agent"])
            dispatch.assert_not_called()

    def test_other_hosts_cannot_use_desktop_component(self):
        with patch.object(component.sys, "platform", "linux"):
            with self.assertRaisesRegex(RuntimeError, "requires Windows"):
                component.main([])


if __name__ == "__main__":
    unittest.main()
