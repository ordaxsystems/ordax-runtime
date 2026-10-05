"""Chromium DevTools Protocol capture provider for ORDAX previews."""
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


class BrowserCaptureError(RuntimeError):
    """Raised when a deterministic browser capture cannot be produced."""


def find_chromium() -> Path | None:
    candidates = [shutil.which("chrome"), shutil.which("msedge")]
    candidates += [
        str(Path.home() / "AppData/Local/Google/Chrome/Application/chrome.exe"),
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    ]
    for raw in candidates:
        if raw and Path(raw).is_file():
            return Path(raw).resolve()
    return None


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _json_get(url: str, timeout: float = 1.0) -> Any:
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return json.loads(response.read())


def _same_origin(left: str, right: str) -> bool:
    a = urllib.parse.urlsplit(left)
    b = urllib.parse.urlsplit(right)
    return (a.scheme, a.hostname, a.port) == (b.scheme, b.hostname, b.port)


def _page_target(port: int, target_url: str, deadline: float) -> dict[str, Any]:
    endpoint = f"http://127.0.0.1:{port}/json/list"
    while time.monotonic() < deadline:
        try:
            items = _json_get(endpoint)
            for item in items if isinstance(items, list) else []:
                if item.get("type") == "page" and item.get("webSocketDebuggerUrl"):
                    current = str(item.get("url") or "")
                    if current and _same_origin(current, target_url):
                        return item
        except Exception:
            pass
        time.sleep(0.1)
    raise BrowserCaptureError("Chromium page target did not become ready")

def _cdp_call(ws, request_id: int, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    ws.send(json.dumps({"id": request_id, "method": method, "params": params or {}}))
    while True:
        try:
            raw = ws.recv(timeout=15)
        except TimeoutError as error:
            raise BrowserCaptureError(f"CDP {method} timed out") from error
        message = json.loads(raw)
        if message.get("id") == request_id:
            if "error" in message:
                raise BrowserCaptureError(f"CDP {method} failed: {message['error']}")
            return message.get("result") or {}


def _stop_tree(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    try:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                capture_output=True, text=True, shell=False, timeout=10,
            )
        else:
            process.kill()
    except Exception:
        pass
    try:
        process.wait(timeout=5)
    except Exception:
        pass



def _stop_profile_processes(profile: Path) -> None:
    if os.name != "nt":
        return
    raw = str(profile).replace("'", "''")
    script = (
        f"$p='{raw}'; Get-CimInstance Win32_Process | "
        "Where-Object { ($_.Name -eq 'chrome.exe' -or $_.Name -eq 'msedge.exe') "
        "-and $_.CommandLine -like ('*' + $p + '*') } | "
        "ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"
    )
    try:
        subprocess.run(["powershell.exe", "-NoProfile", "-Command", script],
                       capture_output=True, text=True, shell=False, timeout=8)
    except Exception:
        pass


def capture_url(browser: Path, url: str, output: Path, profile_root: Path, *, width: int,
                height: int, timeout_seconds: float) -> dict[str, Any]:
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise BrowserCaptureError("preview capture requires an http/https URL")
    debug_port = _free_port()
    profile = (profile_root / f"capture-{uuid.uuid4().hex}").resolve()
    profile.mkdir(parents=True, exist_ok=True)
    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    command = [
        str(browser), "--headless=new", "--disable-gpu", "--no-first-run",
        "--no-default-browser-check", "--disable-extensions",
        "--disable-background-networking", f"--user-data-dir={profile}",
        f"--remote-debugging-port={debug_port}", url,
    ]
    process: subprocess.Popen | None = None
    deadline = time.monotonic() + max(5.0, timeout_seconds)
    try:
        process = subprocess.Popen(
            command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, shell=False,
            creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
        )
        target = _page_target(debug_port, url, deadline)
        with connect(str(target["webSocketDebuggerUrl"]), open_timeout=3, close_timeout=1) as ws:
            request_id = 1
            _cdp_call(ws, request_id, "Page.enable")
            request_id += 1
            _cdp_call(ws, request_id, "Emulation.setDeviceMetricsOverride", {
                "width": int(width), "height": int(height),
                "deviceScaleFactor": 1, "mobile": False,
            })
            request_id += 1
            while time.monotonic() < deadline:
                ready = _cdp_call(ws, request_id, "Runtime.evaluate", {
                    "expression": "document.readyState", "returnByValue": True,
                })
                request_id += 1
                if ((ready.get("result") or {}).get("value")) in {"interactive", "complete"}:
                    break
                time.sleep(0.1)
            else:
                raise BrowserCaptureError("preview document did not become ready")
            _cdp_call(ws, request_id, "Page.bringToFront")
            request_id += 1
            time.sleep(0.3)
            shot = _cdp_call(ws, request_id, "Page.captureScreenshot", {
                "format": "png", "captureBeyondViewport": False,
            })
            data = base64.b64decode(str(shot.get("data") or ""))
            if not data.startswith(b"\x89PNG\r\n\x1a\n"):
                raise BrowserCaptureError("Chromium returned an invalid PNG")
            output.write_bytes(data)
            return {
                "artifact": str(output), "browser": str(browser),
                "provider": "chromium-cdp", "width": int(width),
                "height": int(height), "size_bytes": len(data), "url": url,
            }
    except BrowserCaptureError:
        raise
    except Exception as error:
        raise BrowserCaptureError(f"Chromium CDP capture failed: {error}") from error
    finally:
        if process is not None:
            _stop_tree(process)
        _stop_profile_processes(profile)
        deadline = time.monotonic() + 3.0
        while profile.exists() and time.monotonic() < deadline:
            shutil.rmtree(profile, ignore_errors=True)
            if profile.exists():
                time.sleep(0.1)
