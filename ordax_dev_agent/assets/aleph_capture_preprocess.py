"""Convert an Aleph capture into neutral local-metre assets for Blender.

Runs with the managed Aleph virtualenv (Pillow available). It consumes only
project-local capture files and writes only into the supplied staging directory.
No network access is performed here.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from PIL import Image

R = 6_378_137.0
MAX_MERCATOR_LAT = 85.0511287798066
ROAD_WIDTHS = {
    "motorway": 12.0,
    "motorway_link": 7.0,
    "trunk": 10.0,
    "trunk_link": 7.0,
    "primary": 8.0,
    "primary_link": 6.0,
    "secondary": 7.0,
    "secondary_link": 5.5,
    "tertiary": 6.0,
    "tertiary_link": 5.0,
    "residential": 4.5,
    "living_street": 4.0,
    "service": 3.0,
    "unclassified": 4.0,
    "track": 2.5,
    "cycleway": 2.0,
    "footway": 1.5,
    "path": 1.5,
    "pedestrian": 3.0,
    "steps": 1.2,
}
_HEIGHT_RE = re.compile(r"^\s*(-?\d+(?:\.\d+)?)\s*(m|meter|meters|metre|metres|ft|feet|')?\s*$", re.I)


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--capture", required=True)
    parser.add_argument("--staging", required=True)
    parser.add_argument("--terrain-samples", type=int, default=128)
    parser.add_argument("--no-terrain", action="store_true")
    parser.add_argument("--no-buildings", action="store_true")
    parser.add_argument("--no-roads", action="store_true")
    return parser.parse_args()


def _mercator(lat: float, lon: float) -> tuple[float, float]:
    lat = max(-MAX_MERCATOR_LAT, min(MAX_MERCATOR_LAT, lat))
    x = R * math.radians(lon)
    y = R * math.log(math.tan(math.pi / 4 + math.radians(lat) / 2))
    return x, y


def _json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path.name} must contain a JSON object")
    return value


def _height(value: str | None) -> float | None:
    if not value:
        return None
    match = _HEIGHT_RE.fullmatch(value.split(";")[0].strip())
    if not match:
        return None
    result = float(match.group(1))
    if (match.group(2) or "m").lower() in {"ft", "feet", "'"}:
        result *= 0.3048
    return result if math.isfinite(result) else None


def _building_height(tags: dict[str, str]) -> tuple[float, float]:
    explicit = _height(tags.get("height"))
    if explicit is None:
        try:
            levels = float(tags.get("building:levels", ""))
        except ValueError:
            levels = 0.0
        explicit = levels * 3.2 if 0 < levels <= 100 else 9.0
    explicit = max(2.0, min(300.0, explicit))
    minimum = _height(tags.get("min_height"))
    if minimum is None:
        try:
            min_levels = float(tags.get("building:min_level", ""))
        except ValueError:
            min_levels = 0.0
        minimum = min_levels * 3.2 if 0 < min_levels <= 100 else 0.0
    return max(0.0, min(explicit - 0.1, minimum)), explicit


def _road_width(tags: dict[str, str]) -> float:
    explicit = _height(tags.get("width"))
    if explicit is not None:
        return max(0.5, min(30.0, explicit))
    return ROAD_WIDTHS.get(tags.get("highway", ""), 3.0)


def _load_osm(path: Path) -> tuple[
    dict[int, tuple[float, float]],
    dict[int, tuple[list[int], dict[str, str]]],
    list[dict[str, Any]],
]:
    """Stream OSM while capturing child attributes before elements are cleared."""
    nodes: dict[int, tuple[float, float]] = {}
    ways: dict[int, tuple[list[int], dict[str, str]]] = {}
    relations: list[dict[str, Any]] = []
    current_way: dict[str, Any] | None = None
    current_relation: dict[str, Any] | None = None

    for event, elem in ET.iterparse(path, events=("start", "end")):
        tag = elem.tag
        if event == "start":
            if tag == "way":
                try:
                    identity = int(elem.attrib["id"])
                except (KeyError, ValueError):
                    identity = 0
                current_way = {"id": identity, "refs": [], "tags": {}}
            elif tag == "relation":
                current_relation = {"tags": {}, "members": []}
            elif tag == "nd" and current_way is not None:
                try:
                    current_way["refs"].append(int(elem.attrib["ref"]))
                except (KeyError, ValueError):
                    pass
            elif tag == "member" and current_relation is not None:
                try:
                    current_relation["members"].append(
                        (
                            elem.attrib.get("type", ""),
                            int(elem.attrib.get("ref", "0")),
                            elem.attrib.get("role", ""),
                        )
                    )
                except ValueError:
                    pass
            elif tag == "tag":
                key = elem.attrib.get("k")
                value = elem.attrib.get("v")
                if key is not None and value is not None:
                    if current_way is not None:
                        current_way["tags"][key] = value
                    elif current_relation is not None:
                        current_relation["tags"][key] = value
            continue

        if tag == "node":
            try:
                nodes[int(elem.attrib["id"])] = (
                    float(elem.attrib["lat"]),
                    float(elem.attrib["lon"]),
                )
            except (KeyError, ValueError):
                pass
        elif tag == "way":
            if current_way is not None and current_way["id"]:
                ways[current_way["id"]] = (
                    current_way["refs"],
                    current_way["tags"],
                )
            current_way = None
        elif tag == "relation":
            if current_relation is not None:
                tags = current_relation["tags"]
                if tags.get("type") == "multipolygon" and tags.get("building") not in {None, "no"}:
                    relations.append(current_relation)
            current_relation = None
        elem.clear()
    return nodes, ways, relations


def _stitch(ref_chains: list[list[int]]) -> list[list[int]]:
    pending = [chain[:] for chain in ref_chains if len(chain) >= 2]
    rings: list[list[int]] = []
    while pending:
        ring = pending.pop(0)
        changed = True
        while ring[0] != ring[-1] and changed:
            changed = False
            for i, chain in enumerate(pending):
                if ring[-1] == chain[0]:
                    ring.extend(chain[1:])
                elif ring[-1] == chain[-1]:
                    ring.extend(reversed(chain[:-1]))
                elif ring[0] == chain[-1]:
                    ring = chain[:-1] + ring
                elif ring[0] == chain[0]:
                    ring = list(reversed(chain[1:])) + ring
                else:
                    continue
                pending.pop(i)
                changed = True
                break
        if len(ring) >= 4 and ring[0] == ring[-1]:
            rings.append(ring)
    return rings


class TerrainSampler:
    def __init__(self, path: Path, bbox: tuple[float, float, float, float], max_samples: int):
        metadata_image = Image.open(path)
        tags = metadata_image.tag_v2
        scale = tags.get(33550)
        tie = tags.get(33922)
        if not scale or not tie or len(scale) < 2 or len(tie) < 6:
            metadata_image.close()
            raise ValueError("terrain.tif is missing GeoTIFF scale/tiepoint tags")
        self.scale_x = float(scale[0])
        self.scale_y = float(scale[1])
        self.west = float(tie[3])
        self.north = float(tie[4])
        if self.scale_x <= 0 or self.scale_y <= 0:
            metadata_image.close()
            raise ValueError("terrain.tif has invalid pixel scale")
        metadata_image.load()
        self.image = metadata_image.convert("F")
        metadata_image.close()

        south_lat, west_lon, north_lat, east_lon = bbox
        self.min_x, self.min_y = _mercator(south_lat, west_lon)
        self.max_x, self.max_y = _mercator(north_lat, east_lon)
        width = max(1.0, self.max_x - self.min_x)
        height = max(1.0, self.max_y - self.min_y)
        max_samples = max(16, min(512, int(max_samples)))
        if width >= height:
            self.cols = max_samples
            self.rows = max(16, int(round(max_samples * height / width)))
        else:
            self.rows = max_samples
            self.cols = max(16, int(round(max_samples * width / height)))
        self.origin_x = (self.min_x + self.max_x) * 0.5
        self.origin_y = (self.min_y + self.max_y) * 0.5
        self.values: list[list[float]] = []
        for row in range(self.rows):
            t = row / (self.rows - 1)
            y = self.max_y - (self.max_y - self.min_y) * t
            line: list[float] = []
            for col in range(self.cols):
                u = col / (self.cols - 1)
                x = self.min_x + (self.max_x - self.min_x) * u
                line.append(self._sample_xy_absolute(x, y))
            self.values.append(line)
        finite = [value for row in self.values for value in row if math.isfinite(value)]
        if not finite:
            raise ValueError("terrain.tif contains no finite elevation samples inside capture bounds")
        self.base_elevation = min(finite)

    def close(self) -> None:
        self.image.close()

    def _sample_xy_absolute(self, x: float, y: float) -> float:
        px = int(round((x - self.west) / self.scale_x))
        py = int(round((self.north - y) / self.scale_y))
        px = max(0, min(self.image.width - 1, px))
        py = max(0, min(self.image.height - 1, py))
        value = float(self.image.getpixel((px, py)))
        return value if math.isfinite(value) else 0.0

    def sample_latlon(self, lat: float, lon: float) -> float:
        x, y = _mercator(lat, lon)
        return self._sample_xy_absolute(x, y) - self.base_elevation

    def write_obj(self, path: Path) -> dict[str, Any]:
        with path.open("w", encoding="utf-8", newline="\n") as out:
            out.write("o Aleph_Terrain\n")
            for row in range(self.rows):
                t = row / (self.rows - 1)
                y = self.max_y - (self.max_y - self.min_y) * t
                for col in range(self.cols):
                    u = col / (self.cols - 1)
                    x = self.min_x + (self.max_x - self.min_x) * u
                    z = self.values[row][col] - self.base_elevation
                    out.write(f"v {x - self.origin_x:.6f} {y - self.origin_y:.6f} {z:.6f}\n")
            for row in range(self.rows):
                v = 1.0 - row / (self.rows - 1)
                for col in range(self.cols):
                    u = col / (self.cols - 1)
                    out.write(f"vt {u:.8f} {v:.8f}\n")
            for row in range(self.rows - 1):
                for col in range(self.cols - 1):
                    a = row * self.cols + col + 1
                    b = a + 1
                    d = (row + 1) * self.cols + col + 1
                    c = d + 1
                    out.write(f"f {a}/{a} {b}/{b} {c}/{c} {d}/{d}\n")
        return {
            "vertices": self.rows * self.cols,
            "faces": (self.rows - 1) * (self.cols - 1),
            "rows": self.rows,
            "columns": self.cols,
            "base_elevation_m": self.base_elevation,
        }


def _local_point(
    nodes: dict[int, tuple[float, float]], ref: int, origin: tuple[float, float]
) -> tuple[float, float] | None:
    point = nodes.get(ref)
    if point is None:
        return None
    x, y = _mercator(point[0], point[1])
    return x - origin[0], y - origin[1]


def _write_buildings(
    path: Path,
    nodes: dict[int, tuple[float, float]],
    ways: dict[int, tuple[list[int], dict[str, str]]],
    relations: list[dict[str, Any]],
    origin: tuple[float, float],
    terrain: TerrainSampler | None,
) -> dict[str, Any]:
    candidates: list[tuple[str, list[int], dict[str, str]]] = []
    relation_member_ids: set[int] = set()
    relation_holes = 0
    for index, relation in enumerate(relations):
        chains = []
        for kind, ref, role in relation["members"]:
            if kind != "way":
                continue
            if role == "inner":
                relation_holes += 1
                continue
            if role not in {"", "outer"}:
                continue
            way = ways.get(ref)
            if way is not None:
                chains.append(way[0])
                relation_member_ids.add(ref)
        for ring_index, ring in enumerate(_stitch(chains)):
            candidates.append((f"relation_{index}_{ring_index}", ring, relation["tags"]))

    for identity, (refs, tags) in ways.items():
        if identity in relation_member_ids:
            continue
        building = tags.get("building")
        if building and building not in {"no", "roof"} and len(refs) >= 4 and refs[0] == refs[-1]:
            candidates.append((f"way_{identity}", refs, tags))

    vertex_index = 1
    buildings = vertices = faces = skipped = 0
    with path.open("w", encoding="utf-8", newline="\n") as out:
        out.write("o Aleph_Buildings\n")
        for name, refs, tags in candidates:
            points: list[tuple[float, float]] = []
            latlon: list[tuple[float, float]] = []
            for ref in refs[:-1]:
                local = _local_point(nodes, ref, origin)
                raw = nodes.get(ref)
                if local is None or raw is None:
                    points = []
                    break
                points.append(local)
                latlon.append(raw)
            if len(points) < 3:
                skipped += 1
                continue
            min_h, height = _building_height(tags)
            if terrain is not None:
                lat = sum(point[0] for point in latlon) / len(latlon)
                lon = sum(point[1] for point in latlon) / len(latlon)
                ground = terrain.sample_latlon(lat, lon)
            else:
                ground = 0.0
            bottom, top = ground + min_h, ground + height
            out.write(f"g {name}\n")
            for x, y in points:
                out.write(f"v {x:.6f} {y:.6f} {bottom:.6f}\n")
            for x, y in points:
                out.write(f"v {x:.6f} {y:.6f} {top:.6f}\n")
            n = len(points)
            bottom_ids = list(range(vertex_index, vertex_index + n))
            top_ids = list(range(vertex_index + n, vertex_index + 2 * n))
            out.write("f " + " ".join(str(i) for i in reversed(bottom_ids)) + "\n")
            out.write("f " + " ".join(str(i) for i in top_ids) + "\n")
            faces += 2
            for i in range(n):
                j = (i + 1) % n
                out.write(f"f {bottom_ids[i]} {bottom_ids[j]} {top_ids[j]} {top_ids[i]}\n")
                faces += 1
            vertex_index += 2 * n
            vertices += 2 * n
            buildings += 1
    return {
        "buildings": buildings,
        "vertices": vertices,
        "faces": faces,
        "skipped": skipped,
        "multipolygon_inner_rings_simplified": relation_holes,
    }


def _write_roads(
    path: Path,
    nodes: dict[int, tuple[float, float]],
    ways: dict[int, tuple[list[int], dict[str, str]]],
    origin: tuple[float, float],
    terrain: TerrainSampler | None,
) -> dict[str, Any]:
    roads: list[dict[str, Any]] = []
    skipped = 0
    for identity, (refs, tags) in ways.items():
        highway = tags.get("highway")
        if not highway or len(refs) < 2:
            continue
        points = []
        for ref in refs:
            raw = nodes.get(ref)
            local = _local_point(nodes, ref, origin)
            if raw is None or local is None:
                continue
            z = terrain.sample_latlon(raw[0], raw[1]) if terrain is not None else 0.0
            points.append([local[0], local[1], z + 0.05])
        if len(points) < 2:
            skipped += 1
            continue
        roads.append(
            {
                "id": identity,
                "highway": highway,
                "name": tags.get("name"),
                "width_m": _road_width(tags),
                "points": points,
            }
        )
    path.write_text(json.dumps({"roads": roads}, separators=(",", ":")), encoding="utf-8")
    return {
        "roads": len(roads),
        "skipped": skipped,
        "points": sum(len(road["points"]) for road in roads),
    }


def main() -> int:
    args = _args()
    capture = Path(args.capture).resolve()
    staging = Path(args.staging).resolve()
    staging.mkdir(parents=True, exist_ok=True)
    manifest_path = capture / "manifest.json"
    if not manifest_path.is_file():
        raise SystemExit("capture does not contain manifest.json")
    manifest = _json(manifest_path)
    if manifest.get("format") != "aleph-python" or not isinstance(manifest.get("version"), int):
        raise SystemExit("unsupported Aleph capture manifest")
    bounds = manifest.get("bounds")
    if not isinstance(bounds, list) or len(bounds) != 4:
        raise SystemExit("Aleph manifest bounds are missing")
    bbox = tuple(float(value) for value in bounds)
    south, west, north, east = bbox
    if not (-90 <= south < north <= 90 and -180 <= west < east <= 180):
        raise SystemExit("Aleph manifest bounds are invalid")
    min_x, min_y = _mercator(south, west)
    max_x, max_y = _mercator(north, east)
    origin = ((min_x + max_x) * 0.5, (min_y + max_y) * 0.5)

    report: dict[str, Any] = {
        "schema": "ordax.aleph-preprocess/1",
        "capture": str(capture),
        "aleph_format_version": manifest["version"],
        "bounds": list(bbox),
        "origin_epsg3857": [origin[0], origin[1]],
        "size_m": [max_x - min_x, max_y - min_y],
        "assets": {},
        "warnings": [],
    }

    terrain: TerrainSampler | None = None
    try:
        terrain_path = capture / "terrain.tif"
        if not args.no_terrain and terrain_path.is_file():
            terrain = TerrainSampler(terrain_path, bbox, args.terrain_samples)
            terrain_obj = staging / "terrain.obj"
            report["assets"]["terrain"] = {
                "path": str(terrain_obj),
                **terrain.write_obj(terrain_obj),
            }
        elif not args.no_terrain:
            report["warnings"].append("terrain.tif is not available")

        osm_path = capture / "map.osm"
        if (not args.no_buildings or not args.no_roads) and osm_path.is_file():
            nodes, ways, relations = _load_osm(osm_path)
            report["osm"] = {
                "nodes": len(nodes),
                "ways": len(ways),
                "building_relations": len(relations),
            }
            if not args.no_buildings:
                building_obj = staging / "buildings.obj"
                report["assets"]["buildings"] = {
                    "path": str(building_obj),
                    **_write_buildings(building_obj, nodes, ways, relations, origin, terrain),
                }
            if not args.no_roads:
                roads_json = staging / "roads.json"
                report["assets"]["roads"] = {
                    "path": str(roads_json),
                    **_write_roads(roads_json, nodes, ways, origin, terrain),
                }
        elif not args.no_buildings or not args.no_roads:
            report["warnings"].append("map.osm is not available")

        satellite = capture / "satellite.png"
        if satellite.is_file():
            report["assets"]["satellite"] = {
                "path": str(satellite),
                "bytes": satellite.stat().st_size,
            }
    finally:
        if terrain is not None:
            terrain.close()

    report_path = staging / "preprocess.json"
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({"ok": True, "report": str(report_path)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
