"""Prepare and audit an OrdaX Three.js WebGPU visual-validation viewer."""
from __future__ import annotations

import hashlib
import json
import shutil
import uuid
from pathlib import Path
from typing import Any

from .game_asset_engine_export_actions import _inspect_glb, verify_engine_export
from .models import ActionResult
from .visual_environment import ENVIRONMENT_SCHEMA, normalize_environment

THREE_VERSION = "0.186.0"
VITE_VERSION = "8.3.0"
THREEJS_VIEWER_SCHEMA = "ordax.threejs-viewer/1"

OCEAN_RUNTIME = {
    "model": "gerstner-tsl-4band",
    "near_field_extent_m": 4096,
    "segments": 256,
    "foam": "crest-mask-overlay",
    "micro_normals": "procedural-four-sample",
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _atomic_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.ordax-{uuid.uuid4().hex}.tmp")
    try:
        temp.write_text(content, encoding="utf-8")
        temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)


def _atomic_copy(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temp = destination.with_name(f".{destination.name}.ordax-{uuid.uuid4().hex}.tmp")
    try:
        shutil.copy2(source, temp)
        temp.replace(destination)
    finally:
        temp.unlink(missing_ok=True)


def _package_json() -> str:
    payload = {
        "name": "ordax-threejs-visual-viewer",
        "private": True,
        "version": "0.2.0",
        "type": "module",
        "scripts": {
            "dev": "vite --host 127.0.0.1",
            "build": "vite build",
            "preview": "vite preview --host 127.0.0.1",
        },
        "dependencies": {"three": THREE_VERSION},
        "devDependencies": {"vite": VITE_VERSION},
    }
    return json.dumps(payload, indent=2, sort_keys=True) + "\n"


def _index_html() -> str:
    return """<!doctype html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>OrdaX Three.js Visual Viewer</title>
    <style>
      html, body, #app { width: 100%; height: 100%; margin: 0; overflow: hidden; background: #07131b; }
      canvas { display: block; }
      #hud { position: fixed; left: 12px; top: 12px; color: #e8f4f8; font: 12px/1.45 system-ui, sans-serif; background: rgb(0 0 0 / 45%); padding: 9px 11px; border-radius: 8px; pointer-events: none; white-space: pre; }
    </style>
  </head>
  <body>
    <div id="app"></div><div id="hud">OrdaX visual runtime</div>
    <script type="module" src="/src/main.js"></script>
  </body>
</html>
"""


def _main_js() -> str:
    return r"""import * as THREE from 'three/webgpu';
import {
  clamp,
  cos,
  dot,
  float,
  positionLocal,
  sin,
  time,
  vec2,
  vec3,
} from 'three/tsl';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { SkyMesh } from 'three/addons/objects/SkyMesh.js';
import { WaterMesh } from 'three/addons/objects/WaterMesh.js';

const app = document.querySelector('#app');
const hud = document.querySelector('#hud');
const diagnostics = {
  errors: 0,
  consoleErrors: 0,
  unhandledRejections: 0,
  lastError: '',
  frameTimes: [],
};

function rememberError(value) {
  diagnostics.errors += 1;
  diagnostics.lastError = String(value ?? 'unknown runtime error').slice(0, 240);
}

const originalConsoleError = console.error.bind(console);
console.error = (...args) => {
  diagnostics.consoleErrors += 1;
  rememberError(args.map((value) => String(value)).join(' '));
  originalConsoleError(...args);
};
addEventListener('error', (event) => {
  rememberError(event.error?.stack || event.message || 'window error');
});
addEventListener('unhandledrejection', (event) => {
  diagnostics.unhandledRejections += 1;
  rememberError(event.reason?.stack || event.reason || 'unhandled rejection');
});

const environment = await fetch('/ordax/environment.json', { cache: 'no-store' }).then((response) => {
  if (!response.ok) throw new Error(`environment manifest HTTP ${response.status}`);
  return response.json();
});
const runtime = await fetch('/ordax/runtime.json', { cache: 'no-store' }).then((response) => {
  if (!response.ok) throw new Error(`runtime manifest HTTP ${response.status}`);
  return response.json();
});

const renderer = new THREE.WebGPURenderer({ antialias: true });
renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
renderer.setSize(innerWidth, innerHeight);
renderer.shadowMap.enabled = true;
renderer.toneMapping = environment.exposure.tone_mapping === 'aces'
  ? THREE.ACESFilmicToneMapping
  : environment.exposure.tone_mapping === 'neutral'
    ? THREE.NeutralToneMapping
    : THREE.AgXToneMapping;
renderer.toneMappingExposure = Math.pow(2, (14.0 - environment.exposure.ev100) * 0.35);
app.appendChild(renderer.domElement);

const scene = new THREE.Scene();
const camera = new THREE.PerspectiveCamera(55, innerWidth / innerHeight, 0.05, 50000);
camera.position.set(14, 10, 18);
const controls = new OrbitControls(camera, renderer.domElement);
controls.enableDamping = true;

function sunDirection() {
  const elevation = THREE.MathUtils.degToRad(environment.sun.elevation_deg);
  const azimuth = THREE.MathUtils.degToRad(environment.sun.azimuth_deg);
  return new THREE.Vector3(
    Math.sin(azimuth) * Math.cos(elevation),
    Math.sin(elevation),
    Math.cos(azimuth) * Math.cos(elevation),
  ).normalize();
}

const sunDir = sunDirection();
const sky = new SkyMesh();
sky.scale.setScalar(45000);
sky.turbidity.value = environment.sky.turbidity;
sky.rayleigh.value = environment.sky.rayleigh;
sky.mieCoefficient.value = environment.sky.mie_coefficient;
sky.mieDirectionalG.value = environment.sky.mie_directional_g;
sky.sunPosition.value.copy(sunDir);
if (sky.cloudCoverage) sky.cloudCoverage.value = environment.sky.cloud_coverage;
if (sky.cloudDensity) sky.cloudDensity.value = environment.sky.cloud_density;
scene.add(sky);

const sunIntensity = THREE.MathUtils.clamp(environment.sun.intensity_lux / 50000, 0, 5);
const sun = new THREE.DirectionalLight(0xffffff, sunIntensity);
sun.position.copy(sunDir).multiplyScalar(8000);
sun.castShadow = true;
sun.shadow.mapSize.set(2048, 2048);
sun.shadow.camera.near = 1;
sun.shadow.camera.far = 20000;
sun.shadow.camera.left = -600;
sun.shadow.camera.right = 600;
sun.shadow.camera.top = 600;
sun.shadow.camera.bottom = -600;
scene.add(sun);
scene.add(new THREE.HemisphereLight(0xbfdfff, 0x5f6b5a, 0.65));

const fogDensity = Math.max(0.000001, environment.atmosphere.fog_density * 0.00009);
scene.fog = new THREE.FogExp2(0x9db5c1, fogDensity);

function makeWaterNormals(size = 256) {
  const data = new Uint8Array(size * size * 4);
  let seed = 0x6d2b79f5;
  const random = () => {
    seed = Math.imul(seed ^ (seed >>> 15), seed | 1);
    seed ^= seed + Math.imul(seed ^ (seed >>> 7), seed | 61);
    return ((seed ^ (seed >>> 14)) >>> 0) / 4294967296;
  };
  for (let i = 0; i < size * size; i += 1) {
    const angle = random() * Math.PI * 2;
    const strength = 0.22 + random() * 0.48;
    data[i * 4] = Math.round((Math.cos(angle) * strength * 0.5 + 0.5) * 255);
    data[i * 4 + 1] = Math.round((Math.sin(angle) * strength * 0.5 + 0.5) * 255);
    data[i * 4 + 2] = 255;
    data[i * 4 + 3] = 255;
  }
  const texture = new THREE.DataTexture(data, size, size, THREE.RGBAFormat);
  texture.wrapS = texture.wrapT = THREE.RepeatWrapping;
  texture.needsUpdate = true;
  return texture;
}

function deepWaterWavelength(periodSeconds) {
  const gravity = 9.80665;
  return gravity * periodSeconds * periodSeconds / (2 * Math.PI);
}

function direction2(degrees) {
  const radians = THREE.MathUtils.degToRad(degrees);
  return [Math.sin(radians), Math.cos(radians)];
}

function gerstnerBand({ directionDeg, wavelength, amplitude, steepness, speedScale = 1 }) {
  const direction = direction2(directionDeg);
  const k = 2 * Math.PI / Math.max(0.05, wavelength);
  const omega = Math.sqrt(9.80665 * k) * speedScale;
  const phase = dot(positionLocal.xy, vec2(direction[0] * k, direction[1] * k)).sub(time.mul(omega));
  const waveSin = sin(phase);
  const waveCos = cos(phase);
  const horizontal = amplitude * steepness;
  return {
    x: waveCos.mul(direction[0] * horizontal),
    y: waveCos.mul(direction[1] * horizontal),
    z: waveSin.mul(amplitude),
    crest: waveSin.mul(0.5).add(0.5),
  };
}

function buildGerstnerSpectrum(ocean) {
  const spectrum = ocean.spectrum;
  const primaryAmplitude = Math.max(0.01, ocean.significant_wave_height_m * 0.36);
  const primaryWavelength = deepWaterWavelength(ocean.swell_period_s);
  const windAmplitude = Math.max(0.005, spectrum.wind_wave_height_m * 0.42);
  const windWavelength = deepWaterWavelength(spectrum.wind_wave_period_s);
  const crossAmplitude = primaryAmplitude * 0.34;
  const shortAmplitude = Math.max(
    0.002,
    spectrum.short_wave_scale_m * 0.018 * spectrum.short_wave_strength,
  );

  const bands = [
    gerstnerBand({
      directionDeg: ocean.swell_direction_deg,
      wavelength: primaryWavelength,
      amplitude: primaryAmplitude,
      steepness: THREE.MathUtils.clamp(ocean.choppiness * 0.22, 0.02, 0.72),
    }),
    gerstnerBand({
      directionDeg: ocean.swell_direction_deg + spectrum.swell_spread_deg,
      wavelength: primaryWavelength * 0.58,
      amplitude: crossAmplitude,
      steepness: THREE.MathUtils.clamp(ocean.choppiness * 0.18, 0.02, 0.58),
      speedScale: 1.06,
    }),
    gerstnerBand({
      directionDeg: spectrum.wind_wave_direction_deg,
      wavelength: windWavelength,
      amplitude: windAmplitude,
      steepness: THREE.MathUtils.clamp(ocean.choppiness * 0.26, 0.03, 0.78),
      speedScale: 1.18,
    }),
    gerstnerBand({
      directionDeg: spectrum.wind_wave_direction_deg - 37,
      wavelength: spectrum.short_wave_scale_m,
      amplitude: shortAmplitude,
      steepness: THREE.MathUtils.clamp(0.16 + spectrum.short_wave_strength * 0.32, 0.05, 0.55),
      speedScale: 1.45,
    }),
  ];

  let dx = float(0);
  let dy = float(0);
  let dz = float(0);
  let crest = float(0);
  for (const band of bands) {
    dx = dx.add(band.x);
    dy = dy.add(band.y);
    dz = dz.add(band.z);
    crest = crest.add(band.crest);
  }

  return {
    positionNode: positionLocal.add(vec3(dx, dy, dz)),
    crestNode: crest.div(bands.length),
  };
}

let water = null;
let foam = null;

function createOcean() {
  if (!environment.ocean.enabled) return;
  const ocean = environment.ocean;
  const spectrum = buildGerstnerSpectrum(ocean);
  const geometry = new THREE.PlaneGeometry(4096, 4096, 256, 256);
  const deep = ocean.deep_color;

  water = new WaterMesh(geometry, {
    waterNormals: makeWaterNormals(),
    sunDirection: sunDir,
    sunColor: 0xffffff,
    waterColor: new THREE.Color(deep[0], deep[1], deep[2]),
    distortionScale: 5 + ocean.choppiness * 9 + ocean.spectrum.short_wave_strength * 5,
    size: Math.max(0.35, ocean.spectrum.short_wave_scale_m),
    resolutionScale: 0.5,
  });
  water.material.positionNode = spectrum.positionNode;
  water.rotation.x = -Math.PI / 2;
  water.position.y = ocean.sea_level_m;
  scene.add(water);

  const foamMaterial = new THREE.MeshBasicNodeMaterial({
    transparent: true,
    depthWrite: false,
    side: THREE.DoubleSide,
  });
  foamMaterial.colorNode = vec3(0.92, 0.97, 1.0);
  foamMaterial.positionNode = spectrum.positionNode.add(vec3(0, 0, 0.025));
  const threshold = ocean.spectrum.crest_foam_threshold;
  const denominator = Math.max(0.02, 1 - threshold);
  foamMaterial.opacityNode = clamp(
    spectrum.crestNode.sub(threshold).div(denominator),
    0,
    1,
  ).mul(Math.min(1, ocean.foam_amount * 2.4));
  foam = new THREE.Mesh(geometry.clone(), foamMaterial);
  foam.rotation.x = -Math.PI / 2;
  foam.position.y = ocean.sea_level_m;
  foam.renderOrder = 2;
  scene.add(foam);
}

createOcean();

const loader = new GLTFLoader();
const gltf = await loader.loadAsync('/ordax/model.glb');
scene.add(gltf.scene);
gltf.scene.traverse((object) => {
  if (object.isMesh) {
    object.castShadow = true;
    object.receiveShadow = true;
  }
});

const bounds = new THREE.Box3().setFromObject(gltf.scene);
if (!bounds.isEmpty()) {
  const sphere = bounds.getBoundingSphere(new THREE.Sphere());
  controls.target.copy(sphere.center);
  const distance = Math.max(12, sphere.radius * 2.4);
  camera.position.copy(sphere.center).add(new THREE.Vector3(distance, distance * 0.55, distance));
  camera.near = Math.max(0.05, distance / 5000);
  camera.far = Math.max(50000, distance * 30);
  camera.updateProjectionMatrix();
  controls.update();

  if (water) {
    water.position.x = sphere.center.x;
    water.position.z = sphere.center.z;
  }
  if (foam) {
    foam.position.x = sphere.center.x;
    foam.position.z = sphere.center.z;
  }
}

function resize() {
  camera.aspect = innerWidth / innerHeight;
  camera.updateProjectionMatrix();
  renderer.setSize(innerWidth, innerHeight);
}
addEventListener('resize', resize);

let lastHud = 0;
let previousFrameTime = null;
function updateFrameMetrics(timeMs) {
  if (previousFrameTime !== null) {
    const delta = timeMs - previousFrameTime;
    if (delta > 0 && delta < 1000) {
      diagnostics.frameTimes.push(delta);
      if (diagnostics.frameTimes.length > 240) diagnostics.frameTimes.shift();
    }
  }
  previousFrameTime = timeMs;
  if (diagnostics.frameTimes.length === 0) {
    return { samples: 0, averageMs: 0, p95Ms: 0, averageFps: 0 };
  }
  const sorted = [...diagnostics.frameTimes].sort((a, b) => a - b);
  const total = diagnostics.frameTimes.reduce((sum, value) => sum + value, 0);
  const averageMs = total / diagnostics.frameTimes.length;
  const p95Index = Math.min(sorted.length - 1, Math.ceil(sorted.length * 0.95) - 1);
  return {
    samples: diagnostics.frameTimes.length,
    averageMs,
    p95Ms: sorted[p95Index],
    averageFps: averageMs > 0 ? 1000 / averageMs : 0,
  };
}

renderer.setAnimationLoop((timeMs) => {
  const frame = updateFrameMetrics(timeMs);
  controls.update();
  renderer.render(scene, camera);
  if (timeMs - lastHud > 500) {
    lastHud = timeMs;
    const backend = renderer.backend?.isWebGPUBackend ? 'WebGPU' : 'WebGL2 fallback';
    hud.textContent = [
      `OrdaX ${runtime.schema}`,
      `backend: ${backend}`,
      `environment: ${environment.name}`,
      `ocean: ${environment.ocean.spectrum.profile} / ${runtime.ocean_runtime.model}`,
      `model: ${runtime.asset.sha256.slice(0, 12)}...`,
      `triangles: ${renderer.info.render.triangles}`,
      `calls: ${renderer.info.render.calls}`,
      `errors: ${diagnostics.errors}`,
      `console errors: ${diagnostics.consoleErrors}`,
      `unhandled rejections: ${diagnostics.unhandledRejections}`,
      `frames: ${frame.samples}`,
      `frame avg ms: ${frame.averageMs.toFixed(2)}`,
      `frame p95 ms: ${frame.p95Ms.toFixed(2)}`,
      `fps avg: ${frame.averageFps.toFixed(1)}`,
    ].join('\n');
  }
});
"""


def _target_files(viewer_root: Path) -> list[Path]:
    return [
        viewer_root / "package.json",
        viewer_root / "index.html",
        viewer_root / "src" / "main.js",
        viewer_root / "public" / "ordax" / "model.glb",
        viewer_root / "public" / "ordax" / "model.glb.ordax.json",
        viewer_root / "public" / "ordax" / "environment.json",
        viewer_root / "public" / "ordax" / "runtime.json",
    ]


class GameAssetThreeJsActions:
    def game_assets_threejs_prepare_viewer(self, payload: dict[str, Any]) -> ActionResult:
        supported = {
            "project",
            "artifact_path",
            "manifest_path",
            "viewer_dir",
            "environment",
            "overwrite",
        }
        unsupported = set(payload) - supported
        if unsupported:
            return ActionResult(False, f"unsupported fields: {', '.join(sorted(unsupported))}")
        project = self._project(payload)
        try:
            artifact = project.path(str(payload.get("artifact_path") or ""))
            if artifact.suffix.lower() != ".glb":
                raise ValueError("Three.js viewer preparation requires a self-contained GLB artifact")
            raw_manifest = payload.get("manifest_path")
            manifest = (
                project.path(str(raw_manifest))
                if raw_manifest is not None
                else artifact.with_name(artifact.name + ".ordax.json")
            )
            verification = verify_engine_export(project, artifact, manifest)
            if str(verification.get("engine") or "").strip().lower() != "web":
                raise ValueError("engine export provenance is not targeted to the web runtime")
            glb = _inspect_glb(artifact)
            if not glb["self_contained"]:
                raise ValueError("Three.js viewer requires a self-contained GLB")

            viewer_raw = payload.get("viewer_dir") or "ordax/threejs-viewer"
            if not isinstance(viewer_raw, str) or not viewer_raw.strip():
                raise ValueError("viewer_dir must be a non-empty project-relative path")
            viewer_root = project.path(viewer_raw.strip(), must_exist=False)
            targets = _target_files(viewer_root)
            if not bool(payload.get("overwrite", False)):
                existing = [path for path in targets if path.exists()]
                if existing:
                    raise ValueError("Three.js viewer target already exists; set overwrite=true explicitly")

            environment = normalize_environment(payload.get("environment") or {})
            runtime = {
                "schema": THREEJS_VIEWER_SCHEMA,
                "renderer": "WebGPURenderer",
                "fallback": "WebGL2",
                "three_version": THREE_VERSION,
                "vite_version": VITE_VERSION,
                "environment_schema": ENVIRONMENT_SCHEMA,
                "ocean_runtime": dict(OCEAN_RUNTIME),
                "asset": {
                    "path": "ordax/model.glb",
                    "sha256": verification["sha256"],
                    "bytes": verification["bytes"],
                    "glb": glb,
                },
            }
            _atomic_text(viewer_root / "package.json", _package_json())
            _atomic_text(viewer_root / "index.html", _index_html())
            _atomic_text(viewer_root / "src" / "main.js", _main_js())
            _atomic_copy(artifact, viewer_root / "public" / "ordax" / "model.glb")
            _atomic_copy(manifest, viewer_root / "public" / "ordax" / "model.glb.ordax.json")
            _atomic_text(
                viewer_root / "public" / "ordax" / "environment.json",
                json.dumps(environment, indent=2, sort_keys=True) + "\n",
            )
            _atomic_text(
                viewer_root / "public" / "ordax" / "runtime.json",
                json.dumps(runtime, indent=2, sort_keys=True) + "\n",
            )
        except (ValueError, OSError, FileNotFoundError) as error:
            return ActionResult(False, str(error))
        return ActionResult(
            True,
            "Three.js WebGPU visual viewer prepared",
            {
                "viewer_dir": str(viewer_root),
                "schema": THREEJS_VIEWER_SCHEMA,
                "three_version": THREE_VERSION,
                "vite_version": VITE_VERSION,
                "artifact_sha256": verification["sha256"],
                "environment": environment,
                "ocean_runtime": dict(OCEAN_RUNTIME),
                "commands": ["npm install", "npm run dev"],
            },
        )

    def game_assets_threejs_runtime_audit(self, payload: dict[str, Any]) -> ActionResult:
        supported = {"project", "viewer_dir"}
        unsupported = set(payload) - supported
        if unsupported:
            return ActionResult(False, f"unsupported fields: {', '.join(sorted(unsupported))}")
        project = self._project(payload)
        try:
            viewer_raw = payload.get("viewer_dir") or "ordax/threejs-viewer"
            viewer_root = project.path(str(viewer_raw))
            if not viewer_root.is_dir():
                raise ValueError("viewer_dir must be a directory")
            targets = _target_files(viewer_root)
            missing = [path.relative_to(viewer_root).as_posix() for path in targets if not path.is_file()]
            if missing:
                raise ValueError("Three.js viewer is incomplete: " + ", ".join(missing))
            package = json.loads((viewer_root / "package.json").read_text(encoding="utf-8"))
            runtime = json.loads(
                (viewer_root / "public" / "ordax" / "runtime.json").read_text(encoding="utf-8")
            )
            environment_raw = json.loads(
                (viewer_root / "public" / "ordax" / "environment.json").read_text(encoding="utf-8")
            )
            environment = normalize_environment(environment_raw)
            if runtime.get("schema") != THREEJS_VIEWER_SCHEMA:
                raise ValueError("runtime manifest has the wrong schema")
            if package.get("dependencies", {}).get("three") != THREE_VERSION:
                raise ValueError("viewer package does not use the pinned Three.js version")
            if package.get("devDependencies", {}).get("vite") != VITE_VERSION:
                raise ValueError("viewer package does not use the pinned Vite version")
            ocean_runtime = runtime.get("ocean_runtime")
            if not isinstance(ocean_runtime, dict) or ocean_runtime.get("model") != OCEAN_RUNTIME["model"]:
                raise ValueError("viewer runtime does not use the expected ocean-v2 model")
            model = viewer_root / "public" / "ordax" / "model.glb"
            actual_hash = _sha256(model)
            expected_hash = runtime.get("asset", {}).get("sha256")
            if actual_hash != expected_hash:
                raise ValueError("viewer GLB hash does not match runtime provenance")
            glb = _inspect_glb(model)
            if not glb["self_contained"]:
                raise ValueError("viewer GLB is not self-contained")
        except (ValueError, OSError, FileNotFoundError, json.JSONDecodeError) as error:
            return ActionResult(False, str(error))
        return ActionResult(
            True,
            "Three.js visual runtime structure audited",
            {
                "viewer_dir": str(viewer_root),
                "schema": THREEJS_VIEWER_SCHEMA,
                "three_version": THREE_VERSION,
                "vite_version": VITE_VERSION,
                "asset_sha256": actual_hash,
                "environment_schema": environment["schema"],
                "ocean_profile": environment["ocean"]["spectrum"]["profile"],
                "ocean_runtime": ocean_runtime,
                "glb": glb,
                "runtime_build_not_executed": True,
                "dependencies_must_be_installed_explicitly": True,
                "next_gate": "game_assets.threejs_viewer_validate",
            },
        )
