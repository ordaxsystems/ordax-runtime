"""Detached supervised process runtime used by ORDAX persistent processes."""
from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path


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
                    timeout=15,
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
        self.stdin_path.parent.mkdir(parents=True, exist_ok=True)
        self.stdin_path.touch(exist_ok=True)
        while not self._stopping:
            child = self.child
            if child is None or child.poll() is not None:
                return
            try:
                with self.stdin_path.open("r", encoding="utf-8", errors="replace") as handle:
                    handle.seek(position)
                    while True:
                        line = handle.readline()
                        if not line:
                            break
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
            time.sleep(0.05)

    def run(self) -> int:
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self.update(manager_pid=os.getpid(), manager_ready_at_unix=time.time())
        creationflags = 0
        start_new_session = False
        if os.name == "nt":
            creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        else:
            start_new_session = True
        child_env = os.environ.copy()
        child_env.update(self.env)
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
    return runtime.run()


if __name__ == "__main__":
    raise SystemExit(main())
