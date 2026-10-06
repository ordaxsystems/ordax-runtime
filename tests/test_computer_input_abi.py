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

    @unittest.skipUnless(os.name == "nt", "SendInput is Windows-only")
    def test_send_unicode_submits_complete_key_pairs(self):
        user32 = Mock()
        user32.SendInput.return_value = 2
        with patch("ctypes.windll.user32", user32):
            ComputerControlActions._send_unicode("A")

        user32.SendInput.assert_called_once()
        count, inputs, cb_size = user32.SendInput.call_args.args
        self.assertEqual(count, 2)
        self.assertEqual(cb_size, ctypes.sizeof(_INPUT))
        self.assertEqual(inputs[0].type, 1)
        self.assertEqual(inputs[1].type, 1)


if __name__ == "__main__":
    unittest.main()
