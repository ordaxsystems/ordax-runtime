"""Typed armature contracts: same validation in OrdaX host and Blender bpy."""
from __future__ import annotations

import math
import re
from typing import Any


def _name(value: Any, field: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string")
    name = value.strip()
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_. -]{0,62}", name):
        raise ValueError(f"{field} must be a bounded Blender identifier")
    return name


def _vector(value: Any, field: str) -> list[float]:
    if not isinstance(value, list) or len(value) != 3:
        raise ValueError(f"{field} must be a list of three finite numbers")
    result = []
    for item in value:
        if isinstance(item, bool) or not isinstance(item, (int, float)):
            raise ValueError(f"{field} must contain finite numbers")
        number = float(item)
        if not math.isfinite(number) or abs(number) > 10000:
            raise ValueError(f"{field} has an out-of-range number")
        result.append(number)
    return result


def normalize_rig_create(
    payload: dict[str, Any],
    *,
    transport_fields: frozenset[str] | set[str] = frozenset(),
) -> dict[str, Any]:
    """Validate before any mutation; parents must precede their children."""
    if not isinstance(payload, dict):
        raise ValueError("rig_create payload must be an object")
    extras = sorted(set(payload) - {"name", "location", "bones"} - set(transport_fields))
    if extras:
        raise ValueError("unsupported field(s): " + ", ".join(extras))

    name = _name(payload.get("name"), "name")
    location = _vector(payload.get("location", [0, 0, 0]), "location")
    source = payload.get("bones")
    if not isinstance(source, list) or not (1 <= len(source) <= 64):
        raise ValueError("bones must be a list containing 1 to 64 entries")

    seen = set()
    bones = []
    for index, item in enumerate(source):
        field = f"bones[{index}]"
        if not isinstance(item, dict):
            raise ValueError(f"{field} must be an object")
        extras = sorted(set(item) - {"name", "head", "tail", "parent"})
        if extras:
            raise ValueError(f"{field} unsupported field(s): " + ", ".join(extras))
        bone_name = _name(item.get("name"), f"{field}.name")
        if bone_name in seen:
            raise ValueError(f"duplicate bone name: {bone_name}")
        head = _vector(item.get("head"), f"{field}.head")
        tail = _vector(item.get("tail"), f"{field}.tail")
        if math.dist(head, tail) < 0.0001:
            raise ValueError(f"{field} must have a nonzero bone length")
        parent = item.get("parent")
        if parent is not None:
            parent = _name(parent, f"{field}.parent")
            if parent not in seen:
                raise ValueError(f"{field}.parent must reference an earlier bone")
        bones.append({"name": bone_name, "head": head, "tail": tail, "parent": parent})
        seen.add(bone_name)

    return {"name": name, "location": location, "bones": bones}
