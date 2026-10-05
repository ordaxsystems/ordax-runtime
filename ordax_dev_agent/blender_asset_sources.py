"""Typed external asset discovery for Blender workflows.

External providers are queried by the OrdaX agent, never by arbitrary model-
authored Blender Python. Import/download remains a separate explicit capability.
"""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from typing import Any


POLYHAVEN_API = "https://api.polyhaven.com"
USER_AGENT = "OrdaX-Dev-Agent/0.5"


def _request_json(url: str, *, timeout_seconds: float = 30.0) -> Any:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
        },
    )
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        return json.loads(response.read().decode("utf-8"))


def search_polyhaven(
    *,
    query: str = "",
    asset_type: str = "all",
    categories: str | None = None,
    limit: int = 20,
    timeout_seconds: float = 30.0,
) -> dict[str, Any]:
    normalized_type = asset_type.strip().lower()
    if normalized_type not in {"all", "hdris", "textures", "models"}:
        raise ValueError("asset_type must be one of: all, hdris, textures, models")

    limit = max(1, min(int(limit), 100))
    params: dict[str, str] = {}
    if normalized_type != "all":
        params["type"] = normalized_type
    if categories and categories.strip():
        params["categories"] = categories.strip()

    suffix = "?" + urllib.parse.urlencode(params) if params else ""
    raw = _request_json(
        POLYHAVEN_API + "/assets" + suffix,
        timeout_seconds=timeout_seconds,
    )
    if not isinstance(raw, dict):
        raise ValueError("Poly Haven returned an unexpected assets payload")

    tokens = [token for token in query.lower().split() if token]
    matches: list[dict[str, Any]] = []
    total_matching = 0

    for asset_id, metadata in raw.items():
        if not isinstance(metadata, dict):
            continue

        searchable = (
            str(asset_id)
            + " "
            + json.dumps(metadata, ensure_ascii=False, sort_keys=True)
        ).lower()
        if tokens and not all(token in searchable for token in tokens):
            continue

        total_matching += 1
        if len(matches) >= limit:
            continue

        matches.append(
            {
                "provider": "polyhaven",
                "asset_id": str(asset_id),
                "name": metadata.get("name") or metadata.get("display_name") or str(asset_id),
                "type": metadata.get("type"),
                "categories": metadata.get("categories") or [],
                "tags": metadata.get("tags") or [],
                "authors": metadata.get("authors") or {},
                "download_count": metadata.get("download_count"),
                "thumbnail_url": metadata.get("thumbnail_url"),
                "license": "CC0",
            }
        )

    return {
        "provider": "polyhaven",
        "license": "CC0",
        "query": query,
        "asset_type": normalized_type,
        "categories": categories,
        "provider_asset_count": len(raw),
        "matching_count": total_matching,
        "returned_count": len(matches),
        "results": matches,
    }


def polyhaven_file_manifest(
    asset_id: str,
    *,
    timeout_seconds: float = 30.0,
) -> dict[str, Any]:
    normalized = asset_id.strip()
    if not normalized or any(char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for char in normalized):
        raise ValueError("asset_id contains unsupported characters")

    raw = _request_json(
        POLYHAVEN_API + "/files/" + urllib.parse.quote(normalized, safe=""),
        timeout_seconds=timeout_seconds,
    )
    if not isinstance(raw, dict):
        raise ValueError("Poly Haven returned an unexpected file manifest")

    return {
        "provider": "polyhaven",
        "asset_id": normalized,
        "license": "CC0",
        "files": raw,
    }
