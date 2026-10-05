"""Adobe Mixamo round-trip helpers for the game-asset pipeline."""
from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

from mcp_blender_unity.config import find_blender

from .models import ActionResult
from .process_runner import run_command as _run


class MixamoActions:
    """Typed local actions for ingesting FBX returned by Mixamo."""

    def game_assets_blender_import_fbx(self, payload: dict[str, Any]) -> ActionResult:
        supported = {
            "project",
            "source_path",
            "output_blend",
            "timeout_seconds",
            "overwrite",
        }
        unsupported = set(payload) - supported
        if unsupported:
            return ActionResult(False, f"unsupported fields: {', '.join(sorted(unsupported))}")

        project = self._project(payload)
        source = project.path(str(payload.get("source_path") or ""))
        if source.suffix.lower() != ".fbx":
            return ActionResult(False, "source_path must be an FBX file")
        if source.stat().st_size > 1024 * 1024 * 1024:
            return ActionResult(False, "FBX exceeds the 1 GiB local safety limit")

        output = project.path(str(payload.get("output_blend") or ""), must_exist=False)
        if output.suffix.lower() != ".blend":
            return ActionResult(False, "output_blend must end in .blend")
        if output.exists() and not bool(payload.get("overwrite", False)):
            return ActionResult(False, "output_blend already exists; set overwrite=true explicitly")
        output.parent.mkdir(parents=True, exist_ok=True)

        timeout_raw = payload.get("timeout_seconds", 900)
        if isinstance(timeout_raw, bool) or not isinstance(timeout_raw, int):
            return ActionResult(False, "timeout_seconds must be an integer")
        if timeout_raw < 30 or timeout_raw > 3600:
            return ActionResult(False, "timeout_seconds must be between 30 and 3600")

        blender = find_blender()
        if blender is None:
            return ActionResult(False, "Blender executable not found")

        artifact_dir = self.config.state_dir / "artifacts" / project.slug / "game-assets"
        artifact_dir.mkdir(parents=True, exist_ok=True)
        report_path = artifact_dir / f"mixamo-import-{uuid.uuid4().hex}.json"
        script = Path(__file__).resolve().parent / "assets" / "blender_fbx_ingest.py"

        result = _run(
            [
                str(blender),
                "--background",
                "--factory-startup",
                "--python",
                str(script),
                "--",
                "--input",
                str(source),
                "--output",
                str(output),
                "--report",
                str(report_path),
            ],
            cwd=project.root,
            timeout=timeout_raw,
        )
        if not result.ok:
            return result
        if not output.is_file():
            return ActionResult(False, "Blender FBX ingest completed without a .blend output")
        if not report_path.is_file():
            return ActionResult(False, "Blender FBX ingest completed without a report")

        try:
            report = json.loads(report_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            return ActionResult(False, f"cannot read FBX ingest report: {error}")
        if not report.get("ok"):
            return ActionResult(False, "FBX ingest report indicates failure", {"report": report})

        return ActionResult(
            True,
            "Mixamo/FBX character imported into Blender",
            {
                "source_path": str(source),
                "output_blend": str(output),
                "bytes": output.stat().st_size,
                "report_path": str(report_path),
                "report": report,
            },
        )
