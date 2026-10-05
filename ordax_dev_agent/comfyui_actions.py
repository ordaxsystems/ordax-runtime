"""Safe loopback-only ComfyUI workflow bridge for local generative pipelines."""
from __future__ import annotations

import ipaddress
import json
import os
import re
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from .models import ActionResult


_WORKFLOW_MAX_BYTES = 5 * 1024 * 1024
_PROMPT_ID_RE = re.compile(r"[A-Za-z0-9._:-]{1,160}")
_NODE_CLASS_RE = re.compile(r"[A-Za-z0-9_.:+ -]{1,160}")


def _base_url() -> str:
    raw = os.environ.get("ORDAX_COMFYUI_URL", "http://127.0.0.1:8188").strip().rstrip("/")
    parsed = urlparse(raw)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("ORDAX_COMFYUI_URL must be an http(s) URL")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("ORDAX_COMFYUI_URL cannot contain credentials, query, or fragment")
    if parsed.path not in {"", "/"}:
        raise ValueError("ORDAX_COMFYUI_URL must not contain a path")
    host = parsed.hostname
    loopback = host.lower() == "localhost"
    if not loopback:
        try:
            loopback = ipaddress.ip_address(host).is_loopback
        except ValueError:
            loopback = False
    if not loopback:
        raise ValueError("ComfyUI bridge is loopback-only; use localhost/127.0.0.1/::1")
    return raw


def _json(response: httpx.Response, operation: str) -> Any:
    if response.status_code >= 400:
        raise ValueError(f"ComfyUI {operation} HTTP {response.status_code}")
    try:
        return response.json()
    except ValueError as error:
        raise ValueError(f"ComfyUI {operation} returned non-JSON data") from error


