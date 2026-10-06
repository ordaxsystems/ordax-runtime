"""Typed Windows desktop observation and input for ORDAX Dev.

These actions are intentionally separate from browser control. They operate on
the interactive Windows desktop and therefore must be gated by the Chat App's
project capability policy before being exposed to a model.
"""
from __future__ import annotations

import ctypes
import os
import json
import subprocess
import time
import uuid
from ctypes import wintypes
from pathlib import Path
from typing import Any

from PIL import ImageGrab

from .models import ActionResult


_MOUSE = {
    "left": (0x0002, 0x0004),
    "right": (0x0008, 0x0010),
    "middle": (0x0020, 0x0040),
}
_WHEEL = 0x0800
_HWHEEL = 0x01000

_VK = {
    "backspace": 0x08,
    "tab": 0x09,
    "enter": 0x0D,
    "shift": 0x10,
    "ctrl": 0x11,
    "alt": 0x12,
    "pause": 0x13,
    "capslock": 0x14,
    "escape": 0x1B,
    "space": 0x20,
    "pageup": 0x21,
    "pagedown": 0x22,
    "end": 0x23,
    "home": 0x24,
    "left": 0x25,
    "up": 0x26,
    "right": 0x27,
    "down": 0x28,
    "insert": 0x2D,
    "delete": 0x2E,
    "win": 0x5B,
}
for _i in range(1, 25):
    _VK[f"f{_i}"] = 0x6F + _i
for _ch in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789":
    _VK[_ch.lower()] = ord(_ch)

_KEYEVENTF_KEYUP = 0x0002
_KEYEVENTF_UNICODE = 0x0004
_INPUT_KEYBOARD = 1
_WS_EX_TOOLWINDOW = 0x00000080
_WS_EX_APPWINDOW = 0x00040000
_GWL_EXSTYLE = -20


_ULONG_PTR = ctypes.c_size_t


class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", _ULONG_PTR),
    ]


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", _ULONG_PTR),
    ]


class _HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ("uMsg", wintypes.DWORD),
        ("wParamL", wintypes.WORD),
        ("wParamH", wintypes.WORD),
    ]


class _INPUTUNION(ctypes.Union):
    _fields_ = [
        ("mi", _MOUSEINPUT),
        ("ki", _KEYBDINPUT),
        ("hi", _HARDWAREINPUT),
    ]


class _INPUT(ctypes.Structure):
    _anonymous_ = ("union",)
    _fields_ = [("type", wintypes.DWORD), ("union", _INPUTUNION)]


_CRITICAL_PROCESS_NAMES = frozenset({
    "system",
    "registry",
    "memory compression",
    "smss.exe",
    "csrss.exe",
    "wininit.exe",
    "winlogon.exe",
    "services.exe",
    "lsass.exe",
    "fontdrvhost.exe",
})

