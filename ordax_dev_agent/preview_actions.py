"""Unified project preview actions for ORDAX Studio."""
from __future__ import annotations

import json
import os
import shutil
import socket
import sys
import time
import uuid
import urllib.request
import subprocess
from pathlib import Path
from typing import Any

from .models import ActionResult
from .browser_capture import BrowserCaptureError, capture_url, find_chromium

_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}


class PreviewActions:
    def _latest_preview_image(self, project) -> dict[str, Any] | None:
        roots = (
            ("managed", (self.config.state_dir / "artifacts" / project.slug).resolve()),
            ("project", (project.root / "Artifacts").resolve()),
        )
        latest: tuple[int, Path, str, Path] | None = None
        for source, root in roots:
            if not root.is_dir():
                continue
            for path in root.rglob("*"):
                if not path.is_file() or path.suffix.lower() not in _IMAGE_SUFFIXES:
                    continue
                try:
                    stat = path.stat()
                except OSError:
                    continue
                candidate = (stat.st_mtime_ns, path.resolve(), source, root)
                if latest is None or candidate[0] > latest[0]:
                    latest = candidate
        if latest is None:
            return None
        modified_ns, path, source, root = latest
        relative = path.relative_to(root).as_posix()
        return {
            "source": source,
            "path": str(path),
            "relative_path": relative,
            "modified_at_ns": modified_ns,
            "size_bytes": path.stat().st_size,
            "artifact_preview_payload": (
                {"project_artifact_path": relative}
                if source == "project"
                else {"artifact_name": relative}
            ),
        }

    def _preview_mode(self, project) -> str:
        preview = getattr(project, "preview", {}) or {}
        if preview.get("url") or preview.get("entry"):
            return "web"
        if "blender" in project.apps:
            return "blender"
        if "unity" in project.apps:
            return "unity"
        if (project.root / "index.html").is_file():
            return "web"
        package = project.root / "package.json"
        if package.is_file():
            try:
                data = json.loads(package.read_text(encoding="utf-8-sig"))
            except Exception:
                data = {}
            if isinstance(data.get("scripts"), dict) and data["scripts"].get("dev"):
                return "web"
        return "artifact"

    def project_preview_status(self, payload: dict[str, Any]) -> ActionResult:
        project = self._project(payload)
        preview = getattr(project, "preview", {}) or {}
        latest = self._latest_preview_image(project)
        runtime = self._preview_runtime_status(project)
        url = str((runtime.get("url") if runtime.get("running") else "") or preview.get("url") or "").strip()
        # Web preview is a project runtime/host, never a direct file:// view.
        # A configured external URL is allowed; local projects use preview_start.
        return ActionResult(True, "project preview status ready", {
            "project": project.slug,
            "mode": self._preview_mode(project),
            "url": url or None,
            "runtime": runtime,
            "latest_image": latest,
            "auto_refresh_seconds": int(preview.get("refresh_seconds", 3) or 3),
        })

    def project_preview_capture(self, payload: dict[str, Any]) -> ActionResult:
        project = self._project(payload)
        mode = self._preview_mode(project)
        if mode == "blender":
            return self.blender_live_capture({
                "project": project.slug,
                "timeout_seconds": float(payload.get("timeout_seconds", 90)),
            })
        if mode == "unity":
            return self.unity_capture({
                "project": project.slug,
                "width": int(payload.get("width", 1280)),
                "height": int(payload.get("height", 720)),
                "warmup_frames": int(payload.get("warmup_frames", 1)),
                "timeout_seconds": float(payload.get("timeout_seconds", 120)),
            })
        status = self.project_preview_status({"project": project.slug})
        url = str(status.data.get("url") or "") if status.ok else ""
        if not url:
            return ActionResult(False, "no visual or web preview source is configured")
        output = (self.config.state_dir / "artifacts" / project.slug / "web-preview.png").resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        width = max(320, min(int(payload.get("width", 1280)), 2560))
        height = max(240, min(int(payload.get("height", 720)), 1600))
        timeout_seconds = max(5.0, min(float(payload.get("timeout_seconds", 30)), 60.0))
        browser = find_chromium()
        if browser is None:
            return ActionResult(False, "Chrome or Edge executable not found for web preview")
        try:
            captured = capture_url(
                browser, url, output, self.config.state_dir / "previews" / "chromium",
                width=width, height=height, timeout_seconds=timeout_seconds,
            )
        except (BrowserCaptureError, OSError, ValueError) as error:
            return ActionResult(False, f"web preview capture failed: {error}", {
                "url": url, "browser": str(browser), "provider": "chromium-cdp",
            })
        return ActionResult(True, "web preview captured", {
            **captured, "url": url,
        })

    def _preview_state_path(self, project) -> Path:
        return self.config.state_dir / "previews" / f"{project.slug}.json"

    def _preview_log_path(self, project) -> Path:
        return self.config.state_dir / "previews" / f"{project.slug}.log"

    @staticmethod
    def _available_port(configured: int = 0) -> int:
        if configured < 0 or configured > 65535:
            raise ValueError("preview port must be between 1 and 65535")
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            probe.bind(("127.0.0.1", configured))
            return int(probe.getsockname()[1])

    def _write_preview_state(self, project, state: dict[str, Any]) -> None:
        path = self._preview_state_path(project)
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix(".tmp")
        temp.write_text(json.dumps(state, indent=2), encoding="utf-8")
        temp.replace(path)

    @staticmethod
    def _process_running(pid: int) -> bool:
        if pid <= 0:
            return False
        if os.name == "nt":
            try:
                result = subprocess.run(
                    ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                    capture_output=True, text=True, shell=False, timeout=3,
                )
                return result.returncode == 0 and f'"{pid}"' in result.stdout
            except (OSError, subprocess.TimeoutExpired):
                return False
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False

    @staticmethod
    def _process_commandline(pid: int) -> str:
        if pid <= 0:
            return ""
        if os.name == "nt":
            command = (
                "$ErrorActionPreference='SilentlyContinue'; "
                f"(Get-CimInstance Win32_Process -Filter \"ProcessId = {pid}\").CommandLine"
            )
            try:
                result = subprocess.run(
                    ["powershell.exe", "-NoProfile", "-Command", command],
                    capture_output=True, text=True, shell=False, timeout=4,
                )
                return (result.stdout or "").strip()
            except (OSError, subprocess.TimeoutExpired):
                return ""
        proc = Path(f"/proc/{pid}/cmdline")
        try:
            return proc.read_bytes().replace(b"\x00", b" ").decode("utf-8", "replace")
        except OSError:
            return ""

    def _runtime_owned(self, project, state: dict[str, Any]) -> bool:
        pid = int(state.get("pid") or 0)
        token = str(state.get("token") or "")
        if not pid or not token:
            return False
        process = getattr(self, "_preview_processes", {}).get(project.slug)
        if process is not None and process.pid == pid and process.poll() is None:
            return True
        commandline = self._process_commandline(pid)
        return "preview_runtime.py" in commandline and token in commandline

    @staticmethod
    def _url_ready(url: str) -> bool:
        try:
            with urllib.request.urlopen(url, timeout=1.0) as response:
                return 200 <= int(response.status) < 500
        except Exception:
            return False

    def _preview_runtime_status(self, project) -> dict[str, Any]:
        path = self._preview_state_path(project)
        if not path.is_file():
            return {"state": "stopped", "running": False, "ownership_valid": False}
        try:
            state = json.loads(path.read_text(encoding="utf-8-sig"))
            pid = int(state.get("pid") or 0)
        except Exception as error:
            return {"state": "error", "running": False, "ownership_valid": False, "error": str(error)}
        process_running = self._process_running(pid)
        ownership_valid = bool(process_running and self._runtime_owned(project, state))
        if process_running and not ownership_valid:
            return {**state, "state": "stale", "running": False, "ownership_valid": False,
                    "error": "stored preview PID is not owned by ORDAX"}
        if not process_running:
            lifecycle = str(state.get("state") or "stopped")
            if lifecycle in {"starting", "running"}:
                lifecycle = "stopped"
            return {**state, "state": lifecycle, "running": False, "ownership_valid": False,
                    "url_ready": False}
        url = str(state.get("url") or "")
        ready = bool(url and self._url_ready(url))
        lifecycle = "running" if ready else str(state.get("state") or "starting")
        return {**state, "state": lifecycle, "running": True, "ownership_valid": True,
                "url_ready": ready}

    def _web_launch_spec(self, project) -> tuple[list[str], str, dict[str, str]] | ActionResult:
        preview = getattr(project, "preview", {}) or {}
        try:
            port = self._available_port(int(preview.get("port") or 0))
        except (OSError, ValueError) as error:
            return ActionResult(False, f"preview port is unavailable: {error}")
        package = project.root / "package.json"
        env = os.environ.copy()
        if package.is_file():
            try:
                data = json.loads(package.read_text(encoding="utf-8-sig"))
            except Exception as error:
                return ActionResult(False, f"cannot read package.json: {error}")
            scripts = data.get("scripts") or {}
            deps = {**(data.get("dependencies") or {}), **(data.get("devDependencies") or {})}
            npm = shutil.which("npm") or shutil.which("npm.cmd")
            if not npm or "dev" not in scripts:
                return ActionResult(False, "web preview needs npm and a package.json dev script")
            if "vite" in deps:
                command = [npm, "run", "dev", "--", "--host", "127.0.0.1", "--port", str(port)]
            elif "next" in deps:
                command = [npm, "run", "dev", "--", "-H", "127.0.0.1", "-p", str(port)]
            else:
                env["HOST"] = "127.0.0.1"
                env["PORT"] = str(port)
                env["BROWSER"] = "none"
                command = [npm, "run", "dev"]
            return command, f"http://127.0.0.1:{port}", env
        if (project.root / "index.html").is_file():
            command = [sys.executable, "-m", "http.server", str(port),
                       "--bind", "127.0.0.1", "--directory", str(project.root)]
            return command, f"http://127.0.0.1:{port}", env
        return ActionResult(False, "no supported web preview runtime was detected")

    def project_preview_start(self, payload: dict[str, Any]) -> ActionResult:
        project = self._project(payload)
        if self._preview_mode(project) != "web":
            return ActionResult(False, "preview runtime start is available only for web projects")
        current = self._preview_runtime_status(project)
        if current.get("running"):
            return ActionResult(True, "web preview runtime already running", current)
        spec = self._web_launch_spec(project)
        if isinstance(spec, ActionResult):
            return spec
        command, url, env = spec
        log = self._preview_log_path(project)
        log.parent.mkdir(parents=True, exist_ok=True)
        token = uuid.uuid4().hex
        supervisor = Path(__file__).with_name("preview_runtime.py").resolve()
        manager_command = [
            sys.executable, str(supervisor),
            "--token", token, "--cwd", str(project.root),
            "--log", str(log), "--", *command,
        ]
        creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        if os.name == "nt":
            creationflags |= getattr(subprocess, "DETACHED_PROCESS", 0)
        try:
            process = subprocess.Popen(
                manager_command, cwd=str(project.root), env=env,
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                shell=False, creationflags=creationflags,
            )
        except OSError as error:
            return ActionResult(False, f"cannot start web preview runtime: {error}")
        processes = getattr(self, "_preview_processes", None)
        if processes is None:
            processes = {}
            self._preview_processes = processes
        processes[project.slug] = process
        state = {
            "schema_version": 1, "project": project.slug, "token": token,
            "state": "starting", "pid": process.pid, "url": url,
            "command": command, "log": str(log), "started_at_unix": time.time(),
        }
        self._write_preview_state(project, state)
        deadline = time.monotonic() + max(2.0, min(float(payload.get("wait_seconds", 20)), 45.0))
        while time.monotonic() < deadline:
            if self._url_ready(url):
                state.update({"state": "running", "running": True, "url_ready": True,
                              "ready_at_unix": time.time(), "ownership_valid": True})
                self._write_preview_state(project, state)
                return ActionResult(True, "web preview runtime started", state)
            exit_code = process.poll()
            if exit_code is not None:
                state.update({"state": "error", "running": False, "url_ready": False,
                              "exit_code": int(exit_code), "stopped_at_unix": time.time()})
                self._write_preview_state(project, state)
                return ActionResult(False, "web preview runtime exited during startup", state)
            time.sleep(0.25)
        state.update({"state": "starting", "running": self._process_running(process.pid),
                      "url_ready": False, "ownership_valid": self._runtime_owned(project, state)})
        self._write_preview_state(project, state)
        return ActionResult(bool(state["running"]),
                            "web preview runtime started but URL is not ready yet", state)

    def project_preview_stop(self, payload: dict[str, Any]) -> ActionResult:
        project = self._project(payload)
        state = self._preview_runtime_status(project)
        pid = int(state.get("pid") or 0)
        if state.get("state") == "stale" and pid:
            return ActionResult(False, "refusing to stop a process not owned by ORDAX", state)
        if not state.get("running") or not pid:
            return ActionResult(True, "web preview runtime is already stopped", state)
        if not state.get("ownership_valid"):
            return ActionResult(False, "refusing to stop a process not owned by ORDAX", state)
        process = getattr(self, "_preview_processes", {}).pop(project.slug, None)
        try:
            if os.name == "nt":
                completed = subprocess.run(
                    ["taskkill", "/PID", str(pid), "/T", "/F"],
                    capture_output=True, text=True, shell=False, timeout=10,
                )
            else:
                os.kill(pid, 15)
                completed = None
        except (OSError, subprocess.TimeoutExpired) as error:
            return ActionResult(False, f"cannot stop web preview runtime: {error}", state)
        if process is not None:
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
        deadline = time.monotonic() + 3.0
        while self._process_running(pid) and time.monotonic() < deadline:
            time.sleep(0.05)
        stopped = not self._process_running(pid)
        if not stopped:
            details = {**state}
            if completed is not None:
                details["taskkill_returncode"] = completed.returncode
                details["taskkill_stderr"] = (completed.stderr or "")[-1000:]
            return ActionResult(False, "web preview runtime stop failed", details)
        state.update({"state": "stopped", "running": False, "url_ready": False,
                      "ownership_valid": False, "stopped_at_unix": time.time()})
        self._write_preview_state(project, state)
        return ActionResult(True, "web preview runtime stopped", state)

    def project_preview_logs(self, payload: dict[str, Any]) -> ActionResult:
        project = self._project(payload)
        raw_max = payload.get("max_bytes", 32768)
        if type(raw_max) is not int or not 1024 <= raw_max <= 131072:
            return ActionResult(False, "max_bytes must be between 1024 and 131072")
        log = self._preview_log_path(project)
        runtime = self._preview_runtime_status(project)
        if not log.is_file():
            return ActionResult(True, "preview runtime has no log output yet",
                                {"project": project.slug, "runtime": runtime, "log": str(log), "tail": ""})
        size = log.stat().st_size
        offset = max(0, size - raw_max)
        with log.open("rb") as handle:
            handle.seek(offset)
            chunk = handle.read(raw_max)
        text = chunk.decode("utf-8", "replace")
        if offset and "\n" in text:
            text = text.split("\n", 1)[1]
        return ActionResult(True, "preview runtime log ready", {
            "project": project.slug, "runtime": runtime, "log": str(log),
            "size_bytes": size, "truncated": offset > 0, "tail": text,
        })
