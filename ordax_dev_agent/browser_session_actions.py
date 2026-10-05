"""Persistent Chromium CDP sessions for ORDAX Studio browser tools."""
from __future__ import annotations

import base64
import json
import os
import shutil
import socket
import subprocess
import time
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Any

from websockets.sync.client import connect

from .browser_capture import BrowserCaptureError, _cdp_call, find_chromium
from .models import ActionResult


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _http_json(url: str, timeout: float = 2.0) -> Any:
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return json.loads(response.read())


def _same_origin(left: str, right: str) -> bool:
    a = urllib.parse.urlsplit(left)
    b = urllib.parse.urlsplit(right)
    return (a.scheme, a.hostname, a.port) == (b.scheme, b.hostname, b.port)


class BrowserSessionActions:
    def _browser_root(self, project) -> Path:
        return (self.config.state_dir / "browser-sessions" / project.slug).resolve()

    def _browser_state_path(self, project, session_id: str) -> Path:
        try:
            uuid.UUID(session_id)
        except ValueError as error:
            raise ValueError("session_id must be a UUID") from error
        return self._browser_root(project) / f"{session_id}.json"

    def _browser_profile_path(self, project, session_id: str) -> Path:
        return self._browser_root(project) / f"{session_id}-profile"

    @staticmethod
    def _valid_url(raw: str, *, allow_blank: bool = False) -> str:
        value = raw.strip()
        if allow_blank and value == "about:blank":
            return value
        parsed = urllib.parse.urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("browser URL must use http or https")
        return value

    @staticmethod
    def _is_protected_provider_url(raw: str) -> bool:
        try:
            host = (urllib.parse.urlsplit(str(raw or "")).hostname or "").lower().rstrip(".")
        except ValueError:
            return False
        return host in {"chatgpt.com", "chat.openai.com"} or host.endswith(".chatgpt.com")

    @classmethod
    def _assert_automation_url_allowed(cls, raw: str) -> None:
        if cls._is_protected_provider_url(raw):
            raise ValueError(
                "ORDAX browser automation does not operate ChatGPT consumer pages; "
                "use the official ORDAX MCP/Web Bridge for normal ChatGPT"
            )

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
        try:
            raw = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8", errors="replace")
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
                    capture_output=True, text=True, timeout=5, shell=False,
                )
                return (result.stdout or "").strip()
            except (OSError, subprocess.TimeoutExpired):
                return ""
        try:
            return Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\x00", b" ").decode("utf-8", "replace")
        except OSError:
            return ""

    def _write_browser_state(self, project, state: dict[str, Any]) -> None:
        path = self._browser_state_path(project, str(state["session_id"]))
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix(".tmp")
        temp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(path)

    def _load_browser_state(self, project, session_id: str) -> dict[str, Any]:
        path = self._browser_state_path(project, session_id)
        if not path.is_file():
            raise ValueError(f"browser session not found: {session_id}")
        try:
            state = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise ValueError(f"browser session state is invalid: {session_id}") from error
        if not isinstance(state, dict) or state.get("session_id") != session_id or state.get("project") != project.slug:
            raise ValueError("browser session identity mismatch")
        return state

    def _discover_browser_pid(self, project, state: dict[str, Any]) -> int | None:
        profile = str(self._browser_profile_path(project, str(state["session_id"])))
        port_switch = f"--remote-debugging-port={int(state.get('debug_port') or 0)}"
        if os.name == "nt":
            escaped_profile = profile.replace("'", "''")
            escaped_port = port_switch.replace("'", "''")
            script = (
                f"$profile='{escaped_profile}';$port='{escaped_port}';"
                "Get-CimInstance Win32_Process | "
                "Where-Object { ($_.Name -eq 'chrome.exe' -or $_.Name -eq 'msedge.exe') "
                "-and $_.CommandLine -like ('*' + $profile + '*') "
                "-and $_.CommandLine -like ('*' + $port + '*') } | "
                "Select-Object -ExpandProperty ProcessId"
            )
            try:
                result = subprocess.run(
                    ["powershell.exe", "-NoProfile", "-Command", script],
                    capture_output=True, text=True, timeout=6, shell=False,
                )
                for line in (result.stdout or "").splitlines():
                    try:
                        pid = int(line.strip())
                    except ValueError:
                        continue
                    if self._pid_running(pid):
                        return pid
            except (OSError, subprocess.TimeoutExpired):
                return None
            return None

        proc_root = Path("/proc")
        if not proc_root.is_dir():
            return None
        for entry in proc_root.iterdir():
            if not entry.name.isdigit():
                continue
            try:
                raw = (entry / "cmdline").read_bytes().replace(b"\x00", b" ").decode("utf-8", "replace")
            except OSError:
                continue
            if profile in raw and port_switch in raw:
                pid = int(entry.name)
                if self._pid_running(pid):
                    return pid
        return None

    def _refresh_browser_pid(self, project, state: dict[str, Any]) -> dict[str, Any]:
        if self._browser_owned(project, state):
            return state
        discovered = self._discover_browser_pid(project, state)
        if discovered is None:
            return state
        if int(state.get("pid") or 0) != discovered:
            state = dict(state)
            state["pid"] = discovered
            self._write_browser_state(project, state)
        return state

    def _browser_owned(self, project, state: dict[str, Any]) -> bool:
        pid = int(state.get("pid") or 0)
        session_id = str(state.get("session_id") or "")
        handle = getattr(self, "_browser_process_handles", {}).get(session_id)
        if handle is not None and handle.pid == pid and handle.poll() is None:
            return True
        if not self._pid_running(pid):
            return False
        profile = str(self._browser_profile_path(project, session_id))
        port = int(state.get("debug_port") or 0)
        commandline = self._commandline(pid)
        return bool(profile in commandline and f"--remote-debugging-port={port}" in commandline)

    @staticmethod
    def _create_browser_target(state: dict[str, Any], url: str) -> dict[str, Any] | None:
        port = int(state.get("debug_port") or 0)
        encoded = urllib.parse.quote(url, safe=":/?&=#%")
        request = urllib.request.Request(
            f"http://127.0.0.1:{port}/json/new?{encoded}",
            method="PUT",
        )
        try:
            with urllib.request.urlopen(request, timeout=2.0) as response:
                payload = json.loads(response.read())
        except Exception:
            return None
        if (
            isinstance(payload, dict)
            and payload.get("type") == "page"
            and payload.get("webSocketDebuggerUrl")
        ):
            return payload
        return None

    def _browser_target(
        self,
        state: dict[str, Any],
        *,
        timeout_seconds: float = 5.0,
        create_url: str | None = None,
    ) -> dict[str, Any]:
        port = int(state.get("debug_port") or 0)
        deadline = time.monotonic() + timeout_seconds
        cdp_ready = False
        target_create_attempted = False
        while time.monotonic() < deadline:
            try:
                version = _http_json(f"http://127.0.0.1:{port}/json/version")
                cdp_ready = isinstance(version, dict) and bool(version.get("webSocketDebuggerUrl"))
            except Exception:
                cdp_ready = False

            if cdp_ready:
                try:
                    items = _http_json(f"http://127.0.0.1:{port}/json/list")
                    pages = [
                        item for item in items if isinstance(item, dict)
                        and item.get("type") == "page"
                        and item.get("webSocketDebuggerUrl")
                    ] if isinstance(items, list) else []
                    if pages:
                        if create_url:
                            exact = next(
                                (item for item in pages if str(item.get("url") or "") == create_url),
                                None,
                            )
                            if exact is not None:
                                return exact
                            same_origin = next(
                                (
                                    item for item in pages
                                    if _same_origin(str(item.get("url") or ""), create_url)
                                ),
                                None,
                            )
                            if same_origin is not None:
                                return same_origin
                        else:
                            return pages[0]
                except Exception:
                    pass

                if create_url and not target_create_attempted:
                    target_create_attempted = True
                    created = self._create_browser_target(state, create_url)
                    if created is not None:
                        return created
            time.sleep(0.1)
        detail = "CDP ready but no page target" if cdp_ready else "CDP endpoint unavailable"
        raise BrowserCaptureError(f"Chromium CDP page target is unavailable ({detail})")

    def _with_page(self, project, session_id: str, fn, *, preferred_url: str | None = None):
        state = self._refresh_browser_pid(project, self._load_browser_state(project, session_id))
        if not self._browser_owned(project, state):
            raise ValueError("browser session is not running or is not owned by ORDAX")
        target = self._browser_target(state, create_url=preferred_url)
        self._assert_automation_url_allowed(str(target.get("url") or ""))
        with connect(str(target["webSocketDebuggerUrl"]), open_timeout=3, close_timeout=1) as ws:
            return fn(ws, state, target)

    @staticmethod
    def _eval(ws, request_id: int, expression: str, *, await_promise: bool = False) -> Any:
        result = _cdp_call(ws, request_id, "Runtime.evaluate", {
            "expression": expression,
            "returnByValue": True,
            "awaitPromise": await_promise,
        })
        remote = result.get("result") or {}
        if remote.get("subtype") == "error":
            raise BrowserCaptureError(str(remote.get("description") or "browser JavaScript failed"))
        return remote.get("value")

    def browser_start(self, payload: dict[str, Any]) -> ActionResult:
        project = self._project(payload)
        browser = find_chromium()
        if browser is None:
            return ActionResult(False, "Chrome or Edge executable not found")

        url = self._valid_url(str(payload.get("url") or "about:blank"), allow_blank=True)
        if url != "about:blank":
            self._assert_automation_url_allowed(url)
        session_id = str(uuid.uuid4())
        port = _free_port()
        profile = self._browser_profile_path(project, session_id)
        profile.mkdir(parents=True, exist_ok=True)
        command = [
            str(browser),
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-background-networking",
            "--disable-sync",
            "--disable-extensions",
            f"--user-data-dir={profile}",
            f"--remote-debugging-port={port}",
            "--remote-debugging-address=127.0.0.1",
            url,
        ]
        if bool(payload.get("headless", False)):
            command.insert(1, "--headless=new")

        creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        start_new_session = os.name != "nt"
        try:
            process = subprocess.Popen(
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                shell=False,
                creationflags=creationflags,
                start_new_session=start_new_session,
            )
        except OSError as error:
            return ActionResult(False, f"cannot start browser session: {error}")

        state = {
            "schema_version": 1,
            "session_id": session_id,
            "project": project.slug,
            "pid": process.pid,
            "debug_port": port,
            "profile": str(profile),
            "browser": str(browser),
            "headless": bool(payload.get("headless", False)),
            "started_at_unix": time.time(),
        }
        self._write_browser_state(project, state)
        handles = getattr(self, "_browser_process_handles", None)
        if handles is None:
            handles = {}
            self._browser_process_handles = handles
        handles[session_id] = process

        try:
            deadline = time.monotonic() + max(
                30.0,
                min(float(payload.get("wait_seconds", 8)), 60.0),
            )
            target = None
            last_error = None
            while time.monotonic() < deadline:
                exit_code = process.poll()
                if exit_code is not None:
                    raise BrowserCaptureError(
                        f"Chromium exited before CDP became ready (exit code {exit_code})"
                    )
                try:
                    target = self._browser_target(
                        state,
                        timeout_seconds=min(2.0, max(0.2, deadline - time.monotonic())),
                        create_url=url,
                    )
                    break
                except BrowserCaptureError as error:
                    last_error = error
                    time.sleep(0.1)
            if target is None:
                raise last_error or BrowserCaptureError(
                    "Chromium CDP page target is unavailable"
                )
        except BrowserCaptureError as error:
            try:
                if os.name == "nt":
                    subprocess.run(
                        ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        timeout=15,
                        shell=False,
                    )
                else:
                    try:
                        os.killpg(process.pid, 15)
                    except ProcessLookupError:
                        pass
                process.wait(timeout=5)
            except (OSError, subprocess.TimeoutExpired):
                pass
            handles.pop(session_id, None)
            state.update({
                "startup_error": str(error),
                "stopped_at_unix": time.time(),
            })
            self._write_browser_state(project, state)
            return ActionResult(False, str(error), {**state, "running": False, "ownership_valid": False})
        state = self._refresh_browser_pid(project, state)

        if url == "about:blank":
            ready = {
                "url": str(target.get("url") or url),
                "title": str(target.get("title") or ""),
            }
        else:
            def initialize_page(ws, _state, _target):
                _cdp_call(ws, 1, "Page.enable")
                deadline = time.monotonic() + max(
                    5.0,
                    min(float(payload.get("wait_seconds", 8)), 30.0),
                )
                request_id = 2
                current_url = ""
                title = ""
                while time.monotonic() < deadline:
                    ready_state = self._eval(ws, request_id, "document.readyState")
                    request_id += 1
                    current_url = str(self._eval(ws, request_id, "location.href") or "")
                    request_id += 1
                    title = str(self._eval(ws, request_id, "document.title") or "")
                    request_id += 1
                    if ready_state in {"interactive", "complete"} and current_url == url:
                        return {"url": current_url, "title": title}
                    time.sleep(0.1)
                raise BrowserCaptureError(
                    f"Chromium page did not become ready: {current_url or 'unknown URL'}"
                )

            try:
                ready = self._with_page(
                    project,
                    session_id,
                    initialize_page,
                    preferred_url=url,
                )
            except (ValueError, BrowserCaptureError) as error:
                cleanup = None
                try:
                    cleanup = self.browser_stop(
                        {"project": project.slug, "session_id": session_id}
                    )
                except (OSError, ValueError, subprocess.SubprocessError):
                    cleanup = None
                if cleanup is None or not cleanup.ok:
                    try:
                        if os.name == "nt":
                            subprocess.run(
                                [
                                    "taskkill",
                                    "/PID",
                                    str(int(state.get("pid") or process.pid)),
                                    "/T",
                                    "/F",
                                ],
                                stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL,
                                timeout=15,
                                shell=False,
                            )
                        else:
                            try:
                                os.killpg(int(state.get("pid") or process.pid), 15)
                            except ProcessLookupError:
                                pass
                        process.wait(timeout=5)
                    except (OSError, subprocess.TimeoutExpired):
                        pass
                    handles.pop(session_id, None)
                state.update({"startup_error": str(error), "stopped_at_unix": time.time()})
                self._write_browser_state(project, state)
                return ActionResult(
                    False,
                    f"browser initial navigation failed: {error}",
                    {**state, "running": False, "ownership_valid": False},
                )

        return ActionResult(True, "browser session started", {
            **state,
            "running": True,
            "ownership_valid": self._browser_owned(project, state),
            "url": ready["url"],
            "title": ready["title"],
        })

    def browser_status(self, payload: dict[str, Any]) -> ActionResult:
        project = self._project(payload)
        session_id = str(payload.get("session_id") or "")
        state = self._refresh_browser_pid(project, self._load_browser_state(project, session_id))
        running = self._browser_owned(project, state)
        data = {**state, "running": running, "ownership_valid": running}
        if running:
            try:
                target = self._browser_target(state)
                data.update({"url": target.get("url"), "title": target.get("title")})
            except BrowserCaptureError:
                pass
        return ActionResult(True, "browser session status ready", data)

    def browser_list(self, payload: dict[str, Any]) -> ActionResult:
        project = self._project(payload)
        root = self._browser_root(project)
        sessions: list[dict[str, Any]] = []
        if root.is_dir():
            for path in sorted(root.glob("*.json")):
                try:
                    session_id = str(json.loads(path.read_text(encoding="utf-8")).get("session_id") or "")
                    if session_id:
                        sessions.append(self.browser_status({"project": project.slug, "session_id": session_id}).data)
                except Exception:
                    continue
        return ActionResult(True, "browser sessions ready", {"project": project.slug, "sessions": sessions})

    def browser_navigate(self, payload: dict[str, Any]) -> ActionResult:
        project = self._project(payload)
        session_id = str(payload.get("session_id") or "")
        url = self._valid_url(str(payload.get("url") or ""))
        self._assert_automation_url_allowed(url)

        def run(ws, state, _target):
            _cdp_call(ws, 1, "Page.enable")
            _cdp_call(ws, 2, "Page.navigate", {"url": url})
            deadline = time.monotonic() + max(2.0, min(float(payload.get("wait_seconds", 15)), 30.0))
            request_id = 3
            while time.monotonic() < deadline:
                ready = self._eval(ws, request_id, "document.readyState")
                request_id += 1
                if ready in {"interactive", "complete"}:
                    break
                time.sleep(0.1)
            return {
                "session_id": session_id,
                "url": self._eval(ws, request_id, "location.href"),
                "title": self._eval(ws, request_id + 1, "document.title"),
            }

        try:
            data = self._with_page(project, session_id, run)
        except (ValueError, BrowserCaptureError) as error:
            return ActionResult(False, f"browser navigation failed: {error}")
        return ActionResult(True, "browser navigation complete", data)

    def browser_snapshot(self, payload: dict[str, Any]) -> ActionResult:
        project = self._project(payload)
        session_id = str(payload.get("session_id") or "")
        max_elements = max(20, min(int(payload.get("max_elements", 200)), 500))

        script = r"""
(() => {
  const clean = (v, n=500) => String(v || "").replace(/\s+/g, " ").trim().slice(0,n);
  const visible = (el) => {
    const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.visibility !== "hidden" && s.display !== "none";
  };
  const selector = [
    "a[href]","button","input","textarea","select","summary",
    "[role=button]","[role=link]","[role=checkbox]","[role=radio]",
    "[contenteditable=true]","[tabindex]"
  ].join(",");
  window.__ordaxNodeCounter = Number(window.__ordaxNodeCounter || 1);
  const elements = [];
  for (const el of document.querySelectorAll(selector)) {
    if (!visible(el)) continue;
    let id = el.getAttribute("data-ordax-node");
    if (!id) {
      id = "n" + (window.__ordaxNodeCounter++);
      el.setAttribute("data-ordax-node", id);
    }
    const r = el.getBoundingClientRect();
    elements.push({
      id,
      tag: el.tagName.toLowerCase(),
      role: clean(el.getAttribute("role"), 80),
      text: clean(el.innerText || el.getAttribute("aria-label") || el.getAttribute("title"), 300),
      value: clean(el.value, 300),
      type: clean(el.getAttribute("type"), 80),
      href: clean(el.href, 500),
      disabled: !!el.disabled,
      x: Math.round(r.x), y: Math.round(r.y),
      width: Math.round(r.width), height: Math.round(r.height)
    });
  }
  return {
    url: location.href,
    title: document.title,
    text: clean(document.body ? document.body.innerText : "", 16000),
    elements
  };
})()
"""
        try:
            data = self._with_page(
                project,
                session_id,
                lambda ws, _state, _target: self._eval(ws, 1, script),
            )
        except (ValueError, BrowserCaptureError) as error:
            return ActionResult(False, f"browser snapshot failed: {error}")
        if not isinstance(data, dict):
            return ActionResult(False, "browser snapshot returned invalid data")
        elements = data.get("elements") if isinstance(data.get("elements"), list) else []
        data["elements"] = elements[:max_elements]
        data["elements_truncated"] = len(elements) > max_elements
        data["session_id"] = session_id
        return ActionResult(True, "browser snapshot ready", data)

    def browser_click(self, payload: dict[str, Any]) -> ActionResult:
        project = self._project(payload)
        session_id = str(payload.get("session_id") or "")
        node_id = str(payload.get("node_id") or "").strip()
        if not node_id or len(node_id) > 100:
            return ActionResult(False, "node_id is required")
        literal = json.dumps(node_id)
        script = f"""
(() => {{
  const el = document.querySelector('[data-ordax-node=' + CSS.escape({literal}) + ']');
  if (!el) return {{ok:false,error:"node not found; take a new snapshot"}};
  el.scrollIntoView({{block:"center",inline:"center"}});
  el.click();
  return {{ok:true,url:location.href}};
}})()
"""
        try:
            data = self._with_page(project, session_id, lambda ws, _state, _target: self._eval(ws, 1, script))
        except (ValueError, BrowserCaptureError) as error:
            return ActionResult(False, f"browser click failed: {error}")
        if not isinstance(data, dict) or not data.get("ok"):
            return ActionResult(False, str((data or {}).get("error") or "browser node click failed"))
        return ActionResult(True, "browser node clicked", {"session_id": session_id, **data})

    def browser_type(self, payload: dict[str, Any]) -> ActionResult:
        project = self._project(payload)
        session_id = str(payload.get("session_id") or "")
        node_id = str(payload.get("node_id") or "").strip()
        text = str(payload.get("text") or "")
        if not node_id:
            return ActionResult(False, "node_id is required")
        if len(text) > 10000:
            return ActionResult(False, "browser type text is too large")
        node_literal = json.dumps(node_id)
        text_literal = json.dumps(text)
        clear = "true" if bool(payload.get("clear", True)) else "false"
        script = f"""
(() => {{
  const el = document.querySelector('[data-ordax-node=' + CSS.escape({node_literal}) + ']');
  if (!el) return {{ok:false,error:"node not found; take a new snapshot"}};
  el.focus();
  if ({clear} && ("value" in el)) el.value = "";
  if ("value" in el) {{
    const setter = Object.getOwnPropertyDescriptor(Object.getPrototypeOf(el), "value")?.set;
    if (setter) setter.call(el, ({clear} ? "" : el.value) + {text_literal});
    else el.value = ({clear} ? "" : el.value) + {text_literal};
    el.dispatchEvent(new Event("input", {{bubbles:true}}));
    el.dispatchEvent(new Event("change", {{bubbles:true}}));
  }} else if (el.isContentEditable) {{
    if ({clear}) el.textContent = "";
    el.textContent += {text_literal};
    el.dispatchEvent(new InputEvent("input", {{bubbles:true,inputType:"insertText",data:{text_literal}}}));
  }} else return {{ok:false,error:"node is not text-editable"}};
  return {{ok:true,value:("value" in el)?el.value:el.textContent}};
}})()
"""
        try:
            data = self._with_page(project, session_id, lambda ws, _state, _target: self._eval(ws, 1, script))
        except (ValueError, BrowserCaptureError) as error:
            return ActionResult(False, f"browser type failed: {error}")
        if not isinstance(data, dict) or not data.get("ok"):
            return ActionResult(False, str((data or {}).get("error") or "browser type failed"))
        return ActionResult(True, "browser text entered", {"session_id": session_id, **data})

    def browser_screenshot(self, payload: dict[str, Any]) -> ActionResult:
        project = self._project(payload)
        session_id = str(payload.get("session_id") or "")
        width = max(320, min(int(payload.get("width", 1440)), 2560))
        height = max(240, min(int(payload.get("height", 900)), 1600))
        output = (self.config.state_dir / "artifacts" / project.slug / f"browser-{session_id}.png").resolve()
        output.parent.mkdir(parents=True, exist_ok=True)

        def run(ws, _state, _target):
            _cdp_call(ws, 1, "Emulation.setDeviceMetricsOverride", {
                "width": width, "height": height, "deviceScaleFactor": 1, "mobile": False,
            })
            shot = _cdp_call(ws, 2, "Page.captureScreenshot", {
                "format": "png", "captureBeyondViewport": False,
            })
            data = base64.b64decode(str(shot.get("data") or ""))
            if not data.startswith(b"\x89PNG\r\n\x1a\n"):
                raise BrowserCaptureError("Chromium returned an invalid PNG")
            output.write_bytes(data)
            return {
                "session_id": session_id,
                "artifact_name": output.name,
                "image_path": str(output),
                "width": width,
                "height": height,
                "size_bytes": len(data),
            }

        try:
            data = self._with_page(project, session_id, run)
        except (ValueError, BrowserCaptureError) as error:
            return ActionResult(False, f"browser screenshot failed: {error}")
        return ActionResult(True, "browser screenshot captured", data)

    def browser_stop(self, payload: dict[str, Any]) -> ActionResult:
        project = self._project(payload)
        session_id = str(payload.get("session_id") or "")
        state = self._refresh_browser_pid(project, self._load_browser_state(project, session_id))
        pid = int(state.get("pid") or 0)
        if not self._pid_running(pid):
            return ActionResult(True, "browser session is already stopped", {**state, "running": False})
        if not self._browser_owned(project, state):
            return ActionResult(False, "refusing to stop a browser process not owned by ORDAX", state)

        try:
            if os.name == "nt":
                deadline = time.monotonic() + 12.0
                attempted: set[int] = set()
                while time.monotonic() < deadline:
                    candidates = [pid]
                    discovered = self._discover_browser_pid(project, state)
                    if discovered is not None:
                        candidates.append(discovered)
                    candidates = [item for item in candidates if item > 0 and item not in attempted]
                    if not candidates:
                        if self._discover_browser_pid(project, state) is None:
                            break
                        time.sleep(0.05)
                        continue
                    for candidate in candidates:
                        attempted.add(candidate)
                        subprocess.run(
                            ["taskkill", "/PID", str(candidate), "/T", "/F"],
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                            timeout=15,
                            shell=False,
                        )
                    time.sleep(0.1)
            else:
                try:
                    os.killpg(pid, 15)
                except ProcessLookupError:
                    pass
        except (OSError, subprocess.TimeoutExpired) as error:
            return ActionResult(False, f"browser stop failed: {error}", state)

        handle = getattr(self, "_browser_process_handles", {}).pop(session_id, None)
        if handle is not None:
            try:
                handle.wait(timeout=5)
            except subprocess.TimeoutExpired:
                try:
                    handle.kill()
                    handle.wait(timeout=5)
                except (OSError, subprocess.TimeoutExpired):
                    pass

        deadline = time.monotonic() + 8.0
        while time.monotonic() < deadline:
            owned_pid = self._discover_browser_pid(project, state)
            original_running = self._pid_running(pid)
            if owned_pid is None and not original_running:
                break
            if os.name == "nt" and owned_pid is not None:
                try:
                    subprocess.run(
                        ["taskkill", "/PID", str(owned_pid), "/T", "/F"],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        timeout=10,
                        shell=False,
                    )
                except (OSError, subprocess.TimeoutExpired):
                    pass
            time.sleep(0.1)

        owned_pid = self._discover_browser_pid(project, state)
        stopped = owned_pid is None and not self._pid_running(pid)
        state.update({
            "stopped_at_unix": time.time(),
            "running": not stopped,
            "ownership_valid": False if stopped else self._browser_owned(project, state),
        })
        self._write_browser_state(project, state)
        return ActionResult(stopped, "browser session stopped", state)
