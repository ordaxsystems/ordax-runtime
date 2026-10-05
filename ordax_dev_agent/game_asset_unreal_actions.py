"""Verified Unreal Editor import validation for OrdaX FBX derivatives."""
from __future__ import annotations

import json
import os
import re
import shutil
import uuid
from pathlib import Path
from typing import Any

from .game_asset_engine_export_actions import verify_engine_export
from .models import ActionResult
from .process_runner import run_command as _run


_DEFAULT_TIMEOUT = 600
_PACKAGE_RE = re.compile(r"/Game(?:/[A-Za-z0-9_.-]+)*")


def _timeout(value: Any) -> int:
    if value is None:
        return _DEFAULT_TIMEOUT
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("timeout_seconds must be an integer")
    if value < 30 or value > 1800:
        raise ValueError("timeout_seconds must be between 30 and 1800")
    return value


def _destination(value: Any) -> str:
    if value is None:
        return "/Game/OrdaX/Generated"
    if not isinstance(value, str):
        raise ValueError("destination_path must be a string")
    result = value.strip().rstrip("/")
    if not _PACKAGE_RE.fullmatch(result):
        raise ValueError("destination_path must be a normalized /Game/... package path")
    segments = result.split("/")[2:]
    if not segments or any(segment in {".", ".."} for segment in segments):
        raise ValueError("destination_path must not contain dot path segments")
    return result


def _find_unreal_editor() -> str:
    configured = os.environ.get("ORDAX_UNREAL_EDITOR_CMD")
    if configured:
        path = Path(configured).expanduser()
        if not path.is_file():
            raise FileNotFoundError("ORDAX_UNREAL_EDITOR_CMD does not point to a file")
        return str(path)
    names = (
        "UnrealEditor-Cmd.exe",
        "UnrealEditor-Cmd",
        "UnrealEditor.exe",
        "UnrealEditor",
    )
    for name in names:
        resolved = shutil.which(name)
        if resolved:
            return resolved
    raise FileNotFoundError(
        "Unreal Editor command binary not found; configure ORDAX_UNREAL_EDITOR_CMD or add UnrealEditor-Cmd to PATH"
    )


def _script(source: Path, destination_path: str, replace_existing: bool) -> str:
    payload = json.dumps(
        {
            "source": str(source),
            "destination": destination_path,
            "replace_existing": replace_existing,
        }
    )
    return f'''import json
import unreal

cfg = json.loads({payload!r})
source = cfg["source"]
destination = cfg["destination"]
replace_existing = bool(cfg["replace_existing"])

task = unreal.AssetImportTask()
task.set_editor_property("filename", source)
task.set_editor_property("destination_path", destination)
task.set_editor_property("automated", True)
task.set_editor_property("replace_existing", replace_existing)
task.set_editor_property("replace_existing_settings", replace_existing)
task.set_editor_property("save", True)

asset_tools = unreal.AssetToolsHelpers.get_asset_tools()
asset_tools.import_asset_tasks([task])
paths = list(task.get_editor_property("imported_object_paths") or [])
if not paths:
    raise RuntimeError("ORDAX Unreal import produced no object paths")
loaded = []
classes = []
for path in paths:
    obj = unreal.EditorAssetLibrary.load_asset(str(path))
    if obj is None:
        raise RuntimeError("ORDAX Unreal failed to load imported asset: " + str(path))
    loaded.append(obj)
    classes.append(obj.get_class().get_name())
if not unreal.EditorAssetLibrary.save_loaded_assets(loaded, False):
    raise RuntimeError("ORDAX Unreal failed to save imported assets")
print("ORDAX_UNREAL_IMPORT_OK|" + json.dumps({{"paths": [str(x) for x in paths], "classes": classes}}, separators=(",", ":")))
'''


def _proof(stdout: str, destination: str) -> dict[str, Any] | None:
    prefix = "ORDAX_UNREAL_IMPORT_OK|"
    expected_prefix = destination.rstrip("/") + "/"
    for line in stdout.splitlines():
        if not line.startswith(prefix):
            continue
        try:
            payload = json.loads(line[len(prefix) :])
        except json.JSONDecodeError:
            return None
        if not isinstance(payload, dict):
            return None
        paths = payload.get("paths")
        classes = payload.get("classes")
        if (
            isinstance(paths, list)
            and paths
            and all(isinstance(item, str) and item.startswith(expected_prefix) for item in paths)
            and isinstance(classes, list)
            and len(classes) == len(paths)
            and all(isinstance(item, str) and item for item in classes)
        ):
            return {"object_paths": paths, "classes": classes}
    return None


