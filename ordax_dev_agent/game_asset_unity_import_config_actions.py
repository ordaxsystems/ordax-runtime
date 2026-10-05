"""Explicit, transactional Unity ModelImporter configuration for game characters."""
from __future__ import annotations

from typing import Any

from .game_asset_unity_companion import request_game_asset_companion
from .models import ActionResult


_CONFIG_FIELDS = frozenset(
    {
        "animation_type",
        "avatar_setup",
        "source_avatar_path",
        "import_animation",
        "optimize_game_objects",
        "resample_curves",
        "is_readable",
    }
)


def _project_asset_path(value: Any, field: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string")
    result = value.strip().replace("\\", "/")
    if not result.startswith("Assets/") or result.startswith("Assets/../"):
        raise ValueError(f"{field} must stay inside Assets/")
    if "/../" in result or result.endswith("/..") or "/./" in result:
        raise ValueError(f"{field} must stay inside Assets/")
    return result


def _animation_type(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("animation_type must be a string")
    token = "".join(character for character in value.lower() if character.isalnum())
    mapping = {
        "none": "None",
        "legacy": "Legacy",
        "generic": "Generic",
        "human": "Human",
        "humanoid": "Human",
    }
    if token not in mapping:
        raise ValueError("animation_type must be one of: none, legacy, generic, human, humanoid")
    return mapping[token]


def _avatar_setup(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("avatar_setup must be a string")
    token = "".join(character for character in value.lower() if character.isalnum())
    mapping = {
        "none": "NoAvatar",
        "noavatar": "NoAvatar",
        "create": "CreateFromThisModel",
        "createfromthismodel": "CreateFromThisModel",
        "copy": "CopyFromOther",
        "copyfromother": "CopyFromOther",
    }
    if token not in mapping:
        raise ValueError(
            "avatar_setup must be one of: no_avatar, create_from_this_model, copy_from_other"
        )
    return mapping[token]


def _optional_bool(value: Any, field: str) -> bool | None:
    if value is None:
        return None
    if not isinstance(value, bool):
        raise ValueError(f"{field} must be a boolean")
    return value


def _timeout_seconds(value: Any) -> float:
    if value is None:
        return 240.0
    if isinstance(value, bool) or not isinstance(value, int) or value < 30 or value > 600:
        raise ValueError("timeout_seconds must be an integer between 30 and 600")
    return float(value)


class GameAssetUnityImportConfigActions:
    """Configure one explicit Unity model import without global postprocessors."""

    def game_assets_unity_character_import_configure(
        self, payload: dict[str, Any]
    ) -> ActionResult:
        supported = {
            "project",
            "asset_path",
            "confirm",
            "timeout_seconds",
            *_CONFIG_FIELDS,
        }
        unsupported = set(payload) - supported
        if unsupported:
            return ActionResult(False, f"unsupported fields: {', '.join(sorted(unsupported))}")
        if payload.get("confirm") is not True:
            return ActionResult(
                False,
                "confirm=true is required because this action changes Unity importer settings and reimports the asset",
            )
        if not any(field in payload for field in _CONFIG_FIELDS):
            return ActionResult(False, "at least one Unity importer setting must be provided")

        try:
            project = self._project(payload)
            if "unity" not in project.apps:
                raise ValueError(f"Unity is not enabled for project {project.slug}")
            asset_path = _project_asset_path(payload.get("asset_path"), "asset_path")
            asset_file = project.path(asset_path)
            if not asset_file.is_file():
                raise ValueError("asset_path must point to a project-local model file")

            animation_type = _animation_type(payload.get("animation_type"))
            avatar_setup = _avatar_setup(payload.get("avatar_setup"))
            source_avatar_path = payload.get("source_avatar_path")
            if source_avatar_path is not None:
                source_avatar_path = _project_asset_path(source_avatar_path, "source_avatar_path")
                source_file = project.path(source_avatar_path)
                if not source_file.is_file():
                    raise ValueError("source_avatar_path must point to an existing Unity asset")
                if avatar_setup != "CopyFromOther":
                    raise ValueError(
                        "source_avatar_path requires avatar_setup=copy_from_other explicitly"
                    )
                if source_avatar_path == asset_path:
                    raise ValueError("source_avatar_path must reference a different Unity asset")

            if avatar_setup == "CopyFromOther" and source_avatar_path is None:
                raise ValueError(
                    "avatar_setup=copy_from_other requires source_avatar_path for deterministic configuration"
                )
            if animation_type == "Human" and avatar_setup not in {
                "CreateFromThisModel",
                "CopyFromOther",
            }:
                raise ValueError(
                    "animation_type=human requires avatar_setup=create_from_this_model or copy_from_other"
                )
            if animation_type in {"None", "Legacy"}:
                if avatar_setup not in {None, "NoAvatar"}:
                    raise ValueError("none/legacy animation types cannot use an Avatar setup")
                avatar_setup = "NoAvatar"

            optional_bools = {
                field: _optional_bool(payload.get(field), field)
                for field in (
                    "import_animation",
                    "optimize_game_objects",
                    "resample_curves",
                    "is_readable",
                )
            }
            timeout = _timeout_seconds(payload.get("timeout_seconds"))
        except (ValueError, OSError, FileNotFoundError) as error:
            return ActionResult(False, str(error))

        command: dict[str, Any] = {"assetPath": asset_path}
        if animation_type is not None:
            command["animationType"] = animation_type
        if avatar_setup is not None:
            command["avatarSetup"] = avatar_setup
        if source_avatar_path is not None:
            command["sourceAvatarPath"] = source_avatar_path
        bool_contract = {
            "import_animation": ("setImportAnimation", "importAnimation"),
            "optimize_game_objects": ("setOptimizeGameObjects", "optimizeGameObjects"),
            "resample_curves": ("setResampleCurves", "resampleCurves"),
            "is_readable": ("setIsReadable", "isReadable"),
        }
        for field, value in optional_bools.items():
            if value is None:
                continue
            set_field, value_field = bool_contract[field]
            command[set_field] = True
            command[value_field] = value

        configured = request_game_asset_companion(
            project=project,
            refresh_editor=self.unity_refresh_editor,
            action="asset_character_import_configure",
            payload=command,
            timeout_seconds=timeout,
        )
        if not configured.ok:
            return configured

        model_audit = self.game_assets_unity_model_audit(
            {
                "project": project.slug,
                "asset_path": asset_path,
                "timeout_seconds": int(timeout),
            }
        )
        advanced_audit = self._advanced_unity_audit(
            project=project,
            asset_path=asset_path,
            timeout_seconds=timeout,
        )
        if not model_audit.ok or not advanced_audit.ok:
            return ActionResult(
                False,
                "Unity importer settings were applied but post-reimport verification was incomplete",
                {
                    "configuration_applied": True,
                    "configuration": configured.data,
                    "model_audit": {
                        "ok": model_audit.ok,
                        "summary": model_audit.summary,
                        "data": model_audit.data,
                    },
                    "advanced_audit": {
                        "ok": advanced_audit.ok,
                        "summary": advanced_audit.summary,
                        "data": advanced_audit.data,
                    },
                    "retryable": True,
                },
            )

        return ActionResult(
            True,
            "Unity character importer configured, reimported and verified",
            {
                "engine": "unity",
                "asset_path": asset_path,
                "configuration_applied": True,
                "configuration": configured.data,
                "model_audit": model_audit.data.get("audit"),
                "advanced_audit": advanced_audit.data,
                "model_source_file_written": False,
                "import_metadata_changed": True,
                "transactional_rollback_supported": True,
            },
        )
