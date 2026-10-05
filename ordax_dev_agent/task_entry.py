"""Small, diagnostic-safe entry point for the Windows Scheduled Task.

Keep imports intentionally minimal so startup failures before the full agent
module loads are still observable in task-entry.log.
"""
from __future__ import annotations

import os
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path


def _state_dir() -> Path:
    local_app_data = Path(
        os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")
    )
    path = Path(
        os.environ.get(
            "ORDAX_AGENT_STATE_DIR",
            local_app_data / "OrdaX" / "DevAgent",
        )
    )
    path.mkdir(parents=True, exist_ok=True)
    return path


def _stamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write(handle, message: str) -> None:
    handle.write(f"{_stamp()} {message}\n")
    handle.flush()


def run() -> int:
    log_path = _state_dir() / "task-entry.log"
    with log_path.open("a", encoding="utf-8", buffering=1) as log:
        _write(log, f"ENTRY_START pid={os.getpid()} executable={sys.executable}")
        # pythonw.exe has no console streams. Redirect both streams to the same
        # durable log so import/runtime tracebacks remain inspectable.
        sys.stdout = log
        sys.stderr = log
        try:
            _write(log, "IMPORT_MAIN_START")
            from .main import main

            _write(log, "IMPORT_MAIN_OK")
            code = int(main())
            _write(log, f"MAIN_RETURN code={code}")
            return code
        except BaseException as error:
            _write(log, f"FATAL {type(error).__name__}: {error}")
            traceback.print_exc(file=log)
            log.flush()
            raise


if __name__ == "__main__":
    raise SystemExit(run())
