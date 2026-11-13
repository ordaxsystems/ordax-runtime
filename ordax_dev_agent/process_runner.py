"""Shared bounded subprocess execution for typed local actions."""
from __future__ import annotations

import os
import signal
import subprocess
import sys
from pathlib import Path

from .models import ActionResult
from .process_environment import external_process_environment


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
    if env is not None or (getattr(sys, "frozen", False) and sys.platform == "linux"):
        process_env = external_process_environment(
            {str(key): str(value) for key, value in env.items()} if env else None,
        )

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
    cleanup_unverified = False
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        if sys.platform == "win32":
            # The foreground Product lane is serial. Never hold it forever
            # after the caller's deadline while Windows descendants still own
            # inherited stdout/stderr pipes. Kill only this process tree.
            try:
                killed = subprocess.run(
                    ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=10,
                    shell=False,
                )
                cleanup_unverified = killed.returncode != 0
            except (subprocess.TimeoutExpired, OSError):
                cleanup_unverified = True
        else:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        try:
            stdout, stderr = process.communicate(timeout=3)
        except subprocess.TimeoutExpired:
            # Even after taskkill, grandchildren may hold inherited pipes.
            # A final communicate() WITHOUT a timeout deadlocks this Runtime's
            # only claim lane. Close our own handles and bound process reaping.
            cleanup_unverified = True
            try:
                process.kill()
            except OSError:
                pass
            for stream in (process.stdout, process.stderr):
                if stream is not None:
                    try:
                        stream.close()
                    except OSError:
                        pass
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                pass
            stdout, stderr = "", ""

    data = {
        "returncode": process.returncode,
        "stdout": (stdout or "")[-40000:],
        "stderr": (stderr or "")[-40000:],
        "command": command,
        "timed_out": timed_out,
        "timeout_seconds": timeout if timed_out else None,
        # A failed tree kill is not a successful cleanup. Keep the caller
        # informed without exposing another process's path or identity.
        **({"cleanup_unverified": True} if cleanup_unverified else {}),
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


