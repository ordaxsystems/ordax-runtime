"""Build and browser-validate the complete prepared OrdaX Three.js viewer."""
from __future__ import annotations

import hashlib
import html
import json
import mimetypes
import os
import re
import shutil
import struct
import tempfile
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

from .game_asset_threejs_actions import THREE_VERSION, THREEJS_VIEWER_SCHEMA, VITE_VERSION
from .game_asset_web_runtime_actions import _find_chrome, _timeout
from .models import ActionResult
from .process_runner import run_command as _run


_HUD_RE = re.compile(
    r"backend:\s*(WebGPU|WebGL2 fallback).*?environment:\s*([^\n<]+).*?model:\s*([^\n<]+).*?triangles:\s*(\d+).*?calls:\s*(\d+)",
    re.DOTALL,
)
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_SCREENSHOT_WIDTH = 1280
_SCREENSHOT_HEIGHT = 720


def _minimum(value: Any, field: str, *, default: int) -> int:
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int) or value < 0 or value > 100000000:
        raise ValueError(f"{field} must be an integer between 0 and 100000000")
    return value


def _find_npm() -> str:
    configured = os.environ.get("ORDAX_NPM_BIN")
    if configured:
        path = Path(configured).expanduser()
        if not path.is_file():
            raise FileNotFoundError("ORDAX_NPM_BIN does not point to a file")
        return str(path)
    for name in ("npm.cmd", "npm"):
        resolved = shutil.which(name)
        if resolved:
            return resolved
    raise FileNotFoundError("npm not found; configure ORDAX_NPM_BIN or add npm to PATH")


def _package(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ValueError(f"invalid JSON package file: {path.name}") from error
    if not isinstance(data, dict):
        raise ValueError(f"invalid package object: {path.name}")
    return data


def _validate_installed_dependencies(viewer_root: Path) -> dict[str, str]:
    package = _package(viewer_root / "package.json")
    if package.get("scripts", {}).get("build") != "vite build":
        raise ValueError("viewer build script must remain exactly 'vite build'")
    if package.get("dependencies", {}).get("three") != THREE_VERSION:
        raise ValueError("viewer package does not use the pinned Three.js version")
    if package.get("devDependencies", {}).get("vite") != VITE_VERSION:
        raise ValueError("viewer package does not use the pinned Vite version")

    three_package = viewer_root / "node_modules" / "three" / "package.json"
    vite_package = viewer_root / "node_modules" / "vite" / "package.json"
    if not three_package.is_file() or not vite_package.is_file():
        raise FileNotFoundError(
            "viewer dependencies are not installed; run npm install explicitly before runtime validation"
        )
    installed_three = _package(three_package).get("version")
    installed_vite = _package(vite_package).get("version")
    if installed_three != THREE_VERSION:
        raise ValueError(
            f"installed Three.js version must be {THREE_VERSION}, got {installed_three or 'missing'}"
        )
    if installed_vite != VITE_VERSION:
        raise ValueError(
            f"installed Vite version must be {VITE_VERSION}, got {installed_vite or 'missing'}"
        )
    return {"three": str(installed_three), "vite": str(installed_vite)}


def _screenshot_evidence(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise ValueError("Chrome reported screenshot success but produced no PNG evidence")
    raw = path.read_bytes()
    if len(raw) < 24 or not raw.startswith(_PNG_SIGNATURE) or raw[12:16] != b"IHDR":
        raise ValueError("browser screenshot evidence is not a valid PNG header")
    width, height = struct.unpack(">II", raw[16:24])
    if width != _SCREENSHOT_WIDTH or height != _SCREENSHOT_HEIGHT:
        raise ValueError(
            f"browser screenshot dimensions must be {_SCREENSHOT_WIDTH}x{_SCREENSHOT_HEIGHT}, got {width}x{height}"
        )
    digest = hashlib.sha256(raw).hexdigest()
    return {
        "path": str(path),
        "sha256": digest,
        "bytes": len(raw),
        "width": width,
        "height": height,
        "format": "png",
    }


class _DistServer:
    def __init__(self, root: Path):
        self.root = root.resolve()
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format, *args):  # noqa: A003
                return

            def do_GET(self):  # noqa: N802
                raw_path = unquote(urlsplit(self.path).path)
                relative = raw_path.lstrip("/") or "index.html"
                candidate = (owner.root / relative).resolve()
                try:
                    candidate.relative_to(owner.root)
                except ValueError:
                    return self.send_error(403)
                if candidate.is_dir():
                    candidate = candidate / "index.html"
                if not candidate.is_file():
                    return self.send_error(404)
                try:
                    body = candidate.read_bytes()
                except OSError:
                    return self.send_error(500)
                content_type = mimetypes.guess_type(str(candidate))[0] or "application/octet-stream"
                if candidate.suffix.lower() == ".js":
                    content_type = "text/javascript; charset=utf-8"
                self.send_response(200)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header(
                    "Content-Security-Policy",
                    "default-src 'self'; script-src 'self'; connect-src 'self'; "
                    "img-src 'self' blob: data:; style-src 'self' 'unsafe-inline'; "
                    "worker-src 'self' blob:; object-src 'none'; base-uri 'none'",
                )
                self.end_headers()
                self.wfile.write(body)

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.httpd.server_port}/"

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, exc_type, exc, tb):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=5)


