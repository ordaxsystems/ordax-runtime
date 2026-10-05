"""Aleph capture inspection and Blender environment staging."""
from __future__ import annotations

import json
import shutil
import uuid
from pathlib import Path
from typing import Any

from mcp_blender_unity.config import find_blender

from .aleph_actions import _component_paths
from .models import ActionResult
from .process_runner import run_command as _run


_SUPPORTED_ALEPH_FORMAT_VERSIONS = {3}


def _bounded_int(value: Any, field: str, minimum: int, maximum: int, default: int) -> int:
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field} must be an integer")
    if value < minimum or value > maximum:
        raise ValueError(f"{field} must be between {minimum} and {maximum}")
    return value


def _read_capture(capture: Path) -> dict[str, Any]:
    manifest_path = capture / "manifest.json"
    if not manifest_path.is_file():
        raise ValueError("capture_dir must contain manifest.json")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ValueError(f"cannot parse Aleph manifest: {error}") from error
    if not isinstance(manifest, dict):
        raise ValueError("Aleph manifest must be a JSON object")
    if manifest.get("format") != "aleph-python":
        raise ValueError("capture is not an Aleph Python capture")
    version = manifest.get("version")
    if version not in _SUPPORTED_ALEPH_FORMAT_VERSIONS:
        raise ValueError(f"unsupported Aleph capture format version: {version}")
    bounds = manifest.get("bounds")
    if not isinstance(bounds, list) or len(bounds) != 4:
        raise ValueError("Aleph manifest does not contain valid bounds")
    try:
        south, west, north, east = [float(value) for value in bounds]
    except (TypeError, ValueError) as error:
        raise ValueError("Aleph manifest bounds must be numeric") from error
    if not (-90 <= south < north <= 90 and -180 <= west < east <= 180):
        raise ValueError("Aleph manifest bounds are outside valid coordinate ranges")
    return manifest


def _file_entry(path: Path) -> dict[str, Any]:
    return {
        "path": str(path),
        "exists": path.is_file(),
        "bytes": path.stat().st_size if path.is_file() else 0,
    }


