"""Bounded, target-specific Win32 Edit automation; never sends global input.

Only the exact top-level window HANDLE and PID observed by computer.windows
may be used. Unsupported controls fail closed; no foreground/clipboard fallback.
"""
from __future__ import annotations

import ctypes
import os
from ctypes import wintypes

WM_GETTEXT = 0x000D
WM_GETTEXTLENGTH = 0x000E
WM_CHAR = 0x0102
EM_SETSEL = 0x00B1
GWL_STYLE = -16
ES_PASSWORD = 0x0020
ES_READONLY = 0x0800
SMTO_ABORTIFHUNG = 0x0002
SMTO_BLOCK = 0x0001
GA_ROOT = 2
MAX_TEXT = 65_536
MESSAGE_TIMEOUT_MS = 2000


class UnsupportedBackgroundTarget(ValueError):
    """The exact target is absent, changed, or lacks a supported Edit control."""


def validate_background_request(payload: dict) -> tuple[int, int, str]:
    """Reject text-only/global input and all unexpected or ambiguous arguments."""
    if not isinstance(payload, dict) or set(payload) - {"text", "handle", "expected_pid", "mode"}:
        raise UnsupportedBackgroundTarget("background_input_invalid_arguments")
    if payload.get("mode", "background") != "background":
        raise UnsupportedBackgroundTarget("background_input_foreground_not_supported")
    handle = payload.get("handle")
    if not isinstance(handle, str) or not handle.startswith("0x"):
        raise UnsupportedBackgroundTarget("background_target_handle_required")
    try:
        hwnd = int(handle, 16)
    except ValueError as error:
        raise UnsupportedBackgroundTarget("background_target_handle_invalid") from error
    pid = payload.get("expected_pid")
    if hwnd <= 0 or hwnd >= (1 << (ctypes.sizeof(ctypes.c_void_p) * 8)):
        raise UnsupportedBackgroundTarget("background_target_handle_invalid")
    if type(pid) is not int or pid <= 0 or pid > 0xFFFFFFFF:
        raise UnsupportedBackgroundTarget("background_target_pid_required")
    text = payload.get("text")
    if (not isinstance(text, str) or not text or len(text) > 256
            or any(ord(c) < 0x20 or 0xD800 <= ord(c) <= 0xDFFF for c in text)):
        raise UnsupportedBackgroundTarget("background_text_printable_256_required")
    return hwnd, pid, text


def type_to_background_edit(payload: dict) -> dict:
    """Write each Unicode character to exactly one classic Edit child.

    Does not change foreground, pointer, clipboard or keyboard queue. Requires
    current process/window identity on *each* character, with synchronous
    bounded Win32 messages and read-after-write confirmation. A minimized
    native Edit works because input is sent to its HWND, not screen coordinates.
    """
    hwnd, pid, text = validate_background_request(payload)
    if os.name != "nt":
        raise UnsupportedBackgroundTarget("background_input_windows_only")

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.IsWindow.argtypes = [wintypes.HWND]
    user32.IsWindow.restype = wintypes.BOOL
    user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
    user32.GetAncestor.restype = wintypes.HWND
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetClassNameW.restype = ctypes.c_int
    user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.GetWindowLongW.restype = ctypes.c_long
    user32.IsWindowEnabled.argtypes = [wintypes.HWND]
    user32.IsWindowEnabled.restype = wintypes.BOOL
    user32.SendMessageTimeoutW.argtypes = [
        wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM,
        wintypes.UINT, wintypes.UINT, ctypes.POINTER(ctypes.c_size_t),
    ]
    user32.SendMessageTimeoutW.restype = wintypes.LPARAM

    def owner(window: int) -> int:
        result = wintypes.DWORD()
        if not user32.IsWindow(window):
            raise UnsupportedBackgroundTarget("background_target_closed")
        if not user32.GetWindowThreadProcessId(window, ctypes.byref(result)):
            raise UnsupportedBackgroundTarget("background_target_no_process")
        return int(result.value)

    if owner(hwnd) != pid or int(user32.GetAncestor(hwnd, GA_ROOT)) != hwnd:
        raise UnsupportedBackgroundTarget("background_target_identity_changed")

    candidates: list[int] = []
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def find_edit(child: int, unused: int) -> bool:
        cls = ctypes.create_unicode_buffer(64)
        user32.GetClassNameW(child, cls, 64)
        if cls.value == "Edit" and owner(child) == pid:
            candidates.append(int(child))
        return len(candidates) <= 1

    user32.EnumChildWindows.argtypes = [wintypes.HWND, callback_type, wintypes.LPARAM]
    user32.EnumChildWindows.restype = wintypes.BOOL
    callback = callback_type(find_edit)
    user32.EnumChildWindows(hwnd, callback, 0)
    if len(candidates) != 1:
        raise UnsupportedBackgroundTarget("background_target_unsupported_or_ambiguous_edit")
    edit = candidates[0]
    style = int(user32.GetWindowLongW(edit, GWL_STYLE)) & 0xFFFFFFFF
    if style & (ES_PASSWORD | ES_READONLY) or not user32.IsWindowEnabled(edit):
        raise UnsupportedBackgroundTarget("background_target_protected_or_readonly")

    def checked() -> None:
        if owner(hwnd) != pid or owner(edit) != pid:
            raise UnsupportedBackgroundTarget("background_target_identity_changed")
        if int(user32.GetAncestor(edit, GA_ROOT)) != hwnd:
            raise UnsupportedBackgroundTarget("background_target_parent_changed")
        cls = ctypes.create_unicode_buffer(64)
        user32.GetClassNameW(edit, cls, 64)
        if cls.value != "Edit":
            raise UnsupportedBackgroundTarget("background_target_class_changed")

    def send(message: int, wp: int, lp: int = 0) -> int:
        checked()
        result = ctypes.c_size_t()
        accepted = user32.SendMessageTimeoutW(
            edit, message, wp, lp, SMTO_ABORTIFHUNG | SMTO_BLOCK,
            MESSAGE_TIMEOUT_MS, ctypes.byref(result),
        )
        if not accepted:
            raise UnsupportedBackgroundTarget("background_target_message_failed_or_timed_out")
        return int(result.value)

    def read() -> str:
        length = send(WM_GETTEXTLENGTH, 0)
        if length > MAX_TEXT:
            raise UnsupportedBackgroundTarget("background_target_text_too_large")
        data = ctypes.create_unicode_buffer(length + 2)
        send(WM_GETTEXT, len(data), ctypes.addressof(data))
        return data.value

    before = read()
    if len(before) + len(text) > MAX_TEXT:
        raise UnsupportedBackgroundTarget("background_target_text_too_large")
    expected = before
    for char in text:
        current = read()
        if current != expected:
            raise UnsupportedBackgroundTarget("background_target_concurrent_modification")
        # Select end in the *target control only*; never touch focus or cursor.
        send(EM_SETSEL, len(current), len(current))
        send(WM_CHAR, ord(char))
        expected += char
        if read() != expected:
            raise UnsupportedBackgroundTarget("background_target_write_unverified")

    if read() != expected:
        raise UnsupportedBackgroundTarget("background_target_final_verification_failed")
    return {
        "characters": len(text),
        "handle": f"0x{hwnd:X}",
        "expected_pid": pid,
        "mode": "background",
        "control": "Win32.Edit",
        "verified": True,
    }
