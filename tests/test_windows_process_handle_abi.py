"""Win64 regression coverage for persistent-process liveness checks."""
from __future__ import annotations

import ctypes
import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ordax_dev_agent.persistent_process_actions import PersistentProcessActions


@unittest.skipUnless(os.name == "nt", "requires Windows")
class WindowsProcessHandleABITests(unittest.TestCase):
    def test_process_handle_is_not_truncated_to_32_bits(self):
        kernel32 = Mock()
        handle = 0x100000001
        kernel32.OpenProcess.return_value = handle
        kernel32.WaitForSingleObject.return_value = 0x102
        with patch("ctypes.windll", SimpleNamespace(kernel32=kernel32)):
            running = PersistentProcessActions._pid_running(1234)
        self.assertTrue(running)
        self.assertEqual(kernel32.OpenProcess.argtypes, [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong])
        self.assertIs(kernel32.OpenProcess.restype, ctypes.c_void_p)
        self.assertEqual(kernel32.WaitForSingleObject.argtypes, [ctypes.c_void_p, ctypes.c_ulong])
        self.assertEqual(kernel32.CloseHandle.argtypes, [ctypes.c_void_p])
        kernel32.WaitForSingleObject.assert_called_once_with(handle, 0)
        kernel32.CloseHandle.assert_called_once_with(handle)

    def test_missing_process_handle_reports_not_running(self):
        kernel32 = Mock()
        kernel32.OpenProcess.return_value = 0
        with patch("ctypes.windll", SimpleNamespace(kernel32=kernel32)):
            running = PersistentProcessActions._pid_running(1234)
        self.assertFalse(running)
        kernel32.CloseHandle.assert_not_called()


if __name__ == "__main__":
    unittest.main()