def _stage_counts(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    stages = manifest.get("stages")
    if not isinstance(stages, list):
        return result
    for stage in stages:
        if not isinstance(stage, dict):
            continue
        mode = stage.get("mode")
        saved = stage.get("results") if isinstance(stage.get("results"), list) else []
        expected = None
        grid = stage.get("grid")
        if isinstance(grid, dict):
            try:
                expected = int(grid.get("rows", 0)) * int(grid.get("columns", 0))
                if mode == "osm":
                    expected += 1
            except (TypeError, ValueError):
                expected = None
        elif mode == "streetview" and isinstance(stage.get("samples"), list):
            expected = len(stage["samples"]) * 2
        result.append({"mode": mode, "saved": len(saved), "expected": expected})
    return result


class AlephSceneActions:
    """Turn immutable Aleph capture data into derived Blender work scenes."""

    def geo_aleph_capture_inspect(self, payload: dict[str, Any]) -> ActionResult:
        unsupported = set(payload) - {"project", "capture_dir"}
        if unsupported:
            return ActionResult(False, f"unsupported fields: {', '.join(sorted(unsupported))}")
        project = self._project(payload)
        try:
            capture = project.path(str(payload.get("capture_dir") or ""))
            manifest = _read_capture(capture)
        except (ValueError, OSError) as error:
            return ActionResult(False, str(error))
        files = {
            "manifest": _file_entry(capture / "manifest.json"),
            "osm": _file_entry(capture / "map.osm"),
            "terrain": _file_entry(capture / "terrain.tif"),
            "satellite_png": _file_entry(capture / "satellite.png"),
            "satellite_tiff": _file_entry(capture / "satellite.tif"),
            "streetview_photos": _file_entry(capture / "streetview" / "photos.geojson"),
            "streetview_paths": _file_entry(capture / "streetview" / "paths.geojson"),
        }
        capabilities = {
            "terrain_mesh": files["terrain"]["exists"],
            "satellite_material": files["satellite_png"]["exists"],
            "osm_buildings": files["osm"]["exists"],
            "osm_roads": files["osm"]["exists"],
            "streetview_references": files["streetview_photos"]["exists"],
        }
        return ActionResult(
            True,
            "Aleph capture inspected",
            {
                "schema": "ordax.aleph-capture-inspection/1",
                "capture_dir": str(capture),
                "format": manifest.get("format"),
                "version": manifest.get("version"),
                "state": manifest.get("state"),
                "bounds": manifest.get("bounds"),
                "options": manifest.get("options"),
                "stages": _stage_counts(manifest),
                "files": files,
                "capabilities": capabilities,
            },
        )

    def geo_aleph_blender_stage(self, payload: dict[str, Any]) -> ActionResult:
        supported = {
            "project",
            "capture_dir",
            "output_blend",
            "terrain_samples",
            "include_terrain",
            "include_buildings",
            "include_roads",
            "use_satellite",
            "overwrite",
            "keep_staging",
            "timeout_seconds",
        }
        unsupported = set(payload) - supported
        if unsupported:
            return ActionResult(False, f"unsupported fields: {', '.join(sorted(unsupported))}")
        project = self._project(payload)
        try:
            capture = project.path(str(payload.get("capture_dir") or ""))
            _read_capture(capture)
            output = project.path(str(payload.get("output_blend") or ""), must_exist=False)
            if output.suffix.lower() != ".blend":
                raise ValueError("output_blend must end in .blend")
            if output.exists() and payload.get("overwrite") is not True:
                raise ValueError("output_blend already exists; set overwrite=true explicitly")
            terrain_samples = _bounded_int(payload.get("terrain_samples"), "terrain_samples", 16, 512, 128)
            timeout = _bounded_int(payload.get("timeout_seconds"), "timeout_seconds", 60, 14400, 7200)
        except (ValueError, OSError) as error:
            return ActionResult(False, str(error))

        include_terrain = payload.get("include_terrain", True) is not False
        include_buildings = payload.get("include_buildings", True) is not False
        include_roads = payload.get("include_roads", True) is not False
        use_satellite = payload.get("use_satellite", True) is not False

        executable, aleph_error = self._aleph_ready()
        if aleph_error is not None:
            return aleph_error
        if executable is None:
            return ActionResult(False, "Aleph executable is unavailable after installation")
        component = _component_paths(self.config.state_dir)
        aleph_python = component["python"]
        if not aleph_python.is_file():
            return ActionResult(False, "Aleph managed Python environment is unavailable")

        blender = find_blender()
        if blender is None:
            return ActionResult(False, "Blender executable not found")

        artifact_root = self.config.state_dir / "artifacts" / project.slug / "aleph-scenes"
        artifact_root.mkdir(parents=True, exist_ok=True)
        staging = artifact_root / f"stage-{uuid.uuid4().hex}"
        staging.mkdir(parents=True, exist_ok=False)
        preprocess_report = staging / "preprocess.json"
        blender_report = artifact_root / f"blender-scene-{uuid.uuid4().hex}.json"
        preprocess_script = Path(__file__).resolve().parent / "assets" / "aleph_capture_preprocess.py"
        blender_script = Path(__file__).resolve().parent / "assets" / "blender_aleph_environment.py"

        preprocess_command = [
            str(aleph_python),
            str(preprocess_script),
            "--capture",
            str(capture),
            "--staging",
            str(staging),
            "--terrain-samples",
            str(terrain_samples),
        ]
        if not include_terrain:
            preprocess_command.append("--no-terrain")
        if not include_buildings:
            preprocess_command.append("--no-buildings")
        if not include_roads:
            preprocess_command.append("--no-roads")

        preprocess = _run(preprocess_command, cwd=project.root, timeout=timeout)
        if not preprocess.ok:
            return ActionResult(
                False,
                "Aleph capture preprocessing failed",
                {"process": preprocess.data, "staging_dir": str(staging)},
            )
        if not preprocess_report.is_file():
            return ActionResult(False, "Aleph preprocessing did not produce preprocess.json", {"staging_dir": str(staging)})
        try:
            preprocess_data = json.loads(preprocess_report.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            return ActionResult(False, f"cannot read Aleph preprocessing report: {error}", {"staging_dir": str(staging)})

        output.parent.mkdir(parents=True, exist_ok=True)
        blender_command = [
            str(blender),
            "--background",
            "--factory-startup",
            "--python",
            str(blender_script),
            "--",
            "--preprocess",
            str(preprocess_report),
            "--output",
            str(output),
            "--report",
            str(blender_report),
        ]
        if not use_satellite:
            blender_command.append("--no-satellite")
        built = _run(blender_command, cwd=project.root, timeout=timeout)
        if not built.ok:
            return ActionResult(
                False,
                "Blender Aleph environment staging failed",
                {"process": built.data, "staging_dir": str(staging), "preprocess": preprocess_data},
            )
        if not output.is_file():
            return ActionResult(False, "Blender staging completed without output .blend", {"staging_dir": str(staging)})
        if not blender_report.is_file():
            return ActionResult(False, "Blender staging completed without a report", {"staging_dir": str(staging)})
        try:
            scene_report = json.loads(blender_report.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            return ActionResult(False, f"cannot read Blender staging report: {error}", {"staging_dir": str(staging)})
        if not isinstance(scene_report, dict) or not scene_report.get("ok"):
            return ActionResult(False, "Blender staging report indicates failure", {"report": scene_report, "staging_dir": str(staging)})

        keep_staging = payload.get("keep_staging") is True
        cleanup_warning = None
        if not keep_staging:
            try:
                shutil.rmtree(staging)
            except OSError as error:
                cleanup_warning = str(error)

        return ActionResult(
            True,
            "Aleph capture staged into Blender",
            {
                "schema": "ordax.aleph-blender-stage/1",
                "capture_dir": str(capture),
                "output_blend": str(output),
                "bytes": output.stat().st_size,
                "terrain_samples": terrain_samples,
                "preprocess": preprocess_data,
                "scene": scene_report,
                "report_path": str(blender_report),
                "staging_dir": str(staging) if keep_staging or cleanup_warning else None,
                "cleanup_warning": cleanup_warning,
            },
        )