class GameAssetUnrealActions:
    """Import a verified FBX through Unreal's supported Editor scripting APIs."""

    def game_assets_unreal_import_validate(self, payload: dict[str, Any]) -> ActionResult:
        supported = {
            "project",
            "artifact_path",
            "manifest_path",
            "uproject_path",
            "destination_path",
            "overwrite",
            "timeout_seconds",
        }
        unsupported = set(payload) - supported
        if unsupported:
            return ActionResult(False, f"unsupported fields: {', '.join(sorted(unsupported))}")

        project = self._project(payload)
        script_path: Path | None = None
        try:
            artifact = project.path(str(payload.get("artifact_path") or ""))
            if artifact.suffix.lower() != ".fbx":
                raise ValueError("Unreal validation requires an FBX artifact")
            raw_manifest = payload.get("manifest_path")
            manifest = (
                project.path(str(raw_manifest))
                if raw_manifest is not None
                else artifact.with_name(artifact.name + ".ordax.json")
            )
            verification = verify_engine_export(project, artifact, manifest)
            if str(verification.get("engine") or "").strip().lower() != "unreal":
                raise ValueError("engine export provenance is not targeted to Unreal")

            uproject_raw = payload.get("uproject_path")
            if not isinstance(uproject_raw, str) or not uproject_raw.strip():
                raise ValueError("uproject_path is required")
            uproject = project.path(uproject_raw.strip())
            if uproject.suffix.lower() != ".uproject" or not uproject.is_file():
                raise ValueError("uproject_path must point to a project-local .uproject file")

            destination = _destination(payload.get("destination_path"))
            replace_existing = bool(payload.get("overwrite", False))
            timeout = _timeout(payload.get("timeout_seconds"))
            editor = _find_unreal_editor()

            state_dir = self.config.state_dir
            state_dir.mkdir(parents=True, exist_ok=True)
            script_path = state_dir / f"unreal-import-check-{uuid.uuid4().hex}.py"
            script_path.write_text(
                _script(artifact.resolve(), destination, replace_existing),
                encoding="utf-8",
            )
            command = [
                editor,
                str(uproject.resolve()),
                "-unattended",
                "-nop4",
                "-nosplash",
                "-nullrhi",
                "-run=pythonscript",
                f"-script={script_path.resolve()}",
            ]
            result = _run(
                command,
                cwd=uproject.parent,
                timeout=timeout,
            )
            if not result.ok:
                return ActionResult(
                    False,
                    "Unreal commandlet failed while importing the verified FBX",
                    {
                        "artifact_path": str(artifact),
                        "uproject_path": str(uproject),
                        "destination_path": destination,
                        "source_preserved": True,
                        "command": result.data,
                    },
                )
            proof = _proof(str(result.data.get("stdout") or ""), destination)
            if proof is None:
                return ActionResult(
                    False,
                    "Unreal process exited successfully without OrdaX import/load proof",
                    {
                        "artifact_path": str(artifact),
                        "uproject_path": str(uproject),
                        "destination_path": destination,
                        "source_preserved": True,
                    },
                )
        except (ValueError, OSError, FileNotFoundError) as error:
            return ActionResult(False, str(error))
        finally:
            if script_path is not None:
                script_path.unlink(missing_ok=True)

        return ActionResult(
            True,
            "Unreal imported, loaded and saved the verified FBX derivative",
            {
                "engine": "unreal",
                "engine_validated": True,
                "artifact_path": str(artifact),
                "uproject_path": str(uproject),
                "destination_path": destination,
                "object_paths": proof["object_paths"],
                "asset_classes": proof["classes"],
                "sha256": verification["sha256"],
                "source_current_matches": verification["source_current_matches"],
                "source_preserved": True,
                "unattended": True,
                "null_rhi": True,
            },
        )
