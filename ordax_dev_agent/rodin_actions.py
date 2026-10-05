"""Typed Hyper3D Rodin Gen-2.5 integration for game-asset generation."""
from __future__ import annotations

import os
import re
import uuid
from typing import Any

import httpx

from .models import ActionResult


_BASE = "https://api.hyper3d.com/api/v2"
_TIERS = frozenset(
    {
        "Gen-2.5-Extreme-Low",
        "Gen-2.5-Low",
        "Gen-2.5-Medium",
        "Gen-2.5-High",
        "Gen-2.5-Extreme-High",
    }
)
_FORMATS = frozenset({"glb", "usdz", "fbx", "obj", "stl"})
_MESH_MODES = frozenset({"Raw", "Quad"})
_MATERIALS = frozenset({"PBR", "Shaded", "All", "Hybrid", "None"})
_QUALITY = frozenset({"high", "medium", "low", "extra-low"})
_SYMMETRY = frozenset({"symmetric", "balanced", "asymmetric", "unknown"})
_TEXTURE_MODES = frozenset(
    {"legacy", "extreme-low", "low", "medium", "high", "extreme-high"}
)
_SUBSCRIPTION_RE = re.compile(r"[A-Za-z0-9._~:+/=-]{1,512}")


def _text(value: Any, field: str, *, maximum: int) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string")
    result = value.strip()
    if not result:
        raise ValueError(f"{field} cannot be empty")
    if len(result) > maximum:
        raise ValueError(f"{field} must be at most {maximum} characters")
    return result


def _choice(value: Any, field: str, allowed: frozenset[str], default: str) -> str:
    if value is None:
        return default
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string")
    result = value.strip()
    if result not in allowed:
        raise ValueError(f"{field} must be one of: {', '.join(sorted(allowed))}")
    return result


def _bounded_int(
    value: Any,
    field: str,
    *,
    minimum: int,
    maximum: int,
) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field} must be an integer")
    if value < minimum or value > maximum:
        raise ValueError(f"{field} must be between {minimum} and {maximum}")
    return value


