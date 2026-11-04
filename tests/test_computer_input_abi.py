from __future__ import annotations

import ctypes
import os
import unittest
from unittest.mock import Mock, patch

from ordax_dev_agent.computer_control_actions import (
    ComputerControlActions,
    _HARDWAREINPUT,
    _INPUT,
    _INPUTUNION,
    _KEYBDINPUT,
    _MOUSEINPUT,
)


class ComputerInputAbiTests(unittest.TestCase):
    def test_input_matches_win32_abi_size(self):
        pointer_bits = ctypes.sizeof(ctypes.c_void_p) * 8
        expected_input_size = 40 if pointer_bits == 64 else 28
        expected_mouse_size = 32 if pointer_bits == 64 else 24

        self.assertEqual(ctypes.sizeof(_MOUSEINPUT), expected_mouse_size)
        self.assertEqual(ctypes.sizeof(_INPUT), expected_input_size)
        self.assertGreaterEqual(ctypes.sizeof(_INPUTUNION), ctypes.sizeof(_KEYBDINPUT))
        self.assertGreaterEqual(ctypes.sizeof(_INPUTUNION), ctypes.sizeof(_HARDWAREINPUT))

    def test_type_without_target_never_reaches_global_sendinput(self):
        with patch.object(ComputerControlActions, "_send_unicode") as send:
            result = ComputerControlActions().computer_type({"text": "A"})
        self.assertFalse(result.ok)
        self.assertEqual(result.data["error_code"], "background_target_handle_required")
        send.assert_not_called()


if __name__ == "__main__":
    unittest.main()
