from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Sequence


MAX_CAPTURE_CHARS = 16000


def _tail(value: str | None) -> str:
    if not value:
        return ""
    if isinstance(value, bytes):
        value = value.decode(errors="replace")
    return value[-MAX_CAPTURE_CHARS:]


def run_process(
    command: Sequence[str],
    *,
    cwd: str | Path | None = None,
    timeout_seconds: int = 1800,
) -> dict:
    try:
        completed = subprocess.run(
            list(command),
            cwd=str(cwd) if cwd is not None else None,
            capture_output=True,
            text=True,
            shell=False,
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired as error:
        return {
            "command": list(command),
            "returncode": None,
            "ok": False,
            "timed_out": True,
            "stdout": _tail(error.stdout),
            "stderr": _tail(error.stderr) or f"Process timed out after {timeout_seconds} seconds.",
        }
    except OSError as error:
        return {
            "command": list(command),
            "returncode": None,
            "ok": False,
            "timed_out": False,
            "stdout": "",
            "stderr": f"Failed to start process: {error}",
        }

    return {
        "command": list(command),
        "returncode": completed.returncode,
        "ok": completed.returncode == 0,
        "timed_out": False,
        "stdout": _tail(completed.stdout),
        "stderr": _tail(completed.stderr),
    }
