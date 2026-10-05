"""Canonical ingestion of generated 3D provider results into registered projects.

The action deliberately retrieves result URLs from the provider API itself rather
than trusting an arbitrary URL supplied by a job. Downloads are streamed with
size limits, written atomically, hashed, and accompanied by a provenance
manifest that never stores signed query parameters or API credentials.
"""
from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx

from .models import ActionResult


_TRIPO_BASE = "https://api.tripo3d.ai/v2/openapi"
_MESHY_BASE = "https://api.meshy.ai"
_RODIN_BASE = "https://api.hyper3d.com/api/v2"

_TASK_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,191}")
_ALLOWED_FORMATS = frozenset({"glb", "fbx", "obj", "mtl", "usdz", "stl", "blend", "zip"})
_DEFAULT_MAX_BYTES = 1024 * 1024 * 1024
_MAX_MAX_BYTES = 2 * 1024 * 1024 * 1024
_CHUNK_BYTES = 1024 * 1024

_MESHY_STATUS_ROUTES = {
    "text_to_3d_preview": "/openapi/v2/text-to-3d",
    "text_to_3d_refine": "/openapi/v2/text-to-3d",
    "image_to_3d": "/openapi/v1/image-to-3d",
    "multi_image_to_3d": "/openapi/v1/multi-image-to-3d",
    "rigging": "/openapi/v1/rigging",
    "text_to_motion": "/openapi/v1/text-to-motion",
    "animation": "/openapi/v1/animations",
}


def _task_id(value: Any, field: str = "task_id") -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string")
    result = value.strip()
    if not _TASK_ID_RE.fullmatch(result):
        raise ValueError(f"{field} contains unsupported characters")
    return result


def _format(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError("format must be a string")
    result = value.strip().lower().lstrip(".")
    if result not in _ALLOWED_FORMATS:
        raise ValueError(f"format must be one of: {', '.join(sorted(_ALLOWED_FORMATS))}")
    return result


def _max_bytes(value: Any) -> int:
    if value is None:
        return _DEFAULT_MAX_BYTES
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("max_bytes must be an integer")
    if value < 1024 or value > _MAX_MAX_BYTES:
        raise ValueError(f"max_bytes must be between 1024 and {_MAX_MAX_BYTES}")
    return value


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
    return data


def _safe_url(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("provider result URL is missing")
    url = value.strip()
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise ValueError("provider result URL must use HTTPS")
    host = parsed.hostname.rstrip(".").lower()
    if host in {"localhost", "localhost.localdomain"}:
        raise ValueError("provider result URL cannot target localhost")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if address is not None and (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_reserved
        or address.is_unspecified
    ):
        raise ValueError("provider result URL cannot target a private/reserved address")
    if parsed.username or parsed.password:
        raise ValueError("provider result URL cannot embed credentials")
    return url


def _redacted_url(url: str) -> str:
    parsed = urlsplit(url)
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))


