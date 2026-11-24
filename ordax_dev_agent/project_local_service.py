"""Bounded in-process transport for an owner-authorized local project service.

The caller must obtain LocalServicePolicy from the existing verified project
and Owner authorization, never from a model-supplied origin or route policy.
This module does not register a public MCP action, issue grants, start a host,
persist media, or decide editorial operations.
"""
from __future__ import annotations

import hashlib
import http.client
import re
from dataclasses import dataclass
from urllib.parse import urlencode

MAX_BATCH_ITEMS = 8
MAX_BATCH_BYTES = 262144
_MEDIA = frozenset({"application/json", "image/jpeg", "image/png", "image/webp"})
_PATH = re.compile(r"/[A-Za-z0-9_./-]*")
_FIELD = re.compile(r"[A-Za-z0-9_-]{1,64}")


class LocalServiceError(ValueError):
    """Stable failure without leaking local URLs, query values or credentials."""


@dataclass(frozen=True, slots=True)
class LocalServiceRoute:
    path: str
    query_fields: frozenset[str]
    media_types: frozenset[str]


@dataclass(frozen=True, slots=True)
class LocalServicePolicy:
    # Numeric loopback only: no DNS, external origin, proxy or redirect.
    host: str
    port: int
    routes: tuple[LocalServiceRoute, ...]


@dataclass(frozen=True, slots=True)
class LocalServiceResponse:
    route: str
    media_type: str
    body: bytes
    sha256: str


def _route_map(policy: LocalServicePolicy) -> dict[str, LocalServiceRoute]:
    if policy.host not in ("127.0.0.1", "::1"):
        raise LocalServiceError("local_service_origin_denied")
    if type(policy.port) is not int or not 1 <= policy.port <= 65535:
        raise LocalServiceError("local_service_port_invalid")
    routes = {}
    for route in policy.routes:
        if (not isinstance(route.path, str) or not _PATH.fullmatch(route.path)
                or "//" in route.path or any(part in (".", "..") for part in route.path.split("/"))
                or route.path in routes):
            raise LocalServiceError("local_service_route_policy_invalid")
        if not route.media_types or not route.media_types <= _MEDIA:
            raise LocalServiceError("local_service_media_policy_invalid")
        if any(not isinstance(field, str) or not _FIELD.fullmatch(field) for field in route.query_fields):
            raise LocalServiceError("local_service_query_policy_invalid")
        routes[route.path] = route
    if not routes:
        raise LocalServiceError("local_service_route_policy_missing")
    return routes


def read_local_service_batch(
    policy: LocalServicePolicy,
    requests: list[dict],
    *,
    max_bytes: int = MAX_BATCH_BYTES,
) -> list[LocalServiceResponse]:
    """GET only, validate the entire batch before I/O, return bytes in memory.

    The response budget is shared across the batch. No partial payload is
    returned on failure. The registered host retains its own authentication,
    authorization and application-specific query semantics.
    """
    if type(max_bytes) is not int or not 1 <= max_bytes <= MAX_BATCH_BYTES:
        raise LocalServiceError("local_service_byte_budget_invalid")
    if not isinstance(requests, list) or not 1 <= len(requests) <= MAX_BATCH_ITEMS:
        raise LocalServiceError("local_service_batch_size_invalid")
    routes = _route_map(policy)
    prepared = []
    for request in requests:
        if not isinstance(request, dict) or set(request) - {"route", "query"}:
            raise LocalServiceError("local_service_request_invalid")
        path = request.get("route")
        if not isinstance(path, str) or path not in routes:
            raise LocalServiceError("local_service_route_denied")
        route = routes[path]
        query = request.get("query", {})
        if not isinstance(query, dict) or len(query) > 16:
            raise LocalServiceError("local_service_query_invalid")
        if any(not isinstance(key, str) or key not in route.query_fields
               for key in query):
            raise LocalServiceError("local_service_query_denied")
        for value in query.values():
            if type(value) not in (str, int) or len(str(value)) > 256:
                raise LocalServiceError("local_service_query_value_invalid")
            if any(ord(char) < 32 or ord(char) == 127 for char in str(value)):
                raise LocalServiceError("local_service_query_value_invalid")
        target = path + ("?" + urlencode(query) if query else "")
        if len(target.encode("ascii")) > 2048:
            raise LocalServiceError("local_service_request_too_large")
        prepared.append((route, target))

    remaining = max_bytes
    results = []
    connection = http.client.HTTPConnection(policy.host, policy.port, timeout=5.0)
    try:
        for route, target in prepared:
            if not remaining:
                raise LocalServiceError("local_service_byte_budget_exceeded")
            connection.request("GET", target, headers={
                "Accept": ", ".join(sorted(route.media_types)),
                "Accept-Encoding": "identity",
            })
            with connection.getresponse() as response:
                # Never follow redirects or forward caller headers/cookies.
                if response.status != 200:
                    raise LocalServiceError("local_service_http_status_" + str(response.status))
                media_type = response.headers.get_content_type()
                if media_type not in route.media_types:
                    raise LocalServiceError("local_service_media_type_denied")
                if response.getheader("Content-Encoding", "identity").lower() != "identity":
                    raise LocalServiceError("local_service_content_encoding_denied")
                declared = response.headers.get_all("Content-Length", [])
                if len(declared) > 1 or (declared and (
                    not declared[0].isascii() or not declared[0].isdecimal()
                    or len(declared[0]) > 10
                )):
                    raise LocalServiceError("local_service_content_length_invalid")
                expected = int(declared[0]) if declared else None
                if expected is not None and expected > remaining:
                    raise LocalServiceError("local_service_byte_budget_exceeded")
                body = response.read(remaining + 1)
                if len(body) > remaining:
                    raise LocalServiceError("local_service_byte_budget_exceeded")
                if expected is not None and len(body) != expected:
                    raise LocalServiceError("local_service_incomplete_response")
                if not body:
                    raise LocalServiceError("local_service_empty_response")
                remaining -= len(body)
                results.append(LocalServiceResponse(
                    route.path, media_type, body, hashlib.sha256(body).hexdigest()
                ))
    except (OSError, http.client.HTTPException) as error:
        raise LocalServiceError("local_service_transport_failed") from error
    finally:
        connection.close()
    return results
