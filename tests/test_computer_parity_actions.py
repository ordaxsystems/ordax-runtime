from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from ordax_dev_agent.computer_parity_actions import ComputerParityActions


class _Harness(ComputerParityActions):
    def _project(self, payload):
        return SimpleNamespace(slug=str(payload.get("project") or "demo"))

    @staticmethod
    def _windows_only():
        return None


class ComputerParityActionTests(unittest.TestCase):
    def setUp(self):
        self.actions = _Harness()
        self.user32 = Mock()
        self.actions._user32 = Mock(return_value=self.user32)

    def test_mouse_move_rejects_coordinate_outside_virtual_desktop(self):
        with patch.object(
            _Harness,
            "_virtual_screen_rect",
            return_value={"left": 0, "top": 0, "right": 1920, "bottom": 1080, "width": 1920, "height": 1080},
        ):
            result = self.actions.computer_mouse_move({"project": "demo", "x": 2500, "y": 100})
        self.assertFalse(result.ok)
        self.assertIn("outside", result.summary)

    def test_mouse_move_uses_bounded_motion_helper(self):
        with patch.object(
            _Harness,
            "_virtual_screen_rect",
            return_value={"left": 0, "top": 0, "right": 1920, "bottom": 1080, "width": 1920, "height": 1080},
        ), patch.object(self.actions, "_move_cursor") as move:
            result = self.actions.computer_mouse_move(
                {"project": "demo", "x": 400, "y": 300, "duration_ms": 250}
            )
        self.assertTrue(result.ok)
        move.assert_called_once_with(self.user32, 400, 300, 250)

    def test_drag_rejects_unbounded_duration_before_mouse_down(self):
        with patch.object(
            _Harness,
            "_virtual_screen_rect",
            return_value={"left": 0, "top": 0, "right": 1920, "bottom": 1080, "width": 1920, "height": 1080},
        ):
            result = self.actions.computer_drag(
                {
                    "project": "demo",
                    "from_x": 10,
                    "from_y": 10,
                    "to_x": 100,
                    "to_y": 100,
                    "duration_ms": 10000,
                }
            )
        self.assertFalse(result.ok)
        self.user32.mouse_event.assert_not_called()

    def test_clipboard_read_rejects_oversized_bound_without_opening_clipboard(self):
        with patch.object(self.actions, "_open_clipboard") as open_clipboard:
            result = self.actions.computer_clipboard_read(
                {"project": "demo", "max_bytes": (1024 * 1024) + 1}
            )
        self.assertFalse(result.ok)
        open_clipboard.assert_not_called()

    def test_clipboard_write_rejects_oversized_text_without_touching_windows(self):
        with patch.object(self.actions, "_open_clipboard") as open_clipboard:
            result = self.actions.computer_clipboard_write(
                {"project": "demo", "text": "x" * ((1024 * 1024) + 1)}
            )
        self.assertFalse(result.ok)
        open_clipboard.assert_not_called()


class ComputerLaunchAppTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.state = self.root / "state"
        self.state.mkdir()
        self.actions = _Harness()
        self.actions.config = SimpleNamespace(state_dir=self.state, agent_repo_path=self.root / "runtime")
        self._write_policy([])

    def _write_policy(self, applications):
        (self.state / "agent-settings.json").write_text(
            json.dumps(
                {
                    "computer_access": {
                        "enabled": True,
                        "full_filesystem": False,
                        "allowed_roots": [str(self.root.resolve())],
                        "allowed_applications": applications,
                    }
                }
            ),
            encoding="utf-8",
        )

    def test_launch_app_rejects_non_executable_absolute_path(self):
        with patch("pathlib.Path.is_file", return_value=True):
            result = self.actions.computer_launch_app(
                {"project": "demo", "application": str(Path.cwd() / "readme.txt")}
            )
        self.assertFalse(result.ok)
        self.assertIn(".exe", result.summary)

    def test_launch_app_rejects_too_many_arguments_before_spawn(self):
        with patch("subprocess.Popen") as popen:
            result = self.actions.computer_launch_app(
                {
                    "project": "demo",
                    "application": "notepad.exe",
                    "args": ["x"] * 33,
                }
            )
        self.assertFalse(result.ok)
        popen.assert_not_called()

    def test_launch_app_denies_executable_not_in_local_allowlist(self):
        executable = self.root / "viewer.exe"
        executable.write_bytes(b"stub")
        with patch("subprocess.Popen") as popen:
            result = self.actions.computer_launch_app(
                {"project": "demo", "application": str(executable)}
            )
        self.assertFalse(result.ok)
        self.assertIn("not allowlisted", result.summary)
        popen.assert_not_called()

    def test_launch_app_allows_explicit_absolute_application(self):
        executable = self.root / "viewer.exe"
        executable.write_bytes(b"stub")
        self._write_policy([str(executable.resolve())])
        process = SimpleNamespace(pid=4242)
        with patch("subprocess.Popen", return_value=process) as popen:
            result = self.actions.computer_launch_app(
                {"project": "demo", "application": str(executable)}
            )
        self.assertTrue(result.ok, result.summary)
        self.assertEqual(4242, result.data["pid"])
        popen.assert_called_once()

    def test_launch_app_allows_explicit_executable_basename(self):
        executable = self.root / "viewer.exe"
        executable.write_bytes(b"stub")
        self._write_policy(["viewer.exe"])
        process = SimpleNamespace(pid=4243)
        with patch("shutil.which", return_value=str(executable)), patch(
            "subprocess.Popen", return_value=process
        ) as popen:
            result = self.actions.computer_launch_app(
                {"project": "demo", "application": "viewer.exe"}
            )
        self.assertTrue(result.ok, result.summary)
        popen.assert_called_once()


if __name__ == "__main__":
    unittest.main()
