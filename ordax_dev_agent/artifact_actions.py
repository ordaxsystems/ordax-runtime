"""Project-scoped artifact preview actions."""
from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
from typing import Any

from .models import ActionResult
from .project_local_service import (
    LocalServiceError, LocalServicePolicy, LocalServiceResponse, read_local_service_batch,
    resolve_local_service_policy,
)


class ArtifactActions:
    def _artifact_path(self, payload: dict[str, Any]) -> Path | ActionResult:
        project_artifact = str(payload.get("project_artifact_path") or "").strip()
        if project_artifact:
            project = self._project(payload)
            root = (project.root / "Artifacts").resolve()
            path = (root / project_artifact).resolve()
        else:
            name = str(payload.get("artifact_name", "hordax-prototype.png"))
            root = (self.config.state_dir / "artifacts" / self._project(payload).slug).resolve()
            if name in {"hordax-prototype.png", "hordax-prototype.json", "latest.png", "latest.json"}:
                manifest = root / "latest.json"
                if not manifest.is_file():
                    return ActionResult(False, "No successful capture for this project yet")
                latest = json.loads(manifest.read_text(encoding="utf-8"))
                path = Path(
                    latest["snapshot_path" if name.endswith(".json") else "artifact"]
                ).resolve()
            else:
                path = (root / name).resolve()

        try:
            path.relative_to(root)
        except ValueError:
            return ActionResult(False, "artifact path escaped allowed root")

        if not path.is_file():
            return ActionResult(False, f"artifact not found: {path}")
        return path

    def artifacts_list(self, payload: dict[str, Any]) -> ActionResult:
        project = self._project(payload)
        max_items = payload.get("max_items", 100)
        if type(max_items) is not int or not 1 <= max_items <= 500:
            return ActionResult(False, "max_items must be between 1 and 500")

        roots = (
            ("managed", (self.config.state_dir / "artifacts" / project.slug).resolve()),
            ("project", (project.root / "Artifacts").resolve()),
        )
        items: list[dict[str, Any]] = []
        truncated = False

        def ordered_files(folder: Path):
            # Sort one directory at a time and stop at max_items + 1.
            # Sorting the entire recursive tree scales poorly for galleries.
            for candidate in sorted(folder.iterdir()):
                if candidate.is_dir() and not candidate.is_symlink():
                    yield from ordered_files(candidate)
                elif candidate.is_file():
                    yield candidate

        for source, root in roots:
            if not root.is_dir():
                continue
            for candidate in ordered_files(root):
                try:
                    resolved = candidate.resolve()
                    relative = resolved.relative_to(root)
                except (OSError, ValueError):
                    continue
                if len(items) >= max_items:
                    truncated = True
                    break
                stat = resolved.stat()
                items.append(
                    {
                        "source": source,
                        "name": resolved.name,
                        "relative_path": relative.as_posix(),
                        "size_bytes": stat.st_size,
                        "modified_at_unix": int(stat.st_mtime),
                    }
                )
            if truncated:
                break

        return ActionResult(
            True,
            "artifact catalog ready",
            {
                "project": project.slug,
                "items": items,
                "truncated": truncated,
                "max_items": max_items,
            },
        )

    def artifact_read_chunk(self, payload: dict[str, Any]) -> ActionResult:
        """Read a bounded part of a project artifact for resumable remote delivery."""
        path = self._artifact_path(payload)
        if isinstance(path, ActionResult):
            return path
        offset = payload.get("offset", 0)
        max_bytes = payload.get("max_bytes", 32768)
        if type(offset) is not int or offset < 0:
            return ActionResult(False, "offset must be a nonnegative integer")
        if type(max_bytes) is not int or not 1 <= max_bytes <= 32768:
            return ActionResult(False, "max_bytes must be between 1 and 32768")
        expected = payload.get("source_version")
        if offset and (not isinstance(expected, str) or len(expected) != 64):
            return ActionResult(False, "source_version from the first chunk is required to resume")

        def version(stat) -> str:
            # A change detector, not a full-file content digest.
            identity = [str(path), stat.st_dev, stat.st_ino, stat.st_size,
                        stat.st_mtime_ns, stat.st_ctime_ns]
            return hashlib.sha256(json.dumps(identity).encode("utf-8")).hexdigest()

        with path.open("rb") as handle:
            before = os.fstat(handle.fileno())
            path_before = path.stat()
            # Windows fstat and stat can report different ctime semantics.
            # Compare the open handle and path by shared identity; compare
            # each source's own change token before and after the read.
            def identity(stat):
                return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns

            if identity(before) != identity(path_before):
                return ActionResult(False, "artifact changed before read; discard download",
                                    {"error_code": "artifact_changed"})
            observed = version(before)
            if expected is not None and expected != observed:
                return ActionResult(False, "artifact changed; restart download from offset zero",
                                    {"error_code": "artifact_changed"})
            if offset > before.st_size:
                return ActionResult(False, "offset is beyond end of artifact")
            handle.seek(offset)
            chunk = handle.read(max_bytes)
            if version(os.fstat(handle.fileno())) != observed or version(path.stat()) != version(path_before):
                return ActionResult(False, "artifact changed during read; discard download",
                                    {"error_code": "artifact_changed"})
        next_offset = offset + len(chunk)
        return ActionResult(True, "artifact chunk ready", {
            "artifact_name": path.name,
            "source_version": observed,
            "source_size_bytes": before.st_size,
            "offset": offset,
            "next_offset": next_offset,
            "size_bytes": len(chunk),
            "eof": next_offset == before.st_size,
            "chunk_sha256": hashlib.sha256(chunk).hexdigest(),
            "base64": base64.b64encode(chunk).decode("ascii"),
        })

    def artifact_local_service_preview(self, payload: dict[str, Any]) -> ActionResult:
        """Read a configured service only within an attested Product scope."""
        allowed = {"project", "service_id", "requests", "max_width", "max_height", "quality", "max_bytes"}
        if set(payload) - allowed:
            return ActionResult(False, "local_service_request_invalid")
        scoped = getattr(getattr(self, "_project_execution_local", None), "project", None)
        if scoped is None or payload.get("project") != scoped.slug:
            return ActionResult(False, "local_service_project_not_attested")
        limits = {}
        for name, default, minimum, maximum in (
            ("max_width", 1600, 64, 4096), ("max_height", 1200, 64, 4096),
            ("quality", 72, 30, 92), ("max_bytes", 262144, 4096, 262144),
        ):
            value = payload.get(name, default)
            if type(value) is not int or not minimum <= value <= maximum:
                return ActionResult(False, "local_service_preview_budget_invalid")
            limits[name] = value
        try:
            project = self._project(payload)
            policy = resolve_local_service_policy(
                self.config.state_dir / "agent-settings.json", project.slug, project.root,
                payload.get("service_id"),
            )
        except LocalServiceError as error:
            return ActionResult(False, str(error))
        return self.local_service_image_sheet(policy, payload.get("requests"), **limits)

    def local_service_image_sheet(
        self, policy: LocalServicePolicy, requests: list[dict], *,
        max_width: int = 1600, max_height: int = 1200, quality: int = 72,
        max_bytes: int = 262144,
    ) -> ActionResult:
        """Internal adapter; policy must come from verified Owner authority.

        Not a registered MCP action. The public artifact grant does not confer
        local HTTP authority. Source hashes identify response bytes, not the
        host's original media or an editorial identity.
        """
        try:
            responses = read_local_service_batch(policy, requests)
        except LocalServiceError as error:
            return ActionResult(False, str(error))
        return self._artifact_image_sheet({
            "max_width": max_width, "max_height": max_height,
            "quality": quality, "max_bytes": max_bytes,
        }, service_responses=responses)

    def _artifact_image_sheet(
        self, payload: dict[str, Any], *,
        service_responses: list[LocalServiceResponse] | None = None,
    ) -> ActionResult:
        """Shared renderer for authorized files and trusted HTTP response bytes."""
        import io
        import math
        import time

        started = time.monotonic()
        if service_responses is None:
            paths = payload.get("project_artifact_paths")
            if not isinstance(paths, list) or not 1 <= len(paths) <= 100:
                return ActionResult(False, "project_artifact_paths must contain 1 to 100 paths")
            if any(not isinstance(path, str) or not path.strip() for path in paths):
                return ActionResult(False, "each artifact path must be a nonempty string")
            if payload.get("artifact_name") is not None or payload.get("project_artifact_path") is not None:
                return ActionResult(False, "choose a single artifact or a batch")
            if sum(len(path.encode("utf-8")) for path in paths) > 16384:
                return ActionResult(False, "batch path byte budget exceeded")
            if any(Path(path).is_absolute() or Path(path).drive for path in paths):
                return ActionResult(False, "batch paths must be relative to project Artifacts")
        else:
            paths = service_responses
            if not paths or len(paths) > 8:
                return ActionResult(False, "invalid local service image batch")
            if any(response.media_type not in {"image/png", "image/jpeg", "image/webp"}
                   for response in paths):
                return ActionResult(False, "local service sheet requires image responses")
        try:
            from PIL import Image, ImageDraw, ImageOps
        except ImportError:
            return ActionResult(False, "Pillow is required for artifact thumbnails")

        def bounded(name: str, default: int, low: int, high: int) -> int:
            value = payload.get(name, default)
            if type(value) is not int or not low <= value <= high:
                raise ValueError(f"{name} must be between {low} and {high}")
            return value

        try:
            width = bounded("max_width", 1600, 64, 4096)
            height = bounded("max_height", 1200, 64, 4096)
            quality = bounded("quality", 72, 30, 92)
            max_bytes = bounded("max_bytes", 262144, 4096, 262144)
            columns = max(1, min(len(paths), round(math.sqrt(len(paths) * width / height))))
            rows = math.ceil(len(paths) / columns)
            cell_width, cell_height = width // columns, height // rows
            if cell_width < 48 or cell_height < 64:
                return ActionResult(False, "sheet resolution is too small for this batch")
            label_height = 22
            sheet = Image.new("RGB", (columns * cell_width, rows * cell_height), "white")
            draw = ImageDraw.Draw(sheet)
            root = None
            if service_responses is None:
                project = self._project(payload)
                root = (project.root / "Artifacts").resolve()
            seen: dict[str, str] = {}
            perceptual: list[tuple[str, int, float]] = []
            response_formats: dict[str, str] = {}
            # Same-path repeats within one authorized operation reuse the digest
            # and thumbnail. Never cache an authorization decision across calls.
            read_cache: dict[Path, tuple[tuple[int, int, int, int, int], str, int]] = {}
            items: list[dict[str, Any]] = []
            source_bytes = 0
            source_read_bytes = 0
            reused_path_count = 0
            relative_path_bytes = 0
            unique_source_bytes = 0

            def identity(stat: os.stat_result) -> tuple[int, int, int, int]:
                return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns

            def version(stat: os.stat_result) -> tuple[int, int, int, int, int]:
                return (*identity(stat), stat.st_ctime_ns)

            for index, relative in enumerate(paths, 1):
                if service_responses is None:
                    # Resolve and authorize every entry, including cache hits.
                    path = self._artifact_path({"project": payload.get("project"), "project_artifact_path": relative})
                    if isinstance(path, ActionResult):
                        return path
                    if path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}:
                        return ActionResult(False, "batch supports PNG, JPEG and WebP images")
                    resolved_relative = path.relative_to(root).as_posix()
                    relative_path_bytes += len(resolved_relative.encode("utf-8"))
                    if relative_path_bytes > 16384:
                        return ActionResult(False, "batch resolved path byte budget exceeded")

                    current = path.stat()
                    observed = version(current)
                    cached = read_cache.get(path)
                    raw: bytes | None = None
                    if cached is not None:
                        if cached[0] != observed:
                            return ActionResult(False, "artifact changed during batch; retry",
                                                {"error_code": "artifact_changed"})
                        digest, size = cached[1], cached[2]
                        reused_path_count += 1
                    else:
                        limit = min(32 * 1024 * 1024, 256 * 1024 * 1024 - source_read_bytes)
                        if limit <= 0:
                            return ActionResult(False, "batch source byte budget exceeded")
                        with path.open("rb") as handle:
                            opened = os.fstat(handle.fileno())
                            if identity(opened) != identity(current):
                                return ActionResult(False, "artifact changed before batch read",
                                                    {"error_code": "artifact_changed"})
                            raw = handle.read(limit + 1)
                            if (identity(os.fstat(handle.fileno())) != identity(opened)
                                    or version(path.stat()) != observed):
                                return ActionResult(False, "artifact changed during batch read",
                                                    {"error_code": "artifact_changed"})
                        if len(raw) > limit or not raw:
                            return ActionResult(False, "batch source byte budget exceeded or empty image")
                        size = len(raw)
                        source_read_bytes += size
                        digest = hashlib.sha256(raw).hexdigest()
                        read_cache[path] = (observed, digest, size)
                    source_metadata = {"project_artifact_path": resolved_relative,
                                       "source_sha256": digest}
                    expected_format = None
                else:
                    response = service_responses[index - 1]
                    raw = response.body
                    size = len(raw)
                    digest = response.sha256
                    source_read_bytes += size
                    source_metadata = {"source_kind": "local_service_response",
                                       "request_index": index - 1, "route": response.route,
                                       "response_sha256": digest}
                    expected_format = {"image/png": "PNG", "image/jpeg": "JPEG",
                                       "image/webp": "WEBP"}[response.media_type]
                    # Check duplicate response declarations too; an identical
                    # byte stream cannot be re-labeled as a different format.
                    if digest in seen and response_formats[digest] != expected_format:
                        return ActionResult(False, "local service image format mismatch")
                source_bytes += size
                image_id = f"I{index:03}"
                tile_id = seen.get(digest)
                if tile_id is None:
                    if raw is None:
                        return ActionResult(False, "artifact changed during batch; retry",
                                            {"error_code": "artifact_changed"})
                    tile_id = image_id
                    tile_index = len(seen)
                    with Image.open(io.BytesIO(raw)) as image:
                        if expected_format is not None:
                            if image.format != expected_format:
                                return ActionResult(False, "local service image format mismatch")
                            response_formats[digest] = expected_format
                        if image.width * image.height > 24_000_000:
                            return ActionResult(False, "batch image pixel budget exceeded")
                        image.draft("RGB", (cell_width, cell_height - label_height))
                        corrected = ImageOps.exif_transpose(image)
                        corrected.thumbnail((cell_width - 4, cell_height - label_height - 4), Image.Resampling.LANCZOS)
                        rgba = corrected.convert("RGBA")
                        tile = Image.new("RGB", corrected.size, "white")
                        tile.paste(rgba, mask=rgba.getchannel("A"))
                        # Reuse the decoded, EXIF-corrected tile. This is a
                        # within-batch near-duplicate hint, not object identity.
                        gray = tile.convert("L").resize((9, 8), Image.Resampling.LANCZOS)
                        pixels = list(gray.getdata())
                        if max(pixels) - min(pixels) >= 24:
                            fingerprint = 0
                            for row in range(8):
                                for col in range(8):
                                    offset = row * 9 + col
                                    fingerprint = (fingerprint << 1) | int(pixels[offset] > pixels[offset + 1])
                            perceptual.append((tile_id, fingerprint, tile.width / tile.height))
                        x = (tile_index % columns) * cell_width
                        y = (tile_index // columns) * cell_height
                        sheet.paste(tile, (x + (cell_width - tile.width) // 2,
                                           y + (cell_height - label_height - tile.height) // 2))
                        draw.text((x + 4, y + cell_height - label_height + 3), tile_id, fill="black")
                    seen[digest] = tile_id
                    unique_source_bytes += size
                items.append({"image_id": image_id, "tile_id": tile_id,
                              **source_metadata, "source_size_bytes": size})
            final_rows = math.ceil(len(seen) / columns)
            if final_rows < rows:
                sheet = sheet.crop((0, 0, sheet.width, final_rows * cell_height))
            # Keep one authorized read/decode of each original. If the image
            # exceeds the inline budget, re-encode only the composed sheet:
            # do not request the originals again or issue another MCP action.
            # At most 9 bounded JPEG encodes; preserve legible tile labels
            # by never shrinking the sheet below half its initial dimensions.
            data = b""
            rendered = sheet
            encoded_quality = quality
            for scale in (1.0, 0.75, 0.5):
                rendered = sheet if scale == 1.0 else sheet.resize(
                    (max(1, round(sheet.width * scale)),
                     max(1, round(sheet.height * scale))),
                    Image.Resampling.LANCZOS,
                )
                candidates = (quality, min(quality, 50), 30)
                for candidate_quality in dict.fromkeys(candidates):
                    buffer = io.BytesIO()
                    rendered.save(buffer, format="JPEG", quality=candidate_quality,
                                  optimize=True, progressive=True)
                    data = buffer.getvalue()
                    encoded_quality = candidate_quality
                    if len(data) <= max_bytes:
                        break
                if len(data) <= max_bytes:
                    break
            if len(data) > max_bytes:
                return ActionResult(False, "sheet exceeds preview byte budget even after bounded adaptation; reduce batch size",
                                    {"error_code": "artifact_sheet_too_large"})
            # A mutable original must not be reported as a stable SHA-backed
            # preview if it was replaced while other tiles were rendered.
            for cached_path, (snapshot, _, _) in read_cache.items():
                if version(cached_path.stat()) != snapshot:
                    return ActionResult(False, "artifact changed during batch; retry",
                                        {"error_code": "artifact_changed"})
            # At most 4,950 comparisons for the existing 100-item bound.
            # Exclude SHA-identical tiles, rank deterministically, and never
            # imply same object, a confirmed match, or an editorial decision.
            candidates = []
            for index, (left, left_hash, left_ratio) in enumerate(perceptual):
                for right, right_hash, right_ratio in perceptual[index + 1:]:
                    if max(left_ratio, right_ratio) / min(left_ratio, right_ratio) > 1.15:
                        continue
                    distance = (left_hash ^ right_hash).bit_count()
                    if distance <= 8:
                        candidates.append({"left_tile_id": left, "right_tile_id": right,
                                           "hamming_distance": distance})
            candidates.sort(key=lambda pair: (pair["hamming_distance"], pair["left_tile_id"],
                                              pair["right_tile_id"]))
            return ActionResult(True, "artifact image sheet ready", {
                "source_kind": "project_artifact" if service_responses is None else "local_service_response",
                "visual_candidates": {"method": "dhash64", "review_only": True,
                                      "max_distance": 8, "pairs": candidates[:48],
                                      "truncated": len(candidates) > 48},
                "items": items, "image_count": len(items), "unique_image_count": len(seen),
                "source_size_bytes": source_bytes, "unique_source_size_bytes": unique_source_bytes,
                "source_read_bytes": source_read_bytes, "reused_path_count": reused_path_count,
                "size_bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(),
                "mime_type": "image/jpeg", "format": "jpeg", "thumbnail": True,
                "thumbnail_size": [rendered.width, rendered.height],
                "encoded_quality": encoded_quality, "adapted_for_budget":
                    encoded_quality != quality or rendered.size != sheet.size,
                "processing_ms": round((time.monotonic() - started) * 1000, 2),
                "base64": base64.b64encode(data).decode("ascii"),
            })
        except (OSError, ValueError, Image.DecompressionBombError) as error:
            return ActionResult(False, f"artifact image sheet failed: {error}")

    def artifact_preview(self, payload: dict[str, Any]) -> ActionResult:
        if any(key.startswith("local_service") or key == "service_responses" for key in payload):
            return ActionResult(False, "local service requires separate verified authority")
        if "project_artifact_paths" in payload:
            return self._artifact_image_sheet(payload)
        path = self._artifact_path(payload)
        if isinstance(path, ActionResult):
            return path

        source_size = path.stat().st_size
        max_bytes = max(4096, min(int(payload.get("max_bytes", 262144)), 2 * 1024 * 1024))
        thumbnail = bool(payload.get("thumbnail", False))
        output_format = path.suffix.lower().lstrip(".")
        mime_type = {
            "png": "image/png",
            "jpg": "image/jpeg",
            "jpeg": "image/jpeg",
            "webp": "image/webp",
            "json": "application/json",
        }.get(output_format, "application/octet-stream")

        thumbnail_size = None
        data = b""
        if thumbnail:
            if path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}:
                return ActionResult(False, "thumbnail is supported only for raster images")
            try:
                import io
                from PIL import Image
            except ImportError:
                return ActionResult(False, "Pillow is required for artifact thumbnails")

            max_width = max(64, min(int(payload.get("max_width", 480)), 1600))
            max_height = max(64, min(int(payload.get("max_height", 320)), 1200))
            quality = max(30, min(int(payload.get("quality", 72)), 92))

            with Image.open(path) as image:
                # Ask JPEG decoders to reduce resolution before allocating pixels.
                image.draft("RGB", (max_width, max_height))
                image.thumbnail((max_width, max_height), Image.Resampling.LANCZOS)
                image = image.convert("RGB")
                thumbnail_size = [image.width, image.height]
                buffer = io.BytesIO()
                image.save(
                    buffer,
                    format="JPEG",
                    quality=quality,
                    optimize=True,
                    progressive=True,
                )
                data = buffer.getvalue()
            output_format = "jpeg"
            mime_type = "image/jpeg"
        elif source_size <= max_bytes:
            # A concurrent writer must not turn a bounded preview into a huge read.
            with path.open("rb") as handle:
                data = handle.read(max_bytes + 1)
        if (not thumbnail and source_size > max_bytes) or len(data) > max_bytes:
            return ActionResult(
                False,
                f"artifact is too large for inline preview: {max(source_size if not thumbnail else 0, len(data))} > {max_bytes}",
                {
                    "path": str(path),
                    "source_size_bytes": source_size,
                    "preview_size_bytes": len(data) if thumbnail else max(source_size, len(data)),
                    "thumbnail": thumbnail,
                },
            )

        return ActionResult(
            True,
            "artifact preview ready",
            {
                "artifact_name": path.name,
                "path": str(path),
                "source_size_bytes": source_size,
                "size_bytes": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
                "mime_type": mime_type,
                "format": output_format,
                "thumbnail": thumbnail,
                "thumbnail_size": thumbnail_size,
                "base64": base64.b64encode(data).decode("ascii"),
            },
        )


