# Lightweight ORDAX Studio Blender add-on: discovery/adoption only.
from __future__ import annotations

bl_info = {
    "name": "ORDAX Studio Bridge",
    "author": "ORDAX",
    "version": (1, 1, 0),
    "blender": (4, 3, 0),
    "location": "System",
    "description": "Discovers Blender windows and safely adopts them into ORDAX Studio",
    "category": "System",
}

import hashlib
import json
import os
import runpy
import sys
import time
from pathlib import Path

import bpy


BOOTSTRAP_VERSION = 1
ADDON_FINGERPRINT = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
ATTACHED_KEY = "_ordax_studio_companion_project"
ERROR_KEY = "_ordax_studio_bootstrap_error"
REQUEST_MAX_AGE_SECONDS = 30.0


def _config_path() -> Path:
    override = os.environ.get("ORDAX_BLENDER_BOOTSTRAP_CONFIG")
    if override:
        return Path(override).expanduser().resolve()
    local = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    return (local / "OrdaX" / "DevAgent" / "blender-bootstrap.json").resolve()


def _read_json(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(data, dict):
        raise ValueError(f"JSON object expected: {path}")
    return data


def _write_json_atomic(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    temp.replace(path)


def _load_config() -> dict:
    data = _read_json(_config_path())
    if int(data.get("version", -1)) != BOOTSTRAP_VERSION:
        raise ValueError("ORDAX Blender bootstrap config version mismatch")
    state_dir = Path(str(data.get("state_dir") or "")).resolve()
    assets_root = Path(str(data.get("assets_root") or "")).resolve()
    companion = Path(str(data.get("companion_path") or "")).resolve()
    if not companion.is_relative_to(assets_root) or not companion.is_file():
        raise ValueError("ORDAX companion path is not trusted")
    projects = data.get("projects")
    if not isinstance(projects, dict):
        raise ValueError("ORDAX Blender projects must be an object")
    return {**data, "state_dir": state_dir, "assets_root": assets_root, "companion_path": companion}


def _current_file() -> Path | None:
    raw = str(getattr(bpy.data, "filepath", "") or "").strip()
    if not raw:
        return None
    try:
        return Path(raw).resolve()
    except OSError:
        return None


def _matching_project(config: dict) -> str | None:
    current = _current_file()
    if current is None:
        return None
    matches: list[str] = []
    for slug, entry in config["projects"].items():
        if not isinstance(entry, dict):
            continue
        try:
            root = Path(str(entry.get("root") or "")).resolve()
        except OSError:
            continue
        if current.is_relative_to(root):
            matches.append(str(slug))
    return matches[0] if len(matches) == 1 else None


def _managed_launch() -> bool:
    if os.environ.get("ORDAX_BLENDER_MANAGED_LAUNCH") == "1":
        return True
    return "--ordax-control-root" in sys.argv and "--ordax-project-slug" in sys.argv


def _discovery_path(config: dict) -> Path:
    return (config["state_dir"] / "blender-discovery" / f"{os.getpid()}.json").resolve()


def _request_path(config: dict) -> Path:
    return (config["state_dir"] / "blender-adoption" / f"{os.getpid()}.json").resolve()


def _write_discovery(config: dict) -> None:
    scene = getattr(bpy.context, "scene", None)
    payload = {
        "bootstrap_version": BOOTSTRAP_VERSION,
        "addon_fingerprint": ADDON_FINGERPRINT,
        "pid": os.getpid(),
        "timestamp": time.time(),
        "blender_version": ".".join(str(v) for v in bpy.app.version),
        "file": str(getattr(bpy.data, "filepath", "") or ""),
        "is_dirty": bool(getattr(bpy.data, "is_dirty", False)),
        "scene": scene.name if scene is not None else "",
        "managed_launch": _managed_launch(),
        "attached_project": bpy.app.driver_namespace.get(ATTACHED_KEY),
        "error": bpy.app.driver_namespace.get(ERROR_KEY),
    }
    _write_json_atomic(_discovery_path(config), payload)


def _read_request(config: dict) -> dict | None:
    path = _request_path(config)
    if not path.is_file():
        return None
    try:
        request = _read_json(path)
        if int(request.get("version", -1)) != BOOTSTRAP_VERSION:
            path.unlink(missing_ok=True)
            return None
        if int(request.get("pid", -1)) != os.getpid():
            return None
        created_at = float(request.get("created_at"))
        age = time.time() - created_at
        if age < 0 or age > REQUEST_MAX_AGE_SECONDS:
            path.unlink(missing_ok=True)
            return None
        project = str(request.get("project") or "")
        if project not in config["projects"]:
            path.unlink(missing_ok=True)
            return None
        return request
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return None


def _current_file_belongs_to(entry: dict) -> bool:
    current = _current_file()
    if current is None:
        return False
    try:
        root = Path(str(entry.get("root") or "")).resolve()
    except OSError:
        return False
    return current.is_relative_to(root)


def _attach(config: dict, project_slug: str, *, allow_blank: bool = False) -> bool:
    attached = bpy.app.driver_namespace.get(ATTACHED_KEY)
    if attached:
        return attached == project_slug

    entry = config["projects"].get(project_slug)
    if not isinstance(entry, dict):
        return False
    current = _current_file()
    if current is None:
        if not allow_blank or bool(getattr(bpy.data, "is_dirty", False)):
            return False
    elif not _current_file_belongs_to(entry):
        return False

    companion = config["companion_path"]
    project_root = Path(str(entry["root"])).resolve()
    scripts_root = Path(str(entry["scripts_root"])).resolve()
    control_root = Path(str(entry["control_root"])).resolve()
    artifacts_root = Path(str(entry["artifacts_root"])).resolve()

    previous_argv = list(sys.argv)
    bpy.app.driver_namespace[ATTACHED_KEY] = project_slug
    bpy.app.driver_namespace.pop(ERROR_KEY, None)
    try:
        sys.argv = [
            str(companion),
            "--",
            "--ordax-control-root",
            str(control_root),
            "--ordax-project-root",
            str(project_root),
            "--ordax-scripts-root",
            str(scripts_root),
            "--ordax-artifacts-root",
            str(artifacts_root),
            "--ordax-project-slug",
            project_slug,
        ]
        runpy.run_path(str(companion), run_name="__ordax_live_companion__")
        return True
    except Exception as error:
        bpy.app.driver_namespace.pop(ATTACHED_KEY, None)
        bpy.app.driver_namespace[ERROR_KEY] = f"{type(error).__name__}: {error}"
        return False
    finally:
        sys.argv = previous_argv


def _tick() -> float:
    try:
        config = _load_config()
        _write_discovery(config)
        if bpy.app.driver_namespace.get(ATTACHED_KEY):
            return 1.0

        # A Blender process started by BlenderLiveBridge already receives the
        # full companion through --python. The persistent add-on must only
        # discover that window, otherwise two companion timers would compete.
        if _managed_launch():
            return 1.0

        automatic = _matching_project(config)
        if automatic and _attach(config, automatic):
            _write_discovery(config)
            return 1.0

        request = _read_request(config)
        if request is not None:
            path = _request_path(config)
            project = str(request["project"])
            allow_blank = request.get("allow_blank", False)
            if not isinstance(allow_blank, bool):
                allow_blank = False
            attached = _attach(config, project, allow_blank=allow_blank)
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
            if attached:
                _write_discovery(config)
    except Exception as error:
        bpy.app.driver_namespace[ERROR_KEY] = f"{type(error).__name__}: {error}"
    return 1.0


def register() -> None:
    if not bpy.app.timers.is_registered(_tick):
        bpy.app.timers.register(_tick, first_interval=0.25, persistent=True)


def unregister() -> None:
    if bpy.app.timers.is_registered(_tick):
        bpy.app.timers.unregister(_tick)
    try:
        config = _load_config()
        _discovery_path(config).unlink(missing_ok=True)
    except Exception:
        pass


if __name__ == "__main__":
    register()
