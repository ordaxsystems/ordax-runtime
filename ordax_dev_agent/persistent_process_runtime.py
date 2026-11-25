"""Detached supervised process runtime used by ORDAX persistent processes."""
from __future__ import annotations

import argparse
import hmac
import json
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path


# The parent action must wait longer than this child-tree termination window.
WINDOWS_CHILD_TREE_STOP_TIMEOUT_SECONDS = 15
# Bound idle journal I/O while retaining a short input handoff latency.
PERSISTENT_PROCESS_STDIN_POLL_INTERVAL_SECONDS = 0.05
PERSISTENT_PROCESS_STDIN_MAX_IDLE_POLL_INTERVAL_SECONDS = 0.2


def atomic_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    temp = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        last_error: OSError | None = None
        for attempt in range(40):
            try:
                os.replace(temp, path)
                last_error = None
                break
            except OSError as error:
                last_error = error
                if attempt < 39:
                    time.sleep(0.025)
                    continue
                raise
        if last_error is not None:
            raise last_error
    finally:
        try:
            temp.unlink()
        except OSError:
            pass


def windows_pid_created_at_unix(pid: int) -> float | None:
    """Use a native Win32 PID birth time before any slow CIM ownership probe.

    A recycled historical manager PID may now belong to Chrome or another
    program. OpenProcess/GetProcessTimes is bounded and does not create a
    PowerShell subprocess for those unrelated processes.
    """
    if os.name != "nt" or pid <= 0:
        return None
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.windll.kernel32
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        handle = kernel32.OpenProcess(0x1000, False, pid)
        if not handle:
            return None
        try:
            times = [wintypes.FILETIME() for _ in range(4)]
            kernel32.GetProcessTimes.argtypes = [
                wintypes.HANDLE, *([ctypes.POINTER(wintypes.FILETIME)] * 4)
            ]
            kernel32.GetProcessTimes.restype = wintypes.BOOL
            if not kernel32.GetProcessTimes(handle, *[ctypes.byref(t) for t in times]):
                return None
            creation = (times[0].dwHighDateTime << 32) | times[0].dwLowDateTime
            # FILETIME counts 100ns intervals since 1601-01-01 UTC.
            unix_time = creation / 10_000_000 - 11_644_473_600
            return unix_time if unix_time > 0 else None
        finally:
            kernel32.CloseHandle(handle)
    except (AttributeError, OSError, TypeError):
        # Absence of a birth-time proof never grants ownership: retain
        # the stricter token-in-command-line validation below.
        return None


def windows_child_identity(child: subprocess.Popen) -> dict:
    """Record the image and creation time from the owned child handle.

    No PID reopen: a quick exit and recycled PID cannot substitute a
    different process. If Windows cannot prove identity, omit the proof.
    """
    if os.name != "nt":
        return {}
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.windll.kernel32
        handle = wintypes.HANDLE(int(child._handle))
        times = [wintypes.FILETIME() for _ in range(4)]
        kernel32.GetProcessTimes.argtypes = [
            wintypes.HANDLE, *([ctypes.POINTER(wintypes.FILETIME)] * 4)
        ]
        kernel32.GetProcessTimes.restype = wintypes.BOOL
        if not kernel32.GetProcessTimes(handle, *[ctypes.byref(t) for t in times]):
            return {}
        creation = (times[0].dwHighDateTime << 32) | times[0].dwLowDateTime
        birth = creation / 10_000_000 - 11_644_473_600
        image = ctypes.create_unicode_buffer(32768)
        length = wintypes.DWORD(len(image))
        query = kernel32.QueryFullProcessImageNameW
        query.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
                          ctypes.POINTER(wintypes.DWORD)]
        query.restype = wintypes.BOOL
        if birth <= 0 or not query(handle, 0, image, ctypes.byref(length)) or not image.value:
            return {}
        return {"child_created_at_unix": birth, "child_executable_path": image.value}
    except (AttributeError, OSError, TypeError, ValueError):
        return {}


