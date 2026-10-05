"""Typed visual-environment actions shared by Blender and realtime engines."""
from __future__ import annotations

import hashlib
import json
import uuid
from pathlib import Path
from typing import Any

from mcp_blender_unity.config import find_blender

from .models import ActionResult
from .process_runner import run_command as _run
from .visual_environment import (
    ENVIRONMENT_SCHEMA,
    environment_preset,
    environment_schema,
    normalize_environment,
)

_MAX_ENVIRONMENT_BYTES = 256 * 1024


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    encoded = (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8")
    if len(encoded) > _MAX_ENVIRONMENT_BYTES:
        raise ValueError("environment JSON exceeds the 256 KiB safety limit")
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.ordax-{uuid.uuid4().hex}.tmp")
    try:
        temp.write_bytes(encoded)
        temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _blender_request(project, raw: Any, *, manifest_path: Path, overwrite: bool) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("blender must be an object")
    allowed = {"blend_file", "output_path", "report_path", "timeout_seconds", "overwrite"}
    unknown = sorted(set(raw) - allowed)
    if unknown:
        raise ValueError("unsupported blender field(s): " + ", ".join(unknown))

    source_raw = raw.get("blend_file") or project.blender.get("blend_file")
    if not isinstance(source_raw, str) or not source_raw.strip():
        raise ValueError("blender.blend_file is required or must be configured for the project")
    source = project.path(source_raw.strip())
    if source.suffix.lower() != ".blend":
        raise ValueError("blender.blend_file must be a .blend file")

    output_raw = raw.get("output_path") or f"ordax/blender/{source.stem}-environment.blend"
    if not isinstance(output_raw, str) or not output_raw.strip():
        raise ValueError("blender.output_path must be a non-empty project-relative path")
    output = project.path(output_raw.strip(), must_exist=False)
    if output.suffix.lower() != ".blend":
        raise ValueError("blender.output_path must be a .blend file")
    if output == source:
        raise ValueError("blender.output_path must differ from blender.blend_file")

    report_raw = raw.get("report_path")
    if report_raw is None:
        report_raw = output.with_name(output.name + ".environment.json").relative_to(project.root).as_posix()
    if not isinstance(report_raw, str) or not report_raw.strip():
        raise ValueError("blender.report_path must be a non-empty project-relative path")
    report = project.path(report_raw.strip(), must_exist=False)
    if report.suffix.lower() != ".json":
        raise ValueError("blender.report_path must be a .json file")
    if report == manifest_path:
        raise ValueError("blender.report_path must differ from output_path environment manifest")

    local_overwrite = raw.get("overwrite", overwrite)
    if not isinstance(local_overwrite, bool):
        raise ValueError("blender.overwrite must be boolean")
    if not local_overwrite and any(path.exists() for path in (output, report)):
        raise ValueError("Blender environment derivative already exists; set overwrite=true explicitly")

    timeout = raw.get("timeout_seconds", 600)
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
        raise ValueError("blender.timeout_seconds must be numeric")
    timeout_seconds = int(timeout)
    if not 30 <= timeout_seconds <= 1800:
        raise ValueError("blender.timeout_seconds must be between 30 and 1800")

    return {
        "source": source,
        "output": output,
        "report": report,
        "timeout_seconds": timeout_seconds,
    }


class VisualEnvironmentActions:
    def visual_environment_schema(self, payload: dict[str, Any]) -> ActionResult:
        unsupported = set(payload) - {"project"}
        if unsupported:
            return ActionResult(False, f"unsupported fields: {', '.join(sorted(unsupported))}")
        schema = environment_schema()
        schema["materializers"] = {
            "blender": {
                "action": "visual.environment_write",
                "optional_block": "blender",
                "mode": "source-preserving-derivative",
                "audit": "save-reopen-readonly",
            }
        }
        return ActionResult(True, "visual environment schema", schema)

    def visual_environment_preset(self, payload: dict[str, Any]) -> ActionResult:
        unsupported = set(payload) - {"project", "preset"}
        if unsupported:
            return ActionResult(False, f"unsupported fields: {', '.join(sorted(unsupported))}")
        try:
            preset = environment_preset(str(payload.get("preset") or ""))
        except ValueError as error:
            return ActionResult(False, str(error))
        return ActionResult(True, "visual environment preset", preset)

    def visual_environment_write(self, payload: dict[str, Any]) -> ActionResult:
        supported = {"project", "environment", "output_path", "overwrite", "blender"}
        unsupported = set(payload) - supported
        if unsupported:
            return ActionResult(False, f"unsupported fields: {', '.join(sorted(unsupported))}")
        project = self._project(payload)
        temporary_paths: list[Path] = []
        try:
            environment = normalize_environment(payload.get("environment") or {})
            overwrite = payload.get("overwrite", False)
            if not isinstance(overwrite, bool):
                raise ValueError("overwrite must be boolean")

            output_raw = payload.get("output_path") or "ordax/environment.json"
            if not isinstance(output_raw, str) or not output_raw.strip():
                raise ValueError("output_path must be a non-empty project-relative path")
            output = project.path(output_raw.strip(), must_exist=False)
            if output.suffix.lower() != ".json":
                raise ValueError("output_path must be a .json file")
            if output.exists() and not overwrite:
                raise ValueError("environment manifest already exists; set overwrite=true explicitly")

            blender_data = None
            raw_blender = payload.get("blender")
            if raw_blender is not None:
                if "blender" not in project.apps:
                    raise ValueError(f"Blender is not enabled for project {project.slug}")
                blender = find_blender()
                if blender is None:
                    raise ValueError("Blender executable not found")
                request = _blender_request(project, raw_blender, manifest_path=output, overwrite=overwrite)
                source = request["source"]
                derivative = request["output"]
                report = request["report"]
                source_before = _sha256(source)

                derivative.parent.mkdir(parents=True, exist_ok=True)
                report.parent.mkdir(parents=True, exist_ok=True)
                token = uuid.uuid4().hex
                temp_blend = derivative.with_name(f".{derivative.stem}.ordax-{token}.tmp.blend")
                temp_report = report.with_name(f".{report.name}.ordax-{token}.tmp")
                request_path = derivative.with_name(f".environment-request-{token}.json")
                temporary_paths.extend([temp_blend, temp_report, request_path])
                _write_json_atomic(
                    request_path,
                    {
                        "environment": environment,
                        "output_path": str(temp_blend),
                        "report_path": str(temp_report),
                    },
                )

                runtime_script = Path(__file__).with_name("game_asset_blender_environment_runtime.py")
                if not runtime_script.is_file():
                    raise ValueError("Blender environment runtime is missing from the Device Agent package")
                # Security-sensitive order: disable auto-execution before Blender opens
                # the untrusted project file.
                executed = _run(
                    [
                        str(blender),
                        "--background",
                        "--disable-autoexec",
                        str(source),
                        "--python",
                        str(runtime_script),
                        "--",
                        str(request_path),
                    ],
                    cwd=project.root,
                    timeout=request["timeout_seconds"],
                )
                source_after = _sha256(source)
                if source_after != source_before:
                    raise ValueError("source blend changed during environment materialization; refusing derivative")
                if not executed.ok:
                    return ActionResult(
                        False,
                        "Blender environment materialization failed",
                        {"execution": executed.data, "source_sha256": source_before},
                    )
                if not temp_blend.is_file() or not temp_report.is_file():
                    raise ValueError("Blender environment materialization produced incomplete artifacts")
                report_data = json.loads(temp_report.read_text(encoding="utf-8"))
                if not isinstance(report_data, dict) or not bool(report_data.get("ok")):
                    raise ValueError("Blender environment audit did not pass")

                derivative_hash = _sha256(temp_blend)
                derivative_bytes = temp_blend.stat().st_size
                temp_blend.replace(derivative)
                temp_report.replace(report)
                _write_json_atomic(output, environment)
                blender_data = {
                    "source_path": str(source),
                    "source_sha256": source_before,
                    "source_preserved": True,
                    "output_path": str(derivative),
                    "output_sha256": derivative_hash,
                    "output_bytes": derivative_bytes,
                    "report_path": str(report),
                    "audit": report_data.get("audit"),
                    "applied": report_data.get("applied"),
                }
            else:
                _write_json_atomic(output, environment)
        except (ValueError, OSError, FileNotFoundError, json.JSONDecodeError) as error:
            return ActionResult(False, str(error))
        finally:
            for path in temporary_paths:
                path.unlink(missing_ok=True)

        data = {
            "schema": ENVIRONMENT_SCHEMA,
            "output_path": str(output),
            "environment": environment,
        }
        if blender_data is not None:
            data["blender"] = blender_data
        return ActionResult(
            True,
            "visual environment manifest written"
            if blender_data is None
            else "visual environment manifest and Blender derivative written",
            data,
        )
