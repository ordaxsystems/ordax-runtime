"""Browser-side Three.js runtime validation for verified Web GLB derivatives."""
from __future__ import annotations

import html
import json
import mimetypes
import os
import re
import shutil
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

from .game_asset_engine_export_actions import _inspect_glb, verify_engine_export
from .models import ActionResult
from .process_runner import run_command as _run


_DEFAULT_TIMEOUT = 120
_RESULT_RE = re.compile(r"ORDAX_THREE_RUNTIME_OK\|([^<]+)")
_ERROR_RE = re.compile(r"ORDAX_THREE_RUNTIME_ERROR\|([^<]+)")


def _timeout(value: Any) -> int:
    if value is None:
        return _DEFAULT_TIMEOUT
    if isinstance(value, bool) or not isinstance(value, int) or value < 15 or value > 600:
        raise ValueError("timeout_seconds must be an integer between 15 and 600")
    return value


def _minimum_int(value: Any, field: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 1 or value > 100000000:
        raise ValueError(f"{field} must be an integer between 1 and 100000000")
    return value


def _minimum_ratio(value: Any) -> float:
    if value is None:
        return 0.001
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("min_visible_pixel_ratio must be numeric")
    result = float(value)
    if result < 0.0 or result > 1.0:
        raise ValueError("min_visible_pixel_ratio must be between 0 and 1")
    return result


def _find_chrome() -> str:
    configured = os.environ.get("ORDAX_CHROME_BIN")
    if configured:
        path = Path(configured).expanduser()
        if not path.is_file():
            raise FileNotFoundError("ORDAX_CHROME_BIN does not point to a file")
        return str(path)
    for name in (
        "google-chrome",
        "google-chrome-stable",
        "chromium",
        "chromium-browser",
        "chrome",
        "chrome.exe",
        "msedge",
        "msedge.exe",
    ):
        resolved = shutil.which(name)
        if resolved:
            return resolved
    raise FileNotFoundError(
        "Chrome/Chromium binary not found; configure ORDAX_CHROME_BIN or add Chrome/Chromium to PATH"
    )


def _find_three_root(web_root: Path, project_root: Path) -> tuple[Path, str | None]:
    current = web_root.resolve()
    boundary = project_root.resolve()
    try:
        current.relative_to(boundary)
    except ValueError as error:
        raise ValueError("web_project_dir must stay inside the registered project") from error

    while True:
        candidate = current / "node_modules" / "three"
        module = candidate / "build" / "three.module.js"
        loader = candidate / "examples" / "jsm" / "loaders" / "GLTFLoader.js"
        if module.is_file() and loader.is_file():
            version: str | None = None
            package_json = candidate / "package.json"
            if package_json.is_file():
                try:
                    data = json.loads(package_json.read_text(encoding="utf-8"))
                    raw = data.get("version") if isinstance(data, dict) else None
                    if isinstance(raw, str) and raw:
                        version = raw
                except (OSError, ValueError):
                    pass
            return candidate, version
        if current == boundary:
            break
        parent = current.parent
        if parent == current:
            break
        current = parent
    raise FileNotFoundError(
        "Three.js package not found under project-local node_modules; install three in the Web project"
    )


def _page() -> bytes:
    source = r'''<!doctype html>
<meta charset="utf-8">
<meta http-equiv="Content-Security-Policy" content="default-src 'self'; script-src 'self' 'nonce-ordax'; connect-src 'self'; img-src 'self' blob: data:; style-src 'unsafe-inline'; worker-src 'self' blob:; object-src 'none'; base-uri 'none'">
<title>OrdaX Three Runtime Gate</title>
<body><pre id="ordax-result">ORDAX_THREE_RUNTIME_PENDING</pre>
<script type="importmap" nonce="ordax">
{"imports":{"three":"/three/build/three.module.js","three/addons/":"/three/examples/jsm/"}}
</script>
<script type="module" nonce="ordax">
import * as THREE from 'three';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';

const output = document.getElementById('ordax-result');
const fail = (error) => {
  const message = error && error.stack ? String(error.stack) : String(error);
  output.textContent = 'ORDAX_THREE_RUNTIME_ERROR|' + JSON.stringify({message});
  document.title = 'ORDAX_THREE_RUNTIME_ERROR';
};

try {
  const loader = new GLTFLoader();
  const gltf = await loader.loadAsync('/asset.glb');
  if (!gltf || !gltf.scene) throw new Error('GLTFLoader returned no scene');

  const stats = {
    three_revision: String(THREE.REVISION),
    meshes: 0,
    skinned_meshes: 0,
    bones: 0,
    vertices: 0,
    triangles: 0,
    materials: 0,
    textures: 0,
    animations: Array.isArray(gltf.animations) ? gltf.animations.length : 0,
  };
  const materialIds = new Set();
  const textureIds = new Set();

  gltf.scene.traverse((object) => {
    if (object.isBone) stats.bones += 1;
    if (!object.isMesh) return;
    stats.meshes += 1;
    if (object.isSkinnedMesh) stats.skinned_meshes += 1;
    const geometry = object.geometry;
    if (geometry && geometry.attributes && geometry.attributes.position) {
      stats.vertices += geometry.attributes.position.count;
      stats.triangles += geometry.index ? geometry.index.count / 3 : geometry.attributes.position.count / 3;
    }
    const materials = Array.isArray(object.material) ? object.material : [object.material];
    for (const material of materials) {
      if (!material) continue;
      materialIds.add(material.uuid || String(material.id));
      for (const value of Object.values(material)) {
        if (value && value.isTexture) textureIds.add(value.uuid || String(value.id));
      }
    }
  });
  stats.materials = materialIds.size;
  stats.textures = textureIds.size;
  stats.triangles = Math.round(stats.triangles);

  gltf.scene.updateMatrixWorld(true);
  const box = new THREE.Box3().setFromObject(gltf.scene);
  if (box.isEmpty()) throw new Error('Three.js scene has an empty world-space bounding box');
  const size = box.getSize(new THREE.Vector3());
  const center = box.getCenter(new THREE.Vector3());
  const radius = Math.max(size.length() * 0.5, 0.01);
  stats.bounds = {x: size.x, y: size.y, z: size.z, radius};

  const scene = new THREE.Scene();
  scene.add(gltf.scene);
  scene.add(new THREE.HemisphereLight(0xffffff, 0x445566, 2.0));
  const key = new THREE.DirectionalLight(0xffffff, 3.0);
  key.position.set(center.x + radius, center.y + radius * 1.5, center.z + radius);
  scene.add(key);

  const camera = new THREE.PerspectiveCamera(35, 1, Math.max(radius / 1000, 0.001), radius * 1000 + 10);
  const direction = new THREE.Vector3(1.0, 0.7, 1.0).normalize();
  const distance = radius / Math.sin(THREE.MathUtils.degToRad(camera.fov * 0.5)) * 1.25;
  camera.position.copy(center).addScaledVector(direction, distance);
  camera.lookAt(center);
  camera.updateProjectionMatrix();

  const canvas = document.createElement('canvas');
  const renderer = new THREE.WebGLRenderer({canvas, antialias: false, alpha: false, preserveDrawingBuffer: true});
  renderer.setPixelRatio(1);
  renderer.setSize(256, 256, false);
  renderer.setClearColor(0x102030, 1);
  if ('outputColorSpace' in renderer && THREE.SRGBColorSpace) renderer.outputColorSpace = THREE.SRGBColorSpace;

  const target = new THREE.WebGLRenderTarget(256, 256, {depthBuffer: true});
  renderer.setRenderTarget(target);
  renderer.clear(true, true, true);
  renderer.render(scene, camera);
  const pixels = new Uint8Array(256 * 256 * 4);
  renderer.readRenderTargetPixels(target, 0, 0, 256, 256, pixels);

  const bg = [pixels[0], pixels[1], pixels[2], pixels[3]];
  let visible = 0;
  let minLuma = 255;
  let maxLuma = 0;
  for (let index = 0; index < pixels.length; index += 4) {
    const delta = Math.abs(pixels[index] - bg[0]) + Math.abs(pixels[index + 1] - bg[1]) + Math.abs(pixels[index + 2] - bg[2]);
    if (delta > 8) visible += 1;
    const luma = Math.round(0.2126 * pixels[index] + 0.7152 * pixels[index + 1] + 0.0722 * pixels[index + 2]);
    minLuma = Math.min(minLuma, luma);
    maxLuma = Math.max(maxLuma, luma);
  }
  stats.visible_pixels = visible;
  stats.visible_pixel_ratio = visible / (256 * 256);
  stats.luminance_range = maxLuma - minLuma;
  stats.render_calls = renderer.info.render.calls;
  stats.rendered_triangles = renderer.info.render.triangles;
  stats.webgl2 = Boolean(renderer.capabilities.isWebGL2);

  target.dispose();
  renderer.dispose();
  output.textContent = 'ORDAX_THREE_RUNTIME_OK|' + JSON.stringify(stats);
  document.title = 'ORDAX_THREE_RUNTIME_OK';
} catch (error) {
  fail(error);
}
</script>
'''
    return source.encode("utf-8")


class _Server:
    def __init__(self, *, artifact: Path, three_root: Path):
        self.artifact = artifact.resolve()
        self.three_root = three_root.resolve()
        self.page = _page()
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format, *args):  # noqa: A003
                return

            def do_GET(self):  # noqa: N802
                path = unquote(urlsplit(self.path).path)
                try:
                    if path in {"/", "/index.html"}:
                        return self._send(owner.page, "text/html; charset=utf-8")
                    if path == "/asset.glb":
                        return self._send(owner.artifact.read_bytes(), "model/gltf-binary")
                    if path.startswith("/three/"):
                        relative = path[len("/three/") :]
                        candidate = (owner.three_root / relative).resolve()
                        try:
                            candidate.relative_to(owner.three_root)
                        except ValueError:
                            return self.send_error(403)
                        if not candidate.is_file():
                            return self.send_error(404)
                        mime = mimetypes.guess_type(str(candidate))[0] or "application/octet-stream"
                        if candidate.suffix.lower() == ".js":
                            mime = "text/javascript; charset=utf-8"
                        return self._send(candidate.read_bytes(), mime)
                    return self.send_error(404)
                except OSError:
                    return self.send_error(500)

            def _send(self, body: bytes, content_type: str):
                self.send_response(200)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
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