def _meshy_result(payload: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    api_key = os.environ.get("MESHY_API_KEY")
    if not api_key:
        raise ValueError("MESHY_API_KEY is not configured")
    operation = str(payload.get("operation") or "").strip()
    route = _MESHY_STATUS_ROUTES.get(operation)
    if route is None:
        raise ValueError("operation is required and unsupported for Meshy result download")
    task = _task_id(payload.get("task_id"))
    response = httpx.get(
        f"{_MESHY_BASE}{route}/{task}",
        headers={"Authorization": f"Bearer {api_key}"},
        timeout=60.0,
    )
    data = _json_response(response, "Meshy")
    status = str(data.get("status") or "").upper()
    if status != "SUCCEEDED":
        raise ValueError(f"Meshy task is not ready: {status or 'UNKNOWN'}")
    fmt = _format(payload.get("format"))
    model_urls = data.get("model_urls")
    if not isinstance(model_urls, dict):
        raise ValueError("Meshy task has no model_urls")
    url = _safe_url(model_urls.get(fmt))
    return url, {
        "provider": "meshy",
        "operation": operation,
        "task_id": task,
        "format": fmt,
        "provider_status": status,
        "provider_created_at": data.get("created_at"),
        "provider_finished_at": data.get("finished_at"),
        "provider_consumed_credits": data.get("consumed_credits"),
    }


def _tripo_result(payload: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    api_key = os.environ.get("TRIPO_API_KEY")
    if not api_key:
        raise ValueError("TRIPO_API_KEY is not configured")
    task = _task_id(payload.get("task_id"))
    response = httpx.get(
        f"{_TRIPO_BASE}/task/{task}",
        headers={"Authorization": f"Bearer {api_key}"},
        timeout=60.0,
    )
    data = _json_response(response, "Tripo")
    task_data = data.get("data") if isinstance(data.get("data"), dict) else data
    status = str(task_data.get("status") or "").lower()
    if status not in {"success", "succeeded", "completed"}:
        raise ValueError(f"Tripo task is not ready: {status or 'unknown'}")
    output = task_data.get("output")
    if not isinstance(output, dict):
        raise ValueError("Tripo task has no output object")
    fmt = _format(payload.get("format"))
    model_values = [
        value
        for value in (output.get("model"), output.get("pbr_model"), output.get("base_model"))
        if isinstance(value, str)
    ]
    chosen: str | None = None
    for candidate in model_values:
        if urlsplit(candidate).path.lower().endswith("." + fmt):
            chosen = candidate
            break
    if chosen is None and len(model_values) == 1:
        # Only allow extensionless fallback. If the provider URL explicitly says
        # another known format, do not silently save it under the requested suffix.
        suffix = Path(urlsplit(model_values[0]).path).suffix.lower().lstrip(".")
        if not suffix or suffix not in _ALLOWED_FORMATS:
            chosen = model_values[0]
    if chosen is None:
        raise ValueError(f"Tripo output does not expose an unambiguous {fmt} model")
    url = _safe_url(chosen)
    return url, {
        "provider": "tripo",
        "operation": str(task_data.get("type") or payload.get("operation") or "task"),
        "task_id": task,
        "format": fmt,
        "provider_status": status,
        "provider_progress": task_data.get("progress"),
    }


def _rodin_result(payload: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    api_key = os.environ.get("RODIN_API_KEY")
    if not api_key:
        raise ValueError("RODIN_API_KEY is not configured")
    task = _task_id(payload.get("task_id"), "task_id")
    try:
        import uuid

        task = str(uuid.UUID(task))
    except ValueError as error:
        raise ValueError("Rodin task_id must be a UUID") from error
    response = httpx.post(
        f"{_RODIN_BASE}/download",
        headers={"Authorization": f"Bearer {api_key}"},
        json={"task_uuid": task},
        timeout=60.0,
    )
    data = _json_response(response, "Rodin")
    if data.get("error"):
        raise ValueError(f"Rodin rejected download manifest: {data.get('error')}")
    fmt = _format(payload.get("format"))
    raw_files = data.get("list")
    if not isinstance(raw_files, list):
        raise ValueError("Rodin download manifest has no file list")
    matches: list[tuple[str, str]] = []
    for item in raw_files:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        url = item.get("url")
        if isinstance(name, str) and isinstance(url, str) and name.lower().endswith("." + fmt):
            matches.append((name, url))
    if len(matches) != 1:
        raise ValueError(f"Rodin download manifest does not contain exactly one .{fmt} result")
    name, raw_url = matches[0]
    return _safe_url(raw_url), {
        "provider": "rodin",
        "operation": "download",
        "task_id": task,
        "format": fmt,
        "provider_filename": name,
    }


def _provider_result(payload: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    provider = str(payload.get("provider") or "").strip().lower()
    if provider == "meshy":
        return _meshy_result(payload)
    if provider == "tripo":
        return _tripo_result(payload)
    if provider in {"rodin", "hyper3d_rodin"}:
        return _rodin_result(payload)
    raise ValueError("provider must be meshy, tripo, or rodin")


def _download(url: str, destination: Path, *, max_bytes: int) -> dict[str, Any]:
    current = _safe_url(url)
    redirects = 0
    temp = destination.with_name(destination.name + ".part")
    temp.unlink(missing_ok=True)
    digest = hashlib.sha256()
    total = 0
    content_type: str | None = None
    try:
        while True:
            with httpx.stream("GET", current, timeout=120.0, follow_redirects=False) as response:
                if response.status_code in {301, 302, 303, 307, 308}:
                    location = response.headers.get("location")
                    if not location:
                        raise ValueError("provider download redirect has no location")
                    redirects += 1
                    if redirects > 5:
                        raise ValueError("provider download exceeded redirect limit")
                    current = _safe_url(str(httpx.URL(current).join(location)))
                    continue
                if response.status_code >= 400:
                    raise ValueError(f"provider artifact download HTTP {response.status_code}")
                content_length = response.headers.get("content-length")
                if content_length:
                    try:
                        expected = int(content_length)
                    except ValueError:
                        expected = None
                    if expected is not None and expected > max_bytes:
                        raise ValueError("provider artifact exceeds max_bytes")
                content_type = response.headers.get("content-type")
                with temp.open("wb") as handle:
                    for chunk in response.iter_bytes(_CHUNK_BYTES):
                        if not chunk:
                            continue
                        total += len(chunk)
                        if total > max_bytes:
                            raise ValueError("provider artifact exceeds max_bytes")
                        digest.update(chunk)
                        handle.write(chunk)
                break
        if total <= 0:
            raise ValueError("provider artifact download returned an empty file")
        temp.replace(destination)
    except Exception:
        temp.unlink(missing_ok=True)
        raise
    return {
        "bytes": total,
        "sha256": digest.hexdigest(),
        "content_type": content_type,
        "source_url": _redacted_url(current),
        "redirects": redirects,
    }


class GameAssetArtifactActions:
    """Provider result ingestion with deterministic local provenance."""

    def game_assets_provider_download(self, payload: dict[str, Any]) -> ActionResult:
        supported = {
            "project",
            "provider",
            "operation",
            "task_id",
            "format",
            "output_path",
            "overwrite",
            "max_bytes",
        }
        unsupported = set(payload) - supported
        if unsupported:
            return ActionResult(False, f"unsupported fields: {', '.join(sorted(unsupported))}")
        project = self._project(payload)
        try:
            fmt = _format(payload.get("format"))
            output = project.path(str(payload.get("output_path") or ""), must_exist=False)
            if output.suffix.lower() != "." + fmt:
                raise ValueError(f"output_path must end in .{fmt}")
            manifest_path = output.with_name(output.name + ".ordax.json")
            overwrite = bool(payload.get("overwrite", False))
            if output.exists() and not overwrite:
                raise ValueError("output_path already exists; set overwrite=true explicitly")
            if manifest_path.exists() and not overwrite:
                raise ValueError("provenance manifest already exists; set overwrite=true explicitly")
            limit = _max_bytes(payload.get("max_bytes"))
            url, provenance = _provider_result(payload)
            output.parent.mkdir(parents=True, exist_ok=True)
            transfer = _download(url, output, max_bytes=limit)
            manifest = {
                "schema": "ordax.generated-asset/1",
                "created_at": datetime.now(timezone.utc).isoformat(),
                "project": project.slug,
                "artifact": {
                    "path": str(output.relative_to(project.root)),
                    "format": fmt,
                    "bytes": transfer["bytes"],
                    "sha256": transfer["sha256"],
                    "content_type": transfer["content_type"],
                },
                "provenance": {
                    **provenance,
                    "source_url": transfer["source_url"],
                    "redirects": transfer["redirects"],
                },
                "security": {
                    "provider_url_supplied_by_job": False,
                    "signed_query_persisted": False,
                    "max_bytes": limit,
                },
            }
            manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
        except (ValueError, OSError, httpx.HTTPError) as error:
            return ActionResult(False, str(error))
        return ActionResult(
            True,
            "provider artifact downloaded and registered",
            {
                "artifact_path": str(output),
                "manifest_path": str(manifest_path),
                "bytes": transfer["bytes"],
                "sha256": transfer["sha256"],
                "provider": provenance["provider"],
                "format": fmt,
            },
        )
