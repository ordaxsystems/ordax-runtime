"""Blender-specific typed actions composed into the central ActionRegistry.

This module contains no registry or remote-shell surface. It relies on the
central registry for project lookup, artifact capture helpers and the strict
allow-list.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import sys
import uuid
from pathlib import Path
from typing import Any

from mcp_blender_unity.config import find_blender

from .assets.blender_modeling_contracts import (
    modeling_schemas,
    normalize_object_selector,
    normalize_transform_fields,
    plan_modeling_operation,
)
from .assets.blender_material_contracts import normalize_material_request
from .blender_asset_sources import polyhaven_file_manifest, search_polyhaven
from .blender_adoption import BlenderAdoptionManager
from .blender_live_bridge import BlenderLiveBridge, blender_companion_bundle_fingerprint
from .models import ActionResult
from .process_runner import run_command as _run


_LOCAL_IMPORT_EXTENSIONS = frozenset({".obj", ".glb", ".gltf"})
_LOCAL_IMPORT_MAX_BYTES = 1024 * 1024 * 1024
_ANIMATION_TRANSFORM_FIELDS = frozenset({"location", "rotation_euler", "scale"})


def _presentation_name(value: Any, field: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string")
    result = value.strip()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_. -]{0,126}", result):
        raise ValueError(f"{field} contains unsupported characters")
    return result


def _presentation_number(
    value: Any,
    field: str,
    *,
    minimum: float,
    maximum: float,
) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be numeric")
    result = float(value)
    if not math.isfinite(result) or result < minimum or result > maximum:
        raise ValueError(f"{field} must be between {minimum} and {maximum}")
    return result


def _presentation_vector(
    value: Any,
    field: str,
    *,
    minimum: float = -1000.0,
    maximum: float = 1000.0,
) -> list[float]:
    if not isinstance(value, list) or len(value) != 3:
        raise ValueError(f"{field} must be a list of three numbers")
    return [
        _presentation_number(component, field, minimum=minimum, maximum=maximum)
        for component in value
    ]


def _presentation_color(value: Any, field: str = "color") -> list[float]:
    if not isinstance(value, list) or len(value) != 3:
        raise ValueError(f"{field} must be an RGB list")
    return [
        _presentation_number(component, field, minimum=0.0, maximum=1.0)
        for component in value
    ]


def _normalize_camera_request(payload: dict[str, Any]) -> dict[str, Any]:
    allowed = {
        "project",
        "timeout_seconds",
        "name",
        "location",
        "target",
        "lens_mm",
        "sensor_width_mm",
        "clip_start",
        "clip_end",
        "make_active",
    }
    unsupported = sorted(set(payload) - allowed)
    if unsupported:
        raise ValueError("unsupported field(s): " + ", ".join(unsupported))
    make_active = payload.get("make_active", True)
    if not isinstance(make_active, bool):
        raise ValueError("make_active must be boolean")
    clip_start = _presentation_number(
        payload.get("clip_start", 0.001),
        "clip_start",
        minimum=0.0001,
        maximum=10.0,
    )
    clip_end = _presentation_number(
        payload.get("clip_end", 100.0),
        "clip_end",
        minimum=0.01,
        maximum=100000.0,
    )
    if clip_end <= clip_start:
        raise ValueError("clip_end must be greater than clip_start")
    return {
        "name": _presentation_name(payload.get("name"), "name"),
        "location": _presentation_vector(payload.get("location"), "location"),
        "target": _presentation_vector(payload.get("target"), "target"),
        "lens_mm": _presentation_number(
            payload.get("lens_mm", 50.0),
            "lens_mm",
            minimum=1.0,
            maximum=500.0,
        ),
        "sensor_width_mm": _presentation_number(
            payload.get("sensor_width_mm", 36.0),
            "sensor_width_mm",
            minimum=1.0,
            maximum=100.0,
        ),
        "clip_start": clip_start,
        "clip_end": clip_end,
        "make_active": make_active,
    }


def _normalize_light_request(payload: dict[str, Any]) -> dict[str, Any]:
    allowed = {
        "project",
        "timeout_seconds",
        "name",
        "light_type",
        "location",
        "target",
        "energy",
        "color",
        "size",
        "angle_degrees",
        "spot_size_degrees",
        "spot_blend",
    }
    unsupported = sorted(set(payload) - allowed)
    if unsupported:
        raise ValueError("unsupported field(s): " + ", ".join(unsupported))
    light_type = str(payload.get("light_type") or "AREA").strip().upper()
    if light_type not in {"AREA", "POINT", "SUN", "SPOT"}:
        raise ValueError("light_type must be AREA, POINT, SUN, or SPOT")
    result = {
        "name": _presentation_name(payload.get("name"), "name"),
        "light_type": light_type,
        "location": _presentation_vector(payload.get("location"), "location"),
        "target": _presentation_vector(payload.get("target", [0.0, 0.0, 0.0]), "target"),
        "energy": _presentation_number(
            payload.get("energy", 100.0),
            "energy",
            minimum=0.0,
            maximum=1000000.0,
        ),
        "color": _presentation_color(payload.get("color", [1.0, 1.0, 1.0])),
        "size": _presentation_number(
            payload.get("size", 0.1),
            "size",
            minimum=0.0001,
            maximum=1000.0,
        ),
        "angle_degrees": _presentation_number(
            payload.get("angle_degrees", 0.526),
            "angle_degrees",
            minimum=0.0,
            maximum=180.0,
        ),
        "spot_size_degrees": _presentation_number(
            payload.get("spot_size_degrees", 45.0),
            "spot_size_degrees",
            minimum=1.0,
            maximum=179.0,
        ),
        "spot_blend": _presentation_number(
            payload.get("spot_blend", 0.15),
            "spot_blend",
            minimum=0.0,
            maximum=1.0,
        ),
    }
    return result


def _normalize_scene_presentation_request(payload: dict[str, Any]) -> dict[str, Any]:
    allowed = {
        "project",
        "timeout_seconds",
        "render_engine",
        "resolution_x",
        "resolution_y",
        "resolution_percentage",
        "world_color",
        "world_strength",
        "transparent_film",
    }
    unsupported = sorted(set(payload) - allowed)
    if unsupported:
        raise ValueError("unsupported field(s): " + ", ".join(unsupported))
    engine = str(payload.get("render_engine") or "BLENDER_EEVEE").strip().upper()
    if engine not in {
        "BLENDER_EEVEE",
        "BLENDER_EEVEE_NEXT",
        "BLENDER_WORKBENCH",
        "CYCLES",
    }:
        raise ValueError(
            "render_engine must be BLENDER_EEVEE, BLENDER_EEVEE_NEXT, "
            "BLENDER_WORKBENCH, or CYCLES"
        )
    transparent = payload.get("transparent_film", False)
    if not isinstance(transparent, bool):
        raise ValueError("transparent_film must be boolean")
    return {
        "render_engine": engine,
        "resolution_x": int(
            _presentation_number(
                payload.get("resolution_x", 1080),
                "resolution_x",
                minimum=64,
                maximum=8192,
            )
        ),
        "resolution_y": int(
            _presentation_number(
                payload.get("resolution_y", 1080),
                "resolution_y",
                minimum=64,
                maximum=8192,
            )
        ),
        "resolution_percentage": int(
            _presentation_number(
                payload.get("resolution_percentage", 100),
                "resolution_percentage",
                minimum=1,
                maximum=100,
            )
        ),
        "world_color": _presentation_color(
            payload.get("world_color", [0.035, 0.035, 0.035]),
            "world_color",
        ),
        "world_strength": _presentation_number(
            payload.get("world_strength", 0.35),
            "world_strength",
            minimum=0.0,
            maximum=1000.0,
        ),
        "transparent_film": transparent,
    }


def _normalize_transform_animation_request(payload: dict[str, Any]) -> dict[str, Any]:
    allowed = {
        "project",
        "timeout_seconds",
        "object_name",
        "ordax_object_id",
        "keyframes",
        "interpolation",
        "replace_existing",
        "set_scene_range",
        "fps",
    }
    unsupported = sorted(set(payload) - allowed)
    if unsupported:
        raise ValueError("unsupported field(s): " + ", ".join(unsupported))

    selector = normalize_object_selector(payload)
    raw_keyframes = payload.get("keyframes")
    if not isinstance(raw_keyframes, list) or not (2 <= len(raw_keyframes) <= 64):
        raise ValueError("keyframes must be a list containing 2 to 64 entries")

    normalized_keyframes = []
    previous_frame = None
    for index, raw in enumerate(raw_keyframes):
        if not isinstance(raw, dict):
            raise ValueError(f"keyframes[{index}] must be an object")
        unknown = sorted(set(raw) - ({"frame"} | _ANIMATION_TRANSFORM_FIELDS))
        if unknown:
            raise ValueError(
                f"keyframes[{index}] unsupported field(s): " + ", ".join(unknown)
            )
        frame = raw.get("frame")
        if isinstance(frame, bool) or not isinstance(frame, int):
            raise ValueError(f"keyframes[{index}].frame must be an integer")
        if frame < 0 or frame > 100000:
            raise ValueError(f"keyframes[{index}].frame must be between 0 and 100000")
        if previous_frame is not None and frame <= previous_frame:
            raise ValueError("keyframe frames must be strictly increasing")
        previous_frame = frame

        transform_payload = {
            key: raw[key]
            for key in _ANIMATION_TRANSFORM_FIELDS
            if key in raw
        }
        try:
            transform = normalize_transform_fields(transform_payload)
        except ValueError as error:
            raise ValueError(f"keyframes[{index}]: {error}") from error
        if "dimensions" in transform:
            transform.pop("dimensions", None)
        normalized_keyframes.append({"frame": frame, **transform})

    interpolation = str(payload.get("interpolation", "BEZIER")).strip().upper()
    if interpolation not in {"BEZIER", "LINEAR", "CONSTANT"}:
        raise ValueError("interpolation must be BEZIER, LINEAR, or CONSTANT")

    replace_existing = payload.get("replace_existing", True)
    set_scene_range = payload.get("set_scene_range", True)
    for value, field in (
        (replace_existing, "replace_existing"),
        (set_scene_range, "set_scene_range"),
    ):
        if not isinstance(value, bool):
            raise ValueError(f"{field} must be boolean")

    fps = payload.get("fps", 24)
    if isinstance(fps, bool) or not isinstance(fps, int) or not (1 <= fps <= 120):
        raise ValueError("fps must be an integer between 1 and 120")

    return {
        **selector,
        "keyframes": normalized_keyframes,
        "interpolation": interpolation,
        "replace_existing": replace_existing,
        "set_scene_range": set_scene_range,
        "fps": fps,
    }


def _resolve_local_import_source(payload: dict[str, Any]) -> tuple[Path | None, list[str] | None, str | None]:
    raw = payload.get("source_path")
    if not isinstance(raw, str) or not raw.strip():
        return None, None, "source_path is required"
    source = Path(raw.strip()).expanduser().resolve()
    home = Path.home().resolve()
    try:
        source.relative_to(home)
    except ValueError:
        return None, None, "source_path must be inside the current user's home directory"

    if not source.exists():
        return None, None, f"source_path not found: {source}"

    if source.is_dir():
        candidates = sorted(
            path
            for path in source.iterdir()
            if path.is_file() and path.suffix.lower() in _LOCAL_IMPORT_EXTENSIONS
        )
        if not candidates:
            return None, [], "no supported 3D asset found in source directory"
        if len(candidates) != 1:
            return None, [str(path) for path in candidates[:50]], (
                "source directory must contain exactly one supported 3D asset"
            )
        source = candidates[0]

    if source.suffix.lower() not in _LOCAL_IMPORT_EXTENSIONS:
        return None, None, "source asset must be OBJ, GLB, or glTF"
    if source.stat().st_size <= 0:
        return None, None, "source asset is empty"
    if source.stat().st_size > _LOCAL_IMPORT_MAX_BYTES:
        return None, None, "source asset exceeds the 1 GiB import limit"
    return source, None, None


class BlenderActions:
    def _blender_live(self, payload: dict[str, Any]) -> BlenderLiveBridge:
        return BlenderLiveBridge(self.config, self._project(payload))

    def blender_benchmark(self, payload: dict[str, Any]) -> ActionResult:
        unsupported = sorted(set(payload) - {"project", "timeout_seconds"})
        if unsupported:
            return ActionResult(
                False,
                "unsupported field(s): " + ", ".join(unsupported),
            )

        try:
            timeout = int(payload.get("timeout_seconds", 1200))
        except (TypeError, ValueError):
            return ActionResult(False, "timeout_seconds must be an integer")
        if timeout < 60 or timeout > 3600:
            return ActionResult(
                False,
                "timeout_seconds must be between 60 and 3600",
            )

        blender = find_blender()
        if blender is None:
            return ActionResult(False, "Blender executable not found")

        source_root = Path(__file__).resolve().parents[1]
        managed_root = self.config.agent_repo_path.resolve()
        candidates = [
            managed_root / "scripts" / "blender_benchmark.py",
            source_root / "scripts" / "blender_benchmark.py",
        ]
        script = next((candidate for candidate in candidates if candidate.is_file()), None)
        if script is None:
            return ActionResult(
                False,
                "BlenderBench script is missing from the managed/source repository",
                {"checked": [str(candidate) for candidate in candidates]},
            )

        run_id = uuid.uuid4().hex
        artifact_root = (
            self.config.state_dir
            / "artifacts"
            / "blenderbench"
            / run_id
        ).resolve()
        artifact_root.mkdir(parents=True, exist_ok=False)

        report_path = artifact_root / "benchmark-report.json"
        result = _run(
            [
                sys.executable,
                str(script),
                "--output-dir",
                str(artifact_root),
                "--report-file",
                str(report_path),
            ],
            cwd=script.parents[1],
            timeout=timeout,
        )
        if not result.ok:
            result.summary = (
                "OrdaX BlenderBench timed out"
                if result.data.get("timed_out")
                else "OrdaX BlenderBench failed"
            )
            result.data["artifact_root"] = str(artifact_root)
            result.data["blender"] = str(blender)
            return result

        if not report_path.is_file():
            return ActionResult(
                False,
                "BlenderBench completed but did not write its durable report",
                {
                    **result.data,
                    "artifact_root": str(artifact_root),
                    "blender": str(blender),
                    "report_path": str(report_path),
                },
            )
        try:
            benchmark = json.loads(
                report_path.read_text(encoding="utf-8-sig")
            )
        except (OSError, json.JSONDecodeError) as error:
            return ActionResult(
                False,
                "BlenderBench durable report is not valid JSON",
                {
                    **result.data,
                    "artifact_root": str(artifact_root),
                    "blender": str(blender),
                    "report_path": str(report_path),
                    "parse_error": str(error),
                },
            )
        if not isinstance(benchmark, dict):
            return ActionResult(
                False,
                "BlenderBench report must be a JSON object",
                {
                    **result.data,
                    "artifact_root": str(artifact_root),
                    "blender": str(blender),
                    "report_path": str(report_path),
                },
            )

        modeling = benchmark.get("modeling_dispatch") or {}
        required_modeling = {
            "transform_positive": True,
            "transform_negative_detected": True,
            "create_positive": True,
            "create_duplicate_detected": True,
            "modifier_positive": True,
            "modifier_duplicate_detected": True,
            "dispatcher_journaled": True,
        }
        missing = [
            key
            for key, expected in required_modeling.items()
            if modeling.get(key) is not expected
        ]
        if missing:
            return ActionResult(
                False,
                "BlenderBench report is missing required staged-modeling evidence",
                {
                    **result.data,
                    "artifact": str(report_path),
                    "artifact_root": str(artifact_root),
                    "blender": str(blender),
                    "benchmark": benchmark,
                    "missing_evidence": missing,
                },
            )

        return ActionResult(
            True,
            "OrdaX BlenderBench passed",
            {
                **result.data,
                "artifact": str(report_path),
                "artifact_root": str(artifact_root),
                "blender": str(blender),
                "benchmark": benchmark,
            },
        )

    def _blender_adoption_manager(self) -> BlenderAdoptionManager:
        assets_root = (Path(__file__).resolve().parent / "assets").resolve()
        try:
            fingerprint = blender_companion_bundle_fingerprint(assets_root)
        except (OSError, ValueError, json.JSONDecodeError):
            fingerprint = None
        return BlenderAdoptionManager(
            self.config,
            self.projects,
            assets_root=assets_root,
            companion_fingerprint=fingerprint,
        )

    def blender_adoption_install(self, payload: dict[str, Any]) -> ActionResult:
        return self._blender_adoption_manager().install()

    def _blender_live_sessions(self) -> list[dict[str, Any]]:
        sessions: list[dict[str, Any]] = []
        for project in self.projects.values():
            if "blender" not in project.apps:
                continue
            live = BlenderLiveBridge(self.config, project)
            if not live.presence_is_fresh():
                continue
            status = live.status()
            presence = status.get("presence")
            if not isinstance(presence, dict):
                continue
            try:
                pid = int(presence.get("pid"))
            except (TypeError, ValueError):
                continue
            if pid <= 0:
                continue
            sessions.append(
                {
                    "project": project.slug,
                    "pid": pid,
                    "file": str(presence.get("file") or ""),
                    "scene": str(presence.get("scene") or ""),
                    "is_dirty": bool(presence.get("is_dirty")),
                    "identity_matches": status.get("identity_matches") is True,
                    "protocol_compatible": status.get("protocol_compatible") is True,
                    "companion_current": status.get("companion_current") is True,
                    "presence_path": str(status.get("presence_path") or live.presence),
                }
            )
        return sorted(sessions, key=lambda item: (item["project"], item["pid"]))

    def blender_instances(self, payload: dict[str, Any]) -> ActionResult:
        manager = self._blender_adoption_manager()
        instances = manager.instances()
        system_pids = manager.system_blender_pids()
        discovered_pids = {int(item.get("pid", -1)) for item in instances}
        live_sessions = self._blender_live_sessions()
        live_session_pids = sorted({int(item["pid"]) for item in live_sessions})
        live_managed_pids = set(live_session_pids)
        unmanaged_pids = [
            pid
            for pid in system_pids
            if pid not in discovered_pids and pid not in live_managed_pids
        ]
        live_healthy_pids = sorted(
            int(item["pid"])
            for item in live_sessions
            if item["identity_matches"]
            and item["protocol_compatible"]
            and item["companion_current"]
        )
        live_attention_pids = sorted(live_managed_pids - set(live_healthy_pids))
        attached_pids = [
            int(item["pid"])
            for item in instances
            if str(item.get("attached_project") or "").strip()
        ]
        restart_required_pids = [
            int(item["pid"])
            for item in instances
            if not bool(item.get("addon_current"))
            and not str(item.get("attached_project") or "").strip()
        ]
        ready_pids = [
            int(item["pid"])
            for item in instances
            if bool(item.get("addon_current"))
            and not str(item.get("attached_project") or "").strip()
        ]
        summary = (
            f"Discovered {len(instances)} Blender adoption window(s): "
            f"{len(attached_pids)} attached, {len(ready_pids)} ready, "
            f"{len(restart_required_pids)} restart-required; "
            f"{len(live_session_pids)} live-managed, {len(live_attention_pids)} live-attention, "
            f"{len(unmanaged_pids)} unmanaged"
        )
        return ActionResult(
            True,
            summary,
            {
                "instances": instances,
                "attached_pids": attached_pids,
                "ready_pids": ready_pids,
                "restart_required_pids": restart_required_pids,
                "live_sessions": live_sessions,
                "live_session_pids": live_session_pids,
                "live_healthy_pids": live_healthy_pids,
                "live_attention_pids": live_attention_pids,
                "system_blender_pids": system_pids,
                "unmanaged_blender_pids": unmanaged_pids,
                "bootstrap_config": str(manager.config_path),
            },
        )

    def blender_adopt(self, payload: dict[str, Any]) -> ActionResult:
        project = self._project(payload)
        raw_pid = payload.get("pid")
        pid = int(raw_pid) if raw_pid is not None else None
        return self._blender_adoption_manager().request_adoption(
            project,
            pid=pid,
            wait_seconds=float(payload.get("wait_seconds", 8.0)),
            allow_blank=bool(payload.get("allow_blank", False)),
        )

    def blender_live_start(self, payload: dict[str, Any]) -> ActionResult:
        project = self._project(payload)
        live = BlenderLiveBridge(self.config, project)
        raw_blend = payload.get("blend_file") or project.blender.get("blend_file")
        blend_file = str(raw_blend) if raw_blend else None
        wait_seconds = float(
            payload.get(
                "wait_seconds",
                payload.get("timeout_seconds", 60),
            )
        )
        if not live.presence_is_fresh():
            raw_pid = payload.get("pid")
            pid = int(raw_pid) if raw_pid is not None else None
            adoption_manager = self._blender_adoption_manager()
            adoption = adoption_manager.request_adoption(
                project,
                pid=pid,
                wait_seconds=min(wait_seconds, 10.0),
                allow_blank=bool(payload.get("adopt_blank", False)),
            )
            if adoption.ok:
                data = live.status()
                return ActionResult(True, "Existing Blender window adopted by ORDAX Studio", data)
            if not bool(adoption.data.get("no_match")):
                return adoption

            instances = adoption_manager.instances()
            discovered_pids = {int(item.get("pid", -1)) for item in instances}
            blank_adoptable_pids = [
                int(item["pid"])
                for item in instances
                if not str(item.get("file") or "").strip()
                and not str(item.get("attached_project") or "").strip()
                and not bool(item.get("is_dirty"))
            ]
            if blank_adoptable_pids:
                return ActionResult(
                    False,
                    "A clean blank Blender window is available; choose its PID explicitly to adopt it instead of opening another window",
                    {
                        "project": project.slug,
                        "blank_adoptable_pids": blank_adoptable_pids,
                        "adopt_blank": True,
                        "retryable": True,
                    },
                )

            system_pids = adoption_manager.system_blender_pids()
            unmanaged_pids = [pid for pid in system_pids if pid not in discovered_pids]
            if system_pids:
                return ActionResult(
                    False,
                    "Blender is already running; refusing to open a second window",
                    {
                        "project": project.slug,
                        "blender_pids": system_pids,
                        "discovered_instances": instances,
                        "unmanaged_blender_pids": unmanaged_pids,
                        "unmanaged_blender_running": bool(unmanaged_pids),
                        "install_action": "blender.adoption_install" if unmanaged_pids else None,
                        "retryable": True,
                    },
                )

        return live.start(
            blend_file=blend_file,
            wait_seconds=wait_seconds,
        )

    def blender_live_status(self, payload: dict[str, Any]) -> ActionResult:
        live = self._blender_live(payload)
        data = live.status()
        if not bool(data.get("presence_fresh")):
            readiness_state = "not_running"
            summary = "Visible Blender live session is not running"
        elif data.get("identity_matches") is not True:
            readiness_state = "identity_mismatch"
            summary = "Visible Blender live session identity does not match this ORDAX project"
        elif data.get("protocol_compatible") is not True:
            readiness_state = "protocol_outdated"
            summary = "Visible Blender companion protocol is outdated"
        elif data.get("companion_current") is not True:
            readiness_state = "companion_outdated"
            summary = "Visible Blender companion code is outdated"
        else:
            readiness_state = "ready"
            summary = "Visible Blender live session ready"
        data["readiness_state"] = readiness_state
        return ActionResult(readiness_state == "ready", summary, data)

    def blender_live_inspect(self, payload: dict[str, Any]) -> ActionResult:
        return self._blender_live(payload).request(
            "inspect",
            {"max_objects": int(payload.get("max_objects", 200))},
            timeout_seconds=float(payload.get("timeout_seconds", 30)),
        )

    def blender_live_scene_snapshot(self, payload: dict[str, Any]) -> ActionResult:
        request = {
            "max_objects": int(payload.get("max_objects", 200)),
        }
        object_names = payload.get("object_names")
        if object_names is not None:
            if not isinstance(object_names, list) or not all(isinstance(name, str) for name in object_names):
                return ActionResult(False, "object_names must be a list of object names")
            request["object_names"] = object_names[:500]

        return self._blender_live(payload).request(
            "scene_snapshot",
            request,
            timeout_seconds=float(payload.get("timeout_seconds", 45)),
        )

    def blender_live_scene_reset(self, payload: dict[str, Any]) -> ActionResult:
        return self._blender_live(payload).request(
            "scene_reset",
            timeout_seconds=float(payload.get("timeout_seconds", 30)),
        )

    def blender_live_object_inspect(self, payload: dict[str, Any]) -> ActionResult:
        object_name = str(payload.get("object_name") or "").strip()
        object_id = str(payload.get("ordax_object_id") or "").strip()
        if bool(object_name) == bool(object_id):
            return ActionResult(False, "provide exactly one of object_name or ordax_object_id")
        request = (
            {"object_name": object_name}
            if object_name
            else {"ordax_object_id": object_id}
        )
        return self._blender_live(payload).request(
            "object_inspect",
            request,
            timeout_seconds=float(payload.get("timeout_seconds", 30)),
        )


    def blender_live_object_fingerprints(self, payload: dict[str, Any]) -> ActionResult:
        raw = payload.get("selectors")
        if not isinstance(raw, list) or not raw:
            return ActionResult(False, "selectors must be a non-empty list")
        if len(raw) > 100:
            return ActionResult(False, "fingerprint request is limited to 100 objects")

        selectors: list[dict[str, Any]] = []
        seen: set[str] = set()
        for index, item in enumerate(raw):
            if isinstance(item, str):
                item = {"object_name": item}
            if not isinstance(item, dict):
                return ActionResult(False, f"fingerprint selector {index} must be a string or object")
            object_name = str(item.get("object_name") or "").strip()
            object_id = str(item.get("ordax_object_id") or "").strip()
            if bool(object_name) == bool(object_id):
                return ActionResult(
                    False,
                    f"fingerprint selector {index} must provide exactly one of object_name or ordax_object_id",
                )
            key = f"name:{object_name}" if object_name else f"id:{object_id}"
            if key in seen:
                return ActionResult(False, f"duplicate fingerprint selector: {key}")
            seen.add(key)
            selector = (
                {"object_name": object_name}
                if object_name
                else {"ordax_object_id": object_id}
            )
            selector["evaluated"] = bool(item.get("evaluated", True))
            selectors.append(selector)

        return self._blender_live(payload).request(
            "object_fingerprints",
            {"selectors": selectors},
            timeout_seconds=float(payload.get("timeout_seconds", 60)),
        )

    def blender_live_contact_audit(self, payload: dict[str, Any]) -> ActionResult:
        pairs = payload.get("pairs")
        if not isinstance(pairs, list) or not pairs:
            return ActionResult(False, "pairs must be a non-empty list of [object_a, object_b]")
        if len(pairs) > 200:
            return ActionResult(False, "pairs is limited to 200 object pairs")

        normalized = []
        for item in pairs:
            if (
                not isinstance(item, list)
                or len(item) != 2
                or not all(isinstance(name, str) and name.strip() for name in item)
            ):
                return ActionResult(False, "each contact pair must contain exactly two non-empty object names")
            normalized.append([item[0].strip(), item[1].strip()])

        return self._blender_live(payload).request(
            "contact_audit",
            {"pairs": normalized},
            timeout_seconds=float(payload.get("timeout_seconds", 60)),
        )


    def blender_live_quality_gate(self, payload: dict[str, Any]) -> ActionResult:
        checks = payload.get("checks")
        if not isinstance(checks, list) or not checks:
            return ActionResult(False, "checks must be a non-empty list")
        if len(checks) > 100:
            return ActionResult(False, "quality gate is limited to 100 checks")

        supported = {"dimensions", "symmetry", "proportion", "containment", "mesh_quality", "uv_quality"}
        normalized: list[dict[str, Any]] = []
        for index, check in enumerate(checks):
            if not isinstance(check, dict):
                return ActionResult(False, f"quality check {index} must be an object")
            if any(key in check for key in ("passed", "ok", "result")):
                return ActionResult(
                    False,
                    f"quality check {index} cannot provide its own completion claim",
                )
            kind = str(check.get("type") or "").strip().lower()
            if kind not in supported:
                return ActionResult(
                    False,
                    f"quality check {index} type must be one of: {', '.join(sorted(supported))}",
                )
            if kind == "mesh_quality":
                diagnostic_limit = check.get("diagnostic_limit", 8)
                if (
                    not isinstance(diagnostic_limit, int)
                    or isinstance(diagnostic_limit, bool)
                    or diagnostic_limit < 0
                    or diagnostic_limit > 16
                ):
                    return ActionResult(
                        False,
                        f"quality check {index} diagnostic_limit must be an "
                        "integer between 0 and 16",
                    )
            normalized.append({**check, "type": kind})

        return self._blender_live(payload).request(
            "quality_gate",
            {"checks": normalized},
            timeout_seconds=float(payload.get("timeout_seconds", 60)),
        )

    def blender_live_modeling_schema(self, payload: dict[str, Any]) -> ActionResult:
        return ActionResult(
            True,
            "Typed Blender modeling contracts",
            {
                "protocol_version": 9,
                "tools": modeling_schemas(),
                "mutation_policy": {
                    "object_transform": "available",
                    "create_primitive": "available",
                    "surface_scatter": modeling_schemas()["surface_scatter"]["status"],
                    "boolean_cut_preview": modeling_schemas()["boolean_cut_preview"]["status"],
                    "mesh_cleanup": modeling_schemas()["mesh_cleanup"]["status"],
                    "degenerate_repair_preview": modeling_schemas()["degenerate_repair_preview"]["status"],
                    "merge_by_distance_preview": modeling_schemas()["merge_by_distance_preview"]["status"],
                    "boundary_hole_fill_preview": modeling_schemas()["boundary_hole_fill_preview"]["status"],
                    "add_modifier": "available",
                },
            },
        )

    def blender_live_modeling_plan(self, payload: dict[str, Any]) -> ActionResult:
        operation = payload.get("operation")
        arguments = {
            key: value
            for key, value in payload.items()
            if key not in {"operation"}
        }
        try:
            plan = plan_modeling_operation(operation, arguments)
        except ValueError as error:
            return ActionResult(False, str(error))
        return ActionResult(
            True,
            "Typed Blender modeling plan",
            {
                **plan,
                "execution": (
                    "available"
                    if plan["executable"]
                    else "disabled_pending_real_blender_smoke"
                ),
            },
        )

    def blender_live_object_transform(self, payload: dict[str, Any]) -> ActionResult:
        try:
            plan = plan_modeling_operation("object_transform", payload)
        except ValueError as error:
            return ActionResult(False, str(error))

        return self._blender_live(payload).request(
            "object_transform",
            plan["arguments"],
            timeout_seconds=float(payload.get("timeout_seconds", 30)),
        )

    def blender_live_object_remove(self, payload: dict[str, Any]) -> ActionResult:
        supported = {"project", "timeout_seconds", "object_names", "missing_ok"}
        unsupported = sorted(set(payload) - supported)
        if unsupported:
            return ActionResult(False, "unsupported field(s): " + ", ".join(unsupported))

        object_names = payload.get("object_names")
        if (
            not isinstance(object_names, list)
            or not object_names
            or len(object_names) > 64
            or not all(isinstance(name, str) and name.strip() for name in object_names)
        ):
            return ActionResult(
                False,
                "object_names must be a non-empty list of at most 64 object names",
            )
        normalized: list[str] = []
        for raw_name in object_names:
            name = raw_name.strip()
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_. +\-]{0,126}", name):
                return ActionResult(False, f"object name contains unsupported characters: {name}")
            if name not in normalized:
                normalized.append(name)

        missing_ok = payload.get("missing_ok", False)
        if not isinstance(missing_ok, bool):
            return ActionResult(False, "missing_ok must be boolean")

        return self._blender_live(payload).request(
            "object_remove",
            {"object_names": normalized, "missing_ok": missing_ok},
            timeout_seconds=float(payload.get("timeout_seconds", 60)),
        )

    def blender_live_extract_region(self, payload: dict[str, Any]) -> ActionResult:
        supported = {
            "project",
            "timeout_seconds",
            "axis",
            "minimum",
            "maximum",
            "translate",
            "keep_names",
        }
        unsupported = sorted(set(payload) - supported)
        if unsupported:
            return ActionResult(False, "unsupported field(s): " + ", ".join(unsupported))

        axis = str(payload.get("axis") or "X").strip().upper()
        if axis not in {"X", "Y", "Z"}:
            return ActionResult(False, "axis must be X, Y, or Z")

        def _number(value: Any, field: str) -> float:
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"{field} must be numeric")
            result = float(value)
            if not math.isfinite(result) or abs(result) > 100000.0:
                raise ValueError(f"{field} is outside the allowed range")
            return result

        try:
            minimum = _number(payload.get("minimum"), "minimum")
            maximum = _number(payload.get("maximum"), "maximum")
        except ValueError as error:
            return ActionResult(False, str(error))
        if maximum <= minimum:
            return ActionResult(False, "maximum must be greater than minimum")

        raw_translate = payload.get("translate", [0.0, 0.0, 0.0])
        if not isinstance(raw_translate, list) or len(raw_translate) != 3:
            return ActionResult(False, "translate must be a list of three numbers")
        try:
            translate = [_number(value, "translate") for value in raw_translate]
        except ValueError as error:
            return ActionResult(False, str(error))

        raw_keep_names = payload.get("keep_names", [])
        if (
            not isinstance(raw_keep_names, list)
            or len(raw_keep_names) > 64
            or not all(isinstance(name, str) and name.strip() for name in raw_keep_names)
        ):
            return ActionResult(
                False,
                "keep_names must be a list of at most 64 non-empty object names",
            )
        keep_names: list[str] = []
        for raw_name in raw_keep_names:
            name = raw_name.strip()
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_. +\-]{0,126}", name):
                return ActionResult(False, f"keep name contains unsupported characters: {name}")
            if name not in keep_names:
                keep_names.append(name)

        return self._blender_live(payload).request(
            "extract_region",
            {
                "axis": axis,
                "minimum": minimum,
                "maximum": maximum,
                "translate": translate,
                "keep_names": keep_names,
            },
            timeout_seconds=float(payload.get("timeout_seconds", 180)),
        )

    def blender_live_create_box_with_cutouts(self, payload: dict[str, Any]) -> ActionResult:
        supported = {
            "project",
            "timeout_seconds",
            "name",
            "location",
            "dimensions",
            "cutouts",
        }
        unsupported = sorted(set(payload) - supported)
        if unsupported:
            return ActionResult(False, "unsupported field(s): " + ", ".join(unsupported))

        try:
            name = _presentation_name(payload.get("name"), "name")
            location = _presentation_vector(payload.get("location"), "location")
            dimensions = _presentation_vector(
                payload.get("dimensions"),
                "dimensions",
                minimum=0.001,
                maximum=1000.0,
            )
        except ValueError as error:
            return ActionResult(False, str(error))

        raw_cutouts = payload.get("cutouts")
        if not isinstance(raw_cutouts, list) or not (1 <= len(raw_cutouts) <= 8):
            return ActionResult(False, "cutouts must contain 1 to 8 entries")

        cutouts: list[dict[str, Any]] = []
        for index, raw in enumerate(raw_cutouts):
            if not isinstance(raw, dict):
                return ActionResult(False, f"cutouts[{index}] must be an object")
            unknown = sorted(set(raw) - {"offset", "dimensions"})
            if unknown:
                return ActionResult(
                    False,
                    f"cutouts[{index}] unsupported field(s): " + ", ".join(unknown),
                )
            try:
                offset = _presentation_vector(
                    raw.get("offset"),
                    f"cutouts[{index}].offset",
                )
                cut_dimensions = _presentation_vector(
                    raw.get("dimensions"),
                    f"cutouts[{index}].dimensions",
                    minimum=0.001,
                    maximum=1000.0,
                )
            except ValueError as error:
                return ActionResult(False, str(error))
            cutouts.append({"offset": offset, "dimensions": cut_dimensions})

        return self._blender_live(payload).request(
            "create_box_with_cutouts",
            {
                "name": name,
                "location": location,
                "dimensions": dimensions,
                "cutouts": cutouts,
            },
            timeout_seconds=float(payload.get("timeout_seconds", 120)),
        )

    def blender_live_cleanup_orphans(self, payload: dict[str, Any]) -> ActionResult:
        supported = {
            "project",
            "timeout_seconds",
            "remove_empty_collections",
        }
        unsupported = sorted(set(payload) - supported)
        if unsupported:
            return ActionResult(False, "unsupported field(s): " + ", ".join(unsupported))

        remove_empty_collections = payload.get("remove_empty_collections", False)
        if not isinstance(remove_empty_collections, bool):
            return ActionResult(False, "remove_empty_collections must be boolean")

        return self._blender_live(payload).request(
            "cleanup_orphans",
            {"remove_empty_collections": remove_empty_collections},
            timeout_seconds=float(payload.get("timeout_seconds", 120)),
        )

    def blender_live_create_primitive(self, payload: dict[str, Any]) -> ActionResult:
        try:
            plan = plan_modeling_operation("create_primitive", payload)
        except ValueError as error:
            return ActionResult(False, str(error))

        return self._blender_live(payload).request(
            "create_primitive",
            plan["arguments"],
            timeout_seconds=float(payload.get("timeout_seconds", 30)),
        )

    def blender_live_add_modifier(self, payload: dict[str, Any]) -> ActionResult:
        try:
            plan = plan_modeling_operation("add_modifier", payload)
        except ValueError as error:
            return ActionResult(False, str(error))

        if not plan["executable"]:
            return ActionResult(
                False,
                f"{plan['status']}: modifier variant is not enabled for execution",
                {"plan": plan},
            )

        return self._blender_live(payload).request(
            "add_modifier",
            plan["arguments"],
            timeout_seconds=float(payload.get("timeout_seconds", 30)),
        )

    def blender_live_surface_scatter(self, payload: dict[str, Any]) -> ActionResult:
        try:
            plan = plan_modeling_operation("surface_scatter", payload)
        except ValueError as error:
            return ActionResult(False, str(error))
        if not plan["executable"]:
            return ActionResult(
                False,
                f"{plan['status']}: surface scatter is not enabled for execution",
                {"plan": plan},
            )
        return self._blender_live(payload).request(
            "surface_scatter",
            plan["arguments"],
            timeout_seconds=float(payload.get("timeout_seconds", 60)),
        )

    def blender_live_boolean_cut_preview(self, payload: dict[str, Any]) -> ActionResult:
        try:
            plan = plan_modeling_operation("boolean_cut_preview", payload)
        except ValueError as error:
            return ActionResult(False, str(error))
        if not plan["executable"]:
            return ActionResult(
                False,
                f"{plan['status']}: boolean cutter preview is not enabled for execution",
                {"plan": plan},
            )
        return self._blender_live(payload).request(
            "boolean_cut_preview",
            plan["arguments"],
            timeout_seconds=float(payload.get("timeout_seconds", 60)),
        )

    def _boolean_cut_transition(self, payload: dict[str, Any], operation: str) -> ActionResult:
        supported = {"project", "timeout_seconds", "preview_id"}
        unsupported = sorted(set(payload) - supported)
        if unsupported:
            return ActionResult(False, "unsupported field(s): " + ", ".join(unsupported))
        preview_id = str(payload.get("preview_id") or "").strip()
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", preview_id):
            return ActionResult(False, "preview_id must be a bounded identifier")
        return self._blender_live(payload).request(
            operation,
            {"preview_id": preview_id},
            timeout_seconds=float(payload.get("timeout_seconds", 30)),
        )

    def blender_live_boolean_cut_commit(self, payload: dict[str, Any]) -> ActionResult:
        return self._boolean_cut_transition(payload, "boolean_cut_commit")

    def blender_live_boolean_cut_cancel(self, payload: dict[str, Any]) -> ActionResult:
        return self._boolean_cut_transition(payload, "boolean_cut_cancel")

    def blender_live_mesh_cleanup(self, payload: dict[str, Any]) -> ActionResult:
        try:
            plan = plan_modeling_operation("mesh_cleanup", payload)
        except ValueError as error:
            return ActionResult(False, str(error))
        if not plan["executable"]:
            return ActionResult(
                False,
                f"{plan['status']}: mesh cleanup is not enabled for execution",
                {"plan": plan},
            )
        return self._blender_live(payload).request(
            "mesh_cleanup",
            plan["arguments"],
            timeout_seconds=float(payload.get("timeout_seconds", 60)),
        )

    def blender_live_degenerate_repair_preview(self, payload: dict[str, Any]) -> ActionResult:
        try:
            plan = plan_modeling_operation("degenerate_repair_preview", payload)
        except ValueError as error:
            return ActionResult(False, str(error))
        if not plan["executable"]:
            return ActionResult(
                False,
                f"{plan['status']}: degenerate repair preview is not enabled for execution",
                {"plan": plan},
            )
        return self._blender_live(payload).request(
            "degenerate_repair_preview",
            plan["arguments"],
            timeout_seconds=float(payload.get("timeout_seconds", 60)),
        )

    def _mesh_repair_transition(self, payload: dict[str, Any], operation: str) -> ActionResult:
        supported = {"project", "timeout_seconds", "preview_id"}
        unsupported = sorted(set(payload) - supported)
        if unsupported:
            return ActionResult(False, "unsupported field(s): " + ", ".join(unsupported))
        preview_id = str(payload.get("preview_id") or "").strip()
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", preview_id):
            return ActionResult(False, "preview_id must be a bounded identifier")
        return self._blender_live(payload).request(
            operation,
            {"preview_id": preview_id},
            timeout_seconds=float(payload.get("timeout_seconds", 30)),
        )

    def blender_live_degenerate_repair_commit(self, payload: dict[str, Any]) -> ActionResult:
        return self._mesh_repair_transition(payload, "degenerate_repair_commit")

    def blender_live_degenerate_repair_cancel(self, payload: dict[str, Any]) -> ActionResult:
        return self._mesh_repair_transition(payload, "degenerate_repair_cancel")

    def blender_live_merge_by_distance_preview(self, payload: dict[str, Any]) -> ActionResult:
        try:
            plan = plan_modeling_operation("merge_by_distance_preview", payload)
        except ValueError as error:
            return ActionResult(False, str(error))
        if not plan["executable"]:
            return ActionResult(
                False,
                f"{plan['status']}: merge-by-distance preview is not enabled for execution",
                {"plan": plan},
            )
        return self._blender_live(payload).request(
            "merge_by_distance_preview",
            plan["arguments"],
            timeout_seconds=float(payload.get("timeout_seconds", 60)),
        )

    def blender_live_merge_by_distance_commit(self, payload: dict[str, Any]) -> ActionResult:
        return self._mesh_repair_transition(payload, "merge_by_distance_commit")

    def blender_live_merge_by_distance_cancel(self, payload: dict[str, Any]) -> ActionResult:
        return self._mesh_repair_transition(payload, "merge_by_distance_cancel")

    def blender_live_boundary_hole_fill_preview(self, payload: dict[str, Any]) -> ActionResult:
        try:
            plan = plan_modeling_operation("boundary_hole_fill_preview", payload)
        except ValueError as error:
            return ActionResult(False, str(error))
        if not plan["executable"]:
            return ActionResult(
                False,
                f"{plan['status']}: boundary hole-fill preview is not enabled for execution",
                {"plan": plan},
            )
        return self._blender_live(payload).request(
            "boundary_hole_fill_preview",
            plan["arguments"],
            timeout_seconds=float(payload.get("timeout_seconds", 60)),
        )

    def blender_live_boundary_hole_fill_commit(self, payload: dict[str, Any]) -> ActionResult:
        return self._mesh_repair_transition(payload, "boundary_hole_fill_commit")

    def blender_live_boundary_hole_fill_cancel(self, payload: dict[str, Any]) -> ActionResult:
        return self._mesh_repair_transition(payload, "boundary_hole_fill_cancel")

    def blender_live_material_apply(self, payload: dict[str, Any]) -> ActionResult:
        try:
            arguments = normalize_material_request(payload)
        except ValueError as error:
            return ActionResult(False, str(error))

        return self._blender_live(payload).request(
            "material_apply",
            arguments,
            timeout_seconds=float(payload.get("timeout_seconds", 30)),
        )

    def blender_live_create_camera(self, payload: dict[str, Any]) -> ActionResult:
        try:
            arguments = _normalize_camera_request(payload)
        except ValueError as error:
            return ActionResult(False, str(error))
        return self._blender_live(payload).request(
            "create_camera",
            arguments,
            timeout_seconds=float(payload.get("timeout_seconds", 30)),
        )

    def blender_live_create_light(self, payload: dict[str, Any]) -> ActionResult:
        try:
            arguments = _normalize_light_request(payload)
        except ValueError as error:
            return ActionResult(False, str(error))
        return self._blender_live(payload).request(
            "create_light",
            arguments,
            timeout_seconds=float(payload.get("timeout_seconds", 30)),
        )

    def blender_live_scene_presentation(self, payload: dict[str, Any]) -> ActionResult:
        try:
            arguments = _normalize_scene_presentation_request(payload)
        except ValueError as error:
            return ActionResult(False, str(error))
        return self._blender_live(payload).request(
            "scene_presentation",
            arguments,
            timeout_seconds=float(payload.get("timeout_seconds", 30)),
        )

    def blender_live_import_asset(self, payload: dict[str, Any]) -> ActionResult:
        supported = {"project", "source_path", "object_name", "timeout_seconds"}
        unsupported = sorted(set(payload) - supported)
        if unsupported:
            return ActionResult(False, "unsupported field(s): " + ", ".join(unsupported))

        source, candidates, error = _resolve_local_import_source(payload)
        if error:
            return ActionResult(
                False,
                error,
                {"candidates": candidates} if candidates is not None else {},
            )
        assert source is not None

        object_name = payload.get("object_name")
        if object_name is not None:
            if not isinstance(object_name, str):
                return ActionResult(False, "object_name must be a string")
            object_name = object_name.strip()
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_. -]{0,126}", object_name):
                return ActionResult(False, "object_name contains unsupported characters")

        try:
            timeout = float(payload.get("timeout_seconds", 900))
        except (TypeError, ValueError):
            return ActionResult(False, "timeout_seconds must be numeric")
        timeout = max(10.0, min(timeout, 1800.0))

        request = {
            "source_path": str(source),
            "source_size_bytes": source.stat().st_size,
        }
        if object_name:
            request["object_name"] = object_name

        return self._blender_live(payload).request(
            "import_asset",
            request,
            timeout_seconds=timeout,
        )

    def blender_live_animate_transform(self, payload: dict[str, Any]) -> ActionResult:
        try:
            arguments = _normalize_transform_animation_request(payload)
        except ValueError as error:
            return ActionResult(False, str(error))

        return self._blender_live(payload).request(
            "animate_transform",
            arguments,
            timeout_seconds=float(payload.get("timeout_seconds", 60)),
        )


    def blender_live_viewport_proxy(self, payload: dict[str, Any]) -> ActionResult:
        supported = {
            "project",
            "timeout_seconds",
            "object_name",
            "ordax_object_id",
            "target_faces",
            "enabled",
        }
        unsupported = sorted(set(payload) - supported)
        if unsupported:
            return ActionResult(False, "unsupported field(s): " + ", ".join(unsupported))

        try:
            selector = normalize_object_selector(payload)
        except ValueError as error:
            return ActionResult(False, str(error))

        enabled = payload.get("enabled", True)
        if not isinstance(enabled, bool):
            return ActionResult(False, "enabled must be boolean")

        target_faces = payload.get("target_faces", 150000)
        if (
            isinstance(target_faces, bool)
            or not isinstance(target_faces, int)
            or target_faces < 10000
            or target_faces > 500000
        ):
            return ActionResult(
                False,
                "target_faces must be an integer between 10000 and 500000",
            )

        return self._blender_live(payload).request(
            "viewport_proxy",
            {
                **selector,
                "target_faces": target_faces,
                "enabled": enabled,
            },
            timeout_seconds=float(payload.get("timeout_seconds", 90)),
        )


    def blender_live_bake_work_proxy(self, payload: dict[str, Any]) -> ActionResult:
        supported = {
            "project",
            "timeout_seconds",
            "object_name",
            "ordax_object_id",
            "proxy_name",
            "target_faces",
            "remove_source",
        }
        unsupported = sorted(set(payload) - supported)
        if unsupported:
            return ActionResult(False, "unsupported field(s): " + ", ".join(unsupported))

        try:
            selector = normalize_object_selector(payload)
        except ValueError as error:
            return ActionResult(False, str(error))

        proxy_name = payload.get("proxy_name")
        if not isinstance(proxy_name, str):
            return ActionResult(False, "proxy_name must be a string")
        proxy_name = proxy_name.strip()
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_. -]{0,126}", proxy_name):
            return ActionResult(False, "proxy_name contains unsupported characters")

        target_faces = payload.get("target_faces", 120000)
        if (
            isinstance(target_faces, bool)
            or not isinstance(target_faces, int)
            or target_faces < 10000
            or target_faces > 500000
        ):
            return ActionResult(
                False,
                "target_faces must be an integer between 10000 and 500000",
            )

        remove_source = payload.get("remove_source", False)
        if not isinstance(remove_source, bool):
            return ActionResult(False, "remove_source must be boolean")

        return self._blender_live(payload).request(
            "bake_work_proxy",
            {
                **selector,
                "proxy_name": proxy_name,
                "target_faces": target_faces,
                "remove_source": remove_source,
            },
            timeout_seconds=float(payload.get("timeout_seconds", 300)),
        )


    def blender_live_batch(self, payload: dict[str, Any]) -> ActionResult:
        supported = {"project", "steps", "stop_on_error"}
        unsupported = sorted(set(payload) - supported)
        if unsupported:
            return ActionResult(False, "unsupported field(s): " + ", ".join(unsupported))

        project = self._project(payload)
        steps = payload.get("steps")
        if not isinstance(steps, list) or not (1 <= len(steps) <= 128):
            return ActionResult(False, "steps must be a list containing 1 to 128 entries")
        stop_on_error = payload.get("stop_on_error", True)
        if not isinstance(stop_on_error, bool):
            return ActionResult(False, "stop_on_error must be boolean")

        handlers = {
            "object_transform": self.blender_live_object_transform,
            "object_remove": self.blender_live_object_remove,
            "extract_region": self.blender_live_extract_region,
            "cleanup_orphans": self.blender_live_cleanup_orphans,
            "create_primitive": self.blender_live_create_primitive,
            "create_box_with_cutouts": self.blender_live_create_box_with_cutouts,
            "add_modifier": self.blender_live_add_modifier,
            "surface_scatter": self.blender_live_surface_scatter,
            "boolean_cut_preview": self.blender_live_boolean_cut_preview,
            "boolean_cut_commit": self.blender_live_boolean_cut_commit,
            "boolean_cut_cancel": self.blender_live_boolean_cut_cancel,
            "mesh_cleanup": self.blender_live_mesh_cleanup,
            "degenerate_repair_preview": self.blender_live_degenerate_repair_preview,
            "degenerate_repair_commit": self.blender_live_degenerate_repair_commit,
            "degenerate_repair_cancel": self.blender_live_degenerate_repair_cancel,
            "merge_by_distance_preview": self.blender_live_merge_by_distance_preview,
            "merge_by_distance_commit": self.blender_live_merge_by_distance_commit,
            "merge_by_distance_cancel": self.blender_live_merge_by_distance_cancel,
            "boundary_hole_fill_preview": self.blender_live_boundary_hole_fill_preview,
            "boundary_hole_fill_commit": self.blender_live_boundary_hole_fill_commit,
            "boundary_hole_fill_cancel": self.blender_live_boundary_hole_fill_cancel,
            "material_apply": self.blender_live_material_apply,
            "create_camera": self.blender_live_create_camera,
            "create_light": self.blender_live_create_light,
            "scene_presentation": self.blender_live_scene_presentation,
            "animate_transform": self.blender_live_animate_transform,
            "viewport_proxy": self.blender_live_viewport_proxy,
            "bake_work_proxy": self.blender_live_bake_work_proxy,
            "checkpoint_create": self.blender_live_checkpoint_create,
            "object_metadata": self.blender_live_object_metadata,
            "scene_snapshot": self.blender_live_scene_snapshot,
            "save": self.blender_live_save,
        }

        results: list[dict[str, Any]] = []
        last_data: dict[str, Any] = {}
        failed_index: int | None = None
        for index, raw_step in enumerate(steps):
            if not isinstance(raw_step, dict):
                return ActionResult(False, f"steps[{index}] must be an object")
            unknown = sorted(set(raw_step) - {"op", "payload"})
            if unknown:
                return ActionResult(
                    False,
                    f"steps[{index}] unsupported field(s): " + ", ".join(unknown),
                )
            op = str(raw_step.get("op") or "").strip()
            handler = handlers.get(op)
            if handler is None:
                return ActionResult(
                    False,
                    f"steps[{index}].op is not allowed in blender.live_batch: {op}",
                )
            step_payload = raw_step.get("payload") or {}
            if not isinstance(step_payload, dict):
                return ActionResult(False, f"steps[{index}].payload must be an object")
            if "project" in step_payload and step_payload.get("project") != project.slug:
                return ActionResult(False, f"steps[{index}].payload project must match the batch project")
            step_payload = {**step_payload, "project": project.slug}
            result = handler(step_payload)
            compact = {
                "index": index,
                "op": op,
                "ok": result.ok,
                "summary": result.summary,
            }
            if not result.ok:
                compact["data"] = result.data
                failed_index = index
            elif isinstance(result.data, dict):
                last_data = result.data
                obj = result.data.get("object")
                if isinstance(obj, dict) and isinstance(obj.get("name"), str):
                    compact["object_name"] = obj["name"]
                if isinstance(result.data.get("file"), str):
                    compact["file"] = result.data["file"]
            results.append(compact)
            if not result.ok and stop_on_error:
                break

        ok = failed_index is None
        return ActionResult(
            ok,
            "Blender live batch completed"
            if ok
            else f"Blender live batch stopped at step {failed_index}",
            {
                "project": project.slug,
                "requested_steps": len(steps),
                "completed_steps": len(results),
                "failed_index": failed_index,
                "results": results,
                "last_data": last_data,
            },
        )


    def blender_live_object_metadata(self, payload: dict[str, Any]) -> ActionResult:
        object_name = str(payload.get("object_name") or "").strip()
        object_id = str(payload.get("ordax_object_id") or "").strip()
        if bool(object_name) == bool(object_id):
            return ActionResult(False, "provide exactly one of object_name or ordax_object_id")

        metadata = payload.get("metadata")
        if not isinstance(metadata, dict) or not metadata:
            return ActionResult(False, "metadata must be a non-empty object")

        request = {
            "metadata": metadata,
        }
        if object_name:
            request["object_name"] = object_name
        else:
            request["ordax_object_id"] = object_id

        return self._blender_live(payload).request(
            "object_metadata",
            request,
            timeout_seconds=float(payload.get("timeout_seconds", 30)),
        )


    def blender_live_api_schema(self, payload: dict[str, Any]) -> ActionResult:
        type_name = str(payload.get("type_name") or "").strip()
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,127}", type_name):
            return ActionResult(False, "type_name must be a bpy.types class name")
        return self._blender_live(payload).request(
            "api_schema",
            {
                "type_name": type_name,
                "max_properties": int(payload.get("max_properties", 200)),
            },
            timeout_seconds=float(payload.get("timeout_seconds", 30)),
        )

    def blender_live_api_lookup(self, payload: dict[str, Any]) -> ActionResult:
        query = str(payload.get("query") or "").strip()
        if not query or len(query) > 300:
            return ActionResult(
                False,
                "query is required and must be at most 300 characters",
            )
        return self._blender_live(payload).request(
            "api_lookup",
            {"query": query},
            timeout_seconds=float(payload.get("timeout_seconds", 30)),
        )

    def blender_live_node_schema(self, payload: dict[str, Any]) -> ActionResult:
        node_type = str(payload.get("node_type") or "").strip()
        tree_type = str(payload.get("tree_type") or "ShaderNodeTree").strip()
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,127}", node_type):
            return ActionResult(False, "node_type must be a Blender node bl_idname")
        if tree_type not in {"ShaderNodeTree", "GeometryNodeTree", "CompositorNodeTree"}:
            return ActionResult(
                False,
                "tree_type must be ShaderNodeTree, GeometryNodeTree, or CompositorNodeTree",
            )
        overrides = payload.get("property_overrides") or {}
        if not isinstance(overrides, dict) or len(overrides) > 30:
            return ActionResult(
                False,
                "property_overrides must be an object with at most 30 entries",
            )
        for key, value in overrides.items():
            if (
                not isinstance(key, str)
                or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key)
                or not isinstance(value, (str, int, float, bool))
            ):
                return ActionResult(
                    False,
                    "property_overrides must contain scalar values under valid property names",
                )

        return self._blender_live(payload).request(
            "node_schema",
            {
                "node_type": node_type,
                "tree_type": tree_type,
                "property_overrides": overrides,
            },
            timeout_seconds=float(payload.get("timeout_seconds", 30)),
        )

    def blender_live_export(self, payload: dict[str, Any]) -> ActionResult:
        project = self._project(payload)
        raw_output = str(payload.get("output_path") or "").strip()
        if not raw_output:
            return ActionResult(False, "output_path is required")
        output = project.path(raw_output, must_exist=False)
        export_format = str(
            payload.get("format") or output.suffix.lstrip(".")
        ).strip().lower()
        expected = {"glb": ".glb", "fbx": ".fbx"}.get(export_format)
        if expected is None:
            return ActionResult(False, "format must be glb or fbx")
        if output.suffix.lower() != expected:
            return ActionResult(False, f"output_path must end with {expected}")

        object_names = payload.get("object_names")
        if object_names is not None and (
            not isinstance(object_names, list)
            or len(object_names) > 500
            or not all(isinstance(name, str) and name.strip() for name in object_names)
        ):
            return ActionResult(
                False,
                "object_names must be a list of at most 500 object names",
            )

        request = {
            "output_path": str(output),
            "format": export_format,
            "selected_only": bool(payload.get("selected_only", False)),
            "animations": bool(payload.get("animations", True)),
            "apply_modifiers": bool(payload.get("apply_modifiers", True)),
        }
        if object_names is not None:
            request["object_names"] = [name.strip() for name in object_names]

        return self._blender_live(payload).request(
            "export_scene",
            request,
            timeout_seconds=float(payload.get("timeout_seconds", 180)),
        )



    def blender_export_headless(self, payload: dict[str, Any]) -> ActionResult:
        blender = find_blender()
        if blender is None:
            return ActionResult(False, "Blender executable not found")

        project = self._project(payload)
        raw_blend = str(payload.get("blend_file") or "").strip()
        if not raw_blend:
            return ActionResult(False, "blend_file is required")
        try:
            blend_file = project.path(raw_blend)
        except FileNotFoundError:
            return ActionResult(False, "blend_file must be an existing .blend file")
        if blend_file.suffix.lower() != ".blend" or not blend_file.is_file():
            return ActionResult(False, "blend_file must be an existing .blend file")

        raw_output = str(payload.get("output_path") or "").strip()
        if not raw_output:
            return ActionResult(False, "output_path is required")
        output = project.path(raw_output, must_exist=False)
        export_format = str(
            payload.get("format") or output.suffix.lstrip(".")
        ).strip().lower()
        expected = {"glb": ".glb", "fbx": ".fbx"}.get(export_format)
        if expected is None:
            return ActionResult(False, "format must be glb or fbx")
        if output.suffix.lower() != expected:
            return ActionResult(False, f"output_path must end with {expected}")

        object_names = payload.get("object_names")
        if object_names is not None and (
            not isinstance(object_names, list)
            or len(object_names) > 500
            or not all(isinstance(name, str) and name.strip() for name in object_names)
        ):
            return ActionResult(
                False,
                "object_names must be a list of at most 500 object names",
            )

        timeout = int(payload.get("timeout_seconds", 300))
        if timeout < 10 or timeout > 3600:
            return ActionResult(False, "timeout_seconds must be between 10 and 3600")

        helper = (
            Path(__file__).resolve().parent
            / "assets"
            / "blender_export_headless.py"
        )
        if not helper.is_file():
            return ActionResult(False, f"headless Blender export helper missing: {helper}")

        output.parent.mkdir(parents=True, exist_ok=True)
        try:
            output.unlink(missing_ok=True)
        except OSError as error:
            return ActionResult(False, f"could not clear previous export: {error}")

        command = [
            str(blender),
            "--background",
            str(blend_file),
            "--disable-autoexec",
            "--python-exit-code",
            "1",
            "--python",
            str(helper),
            "--",
            "--output",
            str(output),
            "--format",
            export_format,
        ]
        if bool(payload.get("selected_only", True)):
            command.append("--selected-only")
        if bool(payload.get("animations", False)):
            command.append("--animations")
        if bool(payload.get("apply_modifiers", True)):
            command.append("--apply-modifiers")
        if object_names is not None:
            for name in object_names:
                command.extend(["--object-name", name.strip()])

        result = _run(
            command,
            cwd=project.root,
            timeout=timeout,
        )
        if not result.ok:
            result.summary = (
                "Headless Blender export timed out"
                if result.data.get("timed_out")
                else "Headless Blender export failed"
            )
            return result

        if not output.is_file() or output.stat().st_size <= 0:
            return ActionResult(
                False,
                "Headless Blender export completed without a non-empty artifact",
                {
                    **result.data,
                    "artifact": str(output),
                },
            )

        digest = hashlib.sha256(output.read_bytes()).hexdigest()
        return ActionResult(
            True,
            "Headless Blender export completed",
            {
                **result.data,
                "artifact": str(output),
                "format": export_format,
                "size_bytes": output.stat().st_size,
                "sha256": digest,
                "blend_file": str(blend_file),
                "selection_only": bool(payload.get("selected_only", True)),
                "isolated_process": True,
            },
        )

    def blender_extract_region_headless(self, payload: dict[str, Any]) -> ActionResult:
        supported = {
            "project",
            "blend_file",
            "output_path",
            "axis",
            "minimum",
            "maximum",
            "translate",
            "cleanup_orphans",
            "overwrite",
            "timeout_seconds",
        }
        unsupported = sorted(set(payload) - supported)
        if unsupported:
            return ActionResult(False, "unsupported field(s): " + ", ".join(unsupported))

        project = self._project(payload)

        raw_blend = str(payload.get("blend_file") or "").strip()
        if not raw_blend:
            return ActionResult(False, "blend_file is required")
        try:
            blend_file = project.path(raw_blend)
        except FileNotFoundError:
            return ActionResult(False, "blend_file must be an existing .blend file")
        if blend_file.suffix.lower() != ".blend" or not blend_file.is_file():
            return ActionResult(False, "blend_file must be an existing .blend file")

        raw_output = str(payload.get("output_path") or "").strip()
        if not raw_output:
            return ActionResult(False, "output_path is required")
        output = project.path(raw_output, must_exist=False)
        if output.suffix.lower() != ".blend":
            return ActionResult(False, "output_path must end with .blend")
        if output.resolve() == blend_file.resolve():
            return ActionResult(False, "output_path must differ from blend_file")

        axis = str(payload.get("axis") or "X").strip().upper()
        if axis not in {"X", "Y", "Z"}:
            return ActionResult(False, "axis must be X, Y, or Z")

        try:
            minimum = _presentation_number(
                payload.get("minimum"),
                "minimum",
                minimum=-100000.0,
                maximum=100000.0,
            )
            maximum = _presentation_number(
                payload.get("maximum"),
                "maximum",
                minimum=-100000.0,
                maximum=100000.0,
            )
            translate = _presentation_vector(
                payload.get("translate", [0.0, 0.0, 0.0]),
                "translate",
                minimum=-100000.0,
                maximum=100000.0,
            )
        except ValueError as error:
            return ActionResult(False, str(error))
        if maximum <= minimum:
            return ActionResult(False, "maximum must be greater than minimum")

        cleanup_orphans = payload.get("cleanup_orphans", True)
        overwrite = payload.get("overwrite", False)
        if not isinstance(cleanup_orphans, bool):
            return ActionResult(False, "cleanup_orphans must be boolean")
        if not isinstance(overwrite, bool):
            return ActionResult(False, "overwrite must be boolean")
        if output.exists() and not overwrite:
            return ActionResult(False, f"output_path already exists: {output}")

        timeout = payload.get("timeout_seconds", 1800)
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
            return ActionResult(False, "timeout_seconds must be numeric")
        timeout = int(timeout)
        if timeout < 30 or timeout > 7200:
            return ActionResult(False, "timeout_seconds must be between 30 and 7200")

        blender = find_blender()
        if blender is None:
            return ActionResult(False, "Blender executable not found")

        helper = (
            Path(__file__).resolve().parent
            / "assets"
            / "blender_extract_region_headless.py"
        )
        if not helper.is_file():
            return ActionResult(
                False,
                f"headless Blender extraction helper missing: {helper}",
            )

        output.parent.mkdir(parents=True, exist_ok=True)
        if overwrite:
            try:
                output.unlink(missing_ok=True)
            except OSError as error:
                return ActionResult(False, f"could not clear previous output: {error}")

        report_root = self.config.state_dir / "blender-headless-extract"
        report_root.mkdir(parents=True, exist_ok=True)
        report = report_root / f"{uuid.uuid4().hex}.json"

        command = [
            str(blender),
            "--background",
            str(blend_file),
            "--disable-autoexec",
            "--python-exit-code",
            "1",
            "--python",
            str(helper),
            "--",
            "--output",
            str(output),
            "--report",
            str(report),
            "--axis",
            axis,
            "--minimum",
            str(minimum),
            "--maximum",
            str(maximum),
            "--translate-x",
            str(translate[0]),
            "--translate-y",
            str(translate[1]),
            "--translate-z",
            str(translate[2]),
        ]
        if cleanup_orphans:
            command.append("--cleanup-orphans")

        result = _run(
            command,
            cwd=project.root,
            timeout=timeout,
        )

        report_data: dict[str, Any] = {}
        if report.is_file():
            try:
                loaded = json.loads(report.read_text(encoding="utf-8-sig"))
                if isinstance(loaded, dict):
                    report_data = loaded
            except (OSError, json.JSONDecodeError):
                report_data = {}
            try:
                report.unlink()
            except OSError:
                pass

        if not result.ok:
            result.summary = (
                "Headless Blender region extraction timed out"
                if result.data.get("timed_out")
                else "Headless Blender region extraction failed"
            )
            if report_data:
                result.data["report"] = report_data
            return result

        if not output.is_file() or output.stat().st_size <= 0:
            return ActionResult(
                False,
                "Headless Blender extraction completed without a non-empty .blend output",
                {
                    **result.data,
                    "output_file": str(output),
                    "report": report_data,
                },
            )

        digest = hashlib.sha256()
        with output.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)

        return ActionResult(
            True,
            "Headless Blender region extracted",
            {
                **result.data,
                **report_data,
                "output_file": str(output),
                "blend_file": str(blend_file),
                "size_bytes": output.stat().st_size,
                "sha256": digest.hexdigest(),
                "isolated_process": True,
            },
        )


    def blender_live_checkpoint_create(self, payload: dict[str, Any]) -> ActionResult:
        label = str(payload.get("label") or "checkpoint").strip()
        return self._blender_live(payload).request(
            "checkpoint_create",
            {"label": label},
            timeout_seconds=float(payload.get("timeout_seconds", 120)),
        )

    def blender_live_checkpoint_list(self, payload: dict[str, Any]) -> ActionResult:
        return self._blender_live(payload).request(
            "checkpoint_list",
            timeout_seconds=float(payload.get("timeout_seconds", 30)),
        )

    def blender_live_checkpoint_restore(self, payload: dict[str, Any]) -> ActionResult:
        checkpoint_id = str(payload.get("checkpoint_id") or "").strip()
        if not checkpoint_id:
            return ActionResult(False, "checkpoint_id is required")
        return self._blender_live(payload).request(
            "checkpoint_restore",
            {
                "checkpoint_id": checkpoint_id,
                "discard_unsaved": bool(payload.get("discard_unsaved", False)),
            },
            timeout_seconds=float(payload.get("timeout_seconds", 30)),
        )

    def blender_live_trajectory(self, payload: dict[str, Any]) -> ActionResult:
        return self._blender_live(payload).trajectory(
            limit=int(payload.get("limit", 50)),
        )

    def blender_live_generation_pass(self, payload: dict[str, Any]) -> ActionResult:
        """Run one recoverable Blender generation pass in the visible session."""
        project = self._project(payload)
        raw_script = payload.get("script_path")
        if not raw_script:
            return ActionResult(False, "script_path is required")

        script = project.path(str(raw_script))
        allowed_root = project.path(
            project.blender.get("scripts_dir", "automation/blender"),
            must_exist=False,
        ).resolve()
        try:
            script.relative_to(allowed_root)
        except ValueError:
            return ActionResult(
                False,
                f"Blender live script must be inside {allowed_root}",
            )
        if script.suffix.lower() != ".py":
            return ActionResult(False, "Blender live script must be a .py file")

        pairs = payload.get("contact_pairs", [])
        if pairs is None:
            pairs = []
        if not isinstance(pairs, list) or len(pairs) > 200:
            return ActionResult(False, "contact_pairs must be a list with at most 200 pairs")
        normalized_pairs = []
        for item in pairs:
            if (
                not isinstance(item, list)
                or len(item) != 2
                or not all(isinstance(name, str) and name.strip() for name in item)
            ):
                return ActionResult(
                    False,
                    "each contact pair must contain exactly two non-empty object names",
                )
            normalized_pairs.append([item[0].strip(), item[1].strip()])


        raw_quality_checks = payload.get("quality_checks", [])
        if raw_quality_checks is None:
            raw_quality_checks = []
        if not isinstance(raw_quality_checks, list) or len(raw_quality_checks) > 100:
            return ActionResult(
                False,
                "quality_checks must be a list with at most 100 checks",
            )
        supported_quality_types = {"dimensions", "symmetry", "proportion", "containment", "mesh_quality", "uv_quality"}
        quality_checks: list[dict[str, Any]] = []
        for index, check in enumerate(raw_quality_checks):
            if not isinstance(check, dict):
                return ActionResult(False, f"quality check {index} must be an object")
            if any(key in check for key in ("passed", "ok", "result")):
                return ActionResult(
                    False,
                    f"quality check {index} cannot provide its own completion claim",
                )
            kind = str(check.get("type") or "").strip().lower()
            if kind not in supported_quality_types:
                return ActionResult(
                    False,
                    f"quality check {index} type must be one of: {', '.join(sorted(supported_quality_types))}",
                )
            quality_checks.append({**check, "type": kind})



        raw_protected = payload.get("protected_objects", [])
        if raw_protected is None:
            raw_protected = []
        if not isinstance(raw_protected, list) or len(raw_protected) > 100:
            return ActionResult(
                False,
                "protected_objects must be a list with at most 100 selectors",
            )
        protected_objects: list[dict[str, Any]] = []
        protected_keys: set[str] = set()
        for index, item in enumerate(raw_protected):
            if isinstance(item, str):
                item = {"object_name": item}
            if not isinstance(item, dict):
                return ActionResult(
                    False,
                    f"protected object {index} must be a string or object",
                )
            object_name = str(item.get("object_name") or "").strip()
            object_id = str(item.get("ordax_object_id") or "").strip()
            if bool(object_name) == bool(object_id):
                return ActionResult(
                    False,
                    f"protected object {index} must provide exactly one of object_name or ordax_object_id",
                )
            key = f"name:{object_name}" if object_name else f"id:{object_id}"
            if key in protected_keys:
                return ActionResult(False, f"duplicate protected object: {key}")
            protected_keys.add(key)
            selector = (
                {"object_name": object_name}
                if object_name
                else {"ordax_object_id": object_id}
            )
            selector["evaluated"] = bool(item.get("evaluated", True))
            protected_objects.append(selector)

        if protected_objects and bool(payload.get("reset_scene", False)):
            return ActionResult(
                False,
                "protected_objects cannot be used with reset_scene=true",
            )


        raw_multiview = payload.get("multiview", False)
        if raw_multiview not in (None, False, True) and not isinstance(raw_multiview, dict):
            return ActionResult(
                False,
                "multiview must be false, true, or an options object",
            )
        multiview_options: dict[str, Any] | None = None
        if raw_multiview is True:
            multiview_options = {}
        elif isinstance(raw_multiview, dict):
            multiview_options = dict(raw_multiview)

        save_target = None
        raw_save_target = payload.get("save_target_path")
        if raw_save_target:
            save_target = project.path(str(raw_save_target), must_exist=False)
            if save_target.suffix.lower() != ".blend":
                return ActionResult(False, "save_target_path must be a .blend file")

        raw_extra_artifacts = payload.get("collect_artifacts", [])
        if raw_extra_artifacts is None:
            raw_extra_artifacts = []
        if not isinstance(raw_extra_artifacts, list) or len(raw_extra_artifacts) > 24:
            return ActionResult(False, "collect_artifacts must be a list with at most 24 entries")

        extra_artifacts: list[tuple[Path, str]] = []
        allowed_artifact_suffixes = {
            ".png", ".jpg", ".jpeg", ".json", ".glb", ".gltf", ".fbx", ".blend"
        }
        for item in raw_extra_artifacts:
            if isinstance(item, str):
                raw_path = item
                kind = "blender-generated-artifact"
            elif isinstance(item, dict):
                raw_path = str(item.get("path") or "").strip()
                kind = str(item.get("kind") or "blender-generated-artifact").strip()
            else:
                return ActionResult(
                    False,
                    "collect_artifacts entries must be paths or {path, kind} objects",
                )
            if not raw_path:
                return ActionResult(False, "collect_artifacts contains an empty path")
            artifact_path = project.path(raw_path, must_exist=False)
            if artifact_path.suffix.lower() not in allowed_artifact_suffixes:
                return ActionResult(
                    False,
                    f"unsupported collected artifact type: {artifact_path.suffix}",
                )
            if not re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", kind):
                return ActionResult(False, "collect_artifacts kind contains unsupported characters")
            extra_artifacts.append((artifact_path, kind))

        live = BlenderLiveBridge(self.config, project)
        phases: dict[str, Any] = {}
        rollback_on_failure = bool(payload.get("rollback_on_failure", True))
        timeout = float(payload.get("timeout_seconds", 300))

        checkpoint = live.request(
            "checkpoint_create",
            {"label": str(payload.get("label") or script.stem)},
            timeout_seconds=min(timeout, 120),
        )
        phases["checkpoint"] = {
            "ok": checkpoint.ok,
            "summary": checkpoint.summary,
            "data": checkpoint.data,
        }
        if not checkpoint.ok:
            return ActionResult(
                False,
                "Blender generation pass could not create a checkpoint",
                {"phases": phases},
            )

        checkpoint_data = checkpoint.data.get("checkpoint") or {}
        checkpoint_id = str(checkpoint_data.get("id") or "").strip()

        if bool(payload.get("reset_scene", False)):
            reset = live.request(
                "scene_reset",
                timeout_seconds=min(timeout, 30),
            )
            phases["scene_reset"] = {
                "ok": reset.ok,
                "summary": reset.summary,
                "data": reset.data,
            }
            if not reset.ok:
                return ActionResult(
                    False,
                    "Blender generation pass could not reset the scene",
                    {
                        "checkpoint_id": checkpoint_id,
                        "phases": phases,
                    },
                )

        def fail(summary: str, failed: ActionResult | None = None) -> ActionResult:
            if failed is not None:
                phases["failure"] = {
                    "ok": failed.ok,
                    "summary": failed.summary,
                    "data": failed.data,
                }
            if rollback_on_failure and checkpoint_id:
                rollback = live.request(
                    "checkpoint_restore",
                    {
                        "checkpoint_id": checkpoint_id,
                        "discard_unsaved": True,
                    },
                    timeout_seconds=30,
                )
                phases["rollback"] = {
                    "ok": rollback.ok,
                    "summary": rollback.summary,
                    "data": rollback.data,
                }
            return ActionResult(
                False,
                summary,
                {
                    "checkpoint_id": checkpoint_id,
                    "rollback_requested": rollback_on_failure,
                    "phases": phases,
                },
            )


        protected_before: dict[str, Any] = {}
        if protected_objects:
            before_lock = live.request(
                "object_fingerprints",
                {"selectors": protected_objects},
                timeout_seconds=min(timeout, 120),
            )
            phases["protected_before"] = {
                "ok": before_lock.ok,
                "summary": before_lock.summary,
                "data": before_lock.data,
            }
            if not before_lock.ok:
                return fail(
                    "Blender protected-object baseline could not be captured",
                    before_lock,
                )
            for entry in before_lock.data.get("fingerprints", []):
                key = str(entry.get("selector_key") or "")
                if key:
                    protected_before[key] = entry

        generated = live.request(
            "run_script",
            {"script_path": str(script)},
            timeout_seconds=timeout,
        )
        phases["generation"] = {
            "ok": generated.ok,
            "summary": generated.summary,
            "data": generated.data,
        }
        if not generated.ok:
            return fail("Blender generation script failed; pass rejected")


        if protected_objects:
            after_lock = live.request(
                "object_fingerprints",
                {"selectors": protected_objects},
                timeout_seconds=min(timeout, 120),
            )
            phases["protected_after"] = {
                "ok": after_lock.ok,
                "summary": after_lock.summary,
                "data": after_lock.data,
            }
            if not after_lock.ok:
                return fail(
                    "A protected Blender object is missing or cannot be fingerprinted",
                    after_lock,
                )

            protected_after = {
                str(entry.get("selector_key") or ""): entry
                for entry in after_lock.data.get("fingerprints", [])
                if str(entry.get("selector_key") or "")
            }
            changed = []
            for key, before_entry in protected_before.items():
                after_entry = protected_after.get(key)
                if after_entry is None:
                    changed.append({
                        "selector_key": key,
                        "reason": "missing_after_generation",
                    })
                    continue
                if before_entry.get("combined_sha256") != after_entry.get("combined_sha256"):
                    changed.append({
                        "selector_key": key,
                        "reason": "fingerprint_changed",
                        "before": {
                            "object_name": before_entry.get("object_name"),
                            "transform_sha256": before_entry.get("transform_sha256"),
                            "geometry_sha256": before_entry.get("geometry_sha256"),
                            "combined_sha256": before_entry.get("combined_sha256"),
                        },
                        "after": {
                            "object_name": after_entry.get("object_name"),
                            "transform_sha256": after_entry.get("transform_sha256"),
                            "geometry_sha256": after_entry.get("geometry_sha256"),
                            "combined_sha256": after_entry.get("combined_sha256"),
                        },
                    })

            phases["protected_objects"] = {
                "ok": not changed,
                "protected_count": len(protected_before),
                "changed_count": len(changed),
                "changed": changed,
            }
            if changed:
                return fail(
                    "Blender generation modified protected approved objects; pass rejected",
                    ActionResult(
                        False,
                        "protected object fingerprint changed",
                        {"changed": changed},
                    ),
                )

        snapshot = live.request(
            "scene_snapshot",
            {"max_objects": int(payload.get("max_objects", 300))},
            timeout_seconds=min(timeout, 60),
        )
        phases["snapshot"] = {
            "ok": snapshot.ok,
            "summary": snapshot.summary,
            "data": snapshot.data,
        }
        if not snapshot.ok:
            return fail("Blender rich snapshot failed; pass rejected")

        if normalized_pairs:
            audit = live.request(
                "contact_audit",
                {"pairs": normalized_pairs},
                timeout_seconds=min(timeout, 120),
            )
            phases["contact_audit"] = {
                "ok": audit.ok,
                "summary": audit.summary,
                "data": audit.data,
            }
            if not audit.ok:
                return fail(
                    "Blender contact audit failed; pass rejected and rollback requested",
                )


        if quality_checks:
            quality = live.request(
                "quality_gate",
                {"checks": quality_checks},
                timeout_seconds=min(timeout, 120),
            )
            phases["quality_gate"] = {
                "ok": quality.ok,
                "summary": quality.summary,
                "data": quality.data,
            }
            if not quality.ok:
                return fail(
                    "Blender deterministic quality gate failed; pass rejected",
                    quality,
                )


        if multiview_options is not None:
            multiview_payload = {
                **multiview_options,
                "project": project.slug,
                "timeout_seconds": min(timeout, 240),
            }
            multiview = self.blender_live_multiview_capture(multiview_payload)
            phases["multiview"] = {
                "ok": multiview.ok,
                "summary": multiview.summary,
                "data": multiview.data,
            }
            if not multiview.ok and bool(
                multiview_options.get("required", True)
            ):
                return fail(
                    "Blender deterministic multiview capture failed; pass rejected",
                    multiview,
                )

            baseline_manifest = str(
                multiview_options.get("baseline_manifest_path") or ""
            ).strip()
            if multiview.ok and baseline_manifest:
                candidate_manifest = str(
                    multiview.data.get("manifest") or ""
                ).strip()
                if not candidate_manifest:
                    return fail(
                        "Blender multiview comparison has no candidate manifest",
                        multiview,
                    )
                compare_payload: dict[str, Any] = {
                    "project": project.slug,
                    "baseline_manifest_path": baseline_manifest,
                    "candidate_manifest_path": candidate_manifest,
                    "require_same_views": bool(
                        multiview_options.get("require_same_views", True)
                    ),
                    "require_same_resolution": bool(
                        multiview_options.get("require_same_resolution", True)
                    ),
                    "require_same_mode": bool(
                        multiview_options.get("require_same_mode", True)
                    ),
                    "write_diff_images": bool(
                        multiview_options.get("write_diff_images", True)
                    ),
                }
                if "max_mae" in multiview_options:
                    compare_payload["max_mae"] = multiview_options.get("max_mae")
                if "max_changed_ratio" in multiview_options:
                    compare_payload["max_changed_ratio"] = multiview_options.get(
                        "max_changed_ratio"
                    )
                if "min_silhouette_iou" in multiview_options:
                    compare_payload["min_silhouette_iou"] = multiview_options.get(
                        "min_silhouette_iou"
                    )

                comparison = self.blender_multiview_compare(compare_payload)
                phases["multiview_compare"] = {
                    "ok": comparison.ok,
                    "summary": comparison.summary,
                    "data": comparison.data,
                }
                if not comparison.ok and bool(
                    multiview_options.get("compare_required", True)
                ):
                    return fail(
                        "Blender multiview comparison failed; pass rejected",
                        comparison,
                    )

        artifact = None
        if bool(payload.get("capture", True)):
            output = self._capture_output(payload, "blender-generation-pass.png")
            capture = live.request(
                "capture_viewport",
                {"output_path": str(output)},
                timeout_seconds=min(timeout, 120),
            )
            phases["capture"] = {
                "ok": capture.ok,
                "summary": capture.summary,
                "data": capture.data,
            }
            if not capture.ok or not output.is_file() or output.stat().st_size == 0:
                if bool(payload.get("capture_required", True)):
                    return fail("Blender viewport capture failed; pass rejected")
            else:
                artifact = str(output)
                self._record_capture(payload, output)

        if save_target is not None:
            saved = live.request(
                "save",
                {"target_path": str(save_target)},
                timeout_seconds=min(timeout, 120),
            )
            phases["save"] = {
                "ok": saved.ok,
                "summary": saved.summary,
                "data": saved.data,
            }
            if not saved.ok:
                return fail("Blender final save failed; pass rejected")

        collected_artifacts = []
        missing_artifacts = []
        for artifact_path, kind in extra_artifacts:
            if artifact_path.is_file() and artifact_path.stat().st_size > 0:
                collected_artifacts.append({
                    "path": str(artifact_path),
                    "kind": kind,
                    "size_bytes": artifact_path.stat().st_size,
                })
            else:
                missing_artifacts.append(str(artifact_path))

        if missing_artifacts and bool(payload.get("collect_artifacts_required", True)):
            return fail(
                "Blender declared artifacts are missing; pass rejected",
                ActionResult(
                    False,
                    "missing declared Blender artifacts",
                    {"missing_artifacts": missing_artifacts},
                ),
            )

        return ActionResult(
            True,
            "Blender generation pass accepted",
            {
                "checkpoint_id": checkpoint_id,
                "script_path": str(script),
                "artifact": artifact,
                "artifacts": collected_artifacts,
                "missing_artifacts": missing_artifacts,
                "save_target_path": str(save_target) if save_target else None,
                "phases": phases,
            },
        )

    def blender_live_result(self, payload: dict[str, Any]) -> ActionResult:
        command_id = str(payload.get("command_id") or "").strip()
        if not command_id:
            return ActionResult(False, "command_id is required")
        return self._blender_live(payload).result(command_id)

    def blender_live_run_script(self, payload: dict[str, Any]) -> ActionResult:
        project = self._project(payload)
        raw_script = payload.get("script_path")
        if not raw_script:
            return ActionResult(False, "script_path is required")

        script = project.path(str(raw_script))
        allowed_root = project.path(
            project.blender.get("scripts_dir", "automation/blender"),
            must_exist=False,
        ).resolve()
        try:
            script.relative_to(allowed_root)
        except ValueError:
            return ActionResult(
                False,
                f"Blender live script must be inside {allowed_root}",
            )
        if script.suffix.lower() != ".py":
            return ActionResult(False, "Blender live script must be a .py file")

        return BlenderLiveBridge(self.config, project).request(
            "run_script",
            {"script_path": str(script)},
            timeout_seconds=float(payload.get("timeout_seconds", 300)),
        )

    def blender_live_capture(self, payload: dict[str, Any]) -> ActionResult:
        project = self._project(payload)
        live = BlenderLiveBridge(self.config, project)
        output = self._capture_output(payload, "blender-live.png")
        result = live.request(
            "capture_viewport",
            {"output_path": str(output)},
            timeout_seconds=float(payload.get("timeout_seconds", 120)),
        )
        if not result.ok:
            return result

        if output.is_file():
            result.data["artifact"] = str(output)
            result.data["sha256"] = hashlib.sha256(output.read_bytes()).hexdigest()

            snapshot_path = output.with_suffix(".json")
            snapshot = result.data.get("snapshot")
            if isinstance(snapshot, dict):
                snapshot_path.write_text(
                    json.dumps(snapshot, indent=2),
                    encoding="utf-8",
                )
                result.data["snapshot_path"] = str(snapshot_path)

            self._record_capture(payload, output)

        return result


    def blender_live_multiview_capture(self, payload: dict[str, Any]) -> ActionResult:
        views = payload.get(
            "views",
            ["front", "back", "left", "right", "top", "three_quarter"],
        )
        supported = {
            "front",
            "back",
            "left",
            "right",
            "top",
            "bottom",
            "three_quarter",
            "three_quarter_back",
        }
        if (
            not isinstance(views, list)
            or not views
            or len(views) > len(supported)
        ):
            return ActionResult(False, "views must be a non-empty bounded list")
        normalized_views: list[str] = []
        for raw in views:
            view = str(raw or "").strip().lower()
            if view not in supported:
                return ActionResult(
                    False,
                    f"unsupported multiview view: {view}",
                    {"supported_views": sorted(supported)},
                )
            if view in normalized_views:
                return ActionResult(False, "multiview views must be unique")
            normalized_views.append(view)

        object_names = payload.get("object_names")
        if object_names is not None and (
            not isinstance(object_names, list)
            or not object_names
            or len(object_names) > 200
            or not all(isinstance(name, str) and name.strip() for name in object_names)
        ):
            return ActionResult(
                False,
                "object_names must be a non-empty list of at most 200 object names",
            )

        mode = str(payload.get("mode") or "material").strip().lower()
        if mode not in {"material", "silhouette"}:
            return ActionResult(False, "multiview mode must be material or silhouette")

        try:
            width = int(payload.get("width", 768))
            height = int(payload.get("height", 768))
            margin = float(payload.get("margin", 1.15))
        except (TypeError, ValueError):
            return ActionResult(False, "width, height and margin must be numeric")
        if width < 128 or width > 4096 or height < 128 or height > 4096:
            return ActionResult(
                False,
                "multiview resolution must be between 128 and 4096",
            )
        if margin < 1.0 or margin > 3.0:
            return ActionResult(False, "multiview margin must be between 1.0 and 3.0")

        project = self._project(payload)
        live = BlenderLiveBridge(self.config, project)
        anchor = self._capture_output(payload, "multiview.json")
        request: dict[str, Any] = {
            "output_dir": str(anchor.parent),
            "views": normalized_views,
            "width": width,
            "height": height,
            "margin": margin,
            "mode": mode,
        }
        if object_names is not None:
            request["object_names"] = [name.strip() for name in object_names]

        result = live.request(
            "multiview_capture",
            request,
            timeout_seconds=float(payload.get("timeout_seconds", 240)),
        )
        if not result.ok:
            return result

        primary = result.data.get("primary_artifact")
        if primary:
            primary_path = Path(str(primary)).resolve()
            manifest_path = primary_path.with_suffix(".json")
            manifest_path.write_text(
                json.dumps(result.data, indent=2),
                encoding="utf-8",
            )
            result.data["primary_snapshot_path"] = str(manifest_path)
            self._record_capture(payload, primary_path)

        return result

    def blender_live_save(self, payload: dict[str, Any]) -> ActionResult:
        project = self._project(payload)
        raw_target = payload.get("target_path")
        data: dict[str, Any] = {}
        if raw_target:
            target = project.path(str(raw_target), must_exist=False)
            if target.suffix.lower() != ".blend":
                return ActionResult(False, "target_path must be a .blend file")
            data["target_path"] = str(target)

        return BlenderLiveBridge(self.config, project).request(
            "save",
            data,
            timeout_seconds=float(payload.get("timeout_seconds", 120)),
        )

    def blender_live_stop(self, payload: dict[str, Any]) -> ActionResult:
        return self._blender_live(payload).request(
            "quit",
            timeout_seconds=float(payload.get("timeout_seconds", 30)),
        )


    def blender_multiview_compare(self, payload: dict[str, Any]) -> ActionResult:
        project = self._project(payload)
        artifact_root = (
            self.config.state_dir / "artifacts" / project.slug
        ).resolve()

        def resolve_manifest(value: Any, field: str) -> Path:
            raw = str(value or "").strip()
            if not raw:
                raise ValueError(f"{field} is required")
            candidate = Path(raw).expanduser()
            path = (
                candidate
                if candidate.is_absolute()
                else artifact_root / candidate
            ).resolve()
            if not path.is_relative_to(artifact_root):
                raise ValueError(f"{field} must be inside the project artifact root")
            if path.suffix.lower() != ".json" or not path.is_file():
                raise FileNotFoundError(path)
            return path

        try:
            baseline_path = resolve_manifest(
                payload.get("baseline_manifest_path"),
                "baseline_manifest_path",
            )
            candidate_path = resolve_manifest(
                payload.get("candidate_manifest_path"),
                "candidate_manifest_path",
            )
        except (ValueError, FileNotFoundError) as error:
            return ActionResult(False, f"{type(error).__name__}: {error}")

        try:
            baseline = json.loads(baseline_path.read_text(encoding="utf-8-sig"))
            candidate = json.loads(candidate_path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError) as error:
            return ActionResult(False, f"invalid multiview manifest: {error}")

        def manifest_views(data: Any, label: str) -> dict[str, dict[str, Any]]:
            if not isinstance(data, dict):
                raise ValueError(f"{label} manifest must contain an object")
            raw_views = data.get("views")
            if not isinstance(raw_views, list) or not raw_views:
                raise ValueError(f"{label} manifest contains no views")
            result: dict[str, dict[str, Any]] = {}
            for index, entry in enumerate(raw_views):
                if not isinstance(entry, dict):
                    raise ValueError(f"{label} view {index} must be an object")
                view = str(entry.get("view") or "").strip()
                artifact = str(entry.get("artifact") or "").strip()
                if not view or not artifact:
                    raise ValueError(
                        f"{label} view {index} needs view and artifact"
                    )
                if view in result:
                    raise ValueError(f"{label} manifest duplicates view: {view}")
                result[view] = entry
            return result

        try:
            baseline_views = manifest_views(baseline, "baseline")
            candidate_views = manifest_views(candidate, "candidate")
        except ValueError as error:
            return ActionResult(False, str(error))

        baseline_mode = str(baseline.get("mode") or "material").strip().lower()
        candidate_mode = str(candidate.get("mode") or "material").strip().lower()
        if (
            baseline_mode != candidate_mode
            and bool(payload.get("require_same_mode", True))
        ):
            return ActionResult(
                False,
                "multiview manifests use different capture modes",
                {
                    "baseline_mode": baseline_mode,
                    "candidate_mode": candidate_mode,
                },
            )

        baseline_names = set(baseline_views)
        candidate_names = set(candidate_views)
        if baseline_names != candidate_names and bool(
            payload.get("require_same_views", True)
        ):
            return ActionResult(
                False,
                "multiview manifests do not contain the same view set",
                {
                    "baseline_only": sorted(baseline_names - candidate_names),
                    "candidate_only": sorted(candidate_names - baseline_names),
                },
            )
        common_views = sorted(baseline_names & candidate_names)
        if not common_views:
            return ActionResult(False, "multiview manifests have no common views")

        try:
            max_mae_raw = payload.get("max_mae")
            max_changed_raw = payload.get("max_changed_ratio")
            min_iou_raw = payload.get("min_silhouette_iou")
            max_mae = float(max_mae_raw) if max_mae_raw is not None else None
            max_changed = (
                float(max_changed_raw) if max_changed_raw is not None else None
            )
            min_iou = float(min_iou_raw) if min_iou_raw is not None else None
        except (TypeError, ValueError):
            return ActionResult(
                False,
                "max_mae, max_changed_ratio and min_silhouette_iou must be numbers",
            )
        for field, value in (
            ("max_mae", max_mae),
            ("max_changed_ratio", max_changed),
            ("min_silhouette_iou", min_iou),
        ):
            if value is not None and (value < 0.0 or value > 1.0):
                return ActionResult(False, f"{field} must be between 0 and 1")
        if min_iou is not None and (
            baseline_mode != "silhouette" or candidate_mode != "silhouette"
        ):
            return ActionResult(
                False,
                "min_silhouette_iou requires silhouette multiview manifests",
            )

        try:
            from PIL import Image, ImageChops, ImageStat
        except ImportError:
            return ActionResult(False, "Pillow is required for multiview comparison")

        comparison_root = self._capture_output(
            payload,
            "multiview-comparison.json",
        ).parent

        def resolve_artifact(entry: dict[str, Any], label: str, view: str) -> Path:
            raw = str(entry.get("artifact") or "").strip()
            path = Path(raw).expanduser().resolve()
            if not path.is_relative_to(artifact_root):
                raise ValueError(
                    f"{label} artifact for {view} escaped project artifact root"
                )
            if path.suffix.lower() not in {".png", ".jpg", ".jpeg"} or not path.is_file():
                raise FileNotFoundError(path)
            return path

        comparisons = []
        failed_views = []
        write_diffs = bool(payload.get("write_diff_images", True))
        require_same_resolution = bool(payload.get("require_same_resolution", True))

        try:
            for view in common_views:
                baseline_image_path = resolve_artifact(
                    baseline_views[view],
                    "baseline",
                    view,
                )
                candidate_image_path = resolve_artifact(
                    candidate_views[view],
                    "candidate",
                    view,
                )
                with (
                    Image.open(baseline_image_path) as baseline_image_raw,
                    Image.open(candidate_image_path) as candidate_image_raw,
                ):
                    baseline_image = baseline_image_raw.convert("RGB")
                    candidate_image = candidate_image_raw.convert("RGB")
                    if baseline_image.size != candidate_image.size:
                        if require_same_resolution:
                            return ActionResult(
                                False,
                                f"multiview resolution differs for {view}",
                                {
                                    "view": view,
                                    "baseline_size": list(baseline_image.size),
                                    "candidate_size": list(candidate_image.size),
                                },
                            )
                        candidate_image = candidate_image.resize(
                            baseline_image.size,
                            Image.Resampling.LANCZOS,
                        )

                    difference = ImageChops.difference(
                        baseline_image,
                        candidate_image,
                    )
                    stats = ImageStat.Stat(difference)
                    channel_mean = [float(value) / 255.0 for value in stats.mean]
                    channel_rms = [float(value) / 255.0 for value in stats.rms]
                    mae = sum(channel_mean) / len(channel_mean)
                    rms = sum(channel_rms) / len(channel_rms)

                    red, green, blue = difference.split()
                    max_channel = ImageChops.lighter(
                        ImageChops.lighter(red, green),
                        blue,
                    )
                    histogram = max_channel.histogram()
                    total_pixels = baseline_image.size[0] * baseline_image.size[1]
                    unchanged = int(histogram[0]) if histogram else 0
                    changed_ratio = (
                        (total_pixels - unchanged) / total_pixels
                        if total_pixels
                        else 0.0
                    )

                    silhouette_iou = None
                    if (
                        baseline_mode == "silhouette"
                        and candidate_mode == "silhouette"
                    ):
                        baseline_gray = baseline_image.convert("L")
                        candidate_gray = candidate_image.convert("L")
                        baseline_mask = baseline_gray.point(
                            lambda value: 255 if value < 128 else 0
                        )
                        candidate_mask = candidate_gray.point(
                            lambda value: 255 if value < 128 else 0
                        )
                        intersection = ImageChops.multiply(
                            baseline_mask,
                            candidate_mask,
                        )
                        union = ImageChops.lighter(
                            baseline_mask,
                            candidate_mask,
                        )
                        intersection_pixels = intersection.histogram()[255]
                        union_pixels = union.histogram()[255]
                        silhouette_iou = (
                            intersection_pixels / union_pixels
                            if union_pixels
                            else 1.0
                        )

                    view_passed = True
                    if max_mae is not None and mae > max_mae:
                        view_passed = False
                    if max_changed is not None and changed_ratio > max_changed:
                        view_passed = False
                    if (
                        min_iou is not None
                        and silhouette_iou is not None
                        and silhouette_iou < min_iou
                    ):
                        view_passed = False

                    diff_path = None
                    if write_diffs:
                        diff_path = comparison_root / f"{view}-diff.png"
                        difference.save(diff_path, format="PNG")

                    record = {
                        "view": view,
                        "passed": view_passed,
                        "mae": round(mae, 8),
                        "rms": round(rms, 8),
                        "changed_pixel_ratio": round(changed_ratio, 8),
                        "silhouette_iou": (
                            round(float(silhouette_iou), 8)
                            if silhouette_iou is not None
                            else None
                        ),
                        "baseline_artifact": str(baseline_image_path),
                        "candidate_artifact": str(candidate_image_path),
                        "diff_artifact": str(diff_path) if diff_path else None,
                        "resolution": list(baseline_image.size),
                    }
                    comparisons.append(record)
                    if not view_passed:
                        failed_views.append(view)
        except (OSError, ValueError, FileNotFoundError) as error:
            return ActionResult(False, f"{type(error).__name__}: {error}")

        def vector3(data: Any, field: str) -> list[float] | None:
            raw = data.get("bounds", {}).get(field) if isinstance(data, dict) else None
            if (
                isinstance(raw, list)
                and len(raw) == 3
                and all(isinstance(value, (int, float)) for value in raw)
            ):
                return [float(value) for value in raw]
            return None

        baseline_dimensions = vector3(baseline, "dimensions")
        candidate_dimensions = vector3(candidate, "dimensions")
        baseline_center = vector3(baseline, "center")
        candidate_center = vector3(candidate, "center")
        dimension_error = None
        center_error = None
        if baseline_dimensions and candidate_dimensions:
            dimension_error = [
                round(abs(candidate_dimensions[i] - baseline_dimensions[i]), 8)
                for i in range(3)
            ]
        if baseline_center and candidate_center:
            center_error = [
                round(abs(candidate_center[i] - baseline_center[i]), 8)
                for i in range(3)
            ]

        thresholds_declared = (
            max_mae is not None
            or max_changed is not None
            or min_iou is not None
        )
        comparison_passed = not failed_views if thresholds_declared else None
        report = {
            "baseline_manifest": str(baseline_path),
            "candidate_manifest": str(candidate_path),
            "views": comparisons,
            "view_count": len(comparisons),
            "capture_modes": {
                "baseline": baseline_mode,
                "candidate": candidate_mode,
            },
            "thresholds": {
                "max_mae": max_mae,
                "max_changed_ratio": max_changed,
                "min_silhouette_iou": min_iou,
            },
            "comparison_passed": comparison_passed,
            "failed_views": failed_views,
            "bounds": {
                "baseline_dimensions": baseline_dimensions,
                "candidate_dimensions": candidate_dimensions,
                "absolute_dimension_error": dimension_error,
                "baseline_center": baseline_center,
                "candidate_center": candidate_center,
                "absolute_center_error": center_error,
            },
        }
        report_path = comparison_root / "multiview-comparison.json"
        report_path.write_text(
            json.dumps(report, indent=2),
            encoding="utf-8",
        )
        report["report"] = str(report_path)

        if thresholds_declared and failed_views:
            return ActionResult(
                False,
                "Multiview comparison exceeded declared thresholds",
                report,
            )
        return ActionResult(
            True,
            "Multiview comparison complete",
            report,
        )

    def blender_asset_search(self, payload: dict[str, Any]) -> ActionResult:
        provider = str(payload.get("provider") or "polyhaven").strip().lower()
        if provider != "polyhaven":
            return ActionResult(False, "supported asset provider: polyhaven")
        try:
            report = search_polyhaven(
                query=str(payload.get("query") or ""),
                asset_type=str(payload.get("asset_type") or "all"),
                categories=(
                    str(payload.get("categories")).strip()
                    if payload.get("categories") is not None
                    else None
                ),
                limit=int(payload.get("limit", 20)),
                timeout_seconds=float(payload.get("timeout_seconds", 30)),
            )
        except Exception as error:
            return ActionResult(False, f"Poly Haven search failed: {type(error).__name__}: {error}")
        return ActionResult(True, "Poly Haven asset search ready", report)

    def blender_asset_manifest(self, payload: dict[str, Any]) -> ActionResult:
        provider = str(payload.get("provider") or "polyhaven").strip().lower()
        if provider != "polyhaven":
            return ActionResult(False, "supported asset provider: polyhaven")
        asset_id = str(payload.get("asset_id") or "").strip()
        if not asset_id:
            return ActionResult(False, "asset_id is required")
        try:
            report = polyhaven_file_manifest(
                asset_id,
                timeout_seconds=float(payload.get("timeout_seconds", 30)),
            )
        except Exception as error:
            return ActionResult(False, f"Poly Haven manifest failed: {type(error).__name__}: {error}")
        return ActionResult(True, "Poly Haven file manifest ready", report)

    def blender_version(self, payload: dict[str, Any]) -> ActionResult:
        blender = find_blender()
        if blender is None:
            return ActionResult(False, "Blender executable not found")
        return _run([str(blender), "--version"], timeout=120)

    def blender_run_python(self, payload: dict[str, Any]) -> ActionResult:
        blender = find_blender()
        if blender is None:
            return ActionResult(False, "Blender executable not found")

        raw_script = payload.get("script_path")
        if not raw_script:
            return ActionResult(False, "script_path is required")

        registered = self._project(payload)
        script = registered.path(raw_script)
        allowed_root = registered.path(registered.blender.get("scripts_dir", "automation/blender"), must_exist=False)
        try:
            script.relative_to(allowed_root)
        except ValueError:
            return ActionResult(
                False,
                f"Blender script must be inside {allowed_root}",
            )
        if not script.is_file():
            return ActionResult(False, f"Blender script not found: {script}")

        command = [str(blender), "--background", "--factory-startup", "--disable-autoexec"]
        raw_blend = payload.get("blend_file")
        if raw_blend:
            blend = registered.path(raw_blend)
            if not blend.is_file():
                return ActionResult(False, f"Blend file not found: {blend}")
            command.append(str(blend))
        command.extend(["--python-exit-code", "1", "--python", str(script)])
        return _run(
            command,
            timeout=int(payload.get("timeout_seconds", 1800)),
        )
