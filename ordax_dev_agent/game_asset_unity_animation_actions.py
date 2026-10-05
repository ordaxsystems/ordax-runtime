"""Editor-side Unity animation sampling gate for imported character assets."""
from __future__ import annotations

from typing import Any

from .game_asset_unity_companion import request_game_asset_animation_companion
from .models import ActionResult


def _asset_path(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError("asset_path must be a string")
    result = value.strip().replace("\\", "/")
    if not result.startswith("Assets/") or result.startswith("Assets/../"):
        raise ValueError("asset_path must stay inside Assets/")
    if "/../" in result or result.endswith("/..") or "/./" in result:
        raise ValueError("asset_path must stay inside Assets/")
    return result


def _clip_name(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("clip_name must be a string")
    result = value.strip()
    if not result or len(result) > 256 or any(ord(character) < 32 for character in result):
        raise ValueError("clip_name must be a non-empty printable string up to 256 characters")
    return result


def _normalized_time(value: Any) -> float:
    if value is None:
        return 0.5
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("normalized_time must be a number")
    result = float(value)
    if result <= 0.0 or result > 1.0:
        raise ValueError("normalized_time must be > 0 and <= 1")
    return result


def _optional_bool(value: Any, field: str) -> bool | None:
    if value is None:
        return None
    if not isinstance(value, bool):
        raise ValueError(f"{field} must be a boolean")
    return value


def _non_negative_int(value: Any, field: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{field} must be a non-negative integer")
    return value


def _timeout_seconds(value: Any) -> float:
    if value is None:
        return 180.0
    if isinstance(value, bool) or not isinstance(value, int) or value < 30 or value > 600:
        raise ValueError("timeout_seconds must be an integer between 30 and 600")
    return float(value)


class GameAssetUnityAnimationActions:
    """Prove imported clips alter an isolated Unity model pose without Play Mode."""

    def game_assets_unity_animation_sample_audit(
        self, payload: dict[str, Any]
    ) -> ActionResult:
        supported = {
            "project",
            "asset_path",
            "clip_name",
            "normalized_time",
            "require_pose_change",
            "require_human_motion",
            "require_root_or_motion_curves",
            "min_changed_transforms",
            "min_changed_blend_shapes",
            "timeout_seconds",
        }
        unsupported = set(payload) - supported
        if unsupported:
            return ActionResult(False, f"unsupported fields: {', '.join(sorted(unsupported))}")

        try:
            project = self._project(payload)
            if "unity" not in project.apps:
                raise ValueError(f"Unity is not enabled for project {project.slug}")
            asset_path = _asset_path(payload.get("asset_path"))
            physical = project.path(asset_path)
            if not physical.is_file():
                raise ValueError("asset_path must point to a project-local model file")
            clip_name = _clip_name(payload.get("clip_name"))
            normalized_time = _normalized_time(payload.get("normalized_time"))
            require_pose_change = _optional_bool(
                payload.get("require_pose_change"), "require_pose_change"
            )
            if require_pose_change is None:
                require_pose_change = True
            require_human_motion = bool(
                _optional_bool(payload.get("require_human_motion"), "require_human_motion")
            )
            require_root_or_motion_curves = bool(
                _optional_bool(
                    payload.get("require_root_or_motion_curves"),
                    "require_root_or_motion_curves",
                )
            )
            min_changed_transforms = _non_negative_int(
                payload.get("min_changed_transforms"), "min_changed_transforms"
            )
            min_changed_blend_shapes = _non_negative_int(
                payload.get("min_changed_blend_shapes"), "min_changed_blend_shapes"
            )
            timeout = _timeout_seconds(payload.get("timeout_seconds"))
        except (ValueError, OSError, FileNotFoundError) as error:
            return ActionResult(False, str(error))

        command: dict[str, Any] = {
            "assetPath": asset_path,
            "normalizedSampleTime": normalized_time,
        }
        if clip_name is not None:
            command["clipName"] = clip_name

        sampled = request_game_asset_animation_companion(
            project=project,
            refresh_editor=self.unity_refresh_editor,
            action="asset_animation_sample_audit",
            payload=command,
            timeout_seconds=timeout,
        )
        if not sampled.ok:
            return sampled
        evidence = sampled.data

        failures: list[str] = []
        if require_pose_change and not bool(evidence.get("poseChanged")):
            failures.append("sampled AnimationClip did not change transforms or blend-shape weights")
        if min_changed_transforms is not None:
            actual = int(evidence.get("changedTransformCount") or 0)
            if actual < min_changed_transforms:
                failures.append(
                    f"changedTransformCount is {actual}, minimum required is {min_changed_transforms}"
                )
        if min_changed_blend_shapes is not None:
            actual = int(evidence.get("changedBlendShapeCount") or 0)
            if actual < min_changed_blend_shapes:
                failures.append(
                    f"changedBlendShapeCount is {actual}, minimum required is {min_changed_blend_shapes}"
                )
        if require_human_motion and not bool(evidence.get("humanMotion")):
            failures.append("sampled AnimationClip is not reported as humanoid motion")
        if require_root_or_motion_curves and not (
            bool(evidence.get("hasRootCurves")) or bool(evidence.get("hasMotionCurves"))
        ):
            failures.append("sampled AnimationClip exposes neither root nor motion curves")

        return ActionResult(
            not failures,
            (
                "Unity AnimationClip changed the isolated model pose"
                if not failures
                else "Unity AnimationClip sampled but animation requirements failed"
            ),
            {
                "engine": "unity",
                "asset_path": asset_path,
                "clip_name": evidence.get("clipName"),
                "editor_sample_validated": True,
                "pose_change_validated": bool(evidence.get("poseChanged")),
                "semantic_requirements_passed": not failures,
                "failures": failures,
                "sample": evidence,
                "requirements": {
                    "normalized_time": normalized_time,
                    "require_pose_change": require_pose_change,
                    "require_human_motion": require_human_motion,
                    "require_root_or_motion_curves": require_root_or_motion_curves,
                    "min_changed_transforms": min_changed_transforms,
                    "min_changed_blend_shapes": min_changed_blend_shapes,
                },
                "scene_isolated": bool(evidence.get("previewSceneUsed")),
                "play_mode_validated": False,
                "root_motion_application_validated": False,
                "next_gate": "unity_play_mode_animation_root_motion_validation",
            },
        )
