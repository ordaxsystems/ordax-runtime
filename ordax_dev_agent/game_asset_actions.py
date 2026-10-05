"""Game-asset generation, character rigging and engine-export orchestration.

The module deliberately keeps third-party services behind a typed allow-list.
API credentials are read from the process environment and are never returned in
action results. Adobe Mixamo is handled as an explicit handoff because Adobe
does not expose a supported public automation API for its web workflow.
"""
from __future__ import annotations

import json
import math
import os
import re
import uuid
from pathlib import Path
from typing import Any

import httpx

from mcp_blender_unity.config import find_blender

from .models import ActionResult
from .process_runner import run_command as _run


_TRIPO_BASE = "https://api.tripo3d.ai/v2/openapi"
_MESHY_BASE = "https://api.meshy.ai"

_TASK_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,191}")
_ALLOWED_MIXAMO_UPLOADS = frozenset({".fbx", ".obj", ".zip"})
_ALLOWED_RIG_TYPES = frozenset(
    {"biped", "quadruped", "hexapod", "octopod", "avian", "serpentine", "aquatic"}
)

_ENGINE_PROFILES: dict[str, dict[str, Any]] = {
    "mixamo": {
        "format": "fbx",
        "purpose": "Adobe Mixamo upload / remapping",
        "include": ["MESH", "ARMATURE"],
        "animation": False,
        "axis_forward": "-Z",
        "axis_up": "Y",
        "add_leaf_bones": False,
        "embed_textures": True,
        "notes": [
            "Export only the character; exclude cameras, lights and helper objects.",
            "For an already-rigged Mixamo upload the file must be FBX.",
            "Use a neutral pose and keep the character centered at world origin.",
        ],
    },
    "unity": {
        "format": "fbx",
        "purpose": "Unity Humanoid / Generic model import",
        "include": ["MESH", "ARMATURE"],
        "animation": True,
        "axis_forward": "-Z",
        "axis_up": "Y",
        "add_leaf_bones": False,
        "embed_textures": False,
        "notes": [
            "Configure the imported ModelImporter as Humanoid when applicable.",
            "Prefer a stable avatar/rest pose and keep scale consistent.",
        ],
    },
    "unreal": {
        "format": "fbx",
        "purpose": "Unreal Engine skeletal mesh / animation import",
        "include": ["MESH", "ARMATURE"],
        "animation": True,
        "axis_forward": "-Z",
        "axis_up": "Y",
        "add_leaf_bones": False,
        "embed_textures": False,
        "notes": [
            "Unreal's documented FBX pipeline targets FBX 2020.2.",
            "Export animation clips separately when deterministic clip naming is required.",
        ],
    },
    "godot": {
        "format": "glb",
        "purpose": "Godot glTF 2.0 scene/character import",
        "include": ["MESH", "ARMATURE"],
        "animation": True,
        "notes": [
            "Godot recommends glTF 2.0 / GLB for 3D scenes.",
            "Keep skins, animations and morph targets in the glTF pipeline.",
        ],
    },
    "web": {
        "format": "glb",
        "purpose": "Web / realtime glTF delivery",
        "include": ["MESH", "ARMATURE"],
        "animation": True,
        "notes": [
            "Run a separate compression pass (Meshopt/Draco/KTX2) only after visual validation.",
        ],
    },
}


def _clean_task_id(value: Any, field: str = "task_id") -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string")
    result = value.strip()
    if not _TASK_ID_RE.fullmatch(result):
        raise ValueError(f"{field} contains unsupported characters")
    return result


def _clean_prompt(value: Any, field: str = "prompt", *, maximum: int = 800) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string")
    result = value.strip()
    if not result:
        raise ValueError(f"{field} cannot be empty")
    if len(result) > maximum:
        raise ValueError(f"{field} must be at most {maximum} characters")
    return result


def _choice(value: Any, field: str, allowed: set[str] | frozenset[str], default: str) -> str:
    if value is None:
        return default
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string")
    result = value.strip()
    if result not in allowed:
        raise ValueError(f"{field} must be one of: {', '.join(sorted(allowed))}")
    return result


