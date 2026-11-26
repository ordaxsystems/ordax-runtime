"""Physical Win32 background dispatch without changing the user's foreground."""
from __future__ import annotations

import ctypes
import os
from ctypes import wintypes
import unittest

from ordax_dev_agent.computer_background_button import click_background_button


@unittest.skipUnless(os.name == "nt", "Windows only")
class PhysicalWin32ButtonTests(unittest.TestCase):
    def test_hidden_native_button_does_not_move_mouse(self):
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        create = user32.CreateWindowExW
        create.argtypes = [
            wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR,
            wintypes.DWORD, ctypes.c_int, ctypes.c_int, ctypes.c_int,
            ctypes.c_int, wintypes.HWND, wintypes.HMENU,
            wintypes.HINSTANCE, ctypes.c_void_p,
        ]
        create.restype = wintypes.HWND
        user32.DestroyWindow.argtypes = [wintypes.HWND]
        user32.DestroyWindow.restype = wintypes.BOOL
        user32.GetForegroundWindow.restype = wintypes.HWND
        user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
        user32.GetCursorPos.restype = wintypes.BOOL
        # Hidden top-level window and real native child Button.
        root = create(0, "STATIC", "OrdaX isolated background test",
                      0x80000000, 0, 0, 200, 100, None, None, None, None)
        self.assertTrue(root)
        try:
            child = create(0, "BUTTON", "OrdaX Test Button",
                           0x40000000 | 0x10000000, 0, 0, 150, 30,
                           root, None, None, None)
            self.assertTrue(child)
            before_cursor = wintypes.POINT()
            self.assertTrue(user32.GetCursorPos(ctypes.byref(before_cursor)))
            receipt = click_background_button({
                "mode": "background", "handle": f"0x{root:X}",
                "expected_pid": os.getpid(),
                "control_text": "OrdaX Test Button",
            })
            after_cursor = wintypes.POINT()
            self.assertTrue(user32.GetCursorPos(ctypes.byref(after_cursor)))
            self.assertTrue(receipt["action_dispatched"])
            self.assertFalse(receipt["side_effect_verified"])
            self.assertEqual((after_cursor.x, after_cursor.y),
                             (before_cursor.x, before_cursor.y))
        finally:
            user32.DestroyWindow(root)


if __name__ == "__main__":
    unittest.main()