def _hud_proof(dom: str) -> dict[str, Any] | None:
    decoded = html.unescape(dom)
    match = _HUD_RE.search(decoded)
    if not match:
        return None
    return {
        "backend": match.group(1),
        "environment": match.group(2).strip(),
        "model": match.group(3).strip(),
        "triangles": int(match.group(4)),
        "calls": int(match.group(5)),
    }


class GameAssetThreeJsViewerRuntimeActions:
    """Build the pinned Vite viewer and prove its whole visual stack renders."""

    def game_assets_threejs_viewer_validate(self, payload: dict[str, Any]) -> ActionResult:
        supported = {
            "project",
            "viewer_dir",
            "timeout_seconds",
            "min_triangles",
            "min_draw_calls",
            "require_webgpu",
        }
        unsupported = set(payload) - supported
        if unsupported:
            return ActionResult(False, f"unsupported fields: {', '.join(sorted(unsupported))}")

        project = self._project(payload)
        try:
            viewer_raw = payload.get("viewer_dir") or "ordax/threejs-viewer"
            if not isinstance(viewer_raw, str) or not viewer_raw.strip():
                raise ValueError("viewer_dir must be a non-empty project-relative directory")
            viewer_root = project.path(viewer_raw.strip())
            if not viewer_root.is_dir():
                raise ValueError("viewer_dir must be an existing directory")

            structural = self.game_assets_threejs_runtime_audit(
                {"project": project.slug, "viewer_dir": viewer_raw.strip()}
            )
            if not structural.ok:
                return structural

            installed = _validate_installed_dependencies(viewer_root)
            runtime = _package(viewer_root / "public" / "ordax" / "runtime.json")
            environment = _package(viewer_root / "public" / "ordax" / "environment.json")
            if runtime.get("schema") != THREEJS_VIEWER_SCHEMA:
                raise ValueError("runtime manifest has the wrong Three.js viewer schema")
            expected_hash = str(runtime.get("asset", {}).get("sha256") or "")
            if len(expected_hash) != 64:
                raise ValueError("runtime manifest asset SHA-256 is invalid")
            environment_name = str(environment.get("name") or "").strip()
            if not environment_name:
                raise ValueError("environment manifest has no name")

            npm = _find_npm()
            chrome = _find_chrome()
            timeout = _timeout(payload.get("timeout_seconds"))
            min_triangles = _minimum(payload.get("min_triangles"), "min_triangles", default=1)
            min_calls = _minimum(payload.get("min_draw_calls"), "min_draw_calls", default=1)
            require_webgpu = bool(payload.get("require_webgpu", False))

            built = _run(
                [npm, "run", "build"],
                cwd=viewer_root,
                timeout=timeout,
                env={"NO_COLOR": "1", "npm_config_audit": "false", "npm_config_fund": "false"},
            )
            if not built.ok:
                return ActionResult(
                    False,
                    "Three.js viewer build failed",
                    {
                        "viewer_dir": str(viewer_root),
                        "source_preserved": True,
                        "dependencies_installed": True,
                        "build": built.data,
                    },
                )
            dist = viewer_root / "dist"
            if not (dist / "index.html").is_file():
                return ActionResult(
                    False,
                    "Vite build completed without dist/index.html",
                    {"viewer_dir": str(viewer_root), "source_preserved": True},
                )
            model_copy = dist / "ordax" / "model.glb"
            if not model_copy.is_file():
                return ActionResult(
                    False,
                    "Vite build did not preserve the viewer GLB",
                    {"viewer_dir": str(viewer_root), "source_preserved": True},
                )

            evidence_dir = self.config.state_dir / "visual-evidence" / project.slug
            evidence_dir.mkdir(parents=True, exist_ok=True)
            evidence_path = evidence_dir / (
                f"threejs-viewer-{expected_hash[:12]}-{uuid.uuid4().hex}.png"
            )
            self.config.state_dir.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(
                prefix="ordax-threejs-viewer-", dir=self.config.state_dir
            ) as profile_raw, _DistServer(dist) as server:
                profile_root = Path(profile_raw)
                common = [
                    chrome,
                    "--headless=new",
                    "--disable-extensions",
                    "--disable-background-networking",
                    "--disable-component-update",
                    "--disable-default-apps",
                    "--disable-sync",
                    "--metrics-recording-only",
                    "--no-first-run",
                    "--no-default-browser-check",
                    f"--window-size={_SCREENSHOT_WIDTH},{_SCREENSHOT_HEIGHT}",
                    f"--timeout={timeout * 1000}",
                    "--virtual-time-budget=2500",
                ]
                browser = _run(
                    common
                    + [
                        f"--user-data-dir={profile_root / 'dom-profile'}",
                        "--dump-dom",
                        server.url,
                    ],
                    cwd=viewer_root,
                    timeout=timeout + 15,
                )
                screenshot = None
                if browser.ok:
                    screenshot = _run(
                        common
                        + [
                            f"--user-data-dir={profile_root / 'screenshot-profile'}",
                            "--hide-scrollbars",
                            f"--screenshot={evidence_path}",
                            server.url,
                        ],
                        cwd=viewer_root,
                        timeout=timeout + 15,
                    )
            if not browser.ok:
                return ActionResult(
                    False,
                    "Chrome/Chromium failed while running the full Three.js viewer",
                    {
                        "viewer_dir": str(viewer_root),
                        "dist_dir": str(dist),
                        "source_preserved": True,
                        "browser": browser.data,
                        "retryable": True,
                    },
                )
            if screenshot is None or not screenshot.ok:
                return ActionResult(
                    False,
                    "Three.js viewer rendered but screenshot evidence capture failed",
                    {
                        "viewer_dir": str(viewer_root),
                        "dist_dir": str(dist),
                        "evidence_path": str(evidence_path),
                        "source_preserved": True,
                        "screenshot": screenshot.data if screenshot is not None else None,
                        "retryable": True,
                    },
                )
            proof = _hud_proof(str(browser.data.get("stdout") or ""))
            if proof is None:
                return ActionResult(
                    False,
                    "Three.js viewer ran without a rendered HUD proof",
                    {
                        "viewer_dir": str(viewer_root),
                        "dist_dir": str(dist),
                        "evidence_path": str(evidence_path),
                        "source_preserved": True,
                        "retryable": True,
                    },
                )
            evidence = _screenshot_evidence(evidence_path)

            failures: list[str] = []
            if proof["environment"] != environment_name:
                failures.append("viewer HUD environment does not match environment manifest")
            if not proof["model"].startswith(expected_hash[:12]):
                failures.append("viewer HUD model hash does not match runtime provenance")
            if proof["triangles"] < min_triangles:
                failures.append(f"viewer rendered fewer than {min_triangles} triangles")
            if proof["calls"] < min_calls:
                failures.append(f"viewer produced fewer than {min_calls} draw calls")
            if require_webgpu and proof["backend"] != "WebGPU":
                failures.append("viewer used WebGL2 fallback while WebGPU was required")
        except (ValueError, OSError, FileNotFoundError, json.JSONDecodeError) as error:
            return ActionResult(False, str(error))

        data = {
            "engine": "web",
            "runtime": "three.js-webgpu-viewer",
            "viewer_dir": str(viewer_root),
            "dist_dir": str(dist),
            "viewer_build_validated": True,
            "viewer_browser_validated": True,
            "visual_stack_initialized": True,
            "visual_evidence_captured": True,
            "visual_evidence": evidence,
            "backend": proof["backend"],
            "render": proof,
            "installed_versions": installed,
            "network_scope": "loopback_only_built_viewer",
            "source_preserved": True,
            "requirements": {
                "min_triangles": min_triangles,
                "min_draw_calls": min_calls,
                "require_webgpu": require_webgpu,
            },
            "failures": failures,
        }
        if failures:
            return ActionResult(False, "Three.js viewer rendered but runtime requirements failed", data)
        return ActionResult(
            True,
            "Three.js WebGPU viewer built, rendered and captured as visual evidence",
            data,
        )
