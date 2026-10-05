"""Managed Belluxx/Aleph geospatial capture integration.

Aleph is installed as an isolated external component rather than vendored into
OrdaX.  The upstream project explicitly relies on undocumented remote APIs, so
pinning a known-good revision and keeping a strict typed wrapper gives the
Device Agent a replaceable failure boundary.
"""
from __future__ import annotations

import json
import math
import os
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .models import ActionResult
from .process_runner import run_command as _run


ALEPH_REPOSITORY = "https://github.com/Belluxx/Aleph.git"
ALEPH_UPSTREAM = "Belluxx/Aleph"
# Audited on 2026-09-24. Updates are explicit through geo.aleph_update.
ALEPH_PINNED_REF = "502667d0b46e67555c7956d4ff281be5e8511a30"
ALEPH_PACKAGE_VERSION = "0.1.0"
ALEPH_COMPONENT_SCHEMA = "ordax.external-component/1"
ALEPH_RESULT_SCHEMA = "ordax.aleph-result/1"
MAX_CAPTURE_AREA_KM2 = 100.0

_ALLOWED_SOURCES = frozenset({"streetview", "satellite", "osm"})
_ALLOWED_STREET_VIEWS = frozenset({"forward", "backward", "left", "right", "both"})
_ALLOWED_IMAGE_FORMATS = frozenset({"jpg", "png"})
_TILE_RE = re.compile(r"^(\d{1,2})/(\d{1,10})/(\d{1,10})$")


def _finite_number(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be numeric")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{field} must be finite")
    return result


def _latitude(value: Any, field: str = "latitude") -> float:
    result = _finite_number(value, field)
    if result < -90 or result > 90:
        raise ValueError(f"{field} must be between -90 and 90")
    return result


def _longitude(value: Any, field: str = "longitude") -> float:
    result = _finite_number(value, field)
    if result < -180 or result > 180:
        raise ValueError(f"{field} must be between -180 and 180")
    return result


def _bounded_int(
    value: Any,
    field: str,
    minimum: int,
    maximum: int,
    default: int | None = None,
) -> int | None:
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field} must be an integer")
    if value < minimum or value > maximum:
        raise ValueError(f"{field} must be between {minimum} and {maximum}")
    return value


def _bounded_float(
    value: Any,
    field: str,
    minimum: float,
    maximum: float,
    default: float | None = None,
) -> float | None:
    if value is None:
        return default
    result = _finite_number(value, field)
    if result < minimum or result > maximum:
        raise ValueError(f"{field} must be between {minimum} and {maximum}")
    return result


def _nonempty_text(value: Any, field: str, maximum: int = 500) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string")
    result = value.strip()
    if not result:
        raise ValueError(f"{field} cannot be empty")
    if len(result) > maximum:
        raise ValueError(f"{field} must be at most {maximum} characters")
    if "\x00" in result:
        raise ValueError(f"{field} contains a NUL byte")
    return result


def _bbox(value: Any) -> tuple[float, float, float, float]:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        raise ValueError("bbox must be [south, west, north, east]")
    south = _latitude(value[0], "south")
    west = _longitude(value[1], "west")
    north = _latitude(value[2], "north")
    east = _longitude(value[3], "east")
    if north <= south:
        raise ValueError("bbox north must be greater than south")
    if east <= west:
        raise ValueError("bbox east must be greater than west; antimeridian boxes are not supported")
    return south, west, north, east


def _bbox_area_km2(box: tuple[float, float, float, float]) -> float:
    south, west, north, east = box
    mean_lat = math.radians((south + north) * 0.5)
    height_km = abs(north - south) * 111.32
    width_km = abs(east - west) * 111.32 * max(0.01, abs(math.cos(mean_lat)))
    return height_km * width_km


def _tile(value: Any) -> str:
    text = _nonempty_text(value, "tile", 64)
    match = _TILE_RE.fullmatch(text)
    if not match:
        raise ValueError("tile must use Z/X/Y, for example 19/280337/194891")
    zoom, x, y = (int(part) for part in match.groups())
    if zoom < 1 or zoom > 21:
        raise ValueError("tile zoom must be between 1 and 21")
    maximum = (1 << zoom) - 1
    if x > maximum or y > maximum:
        raise ValueError("tile X/Y are outside the selected zoom")
    return text


