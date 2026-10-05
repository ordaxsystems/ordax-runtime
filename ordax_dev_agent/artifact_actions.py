"""Project-scoped artifact preview actions."""
from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
from typing import Any

from .models import ActionResult


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

        for source, root in roots:
            if not root.is_dir():
                continue
            for candidate in sorted(root.rglob("*")):
                if not candidate.is_file():
                    continue
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
            observed = version(before)
            if expected is not None and expected != observed:
                return ActionResult(False, "artifact changed; restart download from offset zero",
                                    {"error_code": "artifact_changed"})
            if offset > before.st_size:
                return ActionResult(False, "offset is beyond end of artifact")
            handle.seek(offset)
            chunk = handle.read(max_bytes)
            if version(os.fstat(handle.fileno())) != observed or version(path.stat()) != observed:
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

    def artifact_preview(self, payload: dict[str, Any]) -> ActionResult:
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