def _bounded_int(
    value: Any,
    field: str,
    *,
    minimum: int,
    maximum: int,
    default: int | None = None,
) -> int | None:
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field} must be an integer")
    if value < minimum or value > maximum:
        raise ValueError(f"{field} must be between {minimum} and {maximum}")
    return value


def _bounded_float(
    value: Any,
    field: str,
    *,
    minimum: float,
    maximum: float,
    default: float | None = None,
) -> float | None:
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be numeric")
    result = float(value)
    if not math.isfinite(result) or result < minimum or result > maximum:
        raise ValueError(f"{field} must be between {minimum} and {maximum}")
    return result


def _tripo_request(operation: str, payload: dict[str, Any]) -> dict[str, Any]:
    if operation == "text_to_model":
        request: dict[str, Any] = {
            "type": "text_to_model",
            "prompt": _clean_prompt(payload.get("prompt")),
        }
        model_version = payload.get("model_version")
        if model_version is not None:
            request["model_version"] = _choice(
                model_version,
                "model_version",
                frozenset(
                    {
                        "P1-20260311",
                        "Turbo-v1.0-20250506",
                        "v3.1-20260211",
                        "v3.0-20250812",
                        "v2.5-20250123",
                        "v2.0-20240919",
                        "v1.4-20240625",
                    }
                ),
                "v3.1-20260211",
            )
        return request

    if operation == "animate_prerigcheck":
        return {
            "type": "animate_prerigcheck",
            "original_model_task_id": _clean_task_id(
                payload.get("original_model_task_id"), "original_model_task_id"
            ),
        }

    if operation == "animate_rig":
        request = {
            "type": "animate_rig",
            "original_model_task_id": _clean_task_id(
                payload.get("original_model_task_id"), "original_model_task_id"
            ),
            "out_format": _choice(
                payload.get("out_format"), "out_format", frozenset({"glb", "fbx"}), "glb"
            ),
            "spec": _choice(
                payload.get("spec"), "spec", frozenset({"tripo", "mixamo"}), "mixamo"
            ),
            "rig_type": _choice(
                payload.get("rig_type"), "rig_type", _ALLOWED_RIG_TYPES, "biped"
            ),
        }
        model_version = payload.get("model_version")
        if model_version is not None:
            request["model_version"] = _choice(
                model_version,
                "model_version",
                frozenset({"v2.5-20260210", "v2.0-20250506", "v1.0-20240301"}),
                "v2.5-20260210",
            )
        return request

    if operation == "animate_retarget":
        request = {
            "type": "animate_retarget",
            "original_model_task_id": _clean_task_id(
                payload.get("original_model_task_id"), "original_model_task_id"
            ),
            "out_format": _choice(
                payload.get("out_format"), "out_format", frozenset({"glb", "fbx"}), "glb"
            ),
            "bake_animation": bool(payload.get("bake_animation", True)),
            "export_with_geometry": bool(payload.get("export_with_geometry", True)),
            "animate_in_place": bool(payload.get("animate_in_place", False)),
        }
        raw = payload.get("animations")
        if isinstance(raw, str):
            raw = [raw]
        if not isinstance(raw, list) or not raw or len(raw) > 32:
            raise ValueError("animations must be a non-empty list with at most 32 entries")
        animations: list[str] = []
        for item in raw:
            if not isinstance(item, str) or not re.fullmatch(r"[A-Za-z0-9:_-]{1,80}", item):
                raise ValueError("animation identifiers contain unsupported characters")
            animations.append(item)
        request["animations"] = animations
        return request

    if operation == "highpoly_to_lowpoly":
        request = {
            "type": "highpoly_to_lowpoly",
            "original_model_task_id": _clean_task_id(
                payload.get("original_model_task_id"), "original_model_task_id"
            ),
            "quad": bool(payload.get("quad", False)),
            "bake": bool(payload.get("bake", True)),
        }
        face_limit = _bounded_int(
            payload.get("face_limit"), "face_limit", minimum=500, maximum=20000
        )
        if face_limit is not None:
            request["face_limit"] = face_limit
        return request

    raise ValueError(
        "unsupported Tripo operation; expected text_to_model, animate_prerigcheck, "
        "animate_rig, animate_retarget, or highpoly_to_lowpoly"
    )


