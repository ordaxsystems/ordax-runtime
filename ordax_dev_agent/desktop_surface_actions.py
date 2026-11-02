"""Product-scoped actions that reach the mounted OrdaX Native Surface.

The per-launch Native Host is the sole authority for this loopback channel.
It is never a Windows screenshot, shell, filesystem or browser adapter.
Platform Owner grants still authorize remote dispatch independently.
"""
from __future__ import annotations

import json
import os
import re
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from .models import ActionResult

URL = re.compile(
    r"^http://127\.0\.0\.1:([1-9][0-9]{0,4})"
    r"/__ordax/native/desktop-surface-state$"
)
TOKEN = re.compile(r"^[a-f0-9]{64}$")
REQUEST_ID = re.compile(r"^[a-f0-9]{32}$")
APP_ID = re.compile(r"^[a-z][a-z0-9-]{0,63}$")
NATIVE_APP_IDS = frozenset({"account", "settings", "system", "store"})
SCHEMA = "ordax.desktop-surface-state/1"
COMMAND_SCHEMA = "ordax.desktop-surface-command/1"


class _RejectRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _native_endpoint() -> tuple[str, str] | None:
    if os.environ.get("ORDAX_RUNTIME_HOST_PROFILE") != "ordax-desktop":
        return None
    url = os.environ.get("ORDAX_DESKTOP_SURFACE_BRIDGE_URL", "")
    token = os.environ.get("ORDAX_DESKTOP_SURFACE_BRIDGE_TOKEN", "")
    match = URL.fullmatch(url)
    if not match or int(match.group(1)) > 65535 or not TOKEN.fullmatch(token):
        return None
    return url, token


def _native_request(
    endpoint: tuple[str, str],
    *,
    operation: str,
    body: dict | None = None,
) -> dict:
    url, token = endpoint
    if operation not in {"status", "open"}:
        raise ValueError("Unsupported Native Surface operation")
    target = (url if operation == "status" else url.removesuffix("state") + "command")
    data = None if body is None else json.dumps(body, separators=(",", ":")).encode("utf-8")
    request = Request(
        target, method="GET" if operation == "status" else "POST",
        headers={
            "X-OrdaX-Product-Bridge-Token": token,
            "Accept": "application/json",
            **({"Content-Type": "application/json"} if data is not None else {}),
        },
        data=data,
    )
    # No system proxy, no redirects, no token transfer to another origin.
    with build_opener(ProxyHandler({}), _RejectRedirects()).open(
        request, timeout=7.0 if operation == "open" else 2.0
    ) as reply:
        if reply.status != 200:
            raise ValueError("Native Surface request not confirmed")
        raw = reply.read(1025)
    if len(raw) > 1024:
        raise ValueError("Oversized Native Surface response")
    decoded = json.loads(raw.decode("utf-8"))
    if not isinstance(decoded, dict):
        raise ValueError("Invalid Native Surface response")
    return decoded


def desktop_surface_status(payload: dict) -> ActionResult:
    if payload:
        return ActionResult(False, "Surface status accepts no arguments")
    endpoint = _native_endpoint()
    if endpoint is None:
        return ActionResult(False, "Verified Native Surface bridge unavailable")
    try:
        result = _native_request(endpoint, operation="status")
        if (result.get("schema") != SCHEMA or result.get("host") != "ordax-desktop" or
                result.get("surfaceReady") is not True or
                type(result.get("ageMs")) is not int or
                not (0 <= result["ageMs"] <= 15000) or
                not (result.get("activeAppId") is None or
                     isinstance(result["activeAppId"], str) and
                     APP_ID.fullmatch(result["activeAppId"]))):
            raise ValueError("Invalid Native Surface observation")
        return ActionResult(True, "Native Surface observation", {
            "schema": SCHEMA, "activeAppId": result["activeAppId"],
            "surfaceReady": True, "ageMs": result["ageMs"],
        })
    except (HTTPError, URLError, OSError, ValueError, TypeError,
            json.JSONDecodeError, UnicodeError):
        return ActionResult(False, "Native Surface observation unavailable")


def desktop_surface_open(payload: dict) -> ActionResult:
    if type(payload) is not dict or set(payload) != {"appId"} or (
            type(payload["appId"]) is not str or
            payload["appId"] not in NATIVE_APP_IDS):
        return ActionResult(False, "Only an installed Native system app may be requested")
    endpoint = _native_endpoint()
    if endpoint is None:
        return ActionResult(False, "Verified Native Surface bridge unavailable")
    app_id = payload["appId"]
    try:
        receipt = _native_request(endpoint, operation="open", body={
            "schema": COMMAND_SCHEMA, "appId": app_id,
        })
        if (receipt.get("schema") != COMMAND_SCHEMA or
                receipt.get("state") != "applied" or
                receipt.get("appId") != app_id or
                receipt.get("activeAppId") != app_id or
                type(receipt.get("requestId")) is not str or
                not REQUEST_ID.fullmatch(receipt["requestId"])):
            raise ValueError("Native Surface did not confirm the visible app")
        return ActionResult(True, "Native Surface opened requested app", {
            "schema": COMMAND_SCHEMA, "requestId": receipt["requestId"],
            "appId": app_id, "activeAppId": app_id, "state": "applied",
        })
    except (HTTPError, URLError, OSError, ValueError, TypeError,
            json.JSONDecodeError, UnicodeError):
        return ActionResult(False, "Native Surface activation not confirmed")
