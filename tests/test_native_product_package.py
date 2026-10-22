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

from ordax_dev_agent.native_main import (acquire_native_product_instance_lock, identity_store, main, native_config, self_test)
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
        self.assertGreater(proof["registered_action_count"], 100)
        self.assertEqual(proof["probes"], ["filesystem", "terminal", "supervised-process"])
        self.assertEqual(proof["supervised_process_actions"],
                         ["start", "status", "list", "write_stdin", "logs", "stop"])

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
            self.assertEqual(config.protected_roots, (root / "private",))
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

    def test_full_filesystem_policy_keeps_os_owned_private_state_protected(self):
        from ordax_dev_agent.actions import ActionRegistry
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            private = root / "private"
            config = native_config(private, root, "22222222-2222-4222-8222-222222222222")
            config.state_dir.mkdir(parents=True)
            (config.state_dir / "agent-settings.json").write_text(json.dumps({
                "computer_access": {"enabled": True, "full_access": True, "full_filesystem": True},
            }))
            credential = private / "device-agent/product-device.json"
            credential.parent.mkdir()
            credential.write_text("private-state-probe")
            registry = ActionRegistry(config, computer_adapter={})
            result = registry.execute("computer.text_read", {"path": str(credential)})
            self.assertFalse(result.ok)
            self.assertNotIn("private-state-probe", json.dumps(result.data))

    def test_native_runtime_uses_canonical_exclusive_heartbeat_file_lock(self):
        with tempfile.TemporaryDirectory() as temporary:
            state = Path(temporary) / "product-runtime"
            state.mkdir(mode=0o700)
            descriptor = acquire_native_product_instance_lock(state)
            self.assertTrue((state / "instance.lock").is_file())
            try:
                # A duplicate agent cannot run concurrently even when started
                # on the same host as the OS discovery loop.
                with self.assertRaisesRegex(RuntimeError, "native_product_runtime_instance_busy"):
                    acquire_native_product_instance_lock(state, timeout_seconds=0)
            finally:
                os.close(descriptor)
            retried = acquire_native_product_instance_lock(state, timeout_seconds=0)
            os.close(retried)

    def test_transient_native_discovery_report_contention_can_finish(self):
        with tempfile.TemporaryDirectory() as temporary:
            state = Path(temporary) / "product-runtime"
            state.mkdir(mode=0o700)
            with patch("ordax_dev_agent.native_main.fcntl.flock",
                       side_effect=[BlockingIOError(), None]) as acquire, patch(
                           "ordax_dev_agent.native_main.time.sleep") as sleep:
                descriptor = acquire_native_product_instance_lock(state, timeout_seconds=6)
                os.close(descriptor)
                self.assertEqual(acquire.call_count, 2)
                sleep.assert_called_once()

    def test_native_runtime_rejects_unsafe_lock_file_and_directory(self):
        with tempfile.TemporaryDirectory() as temporary:
            state = Path(temporary) / "product-runtime"
            state.mkdir(mode=0o700)
            lock = state / "instance.lock"
            lock.write_text("owner")
            lock.chmod(0o666)
            with self.assertRaisesRegex(RuntimeError, "native_product_runtime_instance_lock_unsafe"):
                acquire_native_product_instance_lock(state, timeout_seconds=0)
            lock.chmod(0o600)
            state.chmod(0o755)
            with self.assertRaisesRegex(RuntimeError, "native_product_runtime_state_unsafe"):
                acquire_native_product_instance_lock(state, timeout_seconds=0)
            state.chmod(0o700)
            lock.unlink()
            external = Path(temporary) / "external"
            external.write_text("not a Runtime lock")
            lock.symlink_to(external)
            with self.assertRaises(OSError):
                acquire_native_product_instance_lock(state, timeout_seconds=0)
            self.assertEqual(external.read_text(), "not a Runtime lock")

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