def _safe_prompt_id(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError("prompt_id must be a string")
    result = value.strip()
    if not _PROMPT_ID_RE.fullmatch(result):
        raise ValueError("prompt_id contains unsupported characters")
    return result


def _safe_node_class(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError("node_class must be a string")
    result = value.strip()
    if not _NODE_CLASS_RE.fullmatch(result):
        raise ValueError("node_class contains unsupported characters")
    return result


def _load_api_workflow(path: Path) -> dict[str, Any]:
    if path.suffix.lower() != ".json":
        raise ValueError("workflow_path must be a JSON file")
    if path.stat().st_size > _WORKFLOW_MAX_BYTES:
        raise ValueError("workflow JSON exceeds 5 MiB")
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as error:
        raise ValueError(f"cannot parse workflow JSON: {error}") from error
    if not isinstance(payload, dict) or not payload:
        raise ValueError("workflow JSON must be a non-empty object")

    # API-format ComfyUI prompts are maps of node id -> node specification.
    # Refuse UI-only workflow JSON early; users can export API format from ComfyUI.
    checked = 0
    for node_id, node in payload.items():
        if not isinstance(node_id, str) or not isinstance(node, dict):
            raise ValueError("workflow must use ComfyUI API prompt format")
        if "class_type" not in node or "inputs" not in node:
            raise ValueError("workflow must use ComfyUI API prompt format (class_type + inputs)")
        if not isinstance(node.get("class_type"), str) or not isinstance(node.get("inputs"), dict):
            raise ValueError("workflow contains an invalid node specification")
        checked += 1
        if checked > 2000:
            raise ValueError("workflow exceeds the 2000-node safety limit")
    return payload


class ComfyUIActions:
    """Local ComfyUI discovery and project-versioned workflow execution."""

    def game_assets_comfyui_status(self, payload: dict[str, Any]) -> ActionResult:
        unsupported = set(payload) - {"project"}
        if unsupported:
            return ActionResult(False, f"unsupported fields: {', '.join(sorted(unsupported))}")
        self._project(payload)
        try:
            base = _base_url()
            response = httpx.get(f"{base}/system_stats", timeout=5.0)
            data = _json(response, "system_stats")
            if not isinstance(data, dict):
                raise ValueError("ComfyUI system_stats returned an unexpected payload")
            system = data.get("system") if isinstance(data.get("system"), dict) else {}
            devices = data.get("devices") if isinstance(data.get("devices"), list) else []
            # Deliberately do not forward system.argv: launch commands can contain secrets.
            sanitized_system = {
                key: system.get(key)
                for key in (
                    "os",
                    "ram_total",
                    "ram_free",
                    "comfyui_version",
                    "python_version",
                    "pytorch_version",
                    "embedded_python",
                    "deploy_environment",
                )
                if key in system
            }
            sanitized_devices = []
            for item in devices[:16]:
                if not isinstance(item, dict):
                    continue
                sanitized_devices.append(
                    {
                        key: item.get(key)
                        for key in (
                            "name",
                            "type",
                            "index",
                            "vram_total",
                            "vram_free",
                            "torch_vram_total",
                            "torch_vram_free",
                        )
                        if key in item
                    }
                )
        except (httpx.HTTPError, ValueError) as error:
            return ActionResult(False, f"ComfyUI unavailable: {error}")
        return ActionResult(
            True,
            "ComfyUI local status retrieved",
            {"base_url": base, "system": sanitized_system, "devices": sanitized_devices},
        )

    def game_assets_comfyui_node_info(self, payload: dict[str, Any]) -> ActionResult:
        unsupported = set(payload) - {"project", "node_class"}
        if unsupported:
            return ActionResult(False, f"unsupported fields: {', '.join(sorted(unsupported))}")
        self._project(payload)
        try:
            base = _base_url()
            node_class = _safe_node_class(payload.get("node_class"))
            response = httpx.get(f"{base}/object_info/{node_class}", timeout=15.0)
            data = _json(response, "object_info")
        except (httpx.HTTPError, ValueError) as error:
            return ActionResult(False, f"ComfyUI node lookup failed: {error}")
        return ActionResult(
            True,
            "ComfyUI node information retrieved",
            {"node_class": node_class, "response": data},
        )

    def game_assets_comfyui_run_workflow(self, payload: dict[str, Any]) -> ActionResult:
        unsupported = set(payload) - {"project", "workflow_path"}
        if unsupported:
            return ActionResult(False, f"unsupported fields: {', '.join(sorted(unsupported))}")
        project = self._project(payload)
        try:
            workflow_path = project.path(str(payload.get("workflow_path") or ""))
            workflow = _load_api_workflow(workflow_path)
            base = _base_url()
            client_id = f"ordax-{uuid.uuid4().hex}"
            response = httpx.post(
                f"{base}/prompt",
                json={"prompt": workflow, "client_id": client_id},
                timeout=30.0,
            )
            data = _json(response, "prompt submit")
            if not isinstance(data, dict):
                raise ValueError("ComfyUI prompt submit returned an unexpected payload")
            node_errors = data.get("node_errors")
            if node_errors:
                return ActionResult(
                    False,
                    "ComfyUI rejected workflow nodes",
                    {"node_errors": node_errors, "workflow_path": str(workflow_path)},
                )
            prompt_id = data.get("prompt_id")
            if not isinstance(prompt_id, str) or not prompt_id:
                raise ValueError("ComfyUI did not return prompt_id")
        except (httpx.HTTPError, ValueError, OSError) as error:
            return ActionResult(False, f"ComfyUI workflow submission failed: {error}")
        return ActionResult(
            True,
            "ComfyUI workflow queued",
            {
                "workflow_path": str(workflow_path),
                "prompt_id": prompt_id,
                "client_id": client_id,
                "number": data.get("number"),
                "node_count": len(workflow),
            },
        )

    def game_assets_comfyui_history(self, payload: dict[str, Any]) -> ActionResult:
        unsupported = set(payload) - {"project", "prompt_id"}
        if unsupported:
            return ActionResult(False, f"unsupported fields: {', '.join(sorted(unsupported))}")
        self._project(payload)
        try:
            prompt_id = _safe_prompt_id(payload.get("prompt_id"))
            base = _base_url()
            response = httpx.get(f"{base}/history/{prompt_id}", timeout=15.0)
            data = _json(response, "history")
        except (httpx.HTTPError, ValueError) as error:
            return ActionResult(False, f"ComfyUI history failed: {error}")
        return ActionResult(
            True,
            "ComfyUI workflow history retrieved",
            {"prompt_id": prompt_id, "response": data},
        )