def _runtime_proof(dom: str) -> tuple[dict[str, Any] | None, str | None]:
    match = _RESULT_RE.search(dom)
    if match:
        try:
            data = json.loads(html.unescape(match.group(1)))
        except json.JSONDecodeError:
            return None, "Three.js runtime proof contained invalid JSON"
        if not isinstance(data, dict):
            return None, "Three.js runtime proof must be a JSON object"
        required_ints = {
            "meshes",
            "skinned_meshes",
            "bones",
            "vertices",
            "triangles",
            "materials",
            "textures",
            "animations",
            "visible_pixels",
            "luminance_range",
            "render_calls",
            "rendered_triangles",
        }
        if any(isinstance(data.get(key), bool) or not isinstance(data.get(key), int) or data[key] < 0 for key in required_ints):
            return None, "Three.js runtime proof contains invalid numeric metrics"
        ratio = data.get("visible_pixel_ratio")
        if isinstance(ratio, bool) or not isinstance(ratio, (int, float)) or ratio < 0 or ratio > 1:
            return None, "Three.js runtime proof contains an invalid visible-pixel ratio"
        return data, None
    error = _ERROR_RE.search(dom)
    if error:
        try:
            data = json.loads(html.unescape(error.group(1)))
            message = data.get("message") if isinstance(data, dict) else None
        except json.JSONDecodeError:
            message = None
        return None, str(message or "Three.js runtime page reported an unknown error")
    return None, "browser exited without OrdaX Three.js runtime proof"


