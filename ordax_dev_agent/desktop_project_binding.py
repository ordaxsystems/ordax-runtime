"""Read the OS-owned local Project-to-cloud link snapshot over the existing Native bridge.

This module creates no project, path, grant, device identity, server, queue or
authorization store. The Native Host already owns the per-launch loopback
bridge and its Product Runtime credential. A returned project-N is usable only
when the OS Project Catalog has durably and explicitly linked it to the
canonical cloud Project and Space.
"""
from __future__ import annotations

import json
import os
import re
import uuid
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

_SURFACE_URL = re.compile(
    r"^http://127\.0\.0\.1:([1-9][0-9]{0,4})"
    r"/__ordax/native/desktop-surface-state$"
)
_TOKEN = re.compile(r"^[a-f0-9]{64}$")
_LOCAL_PROJECT = re.compile(r"^project-[1-9][0-9]*$")
_SCHEMA = "ordax.desktop-project-links/1"
_MAX_RESPONSE_BYTES = 16 * 1024
_MAX_LINKS = 32


class _RejectRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _canonical_uuid(value: object) -> bool:
    if not isinstance(value, str):
        return False
    try:
        return str(uuid.UUID(value)) == value
    except (AttributeError, TypeError, ValueError):
        return False


def _native_endpoint() -> tuple[str, str] | None:
    if os.environ.get("ORDAX_RUNTIME_HOST_PROFILE") != "ordax-desktop":
        return None
    source = os.environ.get("ORDAX_DESKTOP_SURFACE_BRIDGE_URL", "")
    token = os.environ.get("ORDAX_DESKTOP_SURFACE_BRIDGE_TOKEN", "")
    match = _SURFACE_URL.fullmatch(source)
    if not match or int(match.group(1)) > 65535 or not _TOKEN.fullmatch(token):
        return None
    return (
        source.removesuffix("/desktop-surface-state") + "/desktop-project-links",
        token,
    )


def _validate_snapshot(value: object) -> dict:
    if not isinstance(value, dict) or set(value) != {
        "schema", "host", "persistence", "links",
    }:
        raise ValueError("Invalid Native Project link snapshot")
    if (
        value.get("schema") != _SCHEMA
        or value.get("host") != "ordax-desktop"
        or value.get("persistence") != "device"
        or not isinstance(value.get("links"), list)
        or len(value["links"]) > _MAX_LINKS
    ):
        raise ValueError("Invalid Native Project link snapshot")
    local_seen: set[str] = set()
    cloud_seen: set[str] = set()
    normalized = []
    for raw in value["links"]:
        if not isinstance(raw, dict) or set(raw) != {
            "localProjectId", "cloudProjectId", "spaceId",
        }:
            raise ValueError("Invalid Native Project link")
        local_id = raw.get("localProjectId")
        cloud_id = raw.get("cloudProjectId")
        space_id = raw.get("spaceId")
        if (
            not isinstance(local_id, str)
            or _LOCAL_PROJECT.fullmatch(local_id) is None
            or not _canonical_uuid(cloud_id)
            or not _canonical_uuid(space_id)
            or local_id in local_seen
            or cloud_id in cloud_seen
        ):
            raise ValueError("Invalid Native Project link")
        local_seen.add(local_id)
        cloud_seen.add(cloud_id)
        normalized.append({
            "localProjectId": local_id,
            "cloudProjectId": cloud_id,
            "spaceId": space_id,
        })
    return {
        "schema": _SCHEMA,
        "host": "ordax-desktop",
        "persistence": "device",
        "links": normalized,
    }


def read_desktop_project_links() -> dict | None:
    """Return only a durable, bounded OS Project Catalog link snapshot.

    Missing/stale bridge state means there is no local binding authority to
    confirm. Malformed authenticated data is rejected rather than interpreted
    as a path/repository hint.
    """
    endpoint = _native_endpoint()
    if endpoint is None:
        return None
    url, token = endpoint
    request = Request(
        url,
        method="GET",
        headers={
            "X-OrdaX-Product-Bridge-Token": token,
            "Accept": "application/json",
        },
    )
    try:
        with build_opener(ProxyHandler({}), _RejectRedirects()).open(
            request, timeout=2.0,
        ) as reply:
            if reply.status != 200:
                return None
            raw = reply.read(_MAX_RESPONSE_BYTES + 1)
    except (HTTPError, URLError, OSError):
        return None
    if len(raw) > _MAX_RESPONSE_BYTES:
        raise ValueError("Oversized Native Project link snapshot")
    try:
        decoded = json.loads(raw.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeError) as error:
        raise ValueError("Invalid Native Project link snapshot") from error
    return _validate_snapshot(decoded)
