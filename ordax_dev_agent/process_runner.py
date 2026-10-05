"""Shared bounded subprocess execution for typed local actions."""
from __future__ import annotations

import os
import signal
import subprocess
import sys
from pathlib import Path

from .models import ActionResult


def run_command(
    command: list[str],
    *,
    cwd: Path | None = None,
    timeout: int = 1800,
    env: dict[str, str] | None = None,
) -> ActionResult:
    creationflags = 0
    start_new_session = False
    if sys.platform == "win32":
        creationflags = (
            getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            | getattr(subprocess, "CREATE_NO_WINDOW", 0)
        )
    else:
        start_new_session = True

    process_env = None
    if env is not None:
        process_env = os.environ.copy()
        process_env.update({str(key): str(value) for key, value in env.items()})

    process = subprocess.Popen(
        command,
        cwd=str(cwd) if cwd else None,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        shell=False,
        creationflags=creationflags,
        start_new_session=start_new_session,
        env=process_env,
    )
    timed_out = False
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        if sys.platform == "win32":
            # Kill the full descendant tree. Killing only the immediate Python
            # process can leave Blender/Unity/Git children holding stdout/stderr
            # pipe handles open, which makes communicate() wait forever.
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=30,
                shell=False,
            )
        else:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        try:
            stdout, stderr = process.communicate(timeout=15)
        except subprocess.TimeoutExpired:
            process.kill()
            stdout, stderr = process.communicate()

    data = {
        "returncode": process.returncode,
        "stdout": (stdout or "")[-40000:],
        "stderr": (stderr or "")[-40000:],
        "command": command,
        "timed_out": timed_out,
        "timeout_seconds": timeout if timed_out else None,
    }
    return ActionResult(
        ok=(process.returncode == 0 and not timed_out),
        summary=(
            f"command timed out after {timeout}s"
            if timed_out
            else ("command completed" if process.returncode == 0 else "command failed")
        ),
        data=data,
    )


