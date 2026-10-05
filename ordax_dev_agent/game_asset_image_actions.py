"""Project-local image and multiview generation for supported 3D providers.

The job surface accepts only files inside a registered OrdaX project. Images are
validated locally, bounded before upload, and never replaced by arbitrary remote
URLs. Provider-specific request contracts remain explicit so an upstream API
change fails visibly instead of silently changing generation semantics.
"""
from __future__ import annotations

import base64
import os
from pathlib import Path
from typing import Any

import httpx

from .models import ActionResult


_TRIPO_BASE = "https://api.tripo3d.ai/v2/openapi"
_MESHY_BASE = "https://api.meshy.ai"
_RODIN_BASE = "https://api.hyper3d.com/api/v2"

_MAX_STANDARD_IMAGE_BYTES = 20 * 1024 * 1024
_MAX_RODIN_IMAGE_BYTES = 25 * 1024 * 1024
_ALLOWED_IMAGE_SUFFIXES = frozenset({".jpg", ".jpeg", ".png"})
_MIME_BY_SUFFIX = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png"}
_TRIPO_MODEL_VERSIONS = frozenset(
    {
        "P1-20260311",
        "Turbo-v1.0-20250506",
        "v3.1-20260211",
        "v3.0-20250812",
        "v2.5-20250123",
        "v2.0-20240919",
        "v1.4-20240625",
    }
)
_MESHY_MODELS = frozenset({"meshy-6-lite", "meshy-6", "meshy-7.1", "latest"})
_RODIN_TIERS = frozenset(
    {
        "Gen-2.5-Extreme-Low",
        "Gen-2.5-Low",
        "Gen-2.5-Medium",
        "Gen-2.5-High",
        "Gen-2.5-Extreme-High",
    }
)
_RODIN_MESH_MODES = frozenset({"Raw", "Quad"})
_RODIN_FORMATS = frozenset({"glb", "usdz", "fbx", "obj", "stl"})
_RODIN_MATERIALS = frozenset({"PBR", "Shaded", "All", "Hybrid", "None"})
_RODIN_QUALITY = frozenset({"high", "medium", "low", "extra-low"})
_RODIN_SYMMETRY = frozenset({"symmetric", "balanced", "asymmetric", "unknown"})
_RODIN_LABELS = frozenset({"F", "FL", "FR", "B", "BL", "BR", "L", "R", "U", "D", "?"})


def _choice(value: Any, field: str, allowed: frozenset[str], default: str) -> str:
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
) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field} must be an integer")
    if value < minimum or value > maximum:
        raise ValueError(f"{field} must be between {minimum} and {maximum}")
    return value


def _optional_text(value: Any, field: str, maximum: int) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string")
    result = value.strip()
    if not result:
        return None
    if len(result) > maximum:
        raise ValueError(f"{field} must be at most {maximum} characters")
    return result


def _image_kind(path: Path) -> tuple[str, str]:
    suffix = path.suffix.lower()
    if suffix not in _ALLOWED_IMAGE_SUFFIXES:
        raise ValueError(f"unsupported image extension: {path.name}")
    with path.open("rb") as handle:
        head = handle.read(16)
    if suffix == ".png":
        if not head.startswith(b"\x89PNG\r\n\x1a\n"):
            raise ValueError(f"image content does not match PNG extension: {path.name}")
        return "png", "image/png"
    if not head.startswith(b"\xff\xd8\xff"):
        raise ValueError(f"image content does not match JPEG extension: {path.name}")
    return "jpg", "image/jpeg"


def _project_images(
    project,
    value: Any,
    *,
    minimum: int,
    maximum: int,
    max_bytes: int,
) -> list[dict[str, Any]]:
    if isinstance(value, str):
        raw = [value]
    else:
        raw = value
    if not isinstance(raw, list) or not minimum <= len(raw) <= maximum:
        raise ValueError(f"image_paths must contain between {minimum} and {maximum} images")
    result: list[dict[str, Any]] = []
    seen: set[Path] = set()
    for item in raw:
        if not isinstance(item, str) or not item.strip():
            raise ValueError("each image_paths entry must be a non-empty string")
        path = project.path(item.strip())
        if not path.is_file():
            raise ValueError(f"image is not a file: {path.name}")
        if path in seen:
            raise ValueError("image_paths cannot contain duplicate files")
        seen.add(path)
        size = path.stat().st_size
        if size <= 0:
            raise ValueError(f"image is empty: {path.name}")
        if size > max_bytes:
            raise ValueError(f"image exceeds {max_bytes} bytes: {path.name}")
        image_type, mime = _image_kind(path)
        result.append(
            {
                "path": path,
                "type": image_type,
                "mime": mime,
                "bytes": size,
            }
        )
    return result


