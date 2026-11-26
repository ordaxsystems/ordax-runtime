"""Bounded Win32 Button invocation without global mouse, keyboard or focus."""
from __future__ import annotations

import ctypes
import os
from ctypes import wintypes

from .computer_background_input import UnsupportedBackgroundTarget

GA_ROOT = 2
SMTO_ABORTIFHUNG = 0x0002
SMTO_BLOCK = 0x0001
BM_CLICK = 0x00F5


def validate_button_request(payload: dict) -> tuple[int, int, str]:
    if not isinstance(payload, dict) or set(payload) - {
        "handle", "expected_pid", "control_text", "mode", "button", "clicks"
    }:
        raise UnsupportedBackgroundTarget("background_button_invalid_arguments")
    if payload.get("mode") != "background":
        raise UnsupportedBackgroundTarget("background_button_mode_required")
    if payload.get("button", "left") != "left" or payload.get("clicks", 1) != 1:
        raise UnsupportedBackgroundTarget("background_button_left_single_click_only")
    handle, pid, name = (
        payload.get("handle"), payload.get("expected_pid"), payload.get("control_text")
    )
    if type(handle) is not str or not handle.startswith("0x"):
        raise UnsupportedBackgroundTarget("background_target_handle_required")
    try:
        hwnd = int(handle, 16)
    except ValueError as error:
        raise UnsupportedBackgroundTarget("background_target_handle_invalid") from error
    if not 0 < hwnd < (1 << (ctypes.sizeof(ctypes.c_void_p) * 8)):
        raise UnsupportedBackgroundTarget("background_target_handle_invalid")
    if type(pid) is not int or not 0 < pid <= 0xFFFFFFFF:
        raise UnsupportedBackgroundTarget("background_target_pid_required")
    if (type(name) is not str or not 1 <= len(name) <= 128
            or any(ord(c) < 0x20 or 0xD800 <= ord(c) <= 0xDFFF for c in name)):
        raise UnsupportedBackgroundTarget("background_button_exact_text_required")
    return hwnd, pid, name


def click_background_button(payload: dict) -> dict:
    """Dispatch BM_CLICK to one exact classic Button, without global input.

    No fallback for custom, elevated, owner-drawn or web controls. A dispatched
    message is not evidence that the application completed the requested work.
    """
    hwnd, pid, name = validate_button_request(payload)
    if os.name != "nt":
        raise UnsupportedBackgroundTarget("background_input_windows_only")
    u = ctypes.WinDLL("user32", use_last_error=True)
    u.IsWindow.argtypes = [wintypes.HWND]
    u.IsWindow.restype = wintypes.BOOL
    u.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
    u.GetAncestor.restype = wintypes.HWND
    u.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    u.GetWindowThreadProcessId.restype = wintypes.DWORD
    u.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    u.GetClassNameW.restype = ctypes.c_int
    u.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    u.GetWindowTextW.restype = ctypes.c_int
    u.IsWindowEnabled.argtypes = [wintypes.HWND]
    u.IsWindowEnabled.restype = wintypes.BOOL
    u.SendMessageTimeoutW.argtypes = [
        wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM,
        wintypes.UINT, wintypes.UINT, ctypes.POINTER(ctypes.c_size_t),
    ]
    u.SendMessageTimeoutW.restype = wintypes.LPARAM
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    u.EnumChildWindows.argtypes = [wintypes.HWND, callback_type, wintypes.LPARAM]
    u.EnumChildWindows.restype = wintypes.BOOL

    def owner(window: int) -> int:
        result = wintypes.DWORD()
        if not u.IsWindow(window):
            raise UnsupportedBackgroundTarget("background_target_closed")
        if not u.GetWindowThreadProcessId(window, ctypes.byref(result)):
            raise UnsupportedBackgroundTarget("background_target_no_process")
        return int(result.value)

    def title(window: int) -> str:
        result = ctypes.create_unicode_buffer(256)
        u.GetWindowTextW(window, result, len(result))
        return result.value

    def check_root() -> None:
        if owner(hwnd) != pid or int(u.GetAncestor(hwnd, GA_ROOT)) != hwnd:
            raise UnsupportedBackgroundTarget("background_target_identity_changed")

    check_root()
    matches: list[int] = []

    def visit(child: int, unused: int) -> bool:
        cls = ctypes.create_unicode_buffer(64)
        u.GetClassNameW(child, cls, len(cls))
        if cls.value == "Button" and title(child) == name and owner(child) == pid:
            matches.append(int(child))
        return len(matches) <= 1

    callback = callback_type(visit)
    u.EnumChildWindows(hwnd, callback, 0)
    if len(matches) != 1:
        raise UnsupportedBackgroundTarget("background_button_missing_or_ambiguous")
    target = matches[0]
    check_root()
    cls = ctypes.create_unicode_buffer(64)
    u.GetClassNameW(target, cls, len(cls))
    if (owner(target) != pid or int(u.GetAncestor(target, GA_ROOT)) != hwnd
            or title(target) != name or cls.value != "Button"
            or not u.IsWindowEnabled(target)):
        raise UnsupportedBackgroundTarget("background_button_changed_or_disabled")
    result = ctypes.c_size_t()
    if not u.SendMessageTimeoutW(
        target, BM_CLICK, 0, 0, SMTO_ABORTIFHUNG | SMTO_BLOCK,
        2000, ctypes.byref(result)
    ):
        raise UnsupportedBackgroundTarget("background_button_message_rejected")
    return {
        "mode": "background", "control": "Win32.Button",
        "handle": f"0x{hwnd:X}", "expected_pid": pid,
        "control_text": name, "action_dispatched": True,
        "side_effect_verified": False,
    }
