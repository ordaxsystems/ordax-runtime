"""Windows Blender startup must fail fast on the exact owned GPU dialog."""
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
                self.assertEqual(
                    _verified_software_vulkan_icd(Path(base) / "state"),
                    manifest,
                )
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
                patch.object(bridge, "presence_is_fresh", side_effect=[False, False, True]),
                patch.object(bridge, "status", return_value={
                    "identity_matches": True,
                    "protocol_compatible": True,
                    "companion_current": True,
                }),
                patch("ordax_dev_agent.blender_live_bridge.subprocess.Popen",
                      side_effect=[first, fallback]) as spawn,
                patch("ordax_dev_agent.blender_live_bridge._verified_software_vulkan_icd",
                      return_value=cpu_icd) as driver,
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
            driver.assert_called_once_with(base / "state")
            graphics_probe.assert_called_once_with(1048)
            self.assertEqual(spawn.call_count, 2)
            native_args, native_kwargs = spawn.call_args_list[0]
            cpu_args, cpu_kwargs = spawn.call_args_list[1]
            self.assertNotIn("--gpu-backend", native_args[0])
            self.assertNotIn("env", native_kwargs)
            self.assertIn("--gpu-backend", cpu_args[0])
            self.assertIn("vulkan", cpu_args[0])
            self.assertEqual(cpu_kwargs["env"]["VK_DRIVER_FILES"], str(cpu_icd))
            self.assertEqual(cpu_kwargs["env"]["VK_ICD_FILENAMES"], str(cpu_icd))



if __name__ == "__main__":
    unittest.main()
