from __future__ import annotations

import contextlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from ordax_dev_agent.native_main import identity_store, main, native_config, self_test
from ordax_dev_agent.process_environment import external_process_environment


@unittest.skipUnless(sys.platform == "linux", "native OS executable uses the Linux kernel ABI")
class NativeProductPackageTests(unittest.TestCase):
    def setUp(self):
        environment = patch.dict(os.environ, {}, clear=True)
        environment.start()
        self.addCleanup(environment.stop)
        native_flag = patch.object(sys, "_ordax_native_runtime", False, create=True)
        native_flag.start()
        self.addCleanup(native_flag.stop)

    def test_offline_package_probe_executes_files_terminal_and_supervised_process(self):
        proof = self_test()
        self.assertTrue(proof["ok"])
        self.assertFalse(proof["network_used"])
        self.assertFalse(proof["device_credential_used"])
        self.assertEqual(proof["graphical_actions"], [])
        self.assertEqual(proof["probes"], ["filesystem", "terminal", "supervised-process"])

    def test_run_requires_explicit_owner_usb_profile_before_loading_identity(self):
        for arguments in (["--run"], ["--run", "--distribution-profile", "owner-development"]):
            with self.subTest(arguments=arguments), patch("ordax_dev_agent.native_main.identity_store") as store:
                with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                    main(arguments)
                store.assert_not_called()

    def test_native_configuration_does_not_load_windows_defaults_or_copy_credential(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = native_config(root / "private", root / "workspace", "device-id")
            self.assertEqual(config.projects, {})
            self.assertEqual(config.state_dir, root / "private/product-runtime")
            self.assertEqual(config.workspace_root, root / "workspace")
            self.assertEqual(list(root.iterdir()), [])

    def test_os_identity_owner_is_loaded_without_token_copy_and_rejects_unsafe_source(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            module = root / "system/surface/runtime/product_device_identity.py"
            module.parent.mkdir(parents=True)
            module.write_text("class ProductDeviceIdentityStore:\n    def __init__(self, state): self.state = state\n")
            module.chmod(0o644)
            store = identity_store(root / "system", root / "private")
            self.assertEqual(store.state, str(root / "private"))
            self.assertFalse((root / "private").exists())
            module.chmod(0o666)
            with self.assertRaises(ValueError):
                identity_store(root / "system", root / "private")

    def test_missing_native_credential_never_starts_transport(self):
        with patch("ordax_dev_agent.native_main.identity_store") as owner:
            owner.return_value.read_enrolled_credential.return_value = None
            with patch("ordax_dev_agent.main.run") as run, self.assertRaisesRegex(RuntimeError, "identity_or_visibility"):
                main(["--run", "--distribution-profile", "owner-development", "--product-mode", "usb"])
            run.assert_not_called()

    def test_frozen_system_tools_receive_host_loader_paths_without_mutating_runtime(self):
        with patch.object(sys, "frozen", True, create=True):
            with patch.dict(os.environ, {"LD_LIBRARY_PATH": "/private/bundle"}):
                env = external_process_environment()
                self.assertNotIn("LD_LIBRARY_PATH", env)
                self.assertEqual(os.environ["LD_LIBRARY_PATH"], "/private/bundle")
            with patch.dict(os.environ, {"LD_LIBRARY_PATH": "/private/bundle", "LD_LIBRARY_PATH_ORIG": "/host/lib"}):
                self.assertEqual(external_process_environment()["LD_LIBRARY_PATH"], "/host/lib")
                self.assertEqual(external_process_environment({"LD_LIBRARY_PATH": "/explicit"})["LD_LIBRARY_PATH"], "/explicit")


if __name__ == "__main__":
    unittest.main()
