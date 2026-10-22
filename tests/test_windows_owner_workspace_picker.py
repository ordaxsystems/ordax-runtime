"""No-interaction Windows native picker contract and COM lifecycle tests.

Never opens a dialog, accesses user folders, grants a resource or changes the
host Windows desktop. GitHub Windows runners only exercise mock COM calls.
"""
from __future__ import annotations

import ctypes
from pathlib import Path
import tempfile
import unittest
import uuid
from unittest.mock import Mock, patch

from ordax_dev_agent.project_workspace_port import ProjectWorkspaceDenied
from ordax_dev_agent.projects import Project
from ordax_dev_agent import windows_owner_workspace_picker as native


class OwnerNativePickerTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.project = Project("fixture", self.root)
        self.picker = native.WindowsOwnerWorkspacePicker()

    def test_dialog_callbacks_are_explicit_and_scoped_to_owner_desktop(self):
        with patch.object(native, "_require_interactive_owner_desktop") as require, \
             patch.object(native, "_native_choose_directory", return_value=self.root) as choose, \
             patch.object(native, "_native_confirm_owner", return_value=True) as confirm:
            self.assertEqual(self.picker.choose_directory(self.project), self.root)
            self.assertTrue(self.picker.confirm_owner(self.project, self.root))
            self.assertEqual(require.call_count, 2)
            choose.assert_called_once_with(self.project)
            confirm.assert_called_once_with(self.project, self.root)

    def test_cancelled_dialog_and_negative_confirmation_deny(self):
        with patch.object(native, "_require_interactive_owner_desktop"), \
             patch.object(native, "_native_choose_directory", return_value=None), \
             patch.object(native, "_native_confirm_owner", return_value=False):
            self.assertIsNone(self.picker.choose_directory(self.project))
            self.assertFalse(self.picker.confirm_owner(self.project, self.root))

    def test_untrusted_argument_shapes_never_open_native_ui(self):
        with patch.object(native, "_require_interactive_owner_desktop"), \
             patch.object(native, "_native_choose_directory") as choose, \
             patch.object(native, "_native_confirm_owner") as confirm:
            with self.assertRaises(ProjectWorkspaceDenied):
                self.picker.choose_directory({"root": str(self.root)})
            with self.assertRaises(ProjectWorkspaceDenied):
                self.picker.confirm_owner(self.project, str(self.root))
            choose.assert_not_called()
            confirm.assert_not_called()

    def test_no_interactive_session_fails_before_native_picker_or_confirmation(self):
        with patch.object(native, "_require_interactive_owner_desktop",
                          side_effect=ProjectWorkspaceDenied("workspace_owner_desktop_required")), \
             patch.object(native, "_native_choose_directory") as choose, \
             patch.object(native, "_native_confirm_owner") as confirm:
            for method in (
                lambda: self.picker.choose_directory(self.project),
                lambda: self.picker.confirm_owner(self.project, self.root),
            ):
                with self.assertRaisesRegex(ProjectWorkspaceDenied, "owner_desktop_required"):
                    method()
            choose.assert_not_called()
            confirm.assert_not_called()

    def test_session_gate_rejects_non_windows_and_foreign_worker_threads(self):
        with patch.object(native.os, "name", "posix"), \
             patch.object(native.ctypes, "WinDLL", create=True) as load:
            with self.assertRaisesRegex(ProjectWorkspaceDenied, "owner_desktop_required"):
                native._require_interactive_owner_desktop()
            load.assert_not_called()
        with patch.object(native.os, "name", "nt"), \
             patch.object(native.threading, "current_thread", return_value=object()), \
             patch.object(native.ctypes, "WinDLL", create=True) as load:
            with self.assertRaisesRegex(ProjectWorkspaceDenied, "owner_desktop_required"):
                native._require_interactive_owner_desktop()
            load.assert_not_called()

    def test_native_guid_layout_is_little_endian_com_abi(self):
        sample = "dc1c5a9c-e88a-4dde-a5a1-60f82a20aef7"
        self.assertEqual(bytes(native._Guid.parse(sample)), uuid.UUID(sample).bytes_le)
        self.assertEqual(ctypes.sizeof(native._Guid), 16)

    def test_no_com_initialization_on_failed_session(self):
        # Service session 0, task scheduler and headless app hosts must never
        # start the interactive Windows shell picker.
        with patch.object(native, "_require_interactive_owner_desktop",
                          side_effect=ProjectWorkspaceDenied("workspace_owner_desktop_required")), \
             patch.object(native.ctypes, "WinDLL", create=True) as factory:
            with self.assertRaises(ProjectWorkspaceDenied):
                self.picker.choose_directory(self.project)
            factory.assert_not_called()

    def test_com_cancel_releases_all_interfaces_and_apartment(self):
        ole = Mock()
        shell = Mock()
        ole.CoInitializeEx.return_value = 0
        ole.CoCreateInstance.side_effect = lambda _clsid, _outer, _scope, _iid, output: (
            ctypes.cast(output, ctypes.POINTER(ctypes.c_void_p)).__setitem__(0, 101) or 0
        )
        shell.SHCreateItemFromParsingName.side_effect = lambda _path, _bind, _iid, output: (
            ctypes.cast(output, ctypes.POINTER(ctypes.c_void_p)).__setitem__(0, 102) or 0
        )
        calls = []
        def invoke(ptr, slot, types, *args):
            calls.append((ptr.value, slot))
            if slot == 10:
                ctypes.cast(args[0], ctypes.POINTER(native.wintypes.DWORD))[0] = 0
            if slot == 3:
                return ctypes.c_int32(native._CANCELLED).value
            return 0
        with patch.object(native.ctypes, "WinDLL", create=True,
                          side_effect=lambda name, **kw: ole if name=="ole32" else shell), \
             patch.object(native, "_invoke", side_effect=invoke):
            self.assertIsNone(native._native_choose_directory(self.project))
        self.assertIn((101, 9), calls)  # SetOptions
        self.assertIn((101, 12), calls)  # SetFolder
        self.assertIn((101, 3), calls)  # Show
        self.assertEqual(calls.count((102, 2)), 1)
        self.assertEqual(calls.count((101, 2)), 1)
        ole.CoUninitialize.assert_called_once()
        ole.CoTaskMemFree.assert_not_called()

    def test_com_failure_releases_apartment_and_never_returns_raw_path(self):
        ole = Mock()
        shell = Mock()
        ole.CoInitializeEx.return_value = 0
        ole.CoCreateInstance.return_value = ctypes.c_int32(0x80004005).value
        with patch.object(native.ctypes, "WinDLL", create=True,
                          side_effect=lambda name, **kw: ole if name=="ole32" else shell):
            with self.assertRaisesRegex(ProjectWorkspaceDenied, "workspace_picker_unavailable"):
                native._native_choose_directory(self.project)
        ole.CoUninitialize.assert_called_once()

    def test_com_apartment_incompatible_refuses_dialog(self):
        ole, shell = Mock(), Mock()
        ole.CoInitializeEx.return_value = ctypes.c_int32(0x80010106).value
        with patch.object(native.ctypes, "WinDLL", create=True,
                          side_effect=lambda name, **kw: ole if name=="ole32" else shell):
            with self.assertRaisesRegex(ProjectWorkspaceDenied, "workspace_picker_unavailable"):
                native._native_choose_directory(self.project)
        ole.CoCreateInstance.assert_not_called()
        ole.CoUninitialize.assert_not_called()

    def test_owner_confirmation_defaults_to_no_and_treats_errors_as_deny(self):
        user = Mock()
        with patch.object(native.ctypes, "WinDLL", create=True, return_value=user):
            user.MessageBoxW.return_value = 7  # IDNO
            self.assertFalse(native._native_confirm_owner(self.project, self.root))
            flags = user.MessageBoxW.call_args.args[-1]
            self.assertTrue(flags & native._MB_DEFBUTTON2)
            self.assertTrue(flags & native._MB_YESNO)
            user.MessageBoxW.return_value = 0
            with self.assertRaisesRegex(ProjectWorkspaceDenied, "workspace_confirmation_unavailable"):
                native._native_confirm_owner(self.project, self.root)
            user.MessageBoxW.return_value = 6  # IDYES, explicit action
            self.assertTrue(native._native_confirm_owner(self.project, self.root))


if __name__ == "__main__":
    unittest.main()
