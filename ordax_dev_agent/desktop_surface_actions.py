"""Strict, read-only OS Surface observation for the separately enrolled Desktop.

The Native Host authenticates both the displayed Surface reporter and this
Runtime using different per-launch credentials. Never falls back to Windows
screen capture, filesystem, browser automation, or arbitrary localhost URLs.
"""
from __future__ import annotations

import json
import os
import re
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request, build_opener

from .models import ActionResult

URL = re.compile(
    r"^http://127\.0\.0\.1:([1-9][0-9]{0,4})"
    r"/__ordax/native/desktop-surface-state$"
)
TOKEN = re.compile(r"^[a-f0-9]{64}$")
APP_ID = re.compile(r"^[a-z][a-z0-9-]{0,63}$")
SCHEMA = "ordax.desktop-surface-state/1"


def desktop_surface_status(payload: dict) -> ActionResult:
    if payload:
        return ActionResult(False, "Surface status accepts no arguments")
    if os.environ.get("ORDAX_RUNTIME_HOST_PROFILE") != "ordax-desktop":
        return ActionResult(False, "Native Desktop Runtime profile required")
    url = os.environ.get("ORDAX_DESKTOP_SURFACE_BRIDGE_URL", "")
    token = os.environ.get("ORDAX_DESKTOP_SURFACE_BRIDGE_TOKEN", "")
    match = URL.fullmatch(url)
    if not match or int(match.group(1)) > 65535 or not TOKEN.fullmatch(token):
        return ActionResult(False, "Verified Native Surface bridge unavailable")
    request = Request(url, headers={
        "X-OrdaX-Product-Bridge-Token": token,
        "Accept": "application/json",
    }, method="GET")
    try:
        # Disable HTTP proxies: this credential is strictly for local Native.
        with build_opener(ProxyHandler({})).open(request, timeout=2.0) as reply:
            if reply.status != 200:
                raise ValueError("Native Surface observation failed")
            body = reply.read(1025)
            if len(body) > 1024:
                raise ValueError("Oversized Native Surface response")
        result = json.loads(body.decode("utf-8"))
        if (not isinstance(result, dict) or result.get("schema") != SCHEMA or
                result.get("host") != "ordax-desktop" or
                result.get("surfaceReady") is not True or
                type(result.get("ageMs")) is not int or
                not (0 <= result["ageMs"] <= 15000) or
                not (result.get("activeAppId") is None or
                     isinstance(result["activeAppId"], str) and
                     APP_ID.fullmatch(result["activeAppId"]))):
            raise ValueError("Invalid Native Surface response")
        return ActionResult(True, "Native Surface observation", {
            "schema": SCHEMA,
            "activeAppId": result["activeAppId"],
            "surfaceReady": True,
            "ageMs": result["ageMs"],
        })
    except (HTTPError, URLError, OSError, ValueError, TypeError,
            json.JSONDecodeError, UnicodeError):
        return ActionResult(False, "Native Surface observation unavailable")