def _component_paths(state_dir: Path) -> dict[str, Path]:
    root = state_dir / "components" / "aleph"
    repo = root / "repo"
    venv = root / "venv"
    if os.name == "nt":
        python = venv / "Scripts" / "python.exe"
        executable = venv / "Scripts" / "alephgeo.exe"
    else:
        python = venv / "bin" / "python"
        executable = venv / "bin" / "alephgeo"
    return {
        "root": root,
        "repo": repo,
        "venv": venv,
        "python": python,
        "executable": executable,
        "manifest": root / "component.json",
    }


def _read_manifest(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return raw if isinstance(raw, dict) else None


def _json_stdout(result: ActionResult, operation: str) -> ActionResult:
    if not result.ok:
        return result
    stdout = str(result.data.get("stdout") or "").strip()
    if not stdout:
        return ActionResult(False, f"Aleph {operation} returned no JSON", {"process": result.data})
    try:
        parsed = json.loads(stdout)
    except ValueError:
        return ActionResult(
            False,
            f"Aleph {operation} returned invalid JSON",
            {"stdout": stdout[-4000:], "stderr": str(result.data.get("stderr") or "")[-4000:]},
        )
    return ActionResult(
        True,
        f"Aleph {operation} completed",
        {
            "schema": ALEPH_RESULT_SCHEMA,
            "operation": operation,
            "result": parsed,
            "stderr": str(result.data.get("stderr") or "")[-4000:],
        },
    )


class AlephActions:
    """Install, update and invoke Belluxx/Aleph through a strict typed surface."""

    def _aleph_install(self, *, update: bool = False) -> ActionResult:
        paths = _component_paths(self.config.state_dir)
        paths["root"].mkdir(parents=True, exist_ok=True)
        git = shutil.which("git")
        if not git:
            return ActionResult(False, "Git is required to install Aleph")

        if not (paths["repo"] / ".git").is_dir():
            if paths["repo"].exists():
                return ActionResult(False, "Aleph component directory exists but is not a Git repository")
            clone = _run(
                [git, "clone", "--filter=blob:none", "--no-checkout", ALEPH_REPOSITORY, str(paths["repo"])],
                cwd=paths["root"],
                timeout=600,
            )
            if not clone.ok:
                return ActionResult(False, "Aleph repository clone failed", {"process": clone.data})

        remote = _run(
            [git, "-C", str(paths["repo"]), "remote", "get-url", "origin"],
            timeout=30,
        )
        if not remote.ok:
            return ActionResult(False, "Aleph origin inspection failed", {"process": remote.data})
        remote_url = str(remote.data.get("stdout") or "").strip().rstrip("/")
        if remote_url not in {
            ALEPH_REPOSITORY.rstrip("/"),
            "https://github.com/Belluxx/Aleph",
            "git@github.com:Belluxx/Aleph.git",
        }:
            return ActionResult(False, "Aleph origin does not match Belluxx/Aleph")

        if update:
            fetch_args = [git, "-C", str(paths["repo"]), "fetch", "--prune", "origin"]
            target = "origin/main"
        else:
            fetch_args = [
                git,
                "-C",
                str(paths["repo"]),
                "fetch",
                "--prune",
                "--depth=1",
                "origin",
                ALEPH_PINNED_REF,
            ]
            target = ALEPH_PINNED_REF
        fetch = _run(fetch_args, timeout=600)
        if not fetch.ok:
            return ActionResult(False, "Aleph upstream fetch failed", {"process": fetch.data})

        checkout = _run(
            [git, "-C", str(paths["repo"]), "checkout", "--detach", "--force", target],
            timeout=120,
        )
        if not checkout.ok:
            return ActionResult(False, "Aleph checkout failed", {"process": checkout.data})

        rev = _run([git, "-C", str(paths["repo"]), "rev-parse", "HEAD"], timeout=30)
        if not rev.ok:
            return ActionResult(False, "Aleph revision inspection failed", {"process": rev.data})
        commit = str(rev.data.get("stdout") or "").strip()
        if not re.fullmatch(r"[0-9a-f]{40}", commit):
            return ActionResult(False, "Aleph returned an invalid Git revision")

        if not paths["python"].is_file():
            create_venv = _run(
                [sys.executable, "-m", "venv", str(paths["venv"])],
                cwd=paths["root"],
                timeout=600,
            )
            if not create_venv.ok:
                return ActionResult(False, "Aleph virtual environment creation failed", {"process": create_venv.data})

        install = _run(
            [
                str(paths["python"]),
                "-m",
                "pip",
                "install",
                "--disable-pip-version-check",
                "--no-input",
                "-e",
                str(paths["repo"]),
            ],
            cwd=paths["repo"],
            timeout=900,
        )
        if not install.ok:
            return ActionResult(False, "Aleph package installation failed", {"process": install.data})
        if not paths["executable"].is_file():
            return ActionResult(False, "Aleph installed without the alephgeo executable")

        help_result = _run([str(paths["executable"]), "--help"], timeout=30)
        if not help_result.ok:
            return ActionResult(False, "Aleph executable self-check failed", {"process": help_result.data})

        manifest = {
            "schema": ALEPH_COMPONENT_SCHEMA,
            "id": "external-alephgeo",
            "upstream": ALEPH_UPSTREAM,
            "repository": ALEPH_REPOSITORY,
            "license": "MIT",
            "package_version": ALEPH_PACKAGE_VERSION,
            "requested_ref": "origin/main" if update else ALEPH_PINNED_REF,
            "resolved_commit": commit,
            "installed_at": datetime.now(timezone.utc).isoformat(),
            "warning": "Upstream Aleph uses undocumented remote APIs and may break or hit rate limits.",
        }
        paths["manifest"].write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        return ActionResult(True, "Aleph component ready", {"component": manifest})

    def _aleph_ready(self) -> tuple[Path | None, ActionResult | None]:
        paths = _component_paths(self.config.state_dir)
        if not paths["executable"].is_file() or not (paths["repo"] / ".git").is_dir():
            installed = self._aleph_install(update=False)
            if not installed.ok:
                return None, installed
        return paths["executable"], None

    def _run_aleph_json(
        self,
        args: list[str],
        *,
        cwd: Path,
        operation: str,
        timeout: int,
    ) -> ActionResult:
        executable, error = self._aleph_ready()
        if error is not None:
            return error
        assert executable is not None
        result = _run([str(executable), *args, "--json"], cwd=cwd, timeout=timeout)
        return _json_stdout(result, operation)

    def geo_aleph_status(self, payload: dict[str, Any]) -> ActionResult:
        unsupported = set(payload) - {"project"}
        if unsupported:
            return ActionResult(False, f"unsupported fields: {', '.join(sorted(unsupported))}")
        self._project(payload)
        paths = _component_paths(self.config.state_dir)
        manifest = _read_manifest(paths["manifest"])
        installed = paths["executable"].is_file() and (paths["repo"] / ".git").is_dir()
        data: dict[str, Any] = {
            "installed": installed,
            "upstream": ALEPH_UPSTREAM,
            "pinned_ref": ALEPH_PINNED_REF,
            "package_version": ALEPH_PACKAGE_VERSION,
            "component_root": str(paths["root"]),
            "manifest": manifest,
            "auto_install_on_first_use": True,
            "warning": "Aleph upstream uses undocumented remote APIs; rate limits/breakage are possible.",
        }
        if installed:
            help_result = _run([str(paths["executable"]), "--help"], timeout=30)
            data["healthy"] = help_result.ok
        else:
            data["healthy"] = False
        return ActionResult(True, "Aleph component status", data)

    def geo_aleph_ensure(self, payload: dict[str, Any]) -> ActionResult:
        unsupported = set(payload) - {"project"}
        if unsupported:
            return ActionResult(False, f"unsupported fields: {', '.join(sorted(unsupported))}")
        self._project(payload)
        paths = _component_paths(self.config.state_dir)
        if paths["executable"].is_file() and (paths["repo"] / ".git").is_dir():
            return ActionResult(
                True,
                "Aleph component already installed",
                {"component": _read_manifest(paths["manifest"]), "executable": str(paths["executable"])},
            )
        return self._aleph_install(update=False)

    def geo_aleph_update(self, payload: dict[str, Any]) -> ActionResult:
        unsupported = set(payload) - {"project"}
        if unsupported:
            return ActionResult(False, f"unsupported fields: {', '.join(sorted(unsupported))}")
        self._project(payload)
        return self._aleph_install(update=True)

    def geo_aleph_resolve(self, payload: dict[str, Any]) -> ActionResult:
        supported = {"project", "query", "at", "streets", "nearby", "radius", "limit", "refresh"}
        unsupported = set(payload) - supported
        if unsupported:
            return ActionResult(False, f"unsupported fields: {', '.join(sorted(unsupported))}")
        project = self._project(payload)
        args = ["resolve"]
        query = payload.get("query")
        at = payload.get("at")
        if (query is None) == (at is None):
            return ActionResult(False, "provide exactly one of query or at")
        try:
            if query is not None:
                args.append(_nonempty_text(query, "query"))
            else:
                if not isinstance(at, (list, tuple)) or len(at) != 2:
                    raise ValueError("at must be [latitude, longitude]")
                args.extend(["--at", str(_latitude(at[0])), str(_longitude(at[1]))])
            if payload.get("streets") is True:
                args.append("--streets")
            if payload.get("nearby") is True:
                args.append("--nearby")
            radius = _bounded_float(payload.get("radius"), "radius", 1, 5000)
            if radius is not None:
                args.extend(["--radius", str(radius)])
            limit = _bounded_int(payload.get("limit"), "limit", 1, 50)
            if limit is not None:
                args.extend(["--limit", str(limit)])
            if payload.get("refresh") is True:
                args.append("--refresh")
        except ValueError as error:
            return ActionResult(False, str(error))
        return self._run_aleph_json(args, cwd=project.root, operation="resolve", timeout=180)

    def geo_aleph_satellite(self, payload: dict[str, Any]) -> ActionResult:
        supported = {
            "project", "place", "at", "bbox", "tile", "size", "zoom", "best_match", "match",
            "format", "output_dir", "refresh",
        }
        unsupported = set(payload) - supported
        if unsupported:
            return ActionResult(False, f"unsupported fields: {', '.join(sorted(unsupported))}")
        project = self._project(payload)
        args = ["satellite"]
        selectors = {
            "place": payload.get("place"),
            "at": payload.get("at"),
            "bbox": payload.get("bbox"),
            "tile": payload.get("tile"),
        }
        if sum(value is not None for value in selectors.values()) != 1:
            return ActionResult(False, "provide exactly one of place, at, bbox, or tile")
        try:
            if selectors["place"] is not None:
                args.extend(["--place", _nonempty_text(selectors["place"], "place")])
                if payload.get("best_match") is True:
                    args.append("--best-match")
                if payload.get("match") is not None:
                    args.extend(["--match", _nonempty_text(payload.get("match"), "match", 100)])
            elif selectors["at"] is not None:
                at = selectors["at"]
                if not isinstance(at, (list, tuple)) or len(at) != 2:
                    raise ValueError("at must be [latitude, longitude]")
                args.extend(["--at", str(_latitude(at[0])), str(_longitude(at[1]))])
            elif selectors["bbox"] is not None:
                box = _bbox(selectors["bbox"])
                args.extend(["--bbox", *[str(value) for value in box]])
            else:
                args.extend(["--tile", _tile(selectors["tile"])])
            size = _bounded_float(payload.get("size"), "size", 10, 20000)
            if size is not None:
                args.extend(["--size", str(size)])
            zoom = _bounded_int(payload.get("zoom"), "zoom", 1, 21)
            if zoom is not None:
                args.extend(["--zoom", str(zoom)])
            image_format = payload.get("format", "jpg")
            if image_format not in _ALLOWED_IMAGE_FORMATS:
                raise ValueError("format must be jpg or png")
            args.extend(["--satellite-format", str(image_format)])
            if payload.get("refresh") is True:
                args.append("--refresh")
            output = project.path(str(payload.get("output_dir") or "generated/aleph"), must_exist=False)
            output.mkdir(parents=True, exist_ok=True)
            args.extend(["-o", str(output)])
        except (ValueError, OSError) as error:
            return ActionResult(False, str(error))
        result = self._run_aleph_json(args, cwd=project.root, operation="satellite", timeout=1800)
        if result.ok:
            result.data["output_root"] = str(output)
        return result

    def geo_aleph_streetview(self, payload: dict[str, Any]) -> ActionResult:
        supported = {
            "project", "place", "at", "street", "pano_id", "best_match", "match", "radius",
            "heading", "look_at", "route", "reverse", "stops", "step", "view", "pitch", "fov",
            "format", "output_dir", "refresh",
        }
        unsupported = set(payload) - supported
        if unsupported:
            return ActionResult(False, f"unsupported fields: {', '.join(sorted(unsupported))}")
        project = self._project(payload)
        args = ["streetview"]
        selectors = {
            "place": payload.get("place"),
            "at": payload.get("at"),
            "street": payload.get("street"),
            "pano_id": payload.get("pano_id"),
        }
        if sum(value is not None for value in selectors.values()) != 1:
            return ActionResult(False, "provide exactly one of place, at, street, or pano_id")
        try:
            if selectors["place"] is not None:
                args.extend(["--place", _nonempty_text(selectors["place"], "place")])
            elif selectors["at"] is not None:
                at = selectors["at"]
                if not isinstance(at, (list, tuple)) or len(at) != 2:
                    raise ValueError("at must be [latitude, longitude]")
                args.extend(["--at", str(_latitude(at[0])), str(_longitude(at[1]))])
            elif selectors["street"] is not None:
                args.extend(["--street", _nonempty_text(selectors["street"], "street")])
            else:
                args.extend(["--pano-id", _nonempty_text(selectors["pano_id"], "pano_id", 256)])

            if selectors["place"] is not None or selectors["street"] is not None:
                if payload.get("best_match") is True:
                    args.append("--best-match")
                if payload.get("match") is not None:
                    args.extend(["--match", _nonempty_text(payload.get("match"), "match", 100)])
            elif payload.get("best_match") is True or payload.get("match") is not None:
                raise ValueError("best_match/match are valid only for place or street selectors")

            radius = _bounded_float(payload.get("radius"), "radius", 1, 5000)
            if radius is not None:
                args.extend(["--radius", str(radius)])
            heading = payload.get("heading")
            look_at = payload.get("look_at")
            if heading is not None and look_at is not None:
                raise ValueError("heading and look_at are mutually exclusive")
            if heading is not None:
                args.extend(["--heading", str(_finite_number(heading, "heading"))])
            if look_at is not None:
                if not isinstance(look_at, (list, tuple)) or len(look_at) != 2:
                    raise ValueError("look_at must be [latitude, longitude]")
                args.extend(["--look-at", str(_latitude(look_at[0])), str(_longitude(look_at[1]))])
            route = _bounded_int(payload.get("route"), "route", 1, 10000)
            if route is not None:
                args.extend(["--route", str(route)])
            if payload.get("reverse") is True:
                args.append("--reverse")
            stops = _bounded_int(payload.get("stops"), "stops", 1, 1000)
            step = _bounded_float(payload.get("step"), "step", 1, 10000)
            if stops is not None and step is not None:
                raise ValueError("stops and step are mutually exclusive")
            if stops is not None:
                args.extend(["--stops", str(stops)])
            if step is not None:
                args.extend(["--step", str(step)])
            view = payload.get("view")
            if view is not None:
                if view not in _ALLOWED_STREET_VIEWS:
                    raise ValueError("view must be forward, backward, left, right, or both")
                args.extend(["--view", str(view)])
            pitch = _bounded_float(payload.get("pitch"), "pitch", -90, 90)
            if pitch is not None:
                args.extend(["--pitch", str(pitch)])
            fov = _bounded_int(payload.get("fov"), "fov", 5, 175)
            if fov is not None:
                args.extend(["--fov", str(fov)])
            image_format = payload.get("format", "jpg")
            if image_format not in _ALLOWED_IMAGE_FORMATS:
                raise ValueError("format must be jpg or png")
            args.extend(["--streetview-format", str(image_format)])
            if payload.get("refresh") is True:
                args.append("--refresh")
            output = project.path(str(payload.get("output_dir") or "generated/aleph"), must_exist=False)
            output.mkdir(parents=True, exist_ok=True)
            args.extend(["-o", str(output)])
        except (ValueError, OSError) as error:
            return ActionResult(False, str(error))
        result = self._run_aleph_json(args, cwd=project.root, operation="streetview", timeout=3600)
        if result.ok:
            result.data["output_root"] = str(output)
        return result

    def geo_aleph_capture(self, payload: dict[str, Any]) -> ActionResult:
        supported = {
            "project", "bbox", "sources", "depth", "step", "fov", "streetview_format",
            "satellite_zoom", "satellite_format", "terrain_zoom", "delay", "output_dir", "refresh",
            "allow_large_area",
        }
        unsupported = set(payload) - supported
        if unsupported:
            return ActionResult(False, f"unsupported fields: {', '.join(sorted(unsupported))}")
        project = self._project(payload)
        try:
            box = _bbox(payload.get("bbox"))
            area_km2 = _bbox_area_km2(box)
            if area_km2 > MAX_CAPTURE_AREA_KM2 and payload.get("allow_large_area") is not True:
                raise ValueError(
                    f"capture area is about {area_km2:.2f} km²; set allow_large_area=true to exceed {MAX_CAPTURE_AREA_KM2:.0f} km²"
                )
            args = ["capture", "create", "--bbox", *[str(value) for value in box], "--no-plan"]
            sources = payload.get("sources")
            if sources is not None:
                if not isinstance(sources, list) or not sources:
                    raise ValueError("sources must be a non-empty list")
                if any(source not in _ALLOWED_SOURCES for source in sources):
                    raise ValueError("sources may contain only streetview, satellite, and osm")
                args.extend(["--sources", *[str(source) for source in sources]])
            depth = payload.get("depth")
            if depth is not None:
                if depth not in {"main", "roads", "all"}:
                    raise ValueError("depth must be main, roads, or all")
                args.extend(["--depth", str(depth)])
            step = _bounded_float(payload.get("step"), "step", 1, 10000)
            if step is not None:
                args.extend(["--step", str(step)])
            fov = _bounded_int(payload.get("fov"), "fov", 5, 175)
            if fov is not None:
                args.extend(["--fov", str(fov)])
            street_fmt = payload.get("streetview_format")
            if street_fmt is not None:
                if street_fmt not in _ALLOWED_IMAGE_FORMATS:
                    raise ValueError("streetview_format must be jpg or png")
                args.extend(["--streetview-format", str(street_fmt)])
            satellite_zoom = _bounded_int(payload.get("satellite_zoom"), "satellite_zoom", 1, 21)
            if satellite_zoom is not None:
                args.extend(["--satellite-zoom", str(satellite_zoom)])
            satellite_fmt = payload.get("satellite_format")
            if satellite_fmt is not None:
                if satellite_fmt not in _ALLOWED_IMAGE_FORMATS:
                    raise ValueError("satellite_format must be jpg or png")
                args.extend(["--satellite-format", str(satellite_fmt)])
            terrain_zoom = _bounded_int(payload.get("terrain_zoom"), "terrain_zoom", 1, 14)
            if terrain_zoom is not None:
                args.extend(["--terrain-zoom", str(terrain_zoom)])
            delay = _bounded_float(payload.get("delay"), "delay", 0, 60)
            if delay is not None:
                args.extend(["--delay", str(delay)])
            if payload.get("refresh") is True:
                args.append("--refresh")
            output = project.path(str(payload.get("output_dir") or "generated/aleph/captures"), must_exist=False)
            output.mkdir(parents=True, exist_ok=True)
            args.extend(["-o", str(output)])
        except (ValueError, OSError) as error:
            return ActionResult(False, str(error))
        result = self._run_aleph_json(args, cwd=project.root, operation="capture", timeout=7200)
        if result.ok:
            result.data["output_root"] = str(output)
            result.data["bbox_area_km2"] = round(area_km2, 4)
        return result

    def geo_aleph_capture_resume(self, payload: dict[str, Any]) -> ActionResult:
        supported = {"project", "capture_dir", "refresh"}
        unsupported = set(payload) - supported
        if unsupported:
            return ActionResult(False, f"unsupported fields: {', '.join(sorted(unsupported))}")
        project = self._project(payload)
        try:
            folder = project.path(str(payload.get("capture_dir") or ""))
            if not (folder / "manifest.json").is_file():
                raise ValueError("capture_dir must contain manifest.json")
            args = ["capture", "resume", str(folder), "--no-plan"]
            if payload.get("refresh") is True:
                args.append("--refresh")
        except (ValueError, OSError) as error:
            return ActionResult(False, str(error))
        return self._run_aleph_json(args, cwd=project.root, operation="capture_resume", timeout=7200)

    def geo_aleph_capture_export(self, payload: dict[str, Any]) -> ActionResult:
        unsupported = set(payload) - {"project", "capture_dir"}
        if unsupported:
            return ActionResult(False, f"unsupported fields: {', '.join(sorted(unsupported))}")
        project = self._project(payload)
        try:
            folder = project.path(str(payload.get("capture_dir") or ""))
            if not (folder / "manifest.json").is_file():
                raise ValueError("capture_dir must contain manifest.json")
        except (ValueError, OSError) as error:
            return ActionResult(False, str(error))
        return self._run_aleph_json(
            ["capture", "export", str(folder)],
            cwd=project.root,
            operation="capture_export",
            timeout=3600,
        )
