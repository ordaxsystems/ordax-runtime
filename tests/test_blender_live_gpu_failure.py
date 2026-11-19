"""Windows Blender startup must fail fast on the exact owned GPU dialog."""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from ordax_dev_agent.blender_live_bridge import (
    BlenderLiveBridge,
    _windows_blender_graphics_error_for_pid,
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


if __name__ == "__main__":
    unittest.main()
