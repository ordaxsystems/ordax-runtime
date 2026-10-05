"""Pure validation helpers for Blender quality-gate inputs."""
from __future__ import annotations

QUALITY_AXES = {"x": 0, "y": 1, "z": 2}


def quality_axis(value, field: str) -> tuple[str, int]:
    axis = str(value or "").strip().lower()
    if axis not in QUALITY_AXES:
        raise ValueError(f"{field} must be one of x, y, z")
    return axis, QUALITY_AXES[axis]


def quality_tolerance(check: dict, default: float = 0.01) -> float:
    try:
        tolerance = float(check.get("tolerance", default))
    except (TypeError, ValueError):
        raise ValueError("tolerance must be a number")
    if tolerance < 0 or tolerance > 1000000:
        raise ValueError("tolerance must be between 0 and 1000000")
    return tolerance
