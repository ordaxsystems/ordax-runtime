"""Safe static-mesh LOD generation for Blender game assets."""
from __future__ import annotations

import json
import math
import uuid
from pathlib import Path
from typing import Any

from mcp_blender_unity.config import find_blender

from .models import ActionResult
from .process_runner import run_command as _run


_DEFAULT_RATIOS = [0.5, 0.25, 0.1]


def _ratios(value: Any) -> list[float]:
    if value is None:
        return list(_DEFAULT_RATIOS)
    if not isinstance(value, list) or not value or len(value) > 5:
        raise ValueError("ratios must be a non-empty list with at most 5 entries")
    result: list[float] = []
    for item in value:
        if isinstance(item, bool) or not isinstance(item, (int, float)):
            raise ValueError("each LOD ratio must be numeric")
        ratio = float(item)
        if not math.isfinite(ratio) or ratio < 0.05 or ratio > 0.95:
            raise ValueError("each LOD ratio must be between 0.05 and 0.95")
        result.append(ratio)
    if any(result[index] <= result[index + 1] for index in range(len(result) - 1)):
        raise ValueError("ratios must be strictly descending from highest to lowest detail")
    return result


class GameAssetLodActions:
    """Generate deterministic static LOD meshes without touching the source blend."""

    def game_assets_blender_generate_static_lods(self, payload: dict[str, Any]) -> ActionResult:
        supported = {
            "project",
            "blend_file",
            "output_blend",
            "ratios",
            "overwrite",
            "timeout_seconds",
        }
        unsupported = set(payload) - supported
        if unsupported:
            return ActionResult(False, f"unsupported fields: {', '.join(sorted(unsupported))}")
        project = self._project(payload)
        try:
            blend_file = project.path(str(payload.get("blend_file") or ""))
            if blend_file.suffix.lower() != ".blend":
                raise ValueError("blend_file must be a .blend file")
            output = project.path(str(payload.get("output_blend") or ""), must_exist=False)
            if output.suffix.lower() != ".blend":
                raise ValueError("output_blend must end in .blend")
            if output.resolve() == blend_file.resolve():
                raise ValueError("output_blend must be different from blend_file")
            overwrite = bool(payload.get("overwrite", False))
            if output.exists() and not overwrite:
                raise ValueError("output_blend already exists; set overwrite=true explicitly")
            ratios = _ratios(payload.get("ratios"))
            timeout = payload.get("timeout_seconds", 1800)
            if isinstance(timeout, bool) or not isinstance(timeout, int) or timeout < 30 or timeout > 3600:
                raise ValueError("timeout_seconds must be an integer between 30 and 3600")
            blender = find_blender()
            if blender is None:
                raise ValueError("Blender executable not found")
            output.parent.mkdir(parents=True, exist_ok=True)
            artifact_dir = self.config.state_dir / "artifacts" / project.slug / "game-assets"
            artifact_dir.mkdir(parents=True, exist_ok=True)
            report_path = artifact_dir / f"static-lod-{uuid.uuid4().hex}.json"
            script = Path(__file__).resolve().parent / "assets" / "blender_static_lod.py"
            result = _run(
                [
                    str(blender),
                    "--background",
                    str(blend_file),
                    "--python",
                    str(script),
                    "--",
                    "--output",
                    str(output),
                    "--report",
                    str(report_path),
                    "--ratios",
                    ",".join(f"{ratio:.6f}" for ratio in ratios),
                ],
                cwd=project.root,
                timeout=timeout,
            )
            if not result.ok:
                if report_path.is_file():
                    try:
                        report = json.loads(report_path.read_text(encoding="utf-8"))
                    except (OSError, ValueError):
                        report = None
                    if isinstance(report, dict):
                        return ActionResult(False, str(report.get("error") or result.summary), {"report": report})
                return result
            if not output.is_file():
                return ActionResult(False, "static LOD generation completed without output .blend")
            if not report_path.is_file():
                return ActionResult(False, "static LOD generation completed without report")
            try:
                report = json.loads(report_path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as error:
                return ActionResult(False, f"cannot read static LOD report: {error}")
            if not isinstance(report, dict) or not report.get("ok"):
                return ActionResult(False, "static LOD report indicates failure", {"report": report})
        except (ValueError, OSError, FileNotFoundError) as error:
            return ActionResult(False, str(error))
        return ActionResult(
            True,
            "static LOD derivative generated",
            {
                "source_blend": str(blend_file),
                "output_blend": str(output),
                "ratios": ratios,
                "report_path": str(report_path),
                "report": report,
            },
        )