def _data_uri(image: dict[str, Any]) -> str:
    encoded = base64.b64encode(image["path"].read_bytes()).decode("ascii")
    return f"data:{image['mime']};base64,{encoded}"


def _json_response(response: httpx.Response, provider: str) -> dict[str, Any]:
    try:
        data = response.json()
    except ValueError as error:
        raise ValueError(f"{provider} returned a non-JSON response") from error
    if not isinstance(data, dict):
        raise ValueError(f"{provider} returned an unexpected JSON payload")
    if response.status_code >= 400:
        detail = data.get("message") or data.get("error") or data.get("code") or "request failed"
        raise ValueError(f"{provider} HTTP {response.status_code}: {detail}")
    if data.get("error"):
        raise ValueError(f"{provider} rejected request: {data.get('error')}: {data.get('message') or ''}".rstrip())
    return data


def _tripo_upload(api_key: str, image: dict[str, Any]) -> str:
    with image["path"].open("rb") as handle:
        response = httpx.post(
            f"{_TRIPO_BASE}/upload",
            headers={"Authorization": f"Bearer {api_key}"},
            files={"file": (image["path"].name, handle, image["mime"])},
            timeout=120.0,
        )
    data = _json_response(response, "Tripo")
    nested = data.get("data")
    if not isinstance(nested, dict):
        raise ValueError("Tripo upload response has no data object")
    token = nested.get("image_token") or nested.get("file_token")
    if not isinstance(token, str) or not token.strip():
        raise ValueError("Tripo upload response has no image token")
    return token.strip()