class ProcessRuntime:
    def __init__(
        self,
        *,
        state_path: Path,
        log_path: Path,
        stdin_path: Path,
        cwd: Path,
        command: list[str],
        env: dict[str, str],
    ):
        self.state_path = state_path
        self.log_path = log_path
        self.stdin_path = stdin_path
        self.cwd = cwd
        self.command = command
        self.env = env
        self.child: subprocess.Popen | None = None
        self._stopping = False
        self._stdin_thread: threading.Thread | None = None
        self._state_lock = threading.RLock()
        self._state = self._load_initial_state()

    def _load_initial_state(self) -> dict:
        last_error: Exception | None = None
        for _ in range(20):
            try:
                payload = json.loads(self.state_path.read_text(encoding="utf-8"))
                if isinstance(payload, dict) and payload:
                    return payload
            except Exception as error:
                last_error = error
            time.sleep(0.025)
        if last_error is not None:
            raise RuntimeError(f"cannot load persistent process state: {last_error}") from last_error
        raise RuntimeError("cannot load persistent process state")

    def load_state(self) -> dict:
        with self._state_lock:
            return dict(self._state)

    def update(self, **changes) -> None:
        with self._state_lock:
            self._state.update(changes)
            atomic_json(self.state_path, self._state)

    def stop(self, *_args) -> None:
        if self._stopping:
            return
        self._stopping = True
        child = self.child
        self.update(state="stopping", stopping_at_unix=time.time())
        if child is None or child.poll() is not None:
            return
        try:
            if os.name == "nt":
                subprocess.run(
                    ["taskkill", "/PID", str(child.pid), "/T", "/F"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=WINDOWS_CHILD_TREE_STOP_TIMEOUT_SECONDS,
                    shell=False,
                )
            else:
                try:
                    os.killpg(child.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
        except Exception:
            pass

    def _pump_stdin(self) -> None:
        position = 0
        poll_interval = PERSISTENT_PROCESS_STDIN_POLL_INTERVAL_SECONDS
        self.stdin_path.parent.mkdir(parents=True, exist_ok=True)
        self.stdin_path.touch(exist_ok=True)
        while not self._stopping:
            child = self.child
            if child is None or child.poll() is not None:
                return
            new_entries_seen = False
            try:
                with self.stdin_path.open("r", encoding="utf-8", errors="replace") as handle:
                    handle.seek(position)
                    while True:
                        line = handle.readline()
                        if not line or not line.endswith("\n"):
                            # A writer may still be appending this JSONL record.
                            # Leave the cursor at its start and retry next poll.
                            break
                        new_entries_seen = True
                        position = handle.tell()
                        try:
                            payload = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        if not isinstance(payload, dict):
                            continue
                        control = payload.get("control")
                        if control == "stop":
                            self.stop()
                            return
                        if control is not None:
                            continue
                        text = payload.get("text")
                        if not isinstance(text, str):
                            continue
                        if payload.get("newline", True):
                            text += "\n"
                        stream = child.stdin
                        if stream is None:
                            return
                        try:
                            stream.write(text)
                            stream.flush()
                        except (BrokenPipeError, OSError, ValueError):
                            return
            except OSError:
                pass
            if new_entries_seen:
                poll_interval = PERSISTENT_PROCESS_STDIN_POLL_INTERVAL_SECONDS
            time.sleep(poll_interval)
            if not new_entries_seen:
                poll_interval = min(
                    poll_interval * 2,
                    PERSISTENT_PROCESS_STDIN_MAX_IDLE_POLL_INTERVAL_SECONDS,
                )

    def run(self) -> int:
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        manager_pid = os.getpid()
        created_at = windows_pid_created_at_unix(manager_pid)
        self.update(manager_pid=manager_pid, manager_ready_at_unix=time.time(),
                    **({"manager_created_at_unix": created_at} if created_at is not None else {}))
        creationflags = 0
        start_new_session = False
        if os.name == "nt":
            creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        else:
            start_new_session = True
        # The private manager may require host-only ORDAX_* grants to boot,
        # but its supervised external child must NEVER inherit those grants.
        # Task-supplied environment is still applied by the one shared helper.
        from ordax_dev_agent.process_environment import external_process_environment
        child_env = external_process_environment(self.env)
        with self.log_path.open("a", encoding="utf-8", errors="replace", buffering=1) as log:
            log.write(f"\n[ORDAX] starting: {self.command!r}\n")
            try:
                self.child = subprocess.Popen(
                    self.command,
                    cwd=str(self.cwd),
                    env=child_env,
                    stdin=subprocess.PIPE,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    shell=False,
                    creationflags=creationflags,
                    start_new_session=start_new_session,
                )
            except Exception as error:
                self.update(
                    state="failed",
                    error=f"{type(error).__name__}: {error}",
                    ended_at_unix=time.time(),
                )
                log.write(f"[ORDAX] start failed: {type(error).__name__}: {error}\n")
                return 127

            self.update(
                state="running",
                child_pid=self.child.pid,
                running_at_unix=time.time(),
                **windows_child_identity(self.child),
            )
            self._stdin_thread = threading.Thread(
                target=self._pump_stdin,
                name="ordax-process-stdin",
                daemon=True,
            )
            self._stdin_thread.start()
            signal.signal(signal.SIGTERM, self.stop)
            if hasattr(signal, "SIGINT"):
                signal.signal(signal.SIGINT, self.stop)
            returncode = int(self.child.wait())
            final_state = "stopped" if self._stopping else ("exited" if returncode == 0 else "failed")
            self.update(
                state=final_state,
                returncode=returncode,
                ended_at_unix=time.time(),
            )
            log.write(f"[ORDAX] process ended with code {returncode}\n")
            return returncode


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="ordax-persistent-process-runtime")
    p.add_argument("--state", required=True)
    p.add_argument("--log", required=True)
    p.add_argument("--stdin-file", required=True)
    p.add_argument("--cwd", required=True)
    p.add_argument("--env-json", default="{}")
    p.add_argument("--token", required=True)
    p.add_argument("command", nargs=argparse.REMAINDER)
    return p


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    command = list(args.command)
    if command and command[0] == "--":
        command = command[1:]
    if not command:
        raise SystemExit("persistent process command is required")
    try:
        env = json.loads(args.env_json)
    except json.JSONDecodeError as error:
        raise SystemExit(f"invalid env JSON: {error}") from error
    if not isinstance(env, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in env.items()):
        raise SystemExit("env JSON must be an object of strings")
    runtime = ProcessRuntime(
        state_path=Path(args.state).resolve(),
        log_path=Path(args.log).resolve(),
        stdin_path=Path(args.stdin_file).resolve(),
        cwd=Path(args.cwd).resolve(),
        command=command,
        env=env,
    )
    # The same private receipt token must bind the worker before it writes
    # anything or launches a child. No new authority or receipt is created.
    receipt = runtime.load_state()
    token = receipt.get("token")
    if not isinstance(token, str) or not token or not hmac.compare_digest(
        token.encode("utf-8"), args.token.encode("utf-8")
    ) or receipt.get("state") != "starting" or receipt.get("manager_pid"):
        raise SystemExit("persistent process launch receipt mismatch")
    return runtime.run()


if __name__ == "__main__":
    raise SystemExit(main())
