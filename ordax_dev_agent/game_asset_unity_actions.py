"""Verified generated and derived asset handoff into a registered Unity project."""
from __future__ import annotations

from typing import Any

from .game_asset_engine_export_actions import verify_engine_export
from .generated_asset_actions import _verification
from .models import ActionResult
from .unity_assets import asset_inventory, import_project_asset


_SUPPORTED_ENGINE_MODELS = frozenset({".fbx", ".obj", ".glb", ".gltf"})
_DEFAULT_DESTINATION_ROOT = "Assets/OrdaX/Generated"


def _wait_seconds(value: Any) -> float:
    if value is None:
        return 120.0
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("wait_seconds must be a number")
    result = float(value)
    if result < 5.0 or result > 600.0:
        raise ValueError("wait_seconds must be between 5 and 600")
    return result


def _timeout_seconds(value: Any, *, default: int = 180) -> float:
    if value is None:
        return float(default)
    if isinstance(value, bool) or not isinstance(value, int) or value < 5 or value > 600:
        raise ValueError("timeout_seconds must be an integer between 5 and 600")
    return float(value)


def _unity_asset_path(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError("asset_path must be a string")
    result = value.strip().replace("\\", "/")
    if not result.startswith("Assets/") or result.startswith("Assets/../"):
        raise ValueError("asset_path must stay inside Assets/")
    if "/../" in result or result.endswith("/.."):
        raise ValueError("asset_path must stay inside Assets/")
    suffix = "." + result.rsplit(".", 1)[-1].lower() if "." in result.rsplit("/", 1)[-1] else ""
    if suffix not in _SUPPORTED_ENGINE_MODELS and suffix != ".blend":
        raise ValueError("asset_path must be FBX, OBJ, Blend, GLB, or glTF")
    return result


def _unity_prefab_path(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError("prefab_path must be a string")
    result = value.strip().replace("\\", "/")
    if not result.startswith("Assets/") or result.startswith("Assets/../"):
        raise ValueError("prefab_path must stay inside Assets/")
    if "/../" in result or result.endswith("/.."):
        raise ValueError("prefab_path must stay inside Assets/")
    if not result.lower().endswith(".prefab"):
        raise ValueError("prefab_path must end in .prefab")
    return result


def _lod_thresholds(value: Any) -> list[float] | None:
    if value is None:
        return None
    if not isinstance(value, list) or len(value) < 2 or len(value) > 10:
        raise ValueError("lod_thresholds must contain 2-10 transition heights")
    result: list[float] = []
    for item in value:
        if isinstance(item, bool) or not isinstance(item, (int, float)):
            raise ValueError("lod_thresholds must contain only numbers")
        number = float(item)
        if number < 0.01 or number > 0.99:
            raise ValueError("lod_thresholds must be between 0.01 and 0.99")
        result.append(number)
    if any(result[index] <= result[index + 1] for index in range(len(result) - 1)):
        raise ValueError("lod_thresholds must be strictly descending")
    return result


def _default_prefab_path(asset_path: str) -> str:
    parent, filename = asset_path.rsplit("/", 1)
    stem = filename.rsplit(".", 1)[0]
    return f"{parent}/{stem}_LOD.prefab"


class GameAssetUnityActions:
    """Move immutable evidence into Unity with explicit engine gates."""

    def _unity_copy_with_refresh(
        self,
        *,
        project,
        artifact,
        destination_raw: str,
        overwrite: bool,
        refresh_requested: bool,
        wait_seconds: float,
        integrity: dict[str, Any],
        source_kind: str,
    ) -> ActionResult:
        destination_asset_path = _unity_asset_path(destination_raw)
        destination = project.path(destination_asset_path, must_exist=False)
        copy_report = import_project_asset(
            project.root,
            source_path=artifact,
            destination_path=destination,
            overwrite=overwrite,
        )

        refresh_report: dict[str, Any] | None = None
        if refresh_requested:
            refresh_result = self.unity_refresh_editor(
                {
                    "project": project.slug,
                    "force": True,
                    "wait_seconds": wait_seconds,
                }
            )
            refresh_report = {
                "ok": refresh_result.ok,
                "summary": refresh_result.summary,
                "data": refresh_result.data,
            }
            if not refresh_result.ok:
                return ActionResult(
                    False,
                    f"{source_kind} copied into Unity Assets but Editor import refresh was not confirmed",
                    {
                        "integrity": integrity,
                        "copy": copy_report,
                        "unity_refresh": refresh_report,
                        "retryable": True,
                        "source_preserved": True,
                    },
                )

        inventory = asset_inventory(
            project.root,
            terms=[destination.name],
            max_results=100,
        )
        asset_path = copy_report["asset_path"]
        visible = any(
            isinstance(item, dict) and item.get("path") == asset_path
            for item in inventory.get("matches", [])
        )
        if not visible:
            return ActionResult(
                False,
                "Unity asset copy completed but the destination was not visible in the project inventory",
                {
                    "integrity": integrity,
                    "copy": copy_report,
                    "unity_refresh": refresh_report,
                    "inventory": inventory,
                },
            )

        return ActionResult(
            True,
            f"{source_kind} imported into Unity project",
            {
                "integrity": integrity,
                "copy": copy_report,
                "unity_refresh": refresh_report,
                "asset_path": asset_path,
                "engine": "unity",
                "source_preserved": True,
                "import_confirmed": refresh_requested,
            },
        )

    def game_assets_unity_import_generated(self, payload: dict[str, Any]) -> ActionResult:
        supported = {
            "project",
            "artifact_path",
            "manifest_path",
            "destination_path",
            "overwrite",
            "refresh",
            "wait_seconds",
        }
        unsupported = set(payload) - supported
        if unsupported:
            return ActionResult(False, f"unsupported fields: {', '.join(sorted(unsupported))}")

        project = self._project(payload)
        try:
            if "unity" not in project.apps:
                raise ValueError(f"Unity is not enabled for project {project.slug}")
            artifact = project.path(str(payload.get("artifact_path") or ""))
            if not artifact.is_file():
                raise ValueError("artifact_path must be a file")
            if artifact.suffix.lower() not in _SUPPORTED_ENGINE_MODELS:
                raise ValueError("artifact_path must be FBX, OBJ, GLB, or glTF")
            manifest_raw = payload.get("manifest_path")
            manifest = (
                project.path(str(manifest_raw))
                if manifest_raw is not None
                else artifact.with_name(artifact.name + ".ordax.json")
            )
            verification = _verification(project, artifact, manifest)
            destination_raw = str(
                payload.get("destination_path")
                or f"{_DEFAULT_DESTINATION_ROOT}/{artifact.name}"
            )
            result = self._unity_copy_with_refresh(
                project=project,
                artifact=artifact,
                destination_raw=destination_raw,
                overwrite=bool(payload.get("overwrite", False)),
                refresh_requested=bool(payload.get("refresh", True)),
                wait_seconds=_wait_seconds(payload.get("wait_seconds")),
                integrity=verification,
                source_kind="generated asset",
            )
        except (ValueError, OSError, FileNotFoundError) as error:
            return ActionResult(False, str(error))
        return result

    def game_assets_unity_import_engine_export(self, payload: dict[str, Any]) -> ActionResult:
        supported = {
            "project",
            "artifact_path",
            "manifest_path",
            "destination_path",
            "overwrite",
            "refresh",
            "wait_seconds",
        }
        unsupported = set(payload) - supported
        if unsupported:
            return ActionResult(False, f"unsupported fields: {', '.join(sorted(unsupported))}")

        project = self._project(payload)
        try:
            if "unity" not in project.apps:
                raise ValueError(f"Unity is not enabled for project {project.slug}")
            artifact = project.path(str(payload.get("artifact_path") or ""))
            if not artifact.is_file():
                raise ValueError("artifact_path must be a file")
            if artifact.suffix.lower() not in _SUPPORTED_ENGINE_MODELS:
                raise ValueError("artifact_path must be FBX, OBJ, GLB, or glTF")
            manifest_raw = payload.get("manifest_path")
            manifest = (
                project.path(str(manifest_raw))
                if manifest_raw is not None
                else artifact.with_name(artifact.name + ".ordax.json")
            )
            verification = verify_engine_export(project, artifact, manifest)
            if verification.get("engine") != "unity":
                raise ValueError("engine export provenance is not targeted to Unity")
            destination_raw = str(
                payload.get("destination_path")
                or f"{_DEFAULT_DESTINATION_ROOT}/{artifact.name}"
            )
            result = self._unity_copy_with_refresh(
                project=project,
                artifact=artifact,
                destination_raw=destination_raw,
                overwrite=bool(payload.get("overwrite", False)),
                refresh_requested=bool(payload.get("refresh", True)),
                wait_seconds=_wait_seconds(payload.get("wait_seconds")),
                integrity=verification,
                source_kind="verified engine export",
            )
        except (ValueError, OSError, FileNotFoundError) as error:
            return ActionResult(False, str(error))
        return result

    def game_assets_unity_model_audit(self, payload: dict[str, Any]) -> ActionResult:
        supported = {"project", "asset_path", "timeout_seconds"}
        unsupported = set(payload) - supported
        if unsupported:
            return ActionResult(False, f"unsupported fields: {', '.join(sorted(unsupported))}")

        project = self._project(payload)
        try:
            if "unity" not in project.apps:
                raise ValueError(f"Unity is not enabled for project {project.slug}")
            asset_path = _unity_asset_path(payload.get("asset_path"))
            physical = project.path(asset_path)
            if not physical.is_file():
                raise ValueError("asset_path must exist inside the Unity project")
            timeout = _timeout_seconds(payload.get("timeout_seconds"))
            editor = self._editor({"project": project.slug})
            response = editor.request(
                "asset_model_audit",
                {"assetPath": asset_path},
                timeout_seconds=timeout,
            )
            if response is None:
                return ActionResult(
                    False,
                    "Unity companion is not ready for model import audit",
                    {"asset_path": asset_path, "retryable": True},
                )
            if not response.ok:
                return response
        except (ValueError, OSError, FileNotFoundError) as error:
            return ActionResult(False, str(error))

        return ActionResult(
            True,
            "Unity model import audit passed",
            {
                "asset_path": asset_path,
                "engine": "unity",
                "audit": response.data,
            },
        )

    def game_assets_unity_build_static_lod_prefab(self, payload: dict[str, Any]) -> ActionResult:
        supported = {
            "project",
            "asset_path",
            "prefab_path",
            "lod_thresholds",
            "overwrite",
            "timeout_seconds",
        }
        unsupported = set(payload) - supported
        if unsupported:
            return ActionResult(False, f"unsupported fields: {', '.join(sorted(unsupported))}")

        project = self._project(payload)
        try:
            if "unity" not in project.apps:
                raise ValueError(f"Unity is not enabled for project {project.slug}")
            asset_path = _unity_asset_path(payload.get("asset_path"))
            asset_file = project.path(asset_path)
            if not asset_file.is_file():
                raise ValueError("asset_path must exist inside the Unity project")
            prefab_path = _unity_prefab_path(
                payload.get("prefab_path") or _default_prefab_path(asset_path)
            )
            prefab_file = project.path(prefab_path, must_exist=False)
            if not prefab_file.parent.is_dir():
                raise ValueError("prefab_path parent directory must already exist inside Assets/")
            thresholds = _lod_thresholds(payload.get("lod_thresholds"))
            timeout = _timeout_seconds(payload.get("timeout_seconds"), default=240)
            overwrite = bool(payload.get("overwrite", False))

            editor = self._editor({"project": project.slug})
            audit = editor.request(
                "asset_model_audit",
                {"assetPath": asset_path},
                timeout_seconds=timeout,
            )
            if audit is None:
                return ActionResult(
                    False,
                    "Unity companion is not ready for static LOD validation",
                    {"asset_path": asset_path, "retryable": True},
                )
            if not audit.ok:
                return audit
            evidence = audit.data
            required_static_fields = {
                "modelBoneCount",
                "modelBlendShapeCount",
                "modelAnimationClipCount",
                "modelLodGroupCount",
            }
            missing = sorted(required_static_fields - set(evidence))
            if missing:
                return ActionResult(
                    False,
                    "Unity companion is too old for guarded static LOD prefab creation",
                    {"missing_fields": missing, "retryable": True},
                )
            if int(evidence.get("modelBoneCount") or 0) > 0:
                raise ValueError("static LOD prefab creation refuses models with bones")
            if int(evidence.get("modelBlendShapeCount") or 0) > 0:
                raise ValueError("static LOD prefab creation refuses models with blend shapes")
            if int(evidence.get("modelAnimationClipCount") or 0) > 0:
                raise ValueError("static LOD prefab creation refuses models with animation clips")
            if int(evidence.get("modelLodGroupCount") or 0) > 0:
                raise ValueError("source model already contains LODGroup components")

            command: dict[str, Any] = {
                "assetPath": asset_path,
                "prefabPath": prefab_path,
                "overwrite": overwrite,
            }
            if thresholds is not None:
                command["lodThresholds"] = thresholds
            built = editor.request(
                "asset_lod_prefab_build",
                command,
                timeout_seconds=timeout,
            )
            if built is None:
                return ActionResult(
                    False,
                    "Unity companion did not confirm LOD prefab creation",
                    {
                        "asset_path": asset_path,
                        "prefab_path": prefab_path,
                        "audit": evidence,
                        "retryable": True,
                    },
                )
            if not built.ok:
                return built
        except (ValueError, OSError, FileNotFoundError) as error:
            return ActionResult(False, str(error))

        return ActionResult(
            True,
            "Unity static LODGroup prefab created",
            {
                "engine": "unity",
                "asset_path": asset_path,
                "prefab_path": prefab_path,
                "audit": evidence,
                "lod": built.data,
            },
        )
