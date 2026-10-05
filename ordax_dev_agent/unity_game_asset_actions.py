"""Unity-side import profiles for OrdaX generated game assets."""
from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

from .models import ActionResult


_PROFILE_SCHEMA = "ordax.unity-model-profile/1"
_SOURCE_NAME = "OrdaXGeneratedAssetPostprocessor.cs"
_TARGET_RELATIVE = PurePosixPath("Assets/Editor/OrdaX/OrdaXGeneratedAssetPostprocessor.cs")
_ALLOWED_PROFILES = frozenset({"static", "humanoid", "generic"})
_ALLOWED_MODELS = frozenset({".fbx", ".obj"})


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _source_path() -> Path:
    return Path(__file__).resolve().parent / "assets" / _SOURCE_NAME


def _asset_relative(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError("asset_path must be a string")
    raw = value.replace("\\", "/").strip()
    if not raw:
        raise ValueError("asset_path cannot be empty")
    pure = PurePosixPath(raw)
    if pure.is_absolute() or ".." in pure.parts:
        raise ValueError("asset_path must stay inside the Unity project")
    normalized = pure.as_posix()
    if not normalized.startswith("Assets/"):
        raise ValueError("asset_path must be under Assets/")
    return normalized


def _profile(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError("profile must be static, humanoid, or generic")
    result = value.strip().lower()
    if result not in _ALLOWED_PROFILES:
        raise ValueError("profile must be static, humanoid, or generic")
    return result


def _scale(value: Any) -> float:
    if value is None:
        return 1.0
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("global_scale must be numeric")
    result = float(value)
    if not math.isfinite(result) or result < 0.0001 or result > 1000.0:
        raise ValueError("global_scale must be between 0.0001 and 1000")
    return result


def _bool(payload: dict[str, Any], name: str, default: bool) -> bool:
    value = payload.get(name, default)
    if not isinstance(value, bool):
        raise ValueError(f"{name} must be boolean")
    return value


def _write_atomic(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(path)


class UnityGameAssetActions:
    """Install and manage explicit Unity ModelImporter profiles."""

    def _unity_game_asset_install(self, project) -> dict[str, Any]:
        source = _source_path()
        if not source.is_file():
            raise FileNotFoundError(f"Unity game-asset postprocessor source missing: {source}")
        target = project.path(_TARGET_RELATIVE.as_posix(), must_exist=False)
        source_hash = _sha256(source)
        target_hash = _sha256(target) if target.is_file() else None
        changed = target_hash != source_hash
        if changed:
            _write_atomic(target, source.read_text(encoding="utf-8"))
        return {
            "installed": target.is_file(),
            "changed": changed,
            "target": str(target),
            "asset_path": _TARGET_RELATIVE.as_posix(),
            "sha256": source_hash,
            "refresh_required": changed,
        }

    def unity_game_asset_pipeline_status(self, payload: dict[str, Any]) -> ActionResult:
        unsupported = set(payload) - {"project"}
        if unsupported:
            return ActionResult(False, f"unsupported fields: {', '.join(sorted(unsupported))}")
        project = self._project(payload)
        try:
            source = _source_path()
            target = project.path(_TARGET_RELATIVE.as_posix(), must_exist=False)
            source_hash = _sha256(source) if source.is_file() else None
            target_hash = _sha256(target) if target.is_file() else None
        except (OSError, ValueError) as error:
            return ActionResult(False, str(error))
        return ActionResult(
            True,
            "Unity game-asset import pipeline status",
            {
                "installed": bool(source_hash and target_hash == source_hash),
                "source_available": source_hash is not None,
                "target": str(target),
                "asset_path": _TARGET_RELATIVE.as_posix(),
                "source_sha256": source_hash,
                "installed_sha256": target_hash,
                "profile_schema": _PROFILE_SCHEMA,
                "supported_profiles": sorted(_ALLOWED_PROFILES),
                "supported_model_extensions": sorted(_ALLOWED_MODELS),
            },
        )

    def unity_game_asset_pipeline_install(self, payload: dict[str, Any]) -> ActionResult:
        unsupported = set(payload) - {"project"}
        if unsupported:
            return ActionResult(False, f"unsupported fields: {', '.join(sorted(unsupported))}")
        project = self._project(payload)
        try:
            result = self._unity_game_asset_install(project)
        except (OSError, ValueError, FileNotFoundError) as error:
            return ActionResult(False, str(error))
        return ActionResult(
            True,
            "Unity generated-asset import pipeline ready",
            result,
        )

    def unity_game_asset_profile_write(self, payload: dict[str, Any]) -> ActionResult:
        supported = {
            "project",
            "asset_path",
            "profile",
            "global_scale",
            "import_materials",
            "import_animation",
            "import_blend_shapes",
            "add_collider",
            "generate_secondary_uv",
        }
        unsupported = set(payload) - supported
        if unsupported:
            return ActionResult(False, f"unsupported fields: {', '.join(sorted(unsupported))}")
        project = self._project(payload)
        try:
            asset_relative = _asset_relative(payload.get("asset_path"))
            asset = project.path(asset_relative)
            if not asset.is_file():
                raise ValueError("asset_path must be an existing model file")
            if asset.suffix.lower() not in _ALLOWED_MODELS:
                raise ValueError("asset_path must be an FBX or OBJ model")
            profile = _profile(payload.get("profile"))
            install = self._unity_game_asset_install(project)

            default_animation = profile != "static"
            default_blend_shapes = profile != "static"
            body = {
                "schema": _PROFILE_SCHEMA,
                "profile": profile,
                "globalScale": _scale(payload.get("global_scale")),
                "importMaterials": _bool(payload, "import_materials", True),
                "importAnimation": _bool(payload, "import_animation", default_animation),
                "importBlendShapes": _bool(payload, "import_blend_shapes", default_blend_shapes),
                "addCollider": _bool(payload, "add_collider", False),
                "generateSecondaryUV": _bool(payload, "generate_secondary_uv", False),
                "sourceAsset": asset_relative,
                "writtenAt": datetime.now(timezone.utc).isoformat(),
            }
            if profile == "static":
                body["importAnimation"] = False
            sidecar = Path(str(asset) + ".ordax-unity.json")
            _write_atomic(sidecar, json.dumps(body, indent=2, ensure_ascii=False) + "\n")
        except (OSError, ValueError, FileNotFoundError) as error:
            return ActionResult(False, str(error))
        return ActionResult(
            True,
            f"Unity {profile} import profile written",
            {
                "asset_path": asset_relative,
                "sidecar_path": str(sidecar),
                "sidecar_asset_path": asset_relative + ".ordax-unity.json",
                "profile": body,
                "postprocessor": install,
                "unity_refresh_required": True,
                "lod_note": (
                    "Unity can create an LODGroup from FBX meshes using _LOD0/_LOD1/... naming."
                    if profile == "static"
                    else "Skinned LODs require a separate deformation-aware validation path."
                ),
            },
        )
