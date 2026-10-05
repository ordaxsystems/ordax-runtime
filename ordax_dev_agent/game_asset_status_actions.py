"""Sanitized async task status for game-asset providers.

Provider result URLs can contain short-lived signatures. Status actions therefore
return only control-plane fields and never model/download URLs. Actual result
URLs are consumed internally by game_assets.provider_download.
"""
from __future__ import annotations

import os
import re
from typing import Any

import httpx

from .models import ActionResult


_TRIPO_BASE = "https://api.tripo3d.ai/v2/openapi"
_MESHY_BASE = "https://api.meshy.ai"
_RODIN_BASE = "https://api.hyper3d.com/api/v2"
_TASK_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,191}")

_MESHY_ROUTES = {
    "text_to_3d_preview": "/openapi/v2/text-to-3d",
    "text_to_3d_refine": "/openapi/v2/text-to-3d",
    "image_to_3d": "/openapi/v1/image-to-3d",
    "multi_image_to_3d": "/openapi/v1/multi-image-to-3d",
    "rigging": "/openapi/v1/rigging",
    "text_to_motion": "/openapi/v1/text-to-motion",
    "animation": "/openapi/v1/animations",
}


def _token(value: Any, field: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string")
    result = value.strip()
    if not _TASK_ID_RE.fullmatch(result):
        raise ValueError(f"{field} contains unsupported characters")
    return result


def _json(response: httpx.Response, provider: str) -> dict[str, Any]:
    try:
        data = response.json()
    except ValueError as error:
        raise ValueError(f"{provider} returned non-JSON status") from error
    if not isinstance(data, dict):
        raise ValueError(f"{provider} returned an unexpected status payload")
    if response.status_code >= 400:
        detail = data.get("message") or data.get("error") or data.get("code") or "request failed"
        raise ValueError(f"{provider} HTTP {response.status_code}: {detail}")
    return data


def _pick(source: dict[str, Any], names: tuple[str, ...]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for name in names:
        value = source.get(name)
        if value is None or isinstance(value, (str, int, float, bool)):
            if value is not None:
                result[name] = value
    return result


class GameAssetStatusActions:
    """Status-only provider control plane with signed URL redaction by omission."""

    def game_assets_provider_status(self, payload: dict[str, Any]) -> ActionResult:
        supported = {"project", "provider", "operation", "task_id", "subscription_key"}
        unsupported = set(payload) - supported
        if unsupported:
            return ActionResult(False, f"unsupported fields: {', '.join(sorted(unsupported))}")
        self._project(payload)
        provider = str(payload.get("provider") or "").strip().lower()
        operation = str(payload.get("operation") or "").strip()
        try:
            if provider == "tripo":
                api_key = os.environ.get("TRIPO_API_KEY")
                if not api_key:
                    raise ValueError("TRIPO_API_KEY is not configured")
                task_id = _token(payload.get("task_id"), "task_id")
                response = httpx.get(
                    f"{_TRIPO_BASE}/task/{task_id}",
                    headers={"Authorization": f"Bearer {api_key}"},
                    timeout=30.0,
                )
                raw = _json(response, "Tripo")
                data = raw.get("data") if isinstance(raw.get("data"), dict) else raw
                status = _pick(
                    data,
                    (
                        "task_id",
                        "type",
                        "status",
                        "progress",
                        "create_time",
                        "running_left_time",
                        "queuing_num",
                        "error_code",
                        "error_msg",
                    ),
                )
                status.setdefault("task_id", task_id)
            elif provider == "meshy":
                api_key = os.environ.get("MESHY_API_KEY")
                if not api_key:
                    raise ValueError("MESHY_API_KEY is not configured")
                route = _MESHY_ROUTES.get(operation)
                if route is None:
                    raise ValueError("operation is required and unsupported for Meshy task status")
                task_id = _token(payload.get("task_id"), "task_id")
                response = httpx.get(
                    f"{_MESHY_BASE}{route}/{task_id}",
                    headers={"Authorization": f"Bearer {api_key}"},
                    timeout=30.0,
                )
                data = _json(response, "Meshy")
                status = _pick(
                    data,
                    (
                        "id",
                        "task_id",
                        "status",
                        "progress",
                        "created_at",
                        "started_at",
                        "finished_at",
                        "expires_at",
                        "consumed_credits",
                        "task_error",
                    ),
                )
                status.setdefault("task_id", task_id)
            elif provider in {"rodin", "hyper3d_rodin"}:
                api_key = os.environ.get("RODIN_API_KEY")
                if not api_key:
                    raise ValueError("RODIN_API_KEY is not configured")
                subscription_key = _token(payload.get("subscription_key"), "subscription_key")
                response = httpx.post(
                    f"{_RODIN_BASE}/status",
                    headers={"Authorization": f"Bearer {api_key}"},
                    json={"subscription_key": subscription_key},
                    timeout=30.0,
                )
                data = _json(response, "Rodin")
                jobs_raw = data.get("jobs")
                jobs = []
                if isinstance(jobs_raw, list):
                    for item in jobs_raw[:64]:
                        if isinstance(item, dict):
                            jobs.append(_pick(item, ("uuid", "status", "progress", "error")))
                status = {"jobs": jobs, "job_count": len(jobs)}
            else:
                raise ValueError("provider must be tripo, meshy, or rodin")
        except (ValueError, httpx.HTTPError) as error:
            return ActionResult(False, str(error))
        return ActionResult(
            True,
            f"{provider} task status retrieved",
            {
                "provider": "rodin" if provider == "hyper3d_rodin" else provider,
                "operation": operation or None,
                "status": status,
                "signed_result_urls_returned": False,
            },
        )
