from __future__ import annotations

import ctypes
import time
from ctypes import wintypes
from typing import Any

from .computer_filesystem_actions import load_computer_access_policy
from .models import ActionResult

_CF_UNICODETEXT = 13
_GMEM_MOVEABLE = 0x0002
_MONITORINFOF_PRIMARY = 0x00000001
_MOUSE = {
    "left": (0x0002, 0x0004),
    "right": (0x0008, 0x0010),
    "middle": (0x0020, 0x0040),
}


class _MONITORINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("rcMonitor", wintypes.RECT),
        ("rcWork", wintypes.RECT),
        ("dwFlags", wintypes.DWORD),
    ]


class ComputerParityActions:
    @staticmethod
    def _virtual_screen_rect() -> dict[str, int]:
        user32 = ctypes.windll.user32
        left = int(user32.GetSystemMetrics(76))
        top = int(user32.GetSystemMetrics(77))
        width = int(user32.GetSystemMetrics(78))
        height = int(user32.GetSystemMetrics(79))
        return {
            "left": left,
            "top": top,
            "right": left + width,
            "bottom": top + height,
            "width": width,
            "height": height,
        }

    @classmethod
    def _validate_screen_point(cls, x: int, y: int) -> None:
        rect = cls._virtual_screen_rect()
        if not (
            rect["left"] <= x < rect["right"]
            and rect["top"] <= y < rect["bottom"]
        ):
            raise ValueError("coordinates are outside the virtual desktop")

    @staticmethod
    def _move_cursor(user32, x: int, y: int, duration_ms: int) -> None:
        point = wintypes.POINT()
        if not user32.GetCursorPos(ctypes.byref(point)):
            raise OSError("Windows rejected cursor inspection")
        if duration_ms <= 0:
            if not user32.SetCursorPos(x, y):
                raise OSError("Windows rejected cursor positioning")
            return

        steps = max(1, min(120, duration_ms // 16 or 1))
        start_x = int(point.x)
        start_y = int(point.y)
        delay = duration_ms / steps / 1000.0
        for step in range(1, steps + 1):
            ratio = step / steps
            next_x = round(start_x + ((x - start_x) * ratio))
            next_y = round(start_y + ((y - start_y) * ratio))
            if not user32.SetCursorPos(next_x, next_y):
                raise OSError("Windows rejected cursor positioning")
            time.sleep(delay)

    @staticmethod
    def _open_clipboard(user32) -> bool:
        for _ in range(10):
            if user32.OpenClipboard(None):
                return True
            time.sleep(0.025)
        return False
    def computer_screen_info(self, payload: dict[str, Any]) -> ActionResult:
        try:
            user32 = self._user32()
        except ValueError as error:
            return ActionResult(False, str(error))

        monitors: list[dict[str, Any]] = []
        user32.GetMonitorInfoW.argtypes = [wintypes.HMONITOR, ctypes.POINTER(_MONITORINFO)]
        user32.GetMonitorInfoW.restype = wintypes.BOOL
        callback_type = ctypes.WINFUNCTYPE(
            wintypes.BOOL,
            wintypes.HMONITOR,
            wintypes.HDC,
            ctypes.POINTER(wintypes.RECT),
            wintypes.LPARAM,
        )

        def callback(hmonitor, _hdc, _rect, _lparam):
            info = _MONITORINFO()
            info.cbSize = ctypes.sizeof(_MONITORINFO)
            if user32.GetMonitorInfoW(hmonitor, ctypes.byref(info)):
                monitor = info.rcMonitor
                work = info.rcWork
                monitors.append({
                    "index": len(monitors),
                    "primary": bool(info.dwFlags & _MONITORINFOF_PRIMARY),
                    "rect": {
                        "left": int(monitor.left),
                        "top": int(monitor.top),
                        "right": int(monitor.right),
                        "bottom": int(monitor.bottom),
                        "width": int(monitor.right - monitor.left),
                        "height": int(monitor.bottom - monitor.top),
                    },
                    "work_area": {
                        "left": int(work.left),
                        "top": int(work.top),
                        "right": int(work.right),
                        "bottom": int(work.bottom),
                        "width": int(work.right - work.left),
                        "height": int(work.bottom - work.top),
                    },
                })
            return True
        callback_ref = callback_type(callback)
        user32.EnumDisplayMonitors(0, None, callback_ref, 0)
        point = wintypes.POINT()
        user32.GetCursorPos(ctypes.byref(point))
        return ActionResult(
            True,
            "desktop screen information ready",
            {
                "coordinate_space": "physical_pixels",
                "virtual_desktop": self._virtual_screen_rect(),
                "monitor_count": len(monitors),
                "monitors": monitors,
                "cursor": {"x": int(point.x), "y": int(point.y)},
            },
        )
    def computer_mouse_move(self, payload: dict[str, Any]) -> ActionResult:
        try:
            user32 = self._user32()
            x = int(payload.get("x"))
            y = int(payload.get("y"))
            duration_ms = int(payload.get("duration_ms", 0))
            self._validate_screen_point(x, y)
        except (TypeError, ValueError) as error:
            message = str(error) or "x, y and duration_ms must be integers"
            return ActionResult(False, message)
        if not 0 <= duration_ms <= 5000:
            return ActionResult(False, "duration_ms must be between 0 and 5000")
        try:
            self._move_cursor(user32, x, y, duration_ms)
        except OSError as error:
            return ActionResult(False, f"desktop mouse move failed: {error}")
        return ActionResult(
            True,
            "desktop mouse moved",
            {"x": x, "y": y, "duration_ms": duration_ms},
        )
    def computer_drag(self, payload: dict[str, Any]) -> ActionResult:
        try:
            user32 = self._user32()
            from_x = int(payload.get("from_x"))
            from_y = int(payload.get("from_y"))
            to_x = int(payload.get("to_x"))
            to_y = int(payload.get("to_y"))
            duration_ms = int(payload.get("duration_ms", 500))
            self._validate_screen_point(from_x, from_y)
            self._validate_screen_point(to_x, to_y)
        except (TypeError, ValueError) as error:
            message = str(error) or "drag coordinates and duration_ms must be integers"
            return ActionResult(False, message)
        button = str(payload.get("button") or "left").strip().lower()
        if button not in _MOUSE:
            return ActionResult(False, "button must be left, right or middle")
        if not 50 <= duration_ms <= 5000:
            return ActionResult(False, "duration_ms must be between 50 and 5000")
        down, up = _MOUSE[button]
        try:
            if not user32.SetCursorPos(from_x, from_y):
                raise OSError("Windows rejected drag start position")
            user32.mouse_event(down, 0, 0, 0, 0)
            try:
                self._move_cursor(user32, to_x, to_y, duration_ms)
            finally:
                user32.mouse_event(up, 0, 0, 0, 0)
        except OSError as error:
            return ActionResult(False, f"desktop drag failed: {error}")
        return ActionResult(
            True,
            "desktop drag sent",
            {
                "from": {"x": from_x, "y": from_y},
                "to": {"x": to_x, "y": to_y},
                "button": button,
                "duration_ms": duration_ms,
            },
        )

    def computer_clipboard_read(self, payload: dict[str, Any]) -> ActionResult:
        try:
            self._windows_only()
            max_bytes = int(payload.get("max_bytes", 65536))
        except (TypeError, ValueError) as error:
            message = str(error) or "max_bytes must be an integer"
            return ActionResult(False, message)
        if not 1 <= max_bytes <= 1024 * 1024:
            return ActionResult(False, "max_bytes must be between 1 and 1048576")

        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        user32.GetClipboardData.argtypes = [wintypes.UINT]
        user32.GetClipboardData.restype = ctypes.c_void_p
        kernel32.GlobalLock.argtypes = [ctypes.c_void_p]
        kernel32.GlobalLock.restype = ctypes.c_void_p
        if not self._open_clipboard(user32):
            return ActionResult(False, "clipboard is busy")

        handle = None
        pointer = None
        try:
            handle = user32.GetClipboardData(_CF_UNICODETEXT)
            if not handle:
                return ActionResult(
                    True,
                    "clipboard has no Unicode text",
                    {"text": "", "bytes": 0, "truncated": False},
                )
            pointer = kernel32.GlobalLock(handle)
            if not pointer:
                return ActionResult(False, "clipboard text could not be locked")
            value = ctypes.wstring_at(pointer)
        finally:
            if pointer:
                kernel32.GlobalUnlock(handle)
            user32.CloseClipboard()

        encoded = value.encode("utf-8")
        if len(encoded) <= max_bytes:
            return ActionResult(
                True,
                "clipboard text ready",
                {"text": value, "bytes": len(encoded), "truncated": False},
            )
        clipped = encoded[:max_bytes].decode("utf-8", errors="ignore")
        return ActionResult(
            True,
            "clipboard text truncated to bound",
            {
                "text": clipped,
                "bytes": len(encoded),
                "returned_bytes": len(clipped.encode("utf-8")),
                "truncated": True,
            },
        )

    def computer_clipboard_write(self, payload: dict[str, Any]) -> ActionResult:
        try:
            self._windows_only()
        except ValueError as error:
            return ActionResult(False, str(error))
        value = payload.get("text")
        if not isinstance(value, str):
            return ActionResult(False, "text must be a string")
        encoded_utf8 = value.encode("utf-8")
        if len(encoded_utf8) > 1024 * 1024:
            return ActionResult(
                False,
                "clipboard text must be at most 1048576 UTF-8 bytes",
            )

        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
        kernel32.GlobalAlloc.restype = ctypes.c_void_p
        kernel32.GlobalLock.argtypes = [ctypes.c_void_p]
        kernel32.GlobalLock.restype = ctypes.c_void_p
        data = (value + "\0").encode("utf-16-le")
        handle = kernel32.GlobalAlloc(_GMEM_MOVEABLE, len(data))
        if not handle:
            return ActionResult(False, "clipboard allocation failed")
        pointer = kernel32.GlobalLock(handle)
        if not pointer:
            kernel32.GlobalFree(handle)
            return ActionResult(False, "clipboard allocation could not be locked")
        ctypes.memmove(pointer, data, len(data))
        kernel32.GlobalUnlock(handle)

        if not self._open_clipboard(user32):
            kernel32.GlobalFree(handle)
            return ActionResult(False, "clipboard is busy")
        transferred = False
        try:
            if not user32.EmptyClipboard():
                return ActionResult(False, "clipboard could not be cleared")
            user32.SetClipboardData.argtypes = [wintypes.UINT, ctypes.c_void_p]
            user32.SetClipboardData.restype = ctypes.c_void_p
            if not user32.SetClipboardData(_CF_UNICODETEXT, handle):
                return ActionResult(False, "clipboard text could not be set")
            transferred = True
        finally:
            user32.CloseClipboard()
            if not transferred:
                kernel32.GlobalFree(handle)

        return ActionResult(
            True,
            "clipboard text updated",
            {"characters": len(value), "bytes": len(encoded_utf8)},
        )

    def computer_launch_app(self, payload: dict[str, Any]) -> ActionResult:
        try:
            self._windows_only()
        except ValueError as error:
            return ActionResult(False, str(error))

        import shutil
        import subprocess
        from pathlib import Path

        application = payload.get("application")
        if not isinstance(application, str) or not application.strip():
            return ActionResult(False, "application must be a non-empty string")
        application = application.strip()
        if len(application) > 512:
            return ActionResult(False, "application must be at most 512 characters")

        raw_args = payload.get("args", [])
        if not isinstance(raw_args, list) or len(raw_args) > 32:
            return ActionResult(False, "args must be a list with at most 32 entries")
        args: list[str] = []
        for raw in raw_args:
            if not isinstance(raw, str) or len(raw) > 2048:
                return ActionResult(False, "each app argument must be a string up to 2048 characters")
            args.append(raw)
        if sum(len(item) for item in args) > 8192:
            return ActionResult(False, "combined app arguments must be at most 8192 characters")

        candidate = Path(application).expanduser()
        if candidate.is_absolute():
            executable = candidate.resolve()
            if not executable.is_file():
                return ActionResult(False, "application executable was not found")
        else:
            resolved = shutil.which(application)
            if not resolved:
                return ActionResult(False, "application executable was not found on PATH")
            executable = Path(resolved).resolve()

        if executable.suffix.lower() != ".exe":
            return ActionResult(False, "application must resolve to a Windows .exe executable")

        try:
            policy = load_computer_access_policy(self.config)
        except (OSError, ValueError) as error:
            return ActionResult(False, f"computer access policy is invalid: {error}")
        if not policy.enabled:
            return ActionResult(False, "computer access is disabled by local ORDAX policy")
        if not policy.application_allowed(executable):
            return ActionResult(
                False,
                "application is not allowlisted by local ORDAX computer access policy",
            )

        try:
            process = subprocess.Popen(
                [str(executable), *args],
                cwd=str(executable.parent),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                close_fds=True,
            )
        except OSError as error:
            return ActionResult(False, f"application launch failed: {error}")
        return ActionResult(
            True,
            "application launched",
            {"pid": int(process.pid), "application": executable.name, "argument_count": len(args)},
        )