class ComputerControlActions:
    @staticmethod
    def _normalize_process_item(item: dict[str, Any]) -> dict[str, Any]:
        def bounded(value: Any, limit: int = 2000) -> str:
            return str(value or "").replace("\x00", "").strip()[:limit]

        pid = int(item.get("pid") or item.get("ProcessId") or 0)
        ppid = int(item.get("parent_pid") or item.get("ParentProcessId") or 0)
        return {
            "pid": pid,
            "parent_pid": ppid,
            "name": bounded(item.get("name") or item.get("Name"), 260),
            "executable": bounded(item.get("executable") or item.get("ExecutablePath"), 2000),
            "command_line": bounded(item.get("command_line") or item.get("CommandLine"), 4000),
        }

    @classmethod
    def _system_process_snapshot(cls) -> list[dict[str, Any]]:
        if os.name == "nt":
            script = (
                "$ErrorActionPreference='Stop';"
                "Get-CimInstance Win32_Process | "
                "Select-Object ProcessId,ParentProcessId,Name,ExecutablePath,CommandLine | "
                "ConvertTo-Json -Compress -Depth 3"
            )
            completed = subprocess.run(
                ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
                capture_output=True,
                text=True,
                timeout=15,
                shell=False,
            )
            if completed.returncode != 0:
                raise OSError((completed.stderr or "process enumeration failed").strip()[:2000])
            raw = (completed.stdout or "").strip()
            if not raw:
                return []
            payload = json.loads(raw)
            if isinstance(payload, dict):
                payload = [payload]
            if not isinstance(payload, list):
                raise ValueError("Windows process enumeration returned invalid JSON")
            return [
                cls._normalize_process_item(item)
                for item in payload
                if isinstance(item, dict) and int(item.get("ProcessId") or 0) > 0
            ]

        completed = subprocess.run(
            ["ps", "-eo", "pid=,ppid=,comm=,args="],
            capture_output=True,
            text=True,
            timeout=15,
            shell=False,
        )
        if completed.returncode != 0:
            raise OSError((completed.stderr or "process enumeration failed").strip()[:2000])
        items: list[dict[str, Any]] = []
        for line in (completed.stdout or "").splitlines():
            parts = line.strip().split(None, 3)
            if len(parts) < 3:
                continue
            try:
                pid = int(parts[0])
                ppid = int(parts[1])
            except ValueError:
                continue
            name = parts[2]
            command = parts[3] if len(parts) > 3 else name
            items.append(cls._normalize_process_item({
                "pid": pid,
                "parent_pid": ppid,
                "name": name,
                "command_line": command,
            }))
        return items

    @classmethod
    def _system_process_by_pid(cls, pid: int) -> dict[str, Any] | None:
        return next((item for item in cls._system_process_snapshot() if item["pid"] == pid), None)

    def computer_processes(self, payload: dict[str, Any]) -> ActionResult:
        allowed = {"query", "max_items"}
        unsupported = sorted(set(payload) - allowed)
        if unsupported:
            return ActionResult(False, "unsupported field(s): " + ", ".join(unsupported))
        query = str(payload.get("query") or "").strip().casefold()
        try:
            max_items = int(payload.get("max_items", 200))
        except (TypeError, ValueError):
            return ActionResult(False, "max_items must be an integer")
        if not 1 <= max_items <= 1000:
            return ActionResult(False, "max_items must be between 1 and 1000")

        try:
            items = self._system_process_snapshot()
        except (OSError, ValueError, json.JSONDecodeError, subprocess.TimeoutExpired) as error:
            return ActionResult(False, f"process enumeration failed: {type(error).__name__}: {error}")

        if query:
            items = [
                item for item in items
                if query in str(item.get("name") or "").casefold()
                or query in str(item.get("executable") or "").casefold()
                or query in str(item.get("command_line") or "").casefold()
            ]
        items.sort(key=lambda item: (str(item.get("name") or "").casefold(), int(item["pid"])))
        total = len(items)
        return ActionResult(
            True,
            "system processes ready",
            {"processes": items[:max_items], "total_matches": total, "truncated": total > max_items},
        )

    def computer_terminate_process(self, payload: dict[str, Any]) -> ActionResult:
        allowed = {"pid", "expected_name", "force", "tree"}
        unsupported = sorted(set(payload) - allowed)
        if unsupported:
            return ActionResult(False, "unsupported field(s): " + ", ".join(unsupported))
        try:
            pid = int(payload.get("pid"))
        except (TypeError, ValueError):
            return ActionResult(False, "pid must be an integer")
        if pid <= 0:
            return ActionResult(False, "pid must be positive")

        expected_name = str(payload.get("expected_name") or "").strip()
        if not expected_name or len(expected_name) > 260:
            return ActionResult(False, "expected_name is required and must be at most 260 characters")
        force = payload.get("force", False)
        tree = payload.get("tree", True)
        if not isinstance(force, bool) or not isinstance(tree, bool):
            return ActionResult(False, "force and tree must be boolean")

        current_pid = os.getpid()
        parent_pid = os.getppid()
        if pid in {current_pid, parent_pid}:
            return ActionResult(False, "refusing to terminate the active ORDAX process")

        try:
            item = self._system_process_by_pid(pid)
        except (OSError, ValueError, json.JSONDecodeError, subprocess.TimeoutExpired) as error:
            return ActionResult(False, f"process inspection failed: {type(error).__name__}: {error}")
        if item is None:
            return ActionResult(False, "process no longer exists")

        actual_name = str(item.get("name") or "")
        if actual_name.casefold() != expected_name.casefold():
            return ActionResult(False, "process identity changed; expected_name does not match current PID", {
                "pid": pid,
                "expected_name": expected_name,
                "actual_name": actual_name,
            })

        normalized_name = actual_name.casefold()
        if normalized_name in _CRITICAL_PROCESS_NAMES or normalized_name.startswith("ordax"):
            return ActionResult(False, f"refusing to terminate protected process: {actual_name}")

        try:
            if os.name == "nt":
                command = ["taskkill", "/PID", str(pid)]
                if tree:
                    command.append("/T")
                if force:
                    command.append("/F")
                completed = subprocess.run(
                    command,
                    capture_output=True,
                    text=True,
                    timeout=15,
                    shell=False,
                )
                if completed.returncode != 0:
                    return ActionResult(False, "process termination failed", {
                        "pid": pid,
                        "name": actual_name,
                        "stderr": (completed.stderr or "")[-2000:],
                    })
            else:
                import signal
                os.kill(pid, signal.SIGKILL if force else signal.SIGTERM)
        except (OSError, subprocess.TimeoutExpired) as error:
            return ActionResult(False, f"process termination failed: {type(error).__name__}: {error}")

        return ActionResult(True, "process termination requested", {
            "pid": pid,
            "name": actual_name,
            "force": force,
            "tree": tree if os.name == "nt" else False,
        })

    @staticmethod
    def _windows_only() -> None:
        if os.name != "nt":
            raise ValueError("computer control is currently available only on Windows")

    @staticmethod
    def _user32():
        ComputerControlActions._windows_only()
        return ctypes.windll.user32

    def _computer_artifact_path(self) -> Path:
        root = (self.config.state_dir / "artifacts" / "computer").resolve()
        root.mkdir(parents=True, exist_ok=True)
        return root / f"computer-{uuid.uuid4().hex}.png"

    @staticmethod
    def _window_info(hwnd: int) -> dict[str, Any] | None:
        user32 = ctypes.windll.user32
        if not hwnd or not user32.IsWindowVisible(hwnd):
            return None
        length = int(user32.GetWindowTextLengthW(hwnd))
        if length <= 0:
            return None
        buffer = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buffer, length + 1)
        title = buffer.value.strip()
        if not title:
            return None
        rect = wintypes.RECT()
        if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return None
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        return {
            "handle": f"0x{int(hwnd):X}",
            "title": title[:1000],
            "pid": int(pid.value),
            "rect": {
                "left": int(rect.left),
                "top": int(rect.top),
                "right": int(rect.right),
                "bottom": int(rect.bottom),
                "width": max(0, int(rect.right - rect.left)),
                "height": max(0, int(rect.bottom - rect.top)),
            },
            "foreground": int(hwnd) == int(user32.GetForegroundWindow()),
        }

    @staticmethod
    def _window_style_is_user_selectable(ex_style: int) -> bool:
        """Match the shell's app-window semantics for enumeration targets.

        Tool windows are normally auxiliary surfaces and should not compete with
        their owning application in the model-facing window list. APPWINDOW is
        the explicit Win32 opt-in that makes a tool-style surface user-facing.
        """
        style = int(ex_style) & 0xFFFFFFFF
        return not bool(style & _WS_EX_TOOLWINDOW) or bool(style & _WS_EX_APPWINDOW)

    @classmethod
    def _window_is_user_selectable(cls, hwnd: int) -> bool:
        user32 = ctypes.windll.user32
        ex_style = int(user32.GetWindowLongW(hwnd, _GWL_EXSTYLE))
        return cls._window_style_is_user_selectable(ex_style)

    @staticmethod
    def _parse_handle(raw: Any) -> int:
        if isinstance(raw, int):
            value = raw
        else:
            text = str(raw or "").strip().lower()
            try:
                value = int(text, 16) if text.startswith("0x") else int(text)
            except ValueError as error:
                raise ValueError("window handle must be an integer or 0x-prefixed value") from error
        if value <= 0:
            raise ValueError("window handle must be positive")
        return value

    def computer_windows(self, payload: dict[str, Any]) -> ActionResult:
        try:
            self._windows_only()
        except ValueError as error:
            return ActionResult(False, str(error))
        max_items = max(1, min(int(payload.get("max_items", 100)), 500))
        items: list[dict[str, Any]] = []
        callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

        def callback(hwnd, _lparam):
            handle = int(hwnd)
            if not self._window_is_user_selectable(handle):
                return True
            info = self._window_info(handle)
            if info is not None:
                items.append(info)
            return len(items) < max_items

        callback_ref = callback_type(callback)
        ctypes.windll.user32.EnumWindows(callback_ref, 0)
        items.sort(key=lambda item: (not item["foreground"], item["title"].casefold()))
        return ActionResult(
            True,
            "desktop windows ready",
            {"windows": items[:max_items], "truncated": len(items) >= max_items},
        )

    def computer_active_window(self, payload: dict[str, Any]) -> ActionResult:
        try:
            hwnd = int(self._user32().GetForegroundWindow())
        except ValueError as error:
            return ActionResult(False, str(error))
        info = self._window_info(hwnd)
        if info is None:
            return ActionResult(False, "foreground window is unavailable")
        return ActionResult(True, "foreground window ready", info)

    def computer_screenshot(self, payload: dict[str, Any]) -> ActionResult:
        try:
            self._windows_only()
        except ValueError as error:
            return ActionResult(False, str(error))
        mode = str(payload.get("mode") or "desktop").strip().lower()
        if mode not in {"desktop", "active_window"}:
            return ActionResult(False, "mode must be desktop or active_window")
        target = self._computer_artifact_path()
        try:
            if mode == "active_window":
                hwnd = int(self._user32().GetForegroundWindow())
                info = self._window_info(hwnd)
                if info is None:
                    return ActionResult(False, "foreground window is unavailable")
                rect = info["rect"]
                bbox = (rect["left"], rect["top"], rect["right"], rect["bottom"])
                image = ImageGrab.grab(bbox=bbox, all_screens=True)
            else:
                image = ImageGrab.grab(all_screens=True)
            image.save(target, "PNG")
        except Exception as error:
            return ActionResult(False, f"desktop screenshot failed: {type(error).__name__}: {error}")
        width, height = image.size
        return ActionResult(
            True,
            "desktop screenshot captured",
            {
                "scope": "device",
                "mode": mode,
                "coordinate_space": "physical_pixels",
                "artifact_name": target.name,
                "image_path": str(target),
                "width": int(width),
                "height": int(height),
                "size_bytes": int(target.stat().st_size),
            },
        )

    @staticmethod
    def _current_thread_id() -> int:
        return int(ctypes.windll.kernel32.GetCurrentThreadId())

    def computer_focus_window(self, payload: dict[str, Any]) -> ActionResult:
        try:
            user32 = self._user32()
            hwnd = self._parse_handle(payload.get("handle"))
        except ValueError as error:
            return ActionResult(False, str(error))
        if not user32.IsWindow(hwnd):
            return ActionResult(False, "window handle does not exist")

        # Windows may reject SetForegroundWindow when the caller doesn't own the
        # current foreground input queue. Temporarily join the caller, foreground
        # and target input queues, focus the requested visible window, then always
        # detach. This avoids click/ALT-key workarounds and never changes app data.
        attached: list[tuple[int, int]] = []

        def attach(first: int, second: int) -> None:
            if not first or not second or first == second:
                return
            pair = (first, second)
            if pair in attached:
                return
            if bool(user32.AttachThreadInput(first, second, True)):
                attached.append(pair)

        try:
            foreground_hwnd = int(user32.GetForegroundWindow() or 0)
            caller_thread = self._current_thread_id()
            foreground_thread = int(
                user32.GetWindowThreadProcessId(foreground_hwnd, None) or 0
            ) if foreground_hwnd else 0
            target_thread = int(user32.GetWindowThreadProcessId(hwnd, None) or 0)

            attach(caller_thread, foreground_thread)
            attach(caller_thread, target_thread)
            attach(target_thread, foreground_thread)

            if user32.IsIconic(hwnd):
                user32.ShowWindow(hwnd, 9)  # SW_RESTORE
            user32.BringWindowToTop(hwnd)
            ok = bool(user32.SetForegroundWindow(hwnd))
            user32.SetActiveWindow(hwnd)
            user32.SetFocus(hwnd)
            # Keep the input queues joined until Windows has committed the
            # foreground transition; detaching earlier can revert the focus.
            time.sleep(0.08)
            info = self._window_info(hwnd)
        finally:
            for first, second in reversed(attached):
                user32.AttachThreadInput(first, second, False)

        focused = bool(ok or (info and info.get("foreground")))
        return ActionResult(
            focused,
            "window focused" if focused else "window focus was rejected by Windows",
            info or {"handle": f"0x{hwnd:X}"},
        )

    def computer_click(self, payload: dict[str, Any]) -> ActionResult:
        try:
            user32 = self._user32()
        except ValueError as error:
            return ActionResult(False, str(error))
        try:
            x = int(payload.get("x"))
            y = int(payload.get("y"))
            clicks = int(payload.get("clicks", 1))
        except (TypeError, ValueError):
            return ActionResult(False, "x, y and clicks must be integers")
        button = str(payload.get("button") or "left").lower()
        if button not in _MOUSE:
            return ActionResult(False, "button must be left, right or middle")
        if not 1 <= clicks <= 3:
            return ActionResult(False, "clicks must be between 1 and 3")
        if not user32.SetCursorPos(x, y):
            return ActionResult(False, "Windows rejected cursor positioning")
        down, up = _MOUSE[button]
        for _ in range(clicks):
            user32.mouse_event(down, 0, 0, 0, 0)
            user32.mouse_event(up, 0, 0, 0, 0)
            if clicks > 1:
                time.sleep(0.05)
        return ActionResult(True, "desktop click sent", {"x": x, "y": y, "button": button, "clicks": clicks})

    def computer_scroll(self, payload: dict[str, Any]) -> ActionResult:
        try:
            user32 = self._user32()
            amount = int(payload.get("amount"))
        except ValueError as error:
            return ActionResult(False, str(error))
        except (TypeError, ValueError):
            return ActionResult(False, "amount must be an integer")
        horizontal = bool(payload.get("horizontal", False))
        if amount == 0 or abs(amount) > 100:
            return ActionResult(False, "amount must be between -100 and 100 and non-zero")
        flag = _HWHEEL if horizontal else _WHEEL
        user32.mouse_event(flag, 0, 0, int(amount * 120), 0)
        return ActionResult(True, "desktop scroll sent", {"amount": amount, "horizontal": horizontal})

    @staticmethod
    def _send_unicode(text: str) -> None:
        user32 = ctypes.windll.user32
        send_input = user32.SendInput
        send_input.argtypes = [wintypes.UINT, ctypes.POINTER(_INPUT), ctypes.c_int]
        send_input.restype = wintypes.UINT
        units = text.encode("utf-16-le")
        for index in range(0, len(units), 2):
            scan = int.from_bytes(units[index:index + 2], "little")
            inputs = (_INPUT * 2)(
                _INPUT(
                    type=_INPUT_KEYBOARD,
                    ki=_KEYBDINPUT(0, scan, _KEYEVENTF_UNICODE, 0, 0),
                ),
                _INPUT(
                    type=_INPUT_KEYBOARD,
                    ki=_KEYBDINPUT(0, scan, _KEYEVENTF_UNICODE | _KEYEVENTF_KEYUP, 0, 0),
                ),
            )
            sent = send_input(len(inputs), inputs, ctypes.sizeof(_INPUT))
            if sent != len(inputs):
                raise OSError(
                    f"Windows accepted {sent}/{len(inputs)} unicode keyboard events"
                )

    def computer_type(self, payload: dict[str, Any]) -> ActionResult:
        try:
            self._windows_only()
        except ValueError as error:
            return ActionResult(False, str(error))
        text = payload.get("text")
        if not isinstance(text, str):
            return ActionResult(False, "text must be a string")
        if not text or len(text.encode("utf-8")) > 64 * 1024:
            return ActionResult(False, "text must contain 1..65536 UTF-8 bytes")
        try:
            self._send_unicode(text)
        except OSError as error:
            return ActionResult(False, f"desktop typing failed: {error}")
        return ActionResult(True, "desktop text sent", {"characters": len(text)})

    def computer_hotkey(self, payload: dict[str, Any]) -> ActionResult:
        try:
            user32 = self._user32()
        except ValueError as error:
            return ActionResult(False, str(error))
        keys = payload.get("keys")
        if not isinstance(keys, list) or not 1 <= len(keys) <= 6:
            return ActionResult(False, "keys must contain 1..6 key names")
        normalized = [str(item or "").strip().lower() for item in keys]
        if any(key not in _VK for key in normalized):
            invalid = [key for key in normalized if key not in _VK]
            return ActionResult(False, "unsupported hotkey key(s): " + ", ".join(invalid))
        values = [_VK[key] for key in normalized]
        for vk in values:
            user32.keybd_event(vk, 0, 0, 0)
        for vk in reversed(values):
            user32.keybd_event(vk, 0, _KEYEVENTF_KEYUP, 0)
        return ActionResult(True, "desktop hotkey sent", {"keys": normalized})