def _requirement_failures(
    stats: dict[str, Any],
    *,
    min_meshes: int | None,
    min_triangles: int | None,
    min_animations: int | None,
    require_skinned_mesh: bool,
    min_visible_ratio: float,
) -> list[str]:
    failures: list[str] = []
    if min_meshes is not None and stats["meshes"] < min_meshes:
        failures.append(f"Three.js scene has fewer than {min_meshes} meshes")
    if min_triangles is not None and stats["triangles"] < min_triangles:
        failures.append(f"Three.js scene has fewer than {min_triangles} triangles")
    if min_animations is not None and stats["animations"] < min_animations:
        failures.append(f"Three.js asset has fewer than {min_animations} animations")
    if require_skinned_mesh and stats["skinned_meshes"] < 1:
        failures.append("Three.js scene has no SkinnedMesh")
    if float(stats["visible_pixel_ratio"]) < min_visible_ratio:
        failures.append(
            f"Three.js render visible-pixel ratio is below {min_visible_ratio:.6f}"
        )
    if stats["render_calls"] < 1:
        failures.append("Three.js renderer produced zero draw calls")
    return failures


class GameAssetWebRuntimeActions:
    """Load and render a verified GLB in a real local Three.js browser runtime."""

    def game_assets_web_runtime_validate(self, payload: dict[str, Any]) -> ActionResult:
        supported = {
            "project",
            "artifact_path",
            "manifest_path",
            "web_project_dir",
            "timeout_seconds",
            "min_meshes",
            "min_triangles",
            "min_animations",
            "require_skinned_mesh",
            "min_visible_pixel_ratio",
        }
        unsupported = set(payload) - supported
        if unsupported:
            return ActionResult(False, f"unsupported fields: {', '.join(sorted(unsupported))}")

        project = self._project(payload)
        try:
            artifact = project.path(str(payload.get("artifact_path") or ""))
            if artifact.suffix.lower() != ".glb":
                raise ValueError("Three.js runtime validation requires a .glb artifact")
            raw_manifest = payload.get("manifest_path")
            manifest = (
                project.path(str(raw_manifest))
                if raw_manifest is not None
                else artifact.with_name(artifact.name + ".ordax.json")
            )
            verification = verify_engine_export(project, artifact, manifest)
            if str(verification.get("engine") or "").strip().lower() != "web":
                raise ValueError("Three.js runtime validation requires provenance targeted to web")
            glb = _inspect_glb(artifact)
            if not glb["self_contained"]:
                raise ValueError("Three.js runtime validation requires a self-contained GLB")

            web_dir_raw = payload.get("web_project_dir")
            if web_dir_raw is None:
                web_root = project.root
            elif not isinstance(web_dir_raw, str) or not web_dir_raw.strip():
                raise ValueError("web_project_dir must be a non-empty project-local directory")
            else:
                web_root = project.path(web_dir_raw.strip())
            if not web_root.is_dir():
                raise ValueError("web_project_dir must be an existing directory")
            three_root, three_version = _find_three_root(web_root, project.root)
            chrome = _find_chrome()
            timeout = _timeout(payload.get("timeout_seconds"))
            min_meshes = _minimum_int(payload.get("min_meshes"), "min_meshes")
            min_triangles = _minimum_int(payload.get("min_triangles"), "min_triangles")
            min_animations = _minimum_int(payload.get("min_animations"), "min_animations")
            min_visible_ratio = _minimum_ratio(payload.get("min_visible_pixel_ratio"))
            require_skinned_mesh = bool(payload.get("require_skinned_mesh", False))

            self.config.state_dir.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(
                prefix="ordax-three-runtime-", dir=self.config.state_dir
            ) as profile_raw, _Server(artifact=artifact, three_root=three_root) as server:
                profile = Path(profile_raw)
                command = [
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
                    f"--user-data-dir={profile}",
                    "--window-size=512,512",
                    f"--timeout={timeout * 1000}",
                    "--dump-dom",
                    server.url,
                ]
                browser = _run(command, cwd=web_root, timeout=timeout + 15)
            if not browser.ok:
                return ActionResult(
                    False,
                    "Chrome/Chromium failed during Three.js runtime validation",
                    {
                        "artifact_path": str(artifact),
                        "web_project_dir": str(web_root),
                        "source_preserved": True,
                        "browser": browser.data,
                        "retryable": True,
                    },
                )
            stats, proof_error = _runtime_proof(str(browser.data.get("stdout") or ""))
            if stats is None:
                return ActionResult(
                    False,
                    proof_error or "Three.js runtime validation returned no proof",
                    {
                        "artifact_path": str(artifact),
                        "web_project_dir": str(web_root),
                        "three_package_root": str(three_root),
                        "source_preserved": True,
                    },
                )
            failures = _requirement_failures(
                stats,
                min_meshes=min_meshes,
                min_triangles=min_triangles,
                min_animations=min_animations,
                require_skinned_mesh=require_skinned_mesh,
                min_visible_ratio=min_visible_ratio,
            )
        except (ValueError, OSError, FileNotFoundError) as error:
            return ActionResult(False, str(error))

        data = {
            "engine": "web",
            "runtime": "three.js",
            "validated_in_browser": True,
            "render_validated": True,
            "semantic_requirements_passed": not failures,
            "artifact_path": str(artifact),
            "sha256": verification["sha256"],
            "source_current_matches": verification["source_current_matches"],
            "source_preserved": True,
            "three_package_root": str(three_root),
            "three_package_version": three_version,
            "browser_binary_source": "ORDAX_CHROME_BIN_or_PATH",
            "network_scope": "loopback_only_page_resources",
            "glb": glb,
            "runtime_stats": stats,
            "requirements": {
                "min_meshes": min_meshes,
                "min_triangles": min_triangles,
                "min_animations": min_animations,
                "require_skinned_mesh": require_skinned_mesh,
                "min_visible_pixel_ratio": min_visible_ratio,
            },
            "failures": failures,
        }
        if failures:
            return ActionResult(False, "Three.js loaded and rendered the GLB but runtime requirements failed", data)
        return ActionResult(True, "Three.js loaded and rendered the verified GLB in headless Chrome", data)
