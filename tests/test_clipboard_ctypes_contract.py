"""Regression checks for 64-bit Win32 clipboard HGLOBAL signatures.

These checks are intentionally independent of a live desktop clipboard: they
guard the ctypes ABI setup even when CI executes on Linux.
"""
from __future__ import annotations

import ast
import inspect
import unittest

from ordax_dev_agent.computer_parity_actions import ComputerParityActions


def _attributes_assigned_before_call(method_name: str, native_call: str) -> set[str]:
    tree = ast.parse(inspect.getsource(getattr(ComputerParityActions, method_name)))
    method = next(node for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)))
    assignments = {}
    calls = []
    for node in ast.walk(method):
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Attribute) and isinstance(target.value, ast.Attribute):
                    if (isinstance(target.value.value, ast.Name)
                            and target.value.value.id == "kernel32"
                            and target.value.attr == native_call):
                        assignments[target.attr] = node.lineno
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if (isinstance(node.func.value, ast.Name)
                    and node.func.value.id == "kernel32"
                    and node.func.attr == native_call):
                calls.append(node.lineno)
    if not calls:
        raise AssertionError(f"{method_name} must call kernel32.{native_call}")
    return {name for name, line in assignments.items() if line < min(calls)}


class ClipboardCtypesSignatureTests(unittest.TestCase):
    def test_read_declares_global_unlock_pointer_width(self):
        self.assertGreaterEqual(
            _attributes_assigned_before_call("computer_clipboard_read", "GlobalUnlock"),
            {"argtypes", "restype"},
        )

    def test_write_declares_global_unlock_pointer_width(self):
        self.assertGreaterEqual(
            _attributes_assigned_before_call("computer_clipboard_write", "GlobalUnlock"),
            {"argtypes", "restype"},
        )

    def test_write_declares_global_free_pointer_width(self):
        self.assertGreaterEqual(
            _attributes_assigned_before_call("computer_clipboard_write", "GlobalFree"),
            {"argtypes", "restype"},
        )


if __name__ == "__main__":
    unittest.main()
