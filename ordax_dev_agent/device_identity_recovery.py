"""Fail-closed recovery of an already-enrolled ORDAX device identity.

This module never enrolls a new device and never asks for GitHub or product
credentials. It only uses an existing local device token to ask the configured
Cloudflare v3 control plane which device UUID owns that credential, then repairs
agent-settings.json when the identity metadata is missing.
"""
from __future__ import annotations

import os
import uuid
from pathlib import Path
from typing import Any

import httpx

from .config import DEFAULT_CONTROL_PLANE_URL
from .device_credentials import resolve_pending_token_path, resolve_token_path
from .device_setup import atomic_json, load_settings, request
from .identity import machine_id


def _state_dir() -> Path:
    local_app_data = Path(
        os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")
    )
    return Path(
        os.environ.get(
            "ORDAX_AGENT_STATE_DIR",
            local_app_data / "OrdaX" / "DevAgent",
        )
    )


def recover_existing_device_identity(
    state: Path | None = None,
    *,
    control_plane_url: str | None = None,
    client: Any | None = None,
    binding: str | None = None,
) -> dict[str, Any]:
    """Repair missing device metadata using only an existing local credential."""

    state = Path(state or _state_dir())
    state.mkdir(parents=True, exist_ok=True)
    settings_path = state / "agent-settings.json"
    settings = load_settings(settings_path)

    existing_id = str(settings.get("device_id") or "").strip()
    existing_protocol = str(settings.get("control_plane_protocol") or "").strip().lower()
    if existing_id and existing_protocol == "cloudflare-v3":
        return {
            "ok": True,
            "state": "already-configured",
            "device_id": existing_id,
            "changed": False,
        }

    control_plane = str(
        control_plane_url
        or settings.get("control_plane_url")
        or DEFAULT_CONTROL_PLANE_URL
    ).strip().rstrip("/")
    if not control_plane.startswith("https://"):
        return {
            "ok": False,
            "state": "control-plane-invalid",
            "changed": False,
        }

    token = resolve_token_path(state)
    pending = resolve_pending_token_path(state)
    candidates = [candidate for candidate in (token, pending) if candidate.is_file()]
    if not candidates:
        return {
            "ok": False,
            "state": "credential-missing",
            "changed": False,
        }

    owns_client = client is None
    http = client or httpx.Client(timeout=10.0, follow_redirects=False)
    try:
        for candidate in candidates:
            secret = candidate.read_text(encoding="utf-8-sig").strip()
            if not 32 <= len(secret) <= 512:
                continue
            identity = request(
                http,
                control_plane + "/v3/device/setup",
                {
                    "operation": "identify",
                    "machine_binding_sha256": binding or machine_id(),
                },
                {"X-Ordax-Device-Token": secret},
            )
            if identity.get("denied") == 403:
                return {
                    "ok": False,
                    "state": "machine-binding-mismatch",
                    "changed": False,
                }
            if identity.get("ok") is not True:
                continue
            if identity.get("protocol") != "cloudflare-v3":
                return {
                    "ok": False,
                    "state": "protocol-mismatch",
                    "changed": False,
                }
            try:
                device_id = str(uuid.UUID(str(identity["device_id"])))
            except (KeyError, ValueError, TypeError, AttributeError):
                return {
                    "ok": False,
                    "state": "identity-invalid",
                    "changed": False,
                }

            settings.update(
                control_plane_url=control_plane,
                control_plane_protocol="cloudflare-v3",
                device_id=device_id,
            )
            atomic_json(settings_path, settings)
            if candidate == pending:
                os.replace(pending, token)
            return {
                "ok": True,
                "state": "recovered",
                "device_id": device_id,
                "changed": True,
            }

        return {
            "ok": False,
            "state": "credential-rejected",
            "changed": False,
        }
    finally:
        if owns_client:
            http.close()