def _meshy_request(operation: str, payload: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    if operation == "text_to_3d_preview":
        request: dict[str, Any] = {
            "mode": "preview",
            "prompt": _clean_prompt(payload.get("prompt"), maximum=600),
            "target_formats": ["glb"],
        }
        ai_model = payload.get("ai_model")
        if ai_model is not None:
            request["ai_model"] = _choice(
                ai_model,
                "ai_model",
                frozenset({"meshy-6-lite", "meshy-6", "meshy-7.1", "latest"}),
                "latest",
            )
        target_polycount = _bounded_int(
            payload.get("target_polycount"),
            "target_polycount",
            minimum=100,
            maximum=300000,
        )
        if target_polycount is not None:
            request["should_remesh"] = True
            request["target_polycount"] = target_polycount
        pose_mode = payload.get("pose_mode")
        if pose_mode is not None:
            request["pose_mode"] = _choice(
                pose_mode,
                "pose_mode",
                frozenset({"a-pose", "t-pose"}),
                "a-pose",
            )
        return "/openapi/v2/text-to-3d", request

    if operation == "text_to_3d_refine":
        request = {
            "mode": "refine",
            "preview_task_id": _clean_task_id(
                payload.get("preview_task_id"), "preview_task_id"
            ),
            "enable_pbr": bool(payload.get("enable_pbr", True)),
            "target_formats": ["glb"],
            "auto_size": bool(payload.get("auto_size", True)),
        }
        texture_resolution = payload.get("texture_resolution")
        if texture_resolution is not None:
            request["texture_resolution"] = _choice(
                texture_resolution,
                "texture_resolution",
                frozenset({"2k", "4k", "8k"}),
                "2k",
            )
        texture_prompt = payload.get("texture_prompt")
        if texture_prompt is not None:
            request["texture_prompt"] = _clean_prompt(
                texture_prompt, "texture_prompt", maximum=800
            )
        return "/openapi/v2/text-to-3d", request

    if operation == "rigging":
        request = {
            "input_task_id": _clean_task_id(payload.get("input_task_id"), "input_task_id")
        }
        height = _bounded_float(
            payload.get("height_meters"),
            "height_meters",
            minimum=0.1,
            maximum=20.0,
        )
        if height is not None:
            request["height_meters"] = height
        return "/openapi/v1/rigging", request

    if operation == "text_to_motion":
        request = {
            "prompt": _clean_prompt(payload.get("prompt"), maximum=500),
            "mode": _choice(
                payload.get("mode"),
                "mode",
                frozenset({"swift", "prime"}),
                "swift",
            ),
        }
        return "/openapi/v1/text-to-motion", request

    if operation == "animation":
        request = {
            "rig_task_id": _clean_task_id(payload.get("rig_task_id"), "rig_task_id"),
        }
        choices = 0
        if payload.get("motion_task_id") is not None:
            request["motion_task_id"] = _clean_task_id(
                payload.get("motion_task_id"), "motion_task_id"
            )
            choices += 1
        if payload.get("action_id") is not None:
            request["action_id"] = _bounded_int(
                payload.get("action_id"), "action_id", minimum=1, maximum=100000
            )
            choices += 1
        if payload.get("action_ids") is not None:
            raw_ids = payload.get("action_ids")
            if (
                not isinstance(raw_ids, list)
                or not raw_ids
                or len(raw_ids) > 32
                or any(isinstance(item, bool) or not isinstance(item, int) for item in raw_ids)
            ):
                raise ValueError("action_ids must be a non-empty integer list with at most 32 items")
            request["action_ids"] = [
                _bounded_int(item, "action_ids item", minimum=1, maximum=100000)
                for item in raw_ids
            ]
            choices += 1
        if choices != 1:
            raise ValueError(
                "animation requires exactly one of motion_task_id, action_id, or action_ids"
            )
        return "/openapi/v1/animations", request

    raise ValueError(
        "unsupported Meshy operation; expected text_to_3d_preview, text_to_3d_refine, "
        "rigging, text_to_motion, or animation"
    )


def _json_response(response: httpx.Response) -> dict[str, Any]:
    try:
        data = response.json()
    except ValueError as error:
        raise ValueError("provider returned a non-JSON response") from error
    if not isinstance(data, dict):
        raise ValueError("provider returned an unexpected JSON payload")
    if response.status_code >= 400:
        detail = data.get("message") or data.get("error") or data.get("code") or "request failed"
        raise ValueError(f"provider HTTP {response.status_code}: {detail}")
    return data


class GameAssetActions:
    """Typed game-asset provider and Blender handoff actions."""

    def game_assets_providers(self, payload: dict[str, Any]) -> ActionResult:
        unsupported = set(payload) - {"project"}
        if unsupported:
            return ActionResult(False, f"unsupported fields: {', '.join(sorted(unsupported))}")
        providers = {
            "adobe_mixamo": {
                "mode": "manual_handoff",
                "configured": True,
                "capabilities": ["humanoid_auto_rig", "humanoid_animation_library"],
                "upload_formats": ["fbx", "obj", "zip"],
                "notes": [
                    "No unsupported browser scraping or private API is used.",
                    "Already-rigged character uploads must be FBX.",
                ],
            },
            "tripo": {
                "mode": "api",
                "configured": bool(os.environ.get("TRIPO_API_KEY")),
                "credential_env": "TRIPO_API_KEY",
                "capabilities": [
                    "text_to_model",
                    "pre_rig_check",
                    "rig",
                    "mixamo_skeleton_spec",
                    "animation_retarget",
                    "smart_lowpoly",
                ],
            },
            "meshy": {
                "mode": "api",
                "configured": bool(os.environ.get("MESHY_API_KEY")),
                "credential_env": "MESHY_API_KEY",
                "capabilities": [
                    "text_to_3d",
                    "pbr_refine",
                    "humanoid_rig",
                    "animation",
                    "text_to_motion",
                ],
            },
        }
        return ActionResult(True, "game-asset providers inspected", {"providers": providers})

    def game_assets_export_profiles(self, payload: dict[str, Any]) -> ActionResult:
        unsupported = set(payload) - {"project", "engine"}
        if unsupported:
            return ActionResult(False, f"unsupported fields: {', '.join(sorted(unsupported))}")
        engine = payload.get("engine")
        if engine is None:
            return ActionResult(
                True,
                "game export profiles listed",
                {"profiles": _ENGINE_PROFILES},
            )
        if not isinstance(engine, str) or engine not in _ENGINE_PROFILES:
            return ActionResult(
                False,
                f"engine must be one of: {', '.join(sorted(_ENGINE_PROFILES))}",
            )
        return ActionResult(
            True,
            f"{engine} export profile",
            {"engine": engine, "profile": _ENGINE_PROFILES[engine]},
        )

    def game_assets_mixamo_handoff(self, payload: dict[str, Any]) -> ActionResult:
        supported = {"project", "source_path", "rigged"}
        unsupported = set(payload) - supported
        if unsupported:
            return ActionResult(False, f"unsupported fields: {', '.join(sorted(unsupported))}")
        project = self._project(payload)
        source = project.path(str(payload.get("source_path") or ""))
        suffix = source.suffix.lower()
        if suffix not in _ALLOWED_MIXAMO_UPLOADS:
            return ActionResult(False, "Mixamo upload must be .fbx, .obj, or .zip")
        if source.stat().st_size > 1024 * 1024 * 1024:
            return ActionResult(False, "Mixamo handoff file exceeds the 1 GiB local safety limit")
        rigged = bool(payload.get("rigged", False))
        if rigged and suffix != ".fbx":
            return ActionResult(False, "already-rigged Mixamo uploads must use FBX")
        checklist = [
            "humanoid/biped with clear head, torso, arms and legs",
            "neutral/T/A-like pose",
            "character centered near world origin",
            "no cameras, lights, helpers or unrelated scene objects in the upload",
            "connected, clean mesh without obvious topology errors",
            "avoid large extra appendages/props for Mixamo auto-rig",
        ]
        return ActionResult(
            True,
            "Mixamo handoff prepared",
            {
                "source_path": str(source),
                "rigged": rigged,
                "upload_format": suffix.lstrip("."),
                "workflow": "Adobe Mixamo web upload",
                "checklist": checklist,
                "next_steps": [
                    "upload the prepared file to Mixamo with an Adobe ID",
                    "place auto-rig markers if the character is not already rigged",
                    "download the rigged/animated FBX locally",
                    "import the FBX back through the OrdaX/Blender character pipeline",
                ],
            },
        )

    def game_assets_provider_submit(self, payload: dict[str, Any]) -> ActionResult:
        provider = str(payload.get("provider") or "").strip().lower()
        operation = str(payload.get("operation") or "").strip()
        if provider not in {"tripo", "meshy"}:
            return ActionResult(False, "provider must be tripo or meshy")
        if not operation:
            return ActionResult(False, "operation is required")

        # Resolve the project even though cloud tasks do not directly mutate it:
        # this keeps all generation work scoped to a registered OrdaX project.
        self._project(payload)

        try:
            if provider == "tripo":
                api_key = os.environ.get("TRIPO_API_KEY")
                if not api_key:
                    return ActionResult(False, "TRIPO_API_KEY is not configured")
                request = _tripo_request(operation, payload)
                response = httpx.post(
                    f"{_TRIPO_BASE}/task",
                    headers={"Authorization": f"Bearer {api_key}"},
                    json=request,
                    timeout=60.0,
                )
            else:
                api_key = os.environ.get("MESHY_API_KEY")
                if not api_key:
                    return ActionResult(False, "MESHY_API_KEY is not configured")
                path, request = _meshy_request(operation, payload)
                response = httpx.post(
                    f"{_MESHY_BASE}{path}",
                    headers={"Authorization": f"Bearer {api_key}"},
                    json=request,
                    timeout=60.0,
                )
            data = _json_response(response)
        except httpx.HTTPError as error:
            return ActionResult(False, f"{provider} request failed: {type(error).__name__}")
        except ValueError as error:
            return ActionResult(False, str(error))

        task_id = data.get("task_id") or data.get("result") or data.get("id")
        return ActionResult(
            True,
            f"{provider} {operation} submitted",
            {
                "provider": provider,
                "operation": operation,
                "task_id": task_id,
                "response": data,
            },
        )

    def game_assets_provider_status(self, payload: dict[str, Any]) -> ActionResult:
        supported = {"project", "provider", "operation", "task_id"}
        unsupported = set(payload) - supported
        if unsupported:
            return ActionResult(False, f"unsupported fields: {', '.join(sorted(unsupported))}")
        self._project(payload)
        provider = str(payload.get("provider") or "").strip().lower()
        operation = str(payload.get("operation") or "").strip()
        try:
            task_id = _clean_task_id(payload.get("task_id"))
        except ValueError as error:
            return ActionResult(False, str(error))

        try:
            if provider == "tripo":
                api_key = os.environ.get("TRIPO_API_KEY")
                if not api_key:
                    return ActionResult(False, "TRIPO_API_KEY is not configured")
                url = f"{_TRIPO_BASE}/task/{task_id}"
            elif provider == "meshy":
                api_key = os.environ.get("MESHY_API_KEY")
                if not api_key:
                    return ActionResult(False, "MESHY_API_KEY is not configured")
                route_by_operation = {
                    "text_to_3d_preview": "/openapi/v2/text-to-3d",
                    "text_to_3d_refine": "/openapi/v2/text-to-3d",
                    "rigging": "/openapi/v1/rigging",
                    "text_to_motion": "/openapi/v1/text-to-motion",
                    "animation": "/openapi/v1/animations",
                }
                route = route_by_operation.get(operation)
                if route is None:
                    return ActionResult(False, "operation is required for Meshy task status")
                url = f"{_MESHY_BASE}{route}/{task_id}"
            else:
                return ActionResult(False, "provider must be tripo or meshy")
            response = httpx.get(
                url,
                headers={"Authorization": f"Bearer {api_key}"},
                timeout=60.0,
            )
            data = _json_response(response)
        except httpx.HTTPError as error:
            return ActionResult(False, f"{provider} status failed: {type(error).__name__}")
        except ValueError as error:
            return ActionResult(False, str(error))

        return ActionResult(
            True,
            f"{provider} task status retrieved",
            {"provider": provider, "operation": operation, "task_id": task_id, "response": data},
        )

    def game_assets_blender_character_preflight(self, payload: dict[str, Any]) -> ActionResult:
        supported = {"project", "blend_file", "timeout_seconds"}
        unsupported = set(payload) - supported
        if unsupported:
            return ActionResult(False, f"unsupported fields: {', '.join(sorted(unsupported))}")
        project = self._project(payload)
        blend_file = project.path(str(payload.get("blend_file") or ""))
        if blend_file.suffix.lower() != ".blend":
            return ActionResult(False, "blend_file must be a .blend file")
        blender = find_blender()
        if blender is None:
            return ActionResult(False, "Blender executable not found")
        timeout = _bounded_int(
            payload.get("timeout_seconds"),
            "timeout_seconds",
            minimum=30,
            maximum=3600,
            default=600,
        )
        artifact_dir = self.config.state_dir / "artifacts" / project.slug / "game-assets"
        artifact_dir.mkdir(parents=True, exist_ok=True)
        report_path = artifact_dir / f"character-preflight-{uuid.uuid4().hex}.json"
        script = Path(__file__).resolve().parent / "assets" / "blender_game_asset_pipeline.py"
        result = _run(
            [
                str(blender),
                "--background",
                str(blend_file),
                "--python",
                str(script),
                "--",
                "inspect",
                "--report",
                str(report_path),
            ],
            cwd=project.root,
            timeout=int(timeout or 600),
        )
        if not result.ok:
            return result
        if not report_path.is_file():
            return ActionResult(False, "Blender preflight completed without a report")
        try:
            report = json.loads(report_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            return ActionResult(False, f"cannot read Blender preflight report: {error}")
        return ActionResult(
            True,
            "character preflight completed",
            {"report": report, "report_path": str(report_path)},
        )

    def game_assets_blender_export(self, payload: dict[str, Any]) -> ActionResult:
        supported = {
            "project",
            "blend_file",
            "engine",
            "output_path",
            "timeout_seconds",
        }
        unsupported = set(payload) - supported
        if unsupported:
            return ActionResult(False, f"unsupported fields: {', '.join(sorted(unsupported))}")
        project = self._project(payload)
        blend_file = project.path(str(payload.get("blend_file") or ""))
        if blend_file.suffix.lower() != ".blend":
            return ActionResult(False, "blend_file must be a .blend file")
        engine = str(payload.get("engine") or "").strip().lower()
        if engine not in _ENGINE_PROFILES:
            return ActionResult(
                False, f"engine must be one of: {', '.join(sorted(_ENGINE_PROFILES))}"
            )
        expected_suffix = "." + _ENGINE_PROFILES[engine]["format"]
        output = project.path(str(payload.get("output_path") or ""), must_exist=False)
        if output.suffix.lower() != expected_suffix:
            return ActionResult(False, f"{engine} output_path must end in {expected_suffix}")
        output.parent.mkdir(parents=True, exist_ok=True)

        blender = find_blender()
        if blender is None:
            return ActionResult(False, "Blender executable not found")
        timeout = _bounded_int(
            payload.get("timeout_seconds"),
            "timeout_seconds",
            minimum=30,
            maximum=3600,
            default=1800,
        )
        artifact_dir = self.config.state_dir / "artifacts" / project.slug / "game-assets"
        artifact_dir.mkdir(parents=True, exist_ok=True)
        report_path = artifact_dir / f"character-export-{uuid.uuid4().hex}.json"
        script = Path(__file__).resolve().parent / "assets" / "blender_game_asset_pipeline.py"
        result = _run(
            [
                str(blender),
                "--background",
                str(blend_file),
                "--python",
                str(script),
                "--",
                "export",
                "--profile",
                engine,
                "--output",
                str(output),
                "--report",
                str(report_path),
            ],
            cwd=project.root,
            timeout=int(timeout or 1800),
        )
        if not result.ok:
            return result
        if not output.is_file():
            return ActionResult(False, "Blender export completed without the requested artifact")
        report: dict[str, Any] = {}
        if report_path.is_file():
            try:
                report = json.loads(report_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                report = {"warning": "export report could not be parsed"}
        return ActionResult(
            True,
            f"{engine} character export completed",
            {
                "engine": engine,
                "output_path": str(output),
                "bytes": output.stat().st_size,
                "profile": _ENGINE_PROFILES[engine],
                "report": report,
                "report_path": str(report_path),
            },
        )