class GameAssetImageActions:
    """Image-conditioned 3D generation with project-local source validation."""

    def game_assets_meshy_submit_images(self, payload: dict[str, Any]) -> ActionResult:
        supported = {
            "project",
            "image_paths",
            "ai_model",
            "model_type",
            "target_polycount",
            "geometry_resolution",
            "should_texture",
            "enable_pbr",
            "pose_mode",
            "texture_resolution",
            "auto_size",
        }
        unsupported = set(payload) - supported
        if unsupported:
            return ActionResult(False, f"unsupported fields: {', '.join(sorted(unsupported))}")
        project = self._project(payload)
        api_key = os.environ.get("MESHY_API_KEY")
        if not api_key:
            return ActionResult(False, "MESHY_API_KEY is not configured")
        try:
            images = _project_images(
                project,
                payload.get("image_paths"),
                minimum=1,
                maximum=4,
                max_bytes=_MAX_STANDARD_IMAGE_BYTES,
            )
            multi = len(images) > 1
            request: dict[str, Any] = {
                "ai_model": _choice(payload.get("ai_model"), "ai_model", _MESHY_MODELS, "latest"),
                "should_texture": bool(payload.get("should_texture", True)),
                "enable_pbr": bool(payload.get("enable_pbr", True)),
                "target_formats": ["glb"],
                "auto_size": bool(payload.get("auto_size", True)),
            }
            if multi:
                request["image_urls"] = [_data_uri(image) for image in images]
                geometry = payload.get("geometry_resolution")
                if geometry is not None:
                    request["geometry_resolution"] = _choice(
                        geometry,
                        "geometry_resolution",
                        frozenset({"standard", "2k"}),
                        "standard",
                    )
                if payload.get("model_type") is not None or payload.get("target_polycount") is not None or payload.get("pose_mode") is not None:
                    raise ValueError("model_type, target_polycount, and pose_mode are single-image options")
                endpoint = "/openapi/v1/multi-image-to-3d"
                operation = "multi_image_to_3d"
            else:
                request["image_url"] = _data_uri(images[0])
                model_type = payload.get("model_type")
                if model_type is not None:
                    request["model_type"] = _choice(
                        model_type,
                        "model_type",
                        frozenset({"standard", "smart-topology"}),
                        "standard",
                    )
                target_polycount = _bounded_int(
                    payload.get("target_polycount"),
                    "target_polycount",
                    minimum=100,
                    maximum=300000,
                )
                if target_polycount is not None:
                    request["target_polycount"] = target_polycount
                pose_mode = payload.get("pose_mode")
                if pose_mode is not None:
                    request["pose_mode"] = _choice(
                        pose_mode,
                        "pose_mode",
                        frozenset({"a-pose", "t-pose"}),
                        "a-pose",
                    )
                if payload.get("geometry_resolution") is not None:
                    raise ValueError("geometry_resolution is a multi-image option")
                endpoint = "/openapi/v1/image-to-3d"
                operation = "image_to_3d"
            texture_resolution = payload.get("texture_resolution")
            if texture_resolution is not None:
                request["texture_resolution"] = _choice(
                    texture_resolution,
                    "texture_resolution",
                    frozenset({"2k", "4k", "8k"}),
                    "2k",
                )
            response = httpx.post(
                f"{_MESHY_BASE}{endpoint}",
                headers={"Authorization": f"Bearer {api_key}"},
                json=request,
                timeout=120.0,
            )
            data = _json_response(response, "Meshy")
            task_id = data.get("result") or data.get("id") or data.get("task_id")
            if not isinstance(task_id, str) or not task_id.strip():
                raise ValueError("Meshy did not return a task id")
        except (ValueError, OSError, httpx.HTTPError) as error:
            return ActionResult(False, str(error))
        return ActionResult(
            True,
            f"Meshy {operation} submitted",
            {
                "provider": "meshy",
                "operation": operation,
                "task_id": task_id.strip(),
                "image_count": len(images),
                "source_paths": [str(image["path"]) for image in images],
                "source_bytes": [image["bytes"] for image in images],
            },
        )

    def game_assets_tripo_submit_images(self, payload: dict[str, Any]) -> ActionResult:
        supported = {
            "project",
            "image_paths",
            "model_version",
            "face_limit",
            "quad",
            "texture",
            "pbr",
        }
        unsupported = set(payload) - supported
        if unsupported:
            return ActionResult(False, f"unsupported fields: {', '.join(sorted(unsupported))}")
        project = self._project(payload)
        api_key = os.environ.get("TRIPO_API_KEY")
        if not api_key:
            return ActionResult(False, "TRIPO_API_KEY is not configured")
        try:
            raw_paths = payload.get("image_paths")
            count = len(raw_paths) if isinstance(raw_paths, list) else 1 if isinstance(raw_paths, str) else 0
            if count not in {1, 4}:
                raise ValueError("Tripo image_paths must contain exactly 1 image or 4 multiview images")
            images = _project_images(
                project,
                raw_paths,
                minimum=count,
                maximum=count,
                max_bytes=_MAX_STANDARD_IMAGE_BYTES,
            )
            descriptors = []
            for image in images:
                token = _tripo_upload(api_key, image)
                descriptors.append({"type": image["type"], "file_token": token})
            request: dict[str, Any] = {
                "type": "image_to_model" if len(images) == 1 else "multiview_to_model",
            }
            if len(images) == 1:
                request["file"] = descriptors[0]
            else:
                # Strict wrapper order: front, left, back, right.
                request["files"] = descriptors
            model_version = payload.get("model_version")
            if model_version is not None:
                request["model_version"] = _choice(
                    model_version,
                    "model_version",
                    _TRIPO_MODEL_VERSIONS,
                    "v3.1-20260211",
                )
            face_limit = _bounded_int(
                payload.get("face_limit"), "face_limit", minimum=500, maximum=20000
            )
            if face_limit is not None:
                request["face_limit"] = face_limit
            request["quad"] = bool(payload.get("quad", False))
            request["texture"] = bool(payload.get("texture", True))
            request["pbr"] = bool(payload.get("pbr", True))
            response = httpx.post(
                f"{_TRIPO_BASE}/task",
                headers={"Authorization": f"Bearer {api_key}"},
                json=request,
                timeout=120.0,
            )
            data = _json_response(response, "Tripo")
            nested = data.get("data") if isinstance(data.get("data"), dict) else data
            task_id = nested.get("task_id")
            if not isinstance(task_id, str) or not task_id.strip():
                raise ValueError("Tripo did not return a task id")
        except (ValueError, OSError, httpx.HTTPError) as error:
            return ActionResult(False, str(error))
        return ActionResult(
            True,
            f"Tripo {request['type']} submitted",
            {
                "provider": "tripo",
                "operation": request["type"],
                "task_id": task_id.strip(),
                "image_count": len(images),
                "view_order": ["front", "left", "back", "right"] if len(images) == 4 else ["primary"],
                "source_paths": [str(image["path"]) for image in images],
                "source_bytes": [image["bytes"] for image in images],
                "upload_tokens_returned": False,
            },
        )

    def game_assets_rodin_submit_images(self, payload: dict[str, Any]) -> ActionResult:
        supported = {
            "project",
            "image_paths",
            "image_labels",
            "prompt",
            "tier",
            "mesh_mode",
            "geometry_file_format",
            "material",
            "quality",
            "quality_override",
            "t_a_pose",
            "is_symmetric",
            "preview_render",
        }
        unsupported = set(payload) - supported
        if unsupported:
            return ActionResult(False, f"unsupported fields: {', '.join(sorted(unsupported))}")
        project = self._project(payload)
        api_key = os.environ.get("RODIN_API_KEY")
        if not api_key:
            return ActionResult(False, "RODIN_API_KEY is not configured")
        try:
            images = _project_images(
                project,
                payload.get("image_paths"),
                minimum=1,
                maximum=5,
                max_bytes=_MAX_RODIN_IMAGE_BYTES,
            )
            labels_raw = payload.get("image_labels")
            labels: list[str] = []
            if labels_raw is not None:
                if not isinstance(labels_raw, list) or len(labels_raw) != len(images):
                    raise ValueError("image_labels must contain exactly one label per image")
                for label in labels_raw:
                    if not isinstance(label, str) or label not in _RODIN_LABELS:
                        raise ValueError(
                            "image_labels entries must be one of: " + ", ".join(sorted(_RODIN_LABELS))
                        )
                    labels.append(label)
            tier = _choice(payload.get("tier"), "tier", _RODIN_TIERS, "Gen-2.5-Medium")
            mesh_mode = _choice(payload.get("mesh_mode"), "mesh_mode", _RODIN_MESH_MODES, "Raw")
            geometry_format = _choice(
                payload.get("geometry_file_format"),
                "geometry_file_format",
                _RODIN_FORMATS,
                "glb",
            )
            material = _choice(payload.get("material"), "material", _RODIN_MATERIALS, "PBR")
            quality = _choice(payload.get("quality"), "quality", _RODIN_QUALITY, "medium")
            quality_override = _bounded_int(
                payload.get("quality_override"),
                "quality_override",
                minimum=500,
                maximum=2_000_000,
            )
            if quality_override is not None:
                if mesh_mode == "Quad" and quality_override > 200_000:
                    raise ValueError("quality_override cannot exceed 200000 in Quad mode")
                if tier not in {"Gen-2.5-High", "Gen-2.5-Extreme-High"} and quality_override > 1_000_000:
                    raise ValueError(
                        "quality_override above 1000000 requires Gen-2.5-High or Gen-2.5-Extreme-High"
                    )
            prompt = _optional_text(payload.get("prompt"), "prompt", 1024)
            symmetry = _choice(
                payload.get("is_symmetric"),
                "is_symmetric",
                _RODIN_SYMMETRY,
                "unknown",
            )
            data: list[tuple[str, str]] = [
                ("tier", tier),
                ("mesh_mode", mesh_mode),
                ("geometry_file_format", geometry_format),
                ("material", material),
                ("quality", quality),
                ("TAPose", "true" if bool(payload.get("t_a_pose", False)) else "false"),
                ("is_symmetric", symmetry),
                ("preview_render", "true" if bool(payload.get("preview_render", False)) else "false"),
            ]
            if prompt is not None:
                data.append(("prompt", prompt))
            if quality_override is not None:
                data.append(("quality_override", str(quality_override)))
            for label in labels:
                data.append(("image_label", label))
            handles = []
            files = []
            try:
                for image in images:
                    handle = image["path"].open("rb")
                    handles.append(handle)
                    files.append(("images", (image["path"].name, handle, image["mime"])))
                response = httpx.post(
                    f"{_RODIN_BASE}/rodin",
                    headers={"Authorization": f"Bearer {api_key}"},
                    data=data,
                    files=files,
                    timeout=180.0,
                )
            finally:
                for handle in handles:
                    handle.close()
            result = _json_response(response, "Rodin")
            task_uuid = result.get("uuid")
            jobs = result.get("jobs") if isinstance(result.get("jobs"), dict) else {}
            subscription_key = jobs.get("subscription_key")
            if not isinstance(task_uuid, str) or not task_uuid.strip() or not isinstance(subscription_key, str) or not subscription_key.strip():
                raise ValueError("Rodin did not return required task identifiers")
        except (ValueError, OSError, httpx.HTTPError) as error:
            return ActionResult(False, str(error))
        return ActionResult(
            True,
            "Rodin image-to-3D submitted",
            {
                "provider": "rodin",
                "operation": "image_to_3d",
                "task_id": task_uuid.strip(),
                "subscription_key": subscription_key.strip(),
                "image_count": len(images),
                "image_labels": labels,
                "source_paths": [str(image["path"]) for image in images],
                "source_bytes": [image["bytes"] for image in images],
                "geometry_file_format": geometry_format,
                "tier": tier,
                "consumed": result.get("consumed"),
            },
        )
