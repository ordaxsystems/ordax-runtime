from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from ordax_dev_agent.computer_control_actions import ComputerControlActions


class _Harness(ComputerControlActions):
    def _project(self, payload):
        return SimpleNamespace(slug=str(payload.get("project") or "demo"))


class ComputerFocusWindowTests(unittest.TestCase):
    def setUp(self):
        self.actions = _Harness()
        self.user32 = Mock()
        self.actions._user32 = Mock(return_value=self.user32)
        self.user32.IsWindow.return_value = True
        self.user32.IsIconic.return_value = False
        self.user32.GetForegroundWindow.return_value = 0x100
        self.user32.GetWindowThreadProcessId.side_effect = lambda hwnd, _: {0x100: 10, 0x200: 20}.get(hwnd, 0)
        self.user32.AttachThreadInput.return_value = True
        self.user32.SetForegroundWindow.return_value = False

    def test_focus_temporarily_attaches_input_threads_and_detaches_them(self):
        self.actions._window_info = Mock(return_value={"handle": "0x200", "foreground": True})
        with patch.object(_Harness, "_current_thread_id", return_value=30), patch("time.sleep"):
            result = self.actions.computer_focus_window({"project": "demo", "handle": "0x200"})

        self.assertTrue(result.ok)
        self.assertEqual(result.summary, "window focused")
        self.user32.AttachThreadInput.assert_any_call(30, 10, True)
        self.user32.AttachThreadInput.assert_any_call(30, 20, True)
        self.user32.AttachThreadInput.assert_any_call(20, 10, True)
        calls = [c.args for c in self.user32.AttachThreadInput.call_args_list]
        self.assertEqual(calls[-3:], [(20, 10, False), (30, 20, False), (30, 10, False)])
        self.user32.SetActiveWindow.assert_called_once_with(0x200)
        self.user32.SetFocus.assert_called_once_with(0x200)

    def test_focus_failure_still_detaches_all_attached_threads(self):
        self.actions._window_info = Mock(return_value={"handle": "0x200", "foreground": False})
        with patch.object(_Harness, "_current_thread_id", return_value=30), patch("time.sleep"):
            result = self.actions.computer_focus_window({"project": "demo", "handle": "0x200"})

        self.assertFalse(result.ok)
        calls = [c.args for c in self.user32.AttachThreadInput.call_args_list]
        self.assertEqual(calls[-3:], [(20, 10, False), (30, 20, False), (30, 10, False)])

    def test_focus_restores_minimized_target_before_foreground_request(self):
        self.user32.IsIconic.return_value = True
        self.user32.SetForegroundWindow.return_value = True
        self.actions._window_info = Mock(return_value={"handle": "0x200", "foreground": True})
        with patch.object(_Harness, "_current_thread_id", return_value=30), patch("time.sleep"):
            result = self.actions.computer_focus_window({"project": "demo", "handle": "0x200"})

        self.assertTrue(result.ok)
        self.user32.ShowWindow.assert_called_once_with(0x200, 9)

    def test_focus_rejects_missing_window_without_attaching_threads(self):
        self.user32.IsWindow.return_value = False
        result = self.actions.computer_focus_window({"project": "demo", "handle": "0x200"})
        self.assertFalse(result.ok)
        self.user32.AttachThreadInput.assert_not_called()


if __name__ == "__main__":
    unittest.main()
