"""Pure deterministic geometry helpers used by the Blender companion.

This module intentionally imports no Blender APIs so it can be unit-tested in
ordinary Python and safely fingerprinted as part of the companion bundle.
"""
from __future__ import annotations


def triangle_area_2d(points) -> float:
    (ax, ay), (bx, by), (cx, cy) = points
    return abs(
        (bx - ax) * (cy - ay)
        - (by - ay) * (cx - ax)
    ) * 0.5


def signed_area_2d(points) -> float:
    area = 0.0
    for index, point in enumerate(points):
        next_point = points[(index + 1) % len(points)]
        area += point[0] * next_point[1] - next_point[0] * point[1]
    return area * 0.5


def line_intersection_2d(p1, p2, q1, q2):
    px = p2[0] - p1[0]
    py = p2[1] - p1[1]
    qx = q2[0] - q1[0]
    qy = q2[1] - q1[1]
    denominator = px * qy - py * qx
    if abs(denominator) <= 1e-15:
        return p2
    t = (
        (q1[0] - p1[0]) * qy
        - (q1[1] - p1[1]) * qx
    ) / denominator
    return (p1[0] + t * px, p1[1] + t * py)


def triangle_overlap_area_2d(subject, clip) -> float:
    output = list(subject)
    orientation = 1.0 if signed_area_2d(clip) >= 0 else -1.0

    def inside(point, edge_a, edge_b) -> bool:
        cross = (
            (edge_b[0] - edge_a[0]) * (point[1] - edge_a[1])
            - (edge_b[1] - edge_a[1]) * (point[0] - edge_a[0])
        )
        return orientation * cross >= -1e-12

    for index, edge_a in enumerate(clip):
        edge_b = clip[(index + 1) % len(clip)]
        if not output:
            return 0.0
        input_points = output
        output = []
        previous = input_points[-1]
        previous_inside = inside(previous, edge_a, edge_b)
        for current in input_points:
            current_inside = inside(current, edge_a, edge_b)
            if current_inside:
                if not previous_inside:
                    output.append(
                        line_intersection_2d(
                            previous,
                            current,
                            edge_a,
                            edge_b,
                        )
                    )
                output.append(current)
            elif previous_inside:
                output.append(
                    line_intersection_2d(
                        previous,
                        current,
                        edge_a,
                        edge_b,
                    )
                )
            previous = current
            previous_inside = current_inside

    if len(output) < 3:
        return 0.0
    return abs(signed_area_2d(output))

def triangle_shape_distortion(
    geometry_points,
    uv_points,
    epsilon: float,
) -> float | None:
    """Compare normalized triangle edge proportions in 3D and UV space."""
    if len(geometry_points) != 3 or len(uv_points) != 3:
        raise ValueError("triangle_shape_distortion requires exactly three points")

    geometry_lengths = []
    for index in range(3):
        first = geometry_points[index]
        second = geometry_points[(index + 1) % 3]
        if len(first) < 3 or len(second) < 3:
            raise ValueError("geometry points must contain x, y and z")
        geometry_lengths.append(
            (
                (float(second[0]) - float(first[0])) ** 2
                + (float(second[1]) - float(first[1])) ** 2
                + (float(second[2]) - float(first[2])) ** 2
            ) ** 0.5
        )

    uv_lengths = []
    for index in range(3):
        first = uv_points[index]
        second = uv_points[(index + 1) % 3]
        uv_lengths.append(
            (
                (float(second[0]) - float(first[0])) ** 2
                + (float(second[1]) - float(first[1])) ** 2
            ) ** 0.5
        )

    geometry_total = sum(geometry_lengths)
    uv_total = sum(uv_lengths)
    if geometry_total <= epsilon or uv_total <= epsilon:
        return None

    geometry_normalized = [
        value / geometry_total for value in geometry_lengths
    ]
    uv_normalized = [
        value / uv_total for value in uv_lengths
    ]
    return max(
        abs(geometry_normalized[index] - uv_normalized[index])
        for index in range(3)
    )

