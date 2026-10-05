from __future__ import annotations

import ctypes
import os
import threading
from typing import Any

_DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = -4
_PROCESS_PER_MONITOR_DPI_AWARE = 2
_lock = threading.Lock()
_initialized = False
_physical_pixels = False
_mode = "unsupported"


def _current_thread_is_per_monitor(user32: Any) -> bool:
    try:
        get_context = getattr(user32, "GetThreadDpiAwarenessContext")
        get_awareness = getattr(user32, "GetAwarenessFromDpiAwarenessContext")
        context = get_context()
        return int(get_awareness(context)) == _PROCESS_PER_MONITOR_DPI_AWARE
    except (AttributeError, OSError, TypeError, ValueError):
        return False


def ensure_physical_desktop_coordinates() -> dict[str, Any]:
    """Make Win32 desktop geometry use the same physical pixels as screenshots.

    The Device Agent is headless, so process DPI awareness can be established
    before any desktop action is dispatched. Repeated calls are idempotent.
    Older Windows builds fall back to PROCESS_PER_MONITOR_DPI_AWARE.
    """
    global _initialized, _physical_pixels, _mode

    if os.name != "nt":
        return {
            "supported": False,
            "physical_pixels": False,
            "mode": "unsupported",
        }

    with _lock:
        if _initialized:
            return {
                "supported": True,
                "physical_pixels": _physical_pixels,
                "mode": _mode,
            }

        _initialized = True
        user32 = ctypes.windll.user32

        try:
            setter = getattr(user32, "SetProcessDpiAwarenessContext")
            if setter(ctypes.c_void_p(_DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2)):
                _physical_pixels = True
                _mode = "per_monitor_v2"
                return {
                    "supported": True,
                    "physical_pixels": True,
                    "mode": _mode,
                }
        except (AttributeError, OSError, TypeError, ValueError):
            pass

        if _current_thread_is_per_monitor(user32):
            _physical_pixels = True
            _mode = "existing_per_monitor"
            return {
                "supported": True,
                "physical_pixels": True,
                "mode": _mode,
            }

        try:
            shcore = ctypes.windll.shcore
            shcore.SetProcessDpiAwareness(_PROCESS_PER_MONITOR_DPI_AWARE)
        except (AttributeError, OSError, TypeError, ValueError):
            pass

        _physical_pixels = _current_thread_is_per_monitor(user32)
        _mode = "per_monitor_fallback" if _physical_pixels else "dpi_virtualized"
        return {
            "supported": True,
            "physical_pixels": _physical_pixels,
            "mode": _mode,
        }
