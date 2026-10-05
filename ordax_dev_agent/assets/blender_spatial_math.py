"""Pure deterministic spatial math used by the Blender companion."""
from __future__ import annotations


def aabb_overlap(left: dict, right: dict) -> bool:
    left_min, left_max = left.get("aabb_min"), left.get("aabb_max")
    right_min, right_max = right.get("aabb_min"), right.get("aabb_max")
    if not all((left_min, left_max, right_min, right_max)):
        return False
    return all(
        left_min[axis] <= right_max[axis]
        and left_max[axis] >= right_min[axis]
        for axis in range(3)
    )


def aabb_contains(
    outer: dict,
    inner: dict,
    tolerance: float = 1e-6,
) -> bool:
    outer_min, outer_max = outer.get("aabb_min"), outer.get("aabb_max")
    inner_min, inner_max = inner.get("aabb_min"), inner.get("aabb_max")
    if not all((outer_min, outer_max, inner_min, inner_max)):
        return False
    return all(
        inner_min[axis] >= outer_min[axis] - tolerance
        and inner_max[axis] <= outer_max[axis] + tolerance
        for axis in range(3)
    )
