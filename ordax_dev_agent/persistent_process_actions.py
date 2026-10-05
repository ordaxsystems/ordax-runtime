"""Persistent project process manager for ORDAX Dev."""
from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any

from .models import ActionResult


class PersistentProcessActions:
    def _process_root(self, project) -> Path:
        return (self.config.state_dir / "processes" / project.slug).resolve()

    def _process_state_path(self, project, process_id: str) -> Path:
        try:
            uuid.UUID(process_id)
        except ValueError as error:
            raise ValueError("process_id must be a UUID") from error
        return self._process_root(project) / f"{process_id}.json"

    def _process_log_path(self, project, process_id: str) -> Path:
        return self._process_root(project) / f"{process_id}.log"

    def _process_stdin_path(self, project, process_id: str) -> Path:
        return self._process_root(project) / f"{process_id}.stdin.jsonl"

    @staticmethod
    def _pid_running(pid: int) -> bool:
        if pid <= 0:
            return False
        if os.name == "nt":
            try:
                import ctypes

                PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
                SYNCHRONIZE = 0x00100000
                handle = ctypes.windll.kernel32.OpenProcess(
                    PROCESS_QUERY_LIMITED_INFORMATION | SYNCHRONIZE,
                    False,
                    pid,
                )
                if not handle:
                    return False
                try:
                    WAIT_TIMEOUT = 0x00000102
                    return ctypes.windll.kernel32.WaitForSingleObject(handle, 0) == WAIT_TIMEOUT
                finally:
                    ctypes.windll.kernel32.CloseHandle(handle)
            except (AttributeError, OSError):
                return False
        proc_stat = Path(f"/proc/{pid}/stat")
        try:
            raw = proc_stat.read_text(encoding="utf-8", errors="replace")
            parts = raw.split()
            if len(parts) >= 3 and parts[2] == "Z":
                return False
        except OSError:
            pass
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False

    @staticmethod
    def _commandline(pid: int) -> str:
        if pid <= 0:
            return ""
        if os.name == "nt":
            script = (
                "$ErrorActionPreference='SilentlyContinue';"
                f"(Get-CimInstance Win32_Process -Filter \"ProcessId = {pid}\").CommandLine"
            )
            try:
                result = subprocess.run(
                    ["powershell.exe", "-NoProfile", "-Command", script],
                    capture_output=True,
                    text=True,
                    timeout=5,
                    shell=False,
                )
                return (result.stdout or "").strip()
            except (OSError, subprocess.TimeoutExpired):
                return ""
        path = Path(f"/proc/{pid}/cmdline")
        try:
            return path.read_bytes().replace(b"\x00", b" ").decode("utf-8", "replace")
        except OSError:
            return ""

    def _wait_process_files_released(
        self,
        project,
        process_id: str,
        *,
        timeout_seconds: float = 2.0,
    ) -> bool:
        """Wait until Windows has released runtime-owned files for safe cleanup."""
        root = self._process_root(project)
        log_path = self._process_log_path(project, process_id)
        probe_path = log_path.with_name(f".{log_path.name}.release-probe")
        deadline = time.monotonic() + max(0.1, timeout_seconds)
        quiet_since: float | None = None

        while time.monotonic() < deadline:
            files_released = False
            try:
                if probe_path.exists() and not log_path.exists():
                    os.replace(probe_path, log_path)
                if log_path.exists():
                    os.replace(log_path, probe_path)
                    os.replace(probe_path, log_path)
                runtime_temps = list(root.glob(f".{process_id}.json.*.tmp"))
                files_released = not runtime_temps
            except OSError:
                try:
                    if probe_path.exists() and not log_path.exists():
                        os.replace(probe_path, log_path)
                except OSError:
                    pass

            now = time.monotonic()
            if files_released:
                if quiet_since is None:
                    quiet_since = now
                elif now - quiet_since >= 0.25:
                    return True
            else:
                quiet_since = None
            time.sleep(0.05)
        return False

    def _load_process_state(self, project, process_id: str) -> dict[str, Any]:
        path = self._process_state_path(project, process_id)
        if not path.is_file():
            raise ValueError(f"persistent process not found: {process_id}")

        last_error: OSError | json.JSONDecodeError | None = None
        for attempt in range(40):
            try:
                state = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as error:
                last_error = error
                if attempt < 39:
                    time.sleep(0.025)
                    continue
                break
            if not isinstance(state, dict) or state.get("process_id") != process_id or state.get("project") != project.slug:
                raise ValueError("persistent process state identity mismatch")
            return state

        raise ValueError(f"persistent process state is invalid: {process_id}") from last_error

    def _owned(self, state: dict[str, Any]) -> bool:
        pid = int(state.get("manager_pid") or 0)
        token = str(state.get("token") or "")
        process_id = str(state.get("process_id") or "")
        if not token or not process_id:
            return False

        # A live Popen handle created by this ActionRegistry is stronger and much
        # cheaper ownership evidence than spawning tasklist/PowerShell during the
        # startup hot path. On Windows, a venv launcher and the supervised Python
        # runtime may legitimately have different PIDs, so a local ephemeral
        # launch token completes the ownership proof for that case.
        handles = getattr(self, "_persistent_process_handles", {})
        handle = handles.get(process_id)
        handle_running = False
        launch_tokens = getattr(self, "_persistent_process_launch_tokens", {})
        local_token = str(launch_tokens.get(process_id) or "")
        if handle is not None:
            handle_running = handle.poll() is None
            if handle_running and (not pid or handle.pid == pid):
                return True
            if not handle_running:
                # A virtual-environment python.exe may be only a launcher. Once
                # that local handle exits, remove it; the current ORDAX process
                # can still prove ownership with its ephemeral launch token.
                handles.pop(process_id, None)

        if (
            local_token
            and local_token == token
            and state.get("manager_ready_at_unix")
            and pid
        ):
            # Fast-path for a process launched by this ActionRegistry. The
            # runtime-authored manager handshake plus the in-memory launch token
            # is sufficient proof for the current ORDAX instance. A separate PID
            # probe here is racy on Windows during venv launcher handoff.
            # The ephemeral token never survives an ORDAX restart, so recovered
            # state still uses the stricter live-PID + command-line proof below.
            return True

        if not pid or not self._pid_running(pid):
            return False
        commandline = self._commandline(pid)
        return "persistent_process_runtime.py" in commandline and token in commandline

    def _public_process_state(self, state: dict[str, Any]) -> dict[str, Any]:
        manager_pid = int(state.get("manager_pid") or 0)
        owned = self._owned(state)
        # A live managed Popen handle is authoritative for processes launched by
        # this ActionRegistry. OS process enumeration is only needed for
        # recovered state after the ORDAX Runtime itself restarts.
        running = bool(owned or self._pid_running(manager_pid))
        lifecycle = str(state.get("state") or "unknown")
        if running and owned and lifecycle in {"starting", "running"}:
            lifecycle = "running"
        elif running and not owned:
            lifecycle = "stale"
        elif not running and lifecycle in {"starting", "running", "stopping"}:
            lifecycle = "stopped"
        result = dict(state)
        result.update({
            "state": lifecycle,
            "running": bool(running and owned),
            "ownership_valid": owned,
        })
        if not manager_pid and owned:
            handle = getattr(self, "_persistent_process_handles", {}).get(
                str(state.get("process_id") or "")
            )
            if handle is not None and handle.poll() is None:
                # Surface the authoritative local manager PID immediately without
                # racing the detached runtime's atomic state writer. The runtime
                # will persist the same PID as part of its normal startup handshake.
                result["manager_pid"] = handle.pid
        return result

    def process_start(self, payload: dict[str, Any]) -> ActionResult:
        project = self._project(payload)
        argv = payload.get("argv")
        if (
            not isinstance(argv, list)
            or not argv
            or len(argv) > 128
            or not all(isinstance(item, str) and item and "\x00" not in item and len(item) <= 8192 for item in argv)
        ):
            return ActionResult(False, "argv must contain 1..128 valid strings")

        cwd_raw = str(payload.get("cwd") or ".")
        cwd = project.path(cwd_raw, must_exist=True)
        if not cwd.is_dir():
            return ActionResult(False, "cwd is not a directory")

        env_raw = payload.get("env")
        env: dict[str, str] = {}
        if env_raw is not None:
            if not isinstance(env_raw, dict) or len(env_raw) > 64:
                return ActionResult(False, "env must be an object with at most 64 entries")
            for key, value in env_raw.items():
                if not isinstance(key, str) or not key or "\x00" in key or not isinstance(value, str) or "\x00" in value:
                    return ActionResult(False, "env contains an invalid key or value")
                env[key] = value

        command = list(argv)
        resolved = shutil.which(command[0])
        if resolved:
            command[0] = resolved

        process_id = str(uuid.uuid4())
        token = uuid.uuid4().hex
        root = self._process_root(project)
        root.mkdir(parents=True, exist_ok=True)
        state_path = self._process_state_path(project, process_id)
        log_path = self._process_log_path(project, process_id)
        stdin_path = self._process_stdin_path(project, process_id)
        stdin_path.touch(exist_ok=True)
        runtime = Path(__file__).with_name("persistent_process_runtime.py").resolve()
        manager_command = [
            sys.executable,
            str(runtime),
            "--state", str(state_path),
            "--log", str(log_path),
            "--stdin-file", str(stdin_path),
            "--cwd", str(cwd),
            "--env-json", json.dumps(env, ensure_ascii=False, separators=(",", ":")),
            "--token", token,
            "--",
            *command,
        ]

        creationflags = 0
        start_new_session = False
        if os.name == "nt":
            creationflags = (
                getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                | getattr(subprocess, "DETACHED_PROCESS", 0)
                | getattr(subprocess, "CREATE_NO_WINDOW", 0)
            )
        else:
            start_new_session = True

        state = {
            "schema_version": 1,
            "process_id": process_id,
            "project": project.slug,
            "token": token,
            "state": "starting",
            "cwd": cwd.relative_to(project.root.resolve()).as_posix() if cwd != project.root.resolve() else ".",
            "argv": argv,
            "log_path": str(log_path),
            "started_at_unix": time.time(),
        }
        temp = state_path.with_suffix(".tmp")
        temp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(state_path)

        try:
            manager = subprocess.Popen(
                manager_command,
                cwd=str(project.root),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                shell=False,
                creationflags=creationflags,
                start_new_session=start_new_session,
            )
            handles = getattr(self, "_persistent_process_handles", None)
            if handles is None:
                handles = {}
                self._persistent_process_handles = handles
            handles[process_id] = manager
            launch_tokens = getattr(self, "_persistent_process_launch_tokens", None)
            if launch_tokens is None:
                launch_tokens = {}
                self._persistent_process_launch_tokens = launch_tokens
            launch_tokens[process_id] = token
        except OSError as error:
            state.update({"state": "failed", "error": str(error), "ended_at_unix": time.time()})
            state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
            return ActionResult(False, f"cannot start persistent process: {error}", state)

        requested_wait = min(max(float(payload.get("wait_seconds", 0.5)), 0.1), 5.0)
        # The caller's wait preference must not shorten the supervisor's own
        # startup handshake. A small fixed floor prevents Windows launcher/runtime
        # PID transitions and antivirus/process-enumeration latency from turning a
        # healthy process into a false startup failure.
        handshake_deadline = time.monotonic() + max(3.0, requested_wait)
        while time.monotonic() < handshake_deadline:
            time.sleep(0.05)
            current_raw = self._load_process_state(project, process_id)
            lifecycle = str(current_raw.get("state") or "")
            if lifecycle in {"running", "failed", "exited", "stopped"}:
                break

        # After the initial state file is created, the detached runtime is the
        # sole writer of manager_pid/lifecycle. Do not patch those fields from
        # the caller process: doing so can overwrite a concurrent atomic runtime
        # update and reintroduce a startup race.
        current_raw = self._load_process_state(project, process_id)
        current = self._public_process_state(current_raw)
        started_ok = bool(
            current_raw.get("state") == "running"
            and current_raw.get("child_pid")
            and current_raw.get("running_at_unix")
            and current.get("ownership_valid")
            and current.get("running")
        )
        if not started_ok and manager.poll() is None:
            try:
                if os.name == "nt":
                    subprocess.run(
                        ["taskkill", "/PID", str(manager.pid), "/T", "/F"],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        timeout=15,
                        shell=False,
                    )
                else:
                    try:
                        os.killpg(manager.pid, signal.SIGTERM)
                    except ProcessLookupError:
                        pass
                manager.wait(timeout=5)
            except (OSError, subprocess.TimeoutExpired):
                pass
            getattr(self, "_persistent_process_handles", {}).pop(process_id, None)
            getattr(self, "_persistent_process_launch_tokens", {}).pop(process_id, None)
            current = self._public_process_state(self._load_process_state(project, process_id))
        return ActionResult(
            started_ok,
            "persistent process started" if started_ok else "persistent process failed to start",
            current,
        )

    def process_status(self, payload: dict[str, Any]) -> ActionResult:
        project = self._project(payload)
        process_id = str(payload.get("process_id") or "")
        state = self._public_process_state(self._load_process_state(project, process_id))
        return ActionResult(True, "persistent process status ready", state)

    def process_list(self, payload: dict[str, Any]) -> ActionResult:
        project = self._project(payload)
        root = self._process_root(project)
        items: list[dict[str, Any]] = []
        if root.is_dir():
            for path in sorted(root.glob("*.json")):
                try:
                    raw = json.loads(path.read_text(encoding="utf-8"))
                    process_id = str(raw.get("process_id") or "")
                    if process_id:
                        items.append(self._public_process_state(self._load_process_state(project, process_id)))
                except Exception:
                    continue
        return ActionResult(True, "persistent process list ready", {"project": project.slug, "processes": items})

    def process_logs(self, payload: dict[str, Any]) -> ActionResult:
        project = self._project(payload)
        process_id = str(payload.get("process_id") or "")
        state = self._public_process_state(self._load_process_state(project, process_id))
        max_bytes = int(payload.get("max_bytes", 65536))
        if not 1024 <= max_bytes <= 262144:
            return ActionResult(False, "max_bytes must be between 1024 and 262144")
        path = self._process_log_path(project, process_id)
        if not path.is_file():
            return ActionResult(True, "persistent process has no logs yet", {"process": state, "tail": ""})
        size = path.stat().st_size
        offset = max(0, size - max_bytes)
        with path.open("rb") as handle:
            handle.seek(offset)
            chunk = handle.read(max_bytes)
        text = chunk.decode("utf-8", "replace")
        if offset and "\n" in text:
            text = text.split("\n", 1)[1]
        return ActionResult(True, "persistent process logs ready", {
            "process": state,
            "tail": text,
            "size_bytes": size,
            "truncated": offset > 0,
        })

    def process_write_stdin(self, payload: dict[str, Any]) -> ActionResult:
        project = self._project(payload)
        process_id = str(payload.get("process_id") or "")
        state = self._public_process_state(self._load_process_state(project, process_id))
        if not state.get("running") or not state.get("ownership_valid"):
            return ActionResult(False, "persistent process is not running or not owned by ORDAX", state)

        text = payload.get("text")
        if not isinstance(text, str):
            return ActionResult(False, "text must be a string")
        encoded = text.encode("utf-8")
        if len(encoded) > 64 * 1024:
            return ActionResult(False, "stdin payload exceeds 65536 bytes")
        newline = bool(payload.get("newline", True))

        path = self._process_stdin_path(project, process_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        entry = json.dumps(
            {"text": text, "newline": newline, "created_at_unix": time.time()},
            ensure_ascii=False,
            separators=(",", ":"),
        )
        try:
            with path.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(entry + "\n")
                handle.flush()
                os.fsync(handle.fileno())
        except OSError as error:
            return ActionResult(False, f"persistent process stdin write failed: {error}", state)

        return ActionResult(
            True,
            "persistent process stdin queued",
            {
                "process_id": process_id,
                "queued_bytes": len(encoded),
                "newline": newline,
            },
        )

    def process_stop(self, payload: dict[str, Any]) -> ActionResult:
        project = self._project(payload)
        process_id = str(payload.get("process_id") or "")
        state = self._public_process_state(self._load_process_state(project, process_id))
        pid = int(state.get("manager_pid") or 0)
        if not state.get("running"):
            if state.get("state") == "stale":
                return ActionResult(False, "refusing to stop a process not owned by ORDAX", state)
            return ActionResult(True, "persistent process is already stopped", state)
        if not state.get("ownership_valid"):
            return ActionResult(False, "refusing to stop a process not owned by ORDAX", state)

        handles = getattr(self, "_persistent_process_handles", {})
        handle = handles.get(process_id)
        termination_pid = pid
        if handle is not None and handle.poll() is None:
            # A Windows venv launcher can differ from the supervised runtime PID.
            # Keep its root PID available only for the forced fallback path.
            termination_pid = int(handle.pid)

        # Normal shutdown is cooperative. The runtime already owns a durable
        # stdin/control queue, so ask the supervisor to stop its child, flush its
        # final state/log writes, and exit cleanly before considering force.
        control_error = ""
        try:
            stdin_path = self._process_stdin_path(project, process_id)
            control_entry = json.dumps(
                {"control": "stop", "created_at_unix": time.time()},
                ensure_ascii=False,
                separators=(",", ":"),
            )
            with stdin_path.open("a", encoding="utf-8", newline="\n") as stream:
                stream.write(control_entry + "\n")
                stream.flush()
                os.fsync(stream.fileno())
        except OSError as error:
            control_error = str(error)

        graceful_deadline = time.monotonic() + 3.0
        while time.monotonic() < graceful_deadline:
            manager_running = self._pid_running(pid)
            root_running = bool(
                handle is not None
                and handle.poll() is None
            ) or self._pid_running(termination_pid)
            if not manager_running and not root_running:
                break
            time.sleep(0.05)

        manager_running = self._pid_running(pid)
        root_running = bool(
            handle is not None
            and handle.poll() is None
        ) or self._pid_running(termination_pid)

        if manager_running or root_running:
            try:
                if os.name == "nt":
                    completed = subprocess.run(
                        ["taskkill", "/PID", str(termination_pid), "/T", "/F"],
                        capture_output=True,
                        text=True,
                        timeout=15,
                        shell=False,
                    )
                    if (
                        completed.returncode != 0
                        and self._pid_running(termination_pid)
                        and self._pid_running(pid)
                    ):
                        return ActionResult(False, "persistent process stop failed", {
                            **state,
                            "control_error": control_error,
                            "stderr": (completed.stderr or "")[-2000:],
                        })
                else:
                    os.kill(pid, signal.SIGTERM)
            except (OSError, subprocess.TimeoutExpired) as error:
                return ActionResult(False, f"persistent process stop failed: {error}", state)

            forced_deadline = time.monotonic() + 5.0
            while time.monotonic() < forced_deadline:
                root_running = self._pid_running(termination_pid)
                manager_running = self._pid_running(pid)
                if not root_running and not manager_running:
                    break
                time.sleep(0.05)

        handle = handles.pop(process_id, None)
        getattr(self, "_persistent_process_launch_tokens", {}).pop(process_id, None)
        if handle is not None:
            try:
                handle.wait(timeout=2)
            except subprocess.TimeoutExpired:
                pass
        elif os.name != "nt":
            try:
                os.waitpid(pid, os.WNOHANG)
            except (ChildProcessError, OSError):
                pass
        files_released = True
        if os.name == "nt":
            files_released = self._wait_process_files_released(
                project,
                process_id,
                timeout_seconds=2.0,
            )

        final = self._public_process_state(self._load_process_state(project, process_id))
        final["running"] = False
        final["ownership_valid"] = False
        final["files_released"] = files_released
        if final.get("state") in {"starting", "running", "stopping"}:
            final["state"] = "stopped"

        stopped = bool(not self._pid_running(pid) and files_released)
        return ActionResult(
            stopped,
            "persistent process stopped" if stopped else "persistent process stop incomplete",
            final,
        )
