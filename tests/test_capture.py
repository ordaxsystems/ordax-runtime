import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from mcp_blender_unity.server import _unity_command


class UnityCaptureCommandTests(unittest.TestCase):
    def test_capture_command_can_leave_editor_open_for_automation_exit(self) -> None:
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            project = root / "Project"
            project.mkdir()
            unity = root / "Unity.exe"
            unity.write_bytes(b"test")
            log = root / "unity.log"
            upm = root / "upm.log"

            with patch(
                "mcp_blender_unity.server.resolve_unity",
                return_value={
                    "selected_path": str(unity),
                    "required_version": "6000.6.1f1",
                    "explicit_invalid": False,
                },
            ):
                command = _unity_command(
                    project,
                    log,
                    upm,
                    "HORDAX.EditorTools.AutomationCapture.CapturePrototype",
                    quit_editor=False,
                )

            self.assertIn("-batchmode", command)
            self.assertNotIn("-quit", command)
            self.assertIn("-executeMethod", command)
            self.assertIn("HORDAX.EditorTools.AutomationCapture.CapturePrototype", command)

    def test_normal_unity_command_still_quits(self) -> None:
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            project = root / "Project"
            project.mkdir()
            unity = root / "Unity.exe"

            with patch(
                "mcp_blender_unity.server.resolve_unity",
                return_value={
                    "selected_path": str(unity),
                    "required_version": "6000.6.1f1",
                    "explicit_invalid": False,
                },
            ):
                command = _unity_command(
                    project,
                    root / "unity.log",
                    root / "upm.log",
                )

            self.assertIn("-quit", command)


if __name__ == "__main__":
    unittest.main()