def _task_uuid(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError("task_uuid must be a UUID string")
    try:
        return str(uuid.UUID(value.strip()))
    except (ValueError, AttributeError) as error:
        raise ValueError("task_uuid must be a valid UUID") from error


def _subscription_key(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError("subscription_key must be a string")
    result = value.strip()
    if not _SUBSCRIPTION_RE.fullmatch(result):
        raise ValueError("subscription_key contains unsupported characters")
    return result


def _response_json(response: httpx.Response) -> dict[str, Any]:
    try:
        data = response.json()
    except ValueError as error:
        raise ValueError("Rodin returned a non-JSON response") from error
    if not isinstance(data, dict):
        raise ValueError("Rodin returned an unexpected JSON payload")
    if response.status_code >= 400:
        detail = data.get("message") or data.get("error") or "request failed"
        raise ValueError(f"Rodin HTTP {response.status_code}: {detail}")
    body_error = data.get("error")
    if body_error:
        detail = data.get("message") or body_error
        raise ValueError(f"Rodin rejected request: {body_error}: {detail}")
    return data


class RodinActions:
    """Fixed-endpoint Rodin actions; credentials never enter job payloads."""

    def game_assets_rodin_submit_text(self, payload: dict[str, Any]) -> ActionResult:
        supported = {
            "project",
            "prompt",
            "tier",
            "mesh_mode",
            "geometry_file_format",
            "material",
            "quality",
            "quality_override",
            "texture_mode",
            "t_a_pose",
            "is_symmetric",
            "preview_render",
            "seed",
        }
        unsupported = set(payload) - supported
        if unsupported:
            return ActionResult(False, f"unsupported fields: {', '.join(sorted(unsupported))}")
        self._project(payload)
        api_key = os.environ.get("RODIN_API_KEY")
        if not api_key:
            return ActionResult(False, "RODIN_API_KEY is not configured")

        try:
            prompt = _text(payload.get("prompt"), "prompt", maximum=1024)
            tier = _choice(payload.get("tier"), "tier", _TIERS, "Gen-2.5-Medium")
            mesh_mode = _choice(payload.get("mesh_mode"), "mesh_mode", _MESH_MODES, "Raw")
            geometry_format = _choice(
                payload.get("geometry_file_format"),
                "geometry_file_format",
                _FORMATS,
                "glb",
            )
            material = _choice(payload.get("material"), "material", _MATERIALS, "PBR")
            quality = _choice(payload.get("quality"), "quality", _QUALITY, "medium")
            texture_mode = _choice(
                payload.get("texture_mode"), "texture_mode", _TEXTURE_MODES, "medium"
            )
            quality_override = _bounded_int(
                payload.get("quality_override"),
                "quality_override",
                minimum=500,
                maximum=2_000_000,
            )
            seed = _bounded_int(payload.get("seed"), "seed", minimum=0, maximum=65535)
            symmetry = _choice(
                payload.get("is_symmetric"), "is_symmetric", _SYMMETRY, "unknown"
            )
            if quality_override is not None:
                if mesh_mode == "Quad" and quality_override > 200_000:
                    raise ValueError("quality_override cannot exceed 200000 in Quad mode")
                if tier not in {"Gen-2.5-High", "Gen-2.5-Extreme-High"} and quality_override > 1_000_000:
                    raise ValueError(
                        "quality_override above 1000000 requires Gen-2.5-High or Gen-2.5-Extreme-High"
                    )

            form: dict[str, str] = {
                "prompt": prompt,
                "tier": tier,
                "mesh_mode": mesh_mode,
                "geometry_file_format": geometry_format,
                "material": material,
                "quality": quality,
                "texture_mode": texture_mode,
                "TAPose": "true" if bool(payload.get("t_a_pose", False)) else "false",
                "is_symmetric": symmetry,
                "preview_render": "true" if bool(payload.get("preview_render", False)) else "false",
            }
            if quality_override is not None:
                form["quality_override"] = str(quality_override)
            if seed is not None:
                form["seed"] = str(seed)

            response = httpx.post(
                f"{_BASE}/rodin",
                headers={"Authorization": f"Bearer {api_key}"},
                data=form,
                timeout=60.0,
            )
            data = _response_json(response)
        except httpx.HTTPError as error:
            return ActionResult(False, f"Rodin request failed: {type(error).__name__}")
        except ValueError as error:
            return ActionResult(False, str(error))

        task_uuid = data.get("uuid")
        jobs = data.get("jobs") if isinstance(data.get("jobs"), dict) else {}
        subscription_key = jobs.get("subscription_key")
        if not task_uuid or not subscription_key:
            return ActionResult(False, "Rodin accepted HTTP request without required task identifiers")
        return ActionResult(
            True,
            "Rodin Gen-2.5 generation submitted",
            {
                "provider": "rodin",
                "task_uuid": task_uuid,
                "subscription_key": subscription_key,
                "job_uuids": jobs.get("uuids", []),
                "consumed": data.get("consumed"),
                "request": {
                    "tier": tier,
                    "mesh_mode": mesh_mode,
                    "geometry_file_format": geometry_format,
                    "material": material,
                    "quality": quality,
                    "quality_override": quality_override,
                    "texture_mode": texture_mode,
                    "t_a_pose": bool(payload.get("t_a_pose", False)),
                    "is_symmetric": symmetry,
                },
            },
        )

    def game_assets_rodin_status(self, payload: dict[str, Any]) -> ActionResult:
        unsupported = set(payload) - {"project", "subscription_key"}
        if unsupported:
            return ActionResult(False, f"unsupported fields: {', '.join(sorted(unsupported))}")
        self._project(payload)
        api_key = os.environ.get("RODIN_API_KEY")
        if not api_key:
            return ActionResult(False, "RODIN_API_KEY is not configured")
        try:
            key = _subscription_key(payload.get("subscription_key"))
            response = httpx.post(
                f"{_BASE}/status",
                headers={"Authorization": f"Bearer {api_key}"},
                json={"subscription_key": key},
                timeout=30.0,
            )
            data = _response_json(response)
        except httpx.HTTPError as error:
            return ActionResult(False, f"Rodin status failed: {type(error).__name__}")
        except ValueError as error:
            return ActionResult(False, str(error))
        return ActionResult(
            True,
            "Rodin status retrieved",
            {"provider": "rodin", "subscription_key": key, "response": data},
        )

    def game_assets_rodin_download_manifest(self, payload: dict[str, Any]) -> ActionResult:
        unsupported = set(payload) - {"project", "task_uuid"}
        if unsupported:
            return ActionResult(False, f"unsupported fields: {', '.join(sorted(unsupported))}")
        self._project(payload)
        api_key = os.environ.get("RODIN_API_KEY")
        if not api_key:
            return ActionResult(False, "RODIN_API_KEY is not configured")
        try:
            task_uuid = _task_uuid(payload.get("task_uuid"))
            response = httpx.post(
                f"{_BASE}/download",
                headers={"Authorization": f"Bearer {api_key}"},
                json={"task_uuid": task_uuid},
                timeout=30.0,
            )
            data = _response_json(response)
        except httpx.HTTPError as error:
            return ActionResult(False, f"Rodin download manifest failed: {type(error).__name__}")
        except ValueError as error:
            return ActionResult(False, str(error))

        raw_files = data.get("list", [])
        if not isinstance(raw_files, list):
            return ActionResult(False, "Rodin download response has an invalid file list")
        files: list[dict[str, str]] = []
        for item in raw_files:
            if not isinstance(item, dict):
                continue
            name = item.get("name")
            url = item.get("url")
            if isinstance(name, str) and isinstance(url, str) and url.startswith("https://"):
                files.append({"name": name, "url": url})
        return ActionResult(
            True,
            "Rodin download manifest retrieved",
            {"provider": "rodin", "task_uuid": task_uuid, "files": files},
        )
