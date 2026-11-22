"""Shared bounded subprocess execution for typed local actions."""
from __future__ import annotations

import os
import signal
import subprocess
import sys
from pathlib import Path

from .models import ActionResult
from .process_environment import external_process_environment


_WINDOWS_POWERSHELL_NAMES = frozenset({"powershell", "powershell.exe"})
_POWERSHELL_UTF8_PREFIX = (
    "[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new($false); "
)


def _windows_utf8_launch_command(command: list[str] | str) -> list[str] | str:
    """Make legacy Windows PowerShell honor the Runtime UTF-8 output contract."""
    # Windows cmd.exe needs the original command-line string after /c.
    # Passing it as one list item makes subprocess.list2cmdline escape the
    # inner quotes, which cmd.exe passes literally to child executables.
    if isinstance(command, str) or sys.platform != "win32" or not command:
        return command
    executable = command[0].replace("\\", "/").rsplit("/", 1)[-1].lower()
    if executable not in _WINDOWS_POWERSHELL_NAMES:
        return command
    prepared = list(command)
    for index, argument in enumerate(prepared[:-1]):
        if argument.lower() in {"-command", "-c"}:
            prepared[index + 1] = _POWERSHELL_UTF8_PREFIX + prepared[index + 1]
            break
    return prepared


def run_command(
    command: list[str] | str,
    *,
    cwd: Path | None = None,
    timeout: int = 1800,
    env: dict[str, str] | None = None,
) -> ActionResult:
    if isinstance(command, str) and sys.platform != "win32":
        raise ValueError("raw command lines are supported only by the Windows native shell")
    creationflags = 0
    start_new_session = False
    if sys.platform == "win32":
        creationflags = (
            getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            | getattr(subprocess, "CREATE_NO_WINDOW", 0)
        )
    else:
        start_new_session = True

    # All external tools share the same inherited-environment boundary,
    # including Linux source runs without any task-specific overrides.
    process_env = external_process_environment(
        {str(key): str(value) for key, value in env.items()} if env else None,
    )
    if sys.platform == "win32":
        # Python otherwise inherits the Windows ANSI code page for redirected
        # stdio. These are defaults, not overrides: an explicit caller choice
        # remains visible and strict UTF-8 decoding will fail closed if it
        # violates the terminal output contract.
        process_env.setdefault("PYTHONUTF8", "1")
        process_env.setdefault("PYTHONIOENCODING", "utf-8")

    launch_command = _windows_utf8_launch_command(command)

    # Terminal/process output is a UTF-8 text contract. Capture bytes first so
    # Windows locale/ACP never silently decodes UTF-8 as CP1252/Latin-1.
    # Foreground Product commands are explicitly non-interactive. Do not
    # inherit the Runtime service's stdin/console handle: a child requesting
    # input must receive EOF instead of blocking the shared dispatch lane.
    # Interactive sessions already have the supervised process/stdin actions.
    process = subprocess.Popen(
        launch_command,
        cwd=str(cwd) if cwd else None,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=False,
        shell=False,
        creationflags=creationflags,
        start_new_session=start_new_session,
        env=process_env,
    )
    timed_out = False
    cleanup_unverified = False
    timeout_phase: str | None = None
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired as error:
        timed_out = True
        # On Windows, the command may have already exited while a descendant
        # retains an inherited stdout/stderr pipe. In that case taskkill /PID
        # targets a dead parent (or a recycled PID), not the pipe holder.
        parent_already_exited = sys.platform == "win32" and process.poll() is not None
        timeout_phase = "pipe_open_after_parent_exit" if parent_already_exited else "process_wait"
        if parent_already_exited:
            # Fail closed and free the claim lane without touching other PIDs.
            # Preserve the bytes already captured before the deadline.
            cleanup_unverified = True
            stdout = error.stdout or b""
            stderr = error.stderr or b""
            for stream in (process.stdout, process.stderr):
                if stream is not None:
                    try:
                        stream.close()
                    except OSError:
                        pass
        else:
            if sys.platform == "win32":
                # Kill only the still-live foreground process tree.
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
                # Descendants may still hold inherited pipes. Closing only
                # our handles avoids an unbounded communicate() deadlock.
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
                stdout, stderr = error.stdout or b"", error.stderr or b""

    raw_stdout = stdout or b""
    raw_stderr = stderr or b""
    decode_errors: list[dict[str, object]] = []

    def decode_utf8(stream: str, raw: bytes) -> str:
        try:
            return raw.decode("utf-8", errors="strict")
        except UnicodeDecodeError as error:
            decode_errors.append({
                "stream": stream,
                "encoding": "utf-8",
                "byte_offset": error.start,
            })
            return ""

    decoded_stdout = decode_utf8("stdout", raw_stdout)
    decoded_stderr = decode_utf8("stderr", raw_stderr)
    data = {
        "returncode": process.returncode,
        "stdout": decoded_stdout[-40000:],
        "stderr": decoded_stderr[-40000:],
        "output_encoding": "utf-8",
        "command": command,
        "timed_out": timed_out,
        "timeout_seconds": timeout if timed_out else None,
        **({"timeout_phase": timeout_phase} if timed_out else {}),
        # A failed tree kill is not a successful cleanup. Keep the caller
        # informed without exposing another process's path or identity.
        **({"cleanup_unverified": True} if cleanup_unverified else {}),
        **({
            "error_code": "process_output_encoding_invalid",
            "decode_errors": decode_errors,
        } if decode_errors else {}),
    }
    return ActionResult(
        ok=(process.returncode == 0 and not timed_out and not decode_errors),
        summary=(
            f"command timed out after {timeout}s"
            if timed_out
            else (
                "command output is not valid UTF-8"
                if decode_errors
                else ("command completed" if process.returncode == 0 else "command failed")
            )
        ),
        data=data,
    )


