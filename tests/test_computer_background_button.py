"""Targeted Button contract: no implicit global input or silent success."""
from __future__ import annotations

import unittest
from unittest.mock import patch

from ordax_dev_agent.computer_background_button import (
    click_background_button, validate_button_request,
)
from ordax_dev_agent.computer_background_input import UnsupportedBackgroundTarget
from ordax_dev_agent.computer_control_actions import ComputerControlActions


class BackgroundButtonTests(unittest.TestCase):
    def setUp(self):
        self.payload = {
            "mode": "background", "handle": "0xD015A",
            "expected_pid": 15392, "control_text": "SELECT",
            "button": "left", "clicks": 1,
        }

    def test_exact_target_required(self):
        self.assertEqual(validate_button_request(self.payload), (0xD015A, 15392, "SELECT"))
        for bad in (
            {"mode": "background", "control_text": "SELECT"},
            {**self.payload, "expected_pid": "15392"},
            {**self.payload, "expected_pid": True},
            {**self.payload, "handle": "invalid"},
            {**self.payload, "handle": "0x0"},
            {**self.payload, "control_text": ""},
            {**self.payload, "control_text": "SEL\nECT"},
            {**self.payload, "control_text": "A" * 129},
            {**self.payload, "mode": "foreground"},
            {**self.payload, "button": "right"},
            {**self.payload, "clicks": 2},
            {**self.payload, "x": 200},
            {**self.payload, "y": 100},
        ):
            with self.subTest(bad=bad), self.assertRaises(UnsupportedBackgroundTarget):
                validate_button_request(bad)

    def test_non_windows_never_dispatches(self):
        with patch("ordax_dev_agent.computer_background_button.os.name", "posix"):
            with self.assertRaisesRegex(UnsupportedBackgroundTarget, "windows_only"):
                click_background_button(self.payload)

    def test_background_does_not_use_global_mouse(self):
        receipt = {"mode": "background", "action_dispatched": True,
                   "side_effect_verified": False}
        with patch("ordax_dev_agent.computer_background_button.click_background_button",
                   return_value=receipt) as backend, patch.object(
                       ComputerControlActions, "_user32",
                       side_effect=AssertionError("must not call global user32")
                   ):
            result = ComputerControlActions().computer_click(self.payload)
        self.assertTrue(result.ok)
        self.assertFalse(result.data["side_effect_verified"])
        backend.assert_called_once_with(self.payload)

    def test_unavailable_native_control_fails_closed(self):
        with patch("ordax_dev_agent.computer_background_button.click_background_button",
                   side_effect=UnsupportedBackgroundTarget("background_button_missing_or_ambiguous")):
            result = ComputerControlActions().computer_click(self.payload)
        self.assertFalse(result.ok)
        self.assertFalse(result.data["action_dispatched"])
        self.assertEqual(result.data["mode"], "background")

    def test_foreground_rejects_target_override(self):
        result = ComputerControlActions().computer_click({
            "mode": "foreground", "x": 100, "y": 100,
            "handle": "0xABC", "expected_pid": 10,
        })
        self.assertFalse(result.ok)
        self.assertFalse(result.data["action_dispatched"])


if __name__ == "__main__":
    unittest.main()
