"""Closed-world Blender material contracts shared by host and companion."""
from __future__ import annotations

import math
from typing import Any

_HOST_META_FIELDS = {"project", "timeout_seconds"}


def _bounded_string(value: Any, field: str, *, max_utf8_bytes: int = 63) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string")
    result = value.strip()
    if (
        not result
        or len(result.encode("utf-8")) > max_utf8_bytes
        or any(ord(character) < 32 for character in result)
    ):
        raise ValueError(f"{field} must be a non-empty bounded UTF-8 string")
    return result


def _finite_number(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be a finite number")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{field} must be a finite number")
    return result


def _unit_number(value: Any, field: str) -> float:
    result = _finite_number(value, field)
    if result < 0.0 or result > 1.0:
        raise ValueError(f"{field} must be between 0 and 1")
    return result


def _selector(payload: dict[str, Any]) -> dict[str, str]:
    raw_name = payload.get("object_name")
    raw_id = payload.get("ordax_object_id")
    name = raw_name.strip() if isinstance(raw_name, str) else ""
    object_id = raw_id.strip() if isinstance(raw_id, str) else ""
    if bool(name) == bool(object_id):
        raise ValueError("provide exactly one of object_name or ordax_object_id")
    if name:
        return {"object_name": _bounded_string(name, "object_name")}
    return {
        "ordax_object_id": _bounded_string(
            object_id,
            "ordax_object_id",
            max_utf8_bytes=255,
        )
    }


def normalize_material_request(
    payload: dict[str, Any],
    *,
    transport_fields: set[str] | None = None,
) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("material payload must be an object")
    allowed = {
        "object_name",
        "ordax_object_id",
        "material_name",
        "base_color",
        "roughness",
        "metallic",
        "transmission",
        "alpha",
        "ior",
        "surface_render_method",
        "transparency_overlap",
    }
    ignored = _HOST_META_FIELDS if transport_fields is None else set(transport_fields)
    unsupported = sorted(set(payload) - allowed - ignored)
    if unsupported:
        raise ValueError("unsupported field(s): " + ", ".join(unsupported))

    color = payload.get("base_color", [0.8, 0.8, 0.8, 1.0])
    if not isinstance(color, list) or len(color) not in {3, 4}:
        raise ValueError("base_color must be an RGB or RGBA list")
    normalized_color = [_unit_number(value, "base_color") for value in color]
    if len(normalized_color) == 3:
        normalized_color.append(1.0)

    ior = _finite_number(payload.get("ior", 1.45), "ior")
    if ior < 1.0 or ior > 3.0:
        raise ValueError("ior must be between 1 and 3")

    alpha = _unit_number(payload.get("alpha", normalized_color[3]), "alpha")
    normalized_color[3] = alpha

    surface_render_method = payload.get("surface_render_method")
    if surface_render_method is None:
        surface_render_method = (
            "BLENDED"
            if alpha < 0.999 or _unit_number(payload.get("transmission", 0.0), "transmission") > 0.0
            else "DITHERED"
        )
    if not isinstance(surface_render_method, str):
        raise ValueError("surface_render_method must be a string")
    surface_render_method = surface_render_method.strip().upper()
    if surface_render_method not in {"DITHERED", "BLENDED"}:
        raise ValueError("surface_render_method must be DITHERED or BLENDED")

    transparency_overlap = payload.get("transparency_overlap", True)
    if not isinstance(transparency_overlap, bool):
        raise ValueError("transparency_overlap must be boolean")

    transmission = _unit_number(payload.get("transmission", 0.0), "transmission")

    return {
        **_selector(payload),
        "material_name": _bounded_string(payload.get("material_name"), "material_name"),
        "base_color": normalized_color,
        "roughness": _unit_number(payload.get("roughness", 0.4), "roughness"),
        "metallic": _unit_number(payload.get("metallic", 0.0), "metallic"),
        "transmission": transmission,
        "alpha": alpha,
        "ior": ior,
        "surface_render_method": surface_render_method,
        "transparency_overlap": transparency_overlap,
    }
