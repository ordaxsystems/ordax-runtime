"""Project-local Unity asset inventory for typed OrdaX actions."""

from __future__ import annotations

from collections import Counter
import hashlib
import os
import shutil
from pathlib import Path
from typing import Any


CLASS_BY_SUFFIX = {
    ".prefab": "prefab",
    ".mat": "material",
    ".asset": "asset",
    ".terrainlayer": "terrain_layer",
    ".unity": "scene",
    ".fbx": "model",
    ".obj": "model",
    ".blend": "model",
    ".png": "texture",
    ".jpg": "texture",
    ".jpeg": "texture",
    ".tga": "texture",
    ".psd": "texture",
    ".exr": "texture",
    ".hdr": "texture",
    ".wav": "audio",
    ".mp3": "audio",
    ".ogg": "audio",
    ".shader": "shader",
    ".shadergraph": "shader_graph",
    ".cs": "script",
}


def asset_inventory(
    project_root: Path,
    *,
    terms: list[str] | None = None,
    max_results: int = 500,
) -> dict[str, Any]:
    assets = project_root.resolve() / "Assets"
    if not assets.is_dir():
        return {
            "assets_root": str(assets),
            "exists": False,
            "total_files": 0,
            "matches": [],
            "counts_by_kind": {},
        }

    normalized_terms = [
        term.strip().lower()
        for term in (terms or [])
        if isinstance(term, str) and term.strip()
    ]
    max_results = max(1, min(int(max_results), 2000))

    matches: list[dict[str, Any]] = []
    counts: Counter[str] = Counter()
    total = 0

    # os.walk preserves the textual root that was supplied. This matters on
    # Windows, where tempfile / CI paths can be represented simultaneously as
    # an 8.3 alias (RUNNER~1) and a long path (runneradmin). Path.rglob may
    # surface the long spelling and make relative_to() reject the same physical
    # directory as unrelated.
    for current_root, _, file_names in os.walk(str(assets)):
        current = Path(current_root)
        try:
            inside_assets = current.relative_to(assets)
        except ValueError:
            # Defensive fallback: the walk must never escape Assets.
            continue

        for file_name in file_names:
            path = current / file_name
            if path.suffix.lower() == ".meta":
                continue
            total += 1
            suffix = path.suffix.lower()
            kind = CLASS_BY_SUFFIX.get(suffix, "other")
            counts[kind] += 1

            rel_path = Path("Assets") / inside_assets / file_name
            rel = rel_path.as_posix()
            haystack = rel.lower()
            if normalized_terms and not all(term in haystack for term in normalized_terms):
                continue
            if len(matches) < max_results:
                try:
                    size = path.stat().st_size
                except OSError:
                    size = None
                matches.append({
                    "path": rel,
                    "name": path.name,
                    "kind": kind,
                    "extension": suffix,
                    "size_bytes": size,
                })

    return {
        "assets_root": str(assets),
        "exists": True,
        "query_terms": normalized_terms,
        "total_files": total,
        "matched_files": len(matches),
        "matches_truncated": len(matches) >= max_results,
        "counts_by_kind": dict(sorted(counts.items())),
        "matches": matches,
    }


IMPORTABLE_MODEL_SUFFIXES = {".fbx", ".obj", ".blend", ".glb", ".gltf"}


def import_project_asset(
    project_root: Path,
    *,
    source_path: Path,
    destination_path: Path,
    overwrite: bool = False,
) -> dict[str, Any]:
    """Copy one project-local model export into Unity Assets atomically.

    Both source and destination are resolved and constrained to the registered
    project. The destination must be under Assets/ and keep a supported model
    extension. Existing identical files are treated as an idempotent success;
    different content is refused unless overwrite=True.
    """
    root = project_root.resolve()
    source = source_path.resolve()
    destination = destination_path.resolve()
    assets_root = (root / "Assets").resolve()

    try:
        source.relative_to(root)
    except ValueError as error:
        raise ValueError("source_path must be inside the registered project") from error
    try:
        destination.relative_to(assets_root)
    except ValueError as error:
        raise ValueError("destination_path must be inside Assets") from error

    if not source.is_file():
        raise FileNotFoundError(source)
    suffix = source.suffix.lower()
    if suffix not in IMPORTABLE_MODEL_SUFFIXES:
        raise ValueError(
            "source asset extension must be one of: "
            + ", ".join(sorted(IMPORTABLE_MODEL_SUFFIXES))
        )
    if destination.suffix.lower() != suffix:
        raise ValueError("destination asset extension must match source extension")

    source_bytes = source.read_bytes()
    source_sha = hashlib.sha256(source_bytes).hexdigest()
    source_size = len(source_bytes)

    if destination.exists():
        if not destination.is_file():
            raise ValueError("destination_path exists and is not a file")
        destination_sha = hashlib.sha256(destination.read_bytes()).hexdigest()
        if destination_sha == source_sha:
            return {
                "imported": False,
                "already_current": True,
                "source": str(source),
                "destination": str(destination),
                "asset_path": destination.relative_to(root).as_posix(),
                "sha256": source_sha,
                "size_bytes": source_size,
            }
        if not overwrite:
            raise FileExistsError(
                f"destination exists with different content: {destination}"
            )

    destination.parent.mkdir(parents=True, exist_ok=True)
    temp = destination.with_name(destination.name + ".ordax-tmp")
    try:
        shutil.copy2(source, temp)
        copied_sha = hashlib.sha256(temp.read_bytes()).hexdigest()
        if copied_sha != source_sha:
            raise IOError("copied asset hash does not match source")
        temp.replace(destination)
    finally:
        try:
            temp.unlink(missing_ok=True)
        except OSError:
            pass

    return {
        "imported": True,
        "already_current": False,
        "source": str(source),
        "destination": str(destination),
        "asset_path": destination.relative_to(root).as_posix(),
        "sha256": source_sha,
        "size_bytes": source_size,
    }
