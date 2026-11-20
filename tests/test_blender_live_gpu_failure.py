"""Windows Blender startup must fail fast on the exact owned GPU dialog."""
import os
import tempfile
import unittest
from pathlib import Path
import json
from types import SimpleNamespace
from unittest.mock import Mock, patch

from ordax_dev_agent.blender_live_bridge import (
    BlenderLiveBridge,
    _windows_blender_graphics_error_for_pid,
    _verified_software_vulkan_icd,
    _SOFTWARE_VULKAN_LVP_SHA256,
)
from ordax_dev_agent.projects import Project


class BlenderLiveGpuFailureTests(unittest.TestCase):
    def test_invalid_pids_never_probe_windows(self):
        for value in (-1, 0, "1030", None, True):
            with self.subTest(value=value):
                self.assertIsNone(_windows_blender_graphics_error_for_pid(value))

    def test_unsupported_gpu_dialog_fails_fast_and_stops_only_owned_process(self):
        with tempfile.TemporaryDirectory() as root:
            base = Path(root)
            project = Project(slug="gpu_smoke", root=base, apps=("blender",))
            bridge = BlenderLiveBridge(
                SimpleNamespace(state_dir=base / "state"),
                project,
            )
            proc = Mock()
            proc.pid = 1048
            proc.poll.return_value = None
            proc.wait.return_value = 0
            error = {
                "title": "Blender - Unsupported Graphics Card Configuration",
                "message": "OpenGL 4.3 or higher is required",
            }
            with (
                patch("ordax_dev_agent.blender_live_bridge.find_blender", return_value=base / "blender.exe"),
                patch.object(bridge, "_companion_fingerprint", return_value="valid"),
                patch("ordax_dev_agent.blender_live_bridge.subprocess.Popen", return_value=proc) as spawn,
                patch(
                    "ordax_dev_agent.blender_live_bridge._windows_blender_graphics_error_for_pid",
                    return_value=error,
                ) as probe,
            ):
                outcome = bridge.start(wait_seconds=10)
            self.assertFalse(outcome.ok)
            self.assertEqual(
                outcome.data.get("error_code"),
                "blender_graphics_unsupported",
            )
            self.assertEqual(outcome.data["graphics_dialog"], error)
            self.assertFalse(outcome.data["retryable_on_this_gpu"])
            self.assertEqual(outcome.data["pid"], 1048)
            self.assertEqual(outcome.data["log_file"], str(bridge.root / "blender-startup.log"))
            probe.assert_called_once_with(1048)
            spawn.assert_called_once()
            proc.terminate.assert_called_once()
            proc.wait.assert_called_once_with(timeout=2.0)
            proc.kill.assert_not_called()

    def test_no_modal_preserves_existing_exit_diagnostic_without_killing(self):
        with tempfile.TemporaryDirectory() as root:
            base = Path(root)
            project = Project(slug="gpu_smoke", root=base, apps=("blender",))
            bridge = BlenderLiveBridge(
                SimpleNamespace(state_dir=base / "state"),
                project,
            )
            proc = Mock()
            proc.pid = 2048
            proc.poll.side_effect = [None, 7]
            proc.returncode = 7
            with (
                patch("ordax_dev_agent.blender_live_bridge.find_blender", return_value=base / "blender.exe"),
                patch.object(bridge, "_companion_fingerprint", return_value="valid"),
                patch("ordax_dev_agent.blender_live_bridge.subprocess.Popen", return_value=proc),
                patch("ordax_dev_agent.blender_live_bridge._windows_blender_graphics_error_for_pid", return_value=None),
                patch("ordax_dev_agent.blender_live_bridge.time.sleep"),
            ):
                outcome = bridge.start(wait_seconds=3)
            self.assertFalse(outcome.ok)
            self.assertIn("Blender exited before live companion", outcome.summary)
            self.assertEqual(outcome.data.get("returncode"), 7)
            proc.terminate.assert_not_called()


    def test_local_software_vulkan_icd_requires_exact_known_driver_hash(self):
        with tempfile.TemporaryDirectory() as base:
            folder = Path(base) / "state" / "blender-gpu" / "software-vulkan"
            folder.mkdir(parents=True)
            manifest = folder / "lvp_icd.x86_64.json"
            manifest.write_text(json.dumps({
                "file_format_version": "1.0.1",
                "ICD": {
                    "api_version": "1.4.363",
                    "library_arch": "64",
                    "library_path": ".\\vulkan_lvp.dll",
                },
            }), encoding="utf-8")
            (folder / "vulkan_lvp.dll").write_bytes(b"unverified_binary")
            self.assertIsNone(_verified_software_vulkan_icd(Path(base) / "state"))
            expected = Mock()
            expected.hexdigest.return_value = _SOFTWARE_VULKAN_LVP_SHA256
            with patch(
                "ordax_dev_agent.blender_live_bridge.hashlib.file_digest",
                return_value=expected,
            ):
                selected = _verified_software_vulkan_icd(Path(base) / "state")
                self.assertIsNotNone(selected)
                # Windows hosted runners can expose the same file through
                # long-user-profile and 8.3 short-path aliases.
                self.assertTrue(selected.samefile(manifest))
                manifest.write_text('{"ICD":{"library_path":"../escaped.dll"}}', encoding="utf-8")
                self.assertIsNone(_verified_software_vulkan_icd(Path(base) / "state"))

    def test_owned_gpu_failure_uses_one_process_scoped_software_vulkan_fallback(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            project = Project(slug="gpu_smoke", root=base, apps=("blender",))
            bridge = BlenderLiveBridge(
                SimpleNamespace(state_dir=base / "state"), project,
            )
            cpu_icd = base / "state" / "blender-gpu" / "software-vulkan" / "lvp_icd.x86_64.json"
            first = Mock()
            first.pid = 1048
            first.poll.return_value = None
            first.wait.return_value = 0
            fallback = Mock()
            fallback.pid = 2048
            fallback.poll.return_value = None
            graphics_modal = {
                "title": "Blender - Unsupported Graphics Card Configuration",
                "message": "OpenGL 4.3 or higher is required",
            }
            with (
                patch("ordax_dev_agent.blender_live_bridge.find_blender", return_value=base / "blender.exe"),
                patch.object(bridge, "_companion_fingerprint", return_value="valid"),
                patch.object(bridge, "presence_is_fresh", side_effect=[False, False, False, True]),
                patch.object(bridge, "status", return_value={
                    "identity_matches": True,
                    "protocol_compatible": True,
                    "companion_current": True,
                }),
                patch("ordax_dev_agent.blender_live_bridge.subprocess.Popen",
                      side_effect=[first, fallback]) as spawn,
                patch("ordax_dev_agent.blender_live_bridge._verified_software_vulkan_icd",
                      side_effect=[None, cpu_icd]) as driver,
                patch("ordax_dev_agent.blender_live_bridge._windows_blender_graphics_error_for_pid",
                      return_value=graphics_modal) as graphics_probe,
            ):
                result = bridge.start(wait_seconds=10)
            self.assertTrue(result.ok, result.summary)
            self.assertEqual(result.data["pid"], 2048)
            self.assertEqual(result.data["graphics_backend"], "vulkan-software")
            self.assertEqual(result.data["transport"], "blender-visible-companion")
            first.terminate.assert_called_once()
            first.wait.assert_called_once_with(timeout=2.0)
            fallback.terminate.assert_not_called()
            self.assertEqual(driver.call_count, 2)
            self.assertEqual(driver.call_args_list[0].args, (base / "state",))
            self.assertEqual(driver.call_args_list[1].args, (base / "state",))
            graphics_probe.assert_called_once_with(1048)
            self.assertEqual(spawn.call_count, 2)
            native_args, native_kwargs = spawn.call_args_list[0]
            cpu_args, cpu_kwargs = spawn.call_args_list[1]
            self.assertNotIn("--gpu-backend", native_args[0])
            self.assertEqual(native_kwargs["env"]["BLENDER_USER_CONFIG"], str(bridge.user_config_root))
            self.assertEqual(native_kwargs["env"]["BLENDER_USER_SCRIPTS"], str(bridge.user_scripts_root))
            self.assertIn("--gpu-backend", cpu_args[0])
            self.assertIn("vulkan", cpu_args[0])
            self.assertEqual(cpu_kwargs["env"]["VK_DRIVER_FILES"], str(cpu_icd))
            self.assertEqual(cpu_kwargs["env"]["VK_ICD_FILENAMES"], str(cpu_icd))
            self.assertEqual(cpu_kwargs["env"]["OIDN_DEVICE_SYCL"], "0")
            self.assertEqual(native_kwargs["env"].get("OIDN_DEVICE_SYCL"), os.environ.get("OIDN_DEVICE_SYCL"))
            self.assertEqual(cpu_kwargs["env"]["BLENDER_USER_CONFIG"], str(bridge.user_config_root))
            self.assertEqual(cpu_kwargs["env"]["BLENDER_USER_SCRIPTS"], str(bridge.user_scripts_root))



    def test_project_scoped_blender_user_environment_does_not_mutate_global_settings(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            project = Project(slug="gpu_smoke", root=base, apps=("blender",))
            bridge = BlenderLiveBridge(SimpleNamespace(state_dir=base / "state"), project)
            process = Mock()
            process.pid = 1357
            process.poll.return_value = None
            with (
                patch.dict(os.environ, {
                    "BLENDER_USER_CONFIG": "user-global-preferences",
                    "BLENDER_USER_SCRIPTS": "user-global-scripts",
                    "OIDN_DEVICE_SYCL": "1",
                }),
                patch("ordax_dev_agent.blender_live_bridge.find_blender", return_value=base / "blender.exe"),
                patch.object(bridge, "_companion_fingerprint", return_value="valid"),
                patch.object(bridge, "presence_is_fresh", side_effect=[False, True]),
                patch.object(bridge, "status", return_value={
                    "identity_matches": True,
                    "protocol_compatible": True,
                    "companion_current": True,
                }),
                patch("ordax_dev_agent.blender_live_bridge._verified_software_vulkan_icd", return_value=None),
                patch("ordax_dev_agent.blender_live_bridge.subprocess.Popen", return_value=process) as spawn,
            ):
                result = bridge.start(wait_seconds=20)
                self.assertTrue(result.ok, result.summary)
                env = spawn.call_args.kwargs["env"]
                self.assertEqual(env["BLENDER_USER_CONFIG"], str(bridge.user_config_root))
                self.assertEqual(env["BLENDER_USER_SCRIPTS"], str(bridge.user_scripts_root))
                self.assertEqual(env["OIDN_DEVICE_SYCL"], "1")
                self.assertTrue(bridge.user_config_root.is_dir())
                self.assertTrue(bridge.user_scripts_root.is_dir())
                self.assertNotIn("VK_DRIVER_FILES", {
                    k:v for k,v in env.items() if k not in os.environ
                })
                self.assertEqual(os.environ["BLENDER_USER_CONFIG"], "user-global-preferences")
                self.assertEqual(os.environ["BLENDER_USER_SCRIPTS"], "user-global-scripts")
                self.assertEqual(os.environ["OIDN_DEVICE_SYCL"], "1")
            sibling = BlenderLiveBridge(
                SimpleNamespace(state_dir=base / "state"),
                Project(slug="other_registered_project", root=base, apps=("blender",)),
            )
            self.assertNotEqual(bridge.user_config_root, sibling.user_config_root)
            self.assertNotEqual(bridge.user_scripts_root, sibling.user_scripts_root)

    def test_expired_unresponsive_companion_stops_only_owned_startup_pid(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            bridge = BlenderLiveBridge(
                SimpleNamespace(state_dir=base / "state"),
                Project(slug="gpu_smoke", root=base, apps=("blender",)),
            )
            process = Mock()
            process.pid = 2468
            process.poll.side_effect = [None, 0]
            process.wait.return_value = 0
            with (
                patch("ordax_dev_agent.blender_live_bridge.find_blender", return_value=base / "blender.exe"),
                patch.object(bridge, "_companion_fingerprint", return_value="valid"),
                patch("ordax_dev_agent.blender_live_bridge.subprocess.Popen", return_value=process),
                patch("ordax_dev_agent.blender_live_bridge.time.monotonic", side_effect=[1.0, 10.0]),
            ):
                result = bridge.start(wait_seconds=3)
            self.assertFalse(result.ok)
            self.assertEqual(result.data["error_code"], "blender_companion_startup_timeout")
            self.assertTrue(result.data["terminated_owned_startup"])
            self.assertEqual(result.data["pid"], 2468)
            process.terminate.assert_called_once_with()
            process.wait.assert_called_once_with(timeout=2.0)
            process.kill.assert_not_called()

    def test_explicitly_provisioned_verified_driver_is_selected_on_first_launch(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            project = Project(slug="gpu_smoke", root=base, apps=("blender",))
            bridge = BlenderLiveBridge(
                SimpleNamespace(state_dir=base / "state"), project,
            )
            icd = base / "state" / "blender-gpu" / "software-vulkan" / "lvp_icd.x86_64.json"
            process = Mock()
            process.pid = 3056
            process.poll.return_value = None
            with (
                patch("ordax_dev_agent.blender_live_bridge.find_blender", return_value=base / "blender.exe"),
                patch.object(bridge, "_companion_fingerprint", return_value="valid"),
                patch.object(bridge, "presence_is_fresh", side_effect=[False, True]),
                patch.object(bridge, "status", return_value={
                    "identity_matches": True,
                    "protocol_compatible": True,
                    "companion_current": True,
                }),
                patch("ordax_dev_agent.blender_live_bridge._verified_software_vulkan_icd",
                      return_value=icd) as driver,
                patch("ordax_dev_agent.blender_live_bridge.subprocess.Popen",
                      return_value=process) as spawn,
                patch("ordax_dev_agent.blender_live_bridge._windows_blender_graphics_error_for_pid") as gpu_probe,
            ):
                result = bridge.start(wait_seconds=45)
            self.assertTrue(result.ok, result.summary)
            self.assertEqual(result.data["pid"], 3056)
            self.assertEqual(result.data["graphics_backend"], "vulkan-software")
            driver.assert_called_once_with(base / "state")
            spawn.assert_called_once()
            argv, kwargs = spawn.call_args
            self.assertEqual(argv[0][3:5], ["--gpu-backend", "vulkan"])
            self.assertEqual(kwargs["env"]["VK_DRIVER_FILES"], str(icd))
            self.assertEqual(kwargs["env"]["VK_ICD_FILENAMES"], str(icd))
            self.assertEqual(kwargs["env"]["OIDN_DEVICE_SYCL"], "0")
            self.assertEqual(kwargs["env"]["BLENDER_USER_CONFIG"], str(bridge.user_config_root))
            self.assertEqual(kwargs["env"]["BLENDER_USER_SCRIPTS"], str(bridge.user_scripts_root))
            gpu_probe.assert_not_called()
            process.terminate.assert_not_called()



if __name__ == "__main__":
    unittest.main()
