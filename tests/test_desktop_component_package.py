from __future__ import annotations

import importlib.util
import os
import hashlib
import json
import stat
import tempfile
import zipfile
from pathlib import Path
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("desktop_component", ROOT / "packaging/windows/desktop_entrypoint.py")
component = importlib.util.module_from_spec(spec)
spec.loader.exec_module(component)
package_spec = importlib.util.spec_from_file_location("desktop_package", ROOT / "packaging/windows/desktop_package.py")
package_module = importlib.util.module_from_spec(package_spec)
package_spec.loader.exec_module(package_module)


class DesktopComponentPackageTests(unittest.TestCase):
    def test_component_package_is_deterministic_source_bound_and_has_no_activation_authority(self):
        with tempfile.TemporaryDirectory() as tmp:
            binary = Path(tmp) / "ordax-product-agent.exe"
            binary.write_bytes(b"packaging-fixture-not-a-runtime")
            source = "a" * 40
            release = package_module.package(binary, source, "0.4.8")
            archive = binary.with_name("product-runtime.zip")
            first = archive.read_bytes()
            self.assertEqual(release["package"]["sha256"], hashlib.sha256(first).hexdigest())
            with zipfile.ZipFile(archive) as z:
                self.assertEqual(len(z.namelist()), 2)
                manifest = json.loads(z.read("component-package.json"))
                self.assertEqual(manifest["source_commit"], source)
                self.assertEqual(manifest["target_platform"], "windows-amd64")
                self.assertFalse(manifest["activation_allowed"])
                self.assertTrue(manifest["signature_required_before_activation"])
                self.assertTrue(manifest["files"][0]["executable"])
                self.assertEqual(stat.S_IMODE(z.getinfo(manifest["entrypoint"]).external_attr >> 16), 0o555)
                self.assertEqual(hashlib.sha256(z.read("component-package.json")).hexdigest(), release["package"]["manifest_sha256"])
            package_module.package(binary, source, "0.4.8")
            self.assertEqual(first, archive.read_bytes())
            self.assertEqual(release["activation"], {"direct_activation_allowed": False, "pending_health_required": True})

    def test_package_rejects_unbound_source_and_missing_binary(self):
        for source in ("main", "", "a" * 40):
            with self.subTest(source=source), self.assertRaises(ValueError):
                package_module.package(Path("missing.exe"), source, "0.4.8")

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
