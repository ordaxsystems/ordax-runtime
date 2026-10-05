"""Runtime-budget audits for generated or authored Blender assets."""
from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

from mcp_blender_unity.config import find_blender

from .models import ActionResult
from .process_runner import run_command as _run


_LIMITS = {
    "max_triangles": (1, 100_000_000),
    "max_material_slots": (1, 100_000),
    "max_texture_dimension": (64, 32768),
    "max_vertex_influences": (1, 32),
    "max_bones": (1, 100_000),
    "max_actions": (0, 100_000),
}


def _limit(payload: dict[str, Any], name: str) -> int | None:
    value = payload.get(name)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be an integer")
    minimum, maximum = _LIMITS[name]
    if value < minimum or value > maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return value


def _violations(metrics: dict[str, Any], limits: dict[str, int]) -> list[dict[str, Any]]:
    mapping = {
        "max_triangles": "triangles",
        "max_material_slots": "material_slots",
        "max_texture_dimension": "max_texture_dimension",
        "max_vertex_influences": "max_vertex_influences",
        "max_bones": "bones",
        "max_actions": "actions",
    }
    result = []
    for limit_name, limit_value in limits.items():
        metric_name = mapping[limit_name]
        actual = metrics.get(metric_name)
        if isinstance(actual, (int, float)) and actual > limit_value:
            result.append(
                {
                    "metric": metric_name,
                    "actual": actual,
                    "limit": limit_value,
                    "over_by": actual - limit_value,
                }
            )
    return result


class GameAssetRuntimeActions:
    """Inspect runtime cost without destructively optimizing geometry."""

    def game_assets_blender_runtime_audit(self, payload: dict[str, Any]) -> ActionResult:
        supported = {"project", "blend_file", "timeout_seconds", *_LIMITS.keys()}
        unsupported = set(payload) - supported
        if unsupported:
            return ActionResult(False, f"unsupported fields: {', '.join(sorted(unsupported))}")
        project = self._project(payload)
        try:
            blend_file = project.path(str(payload.get("blend_file") or ""))
            if blend_file.suffix.lower() != ".blend":
                raise ValueError("blend_file must be a .blend file")
            limits = {
                name: value
                for name in _LIMITS
                if (value := _limit(payload, name)) is not None
            }
            timeout = payload.get("timeout_seconds", 900)
            if isinstance(timeout, bool) or not isinstance(timeout, int) or timeout < 30 or timeout > 3600:
                raise ValueError("timeout_seconds must be an integer between 30 and 3600")
            blender = find_blender()
            if blender is None:
                raise ValueError("Blender executable not found")
            artifact_dir = self.config.state_dir / "artifacts" / project.slug / "game-assets"
            artifact_dir.mkdir(parents=True, exist_ok=True)
            report_path = artifact_dir / f"runtime-audit-{uuid.uuid4().hex}.json"
            script = Path(__file__).resolve().parent / "assets" / "blender_runtime_budget.py"
            result = _run(
                [
                    str(blender),
                    "--background",
                    str(blend_file),
                    "--python",
                    str(script),
                    "--",
                    "--report",
                    str(report_path),
                ],
                cwd=project.root,
                timeout=timeout,
            )
            if not result.ok:
                return result
            if not report_path.is_file():
                return ActionResult(False, "Blender runtime audit completed without a report")
            try:
                report = json.loads(report_path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as error:
                return ActionResult(False, f"cannot read runtime audit report: {error}")
            if not isinstance(report, dict) or not report.get("ok"):
                return ActionResult(False, "runtime audit report indicates failure", {"report": report})
            metrics = report.get("metrics")
            if not isinstance(metrics, dict):
                return ActionResult(False, "runtime audit report has no metrics")
            violations = _violations(metrics, limits)
        except (ValueError, OSError, FileNotFoundError) as error:
            return ActionResult(False, str(error))
        return ActionResult(
            not violations,
            "runtime budget audit passed" if not violations else "runtime budget audit exceeded limits",
            {
                "blend_file": str(blend_file),
                "metrics": metrics,
                "limits": limits,
                "violations": violations,
                "report_path": str(report_path),
                "details": report,
            },
        )
