from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .models import ActionResult


REGISTRY_SCHEMA = "ordax.app-intelligence-registry/1"
MANIFEST_SCHEMA = "ordax.app-intelligence-manifest/1"
MAX_REGISTRY_BYTES = 256 * 1024
MAX_APPS = 64
_APP_ID_RE = re.compile(r"^[a-z][a-z0-9-]{0,127}$")


def _default_registry_path() -> Path:
    return Path(__file__).resolve().parents[1] / "ordax_studio" / "app_intelligence_registry.json"


def _bounded_text(value: Any, label: str, maximum: int) -> str:
    if not isinstance(value, str) or "\x00" in value:
        raise ValueError(f"{label} is invalid")
    normalized = value.strip()
    if not normalized or len(normalized) > maximum:
        raise ValueError(f"{label} is invalid")
    return normalized


def _validate_manifest(value: Any, *, app_id: str, app_version: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("App Intelligence manifest must be an object")
    required = {
        "schema", "appId", "appVersion", "authority", "execution",
        "instructions", "intents",
    }
    if set(value) != required:
        raise ValueError("App Intelligence manifest fields are not canonical")
    if value.get("schema") != MANIFEST_SCHEMA:
        raise ValueError("App Intelligence manifest schema is incompatible")
    if value.get("appId") != app_id or value.get("appVersion") != app_version:
        raise ValueError("App Intelligence manifest identity mismatch")
    if value.get("authority") != "none" or value.get("execution") != "declarative-only":
        raise ValueError("App Intelligence manifest crossed the authority boundary")

    instructions = value.get("instructions")
    intents = value.get("intents")
    if not isinstance(instructions, list) or not 1 <= len(instructions) <= 32:
        raise ValueError("App Intelligence instructions are outside bounds")
    normalized_instructions = [
        _bounded_text(item, "App Intelligence instruction", 640)
        for item in instructions
    ]
    if len(set(normalized_instructions)) != len(normalized_instructions):
        raise ValueError("App Intelligence instructions must be unique")
    if not isinstance(intents, list) or len(intents) > 64:
        raise ValueError("App Intelligence intents are outside bounds")

    seen: set[str] = set()
    normalized_intents: list[dict[str, Any]] = []
    for intent in intents:
        if not isinstance(intent, dict):
            raise ValueError("App Intelligence intent must be an object")
        expected = {"id", "description", "effect", "confirmation", "parameters", "examples"}
        if set(intent) != expected:
            raise ValueError("App Intelligence intent fields are not canonical")
        intent_id = _bounded_text(intent.get("id"), "App Intelligence intent id", 160)
        if not intent_id.startswith(f"{app_id}.") or intent_id in seen:
            raise ValueError("App Intelligence intent identity is invalid")
        seen.add(intent_id)
        description = _bounded_text(intent.get("description"), "App Intelligence intent description", 640)
        effect = intent.get("effect")
        confirmation = intent.get("confirmation")
        if effect not in {"none", "read", "write", "external-write", "destructive"}:
            raise ValueError("App Intelligence intent effect is invalid")
        if confirmation not in {"none", "policy", "explicit"}:
            raise ValueError("App Intelligence confirmation is invalid")
        if effect in {"external-write", "destructive"} and confirmation == "none":
            raise ValueError("App Intelligence external/destructive intent requires confirmation")

        parameters = intent.get("parameters")
        examples = intent.get("examples")
        if not isinstance(parameters, list) or len(parameters) > 32:
            raise ValueError("App Intelligence parameters are outside bounds")
        if not isinstance(examples, list) or len(examples) > 8:
            raise ValueError("App Intelligence examples are outside bounds")
        normalized_parameters: list[dict[str, Any]] = []
        parameter_names: set[str] = set()
        for parameter in parameters:
            if not isinstance(parameter, dict):
                raise ValueError("App Intelligence parameter must be an object")
            if set(parameter) != {"name", "type", "required", "description"}:
                raise ValueError("App Intelligence parameter fields are not canonical")
            name = _bounded_text(parameter.get("name"), "App Intelligence parameter name", 64)
            if name in parameter_names:
                raise ValueError("App Intelligence parameter is duplicated")
            parameter_names.add(name)
            kind = parameter.get("type")
            if kind not in {"string", "number", "integer", "boolean", "string-list", "json"}:
                raise ValueError("App Intelligence parameter type is invalid")
            if not isinstance(parameter.get("required"), bool):
                raise ValueError("App Intelligence parameter required flag is invalid")
            normalized_parameters.append({
                "name": name,
                "type": kind,
                "required": parameter["required"],
                "description": _bounded_text(
                    parameter.get("description"),
                    "App Intelligence parameter description",
                    320,
                ),
            })
        normalized_examples = [
            _bounded_text(example, "App Intelligence example", 320)
            for example in examples
        ]
        if len(set(normalized_examples)) != len(normalized_examples):
            raise ValueError("App Intelligence examples must be unique")
        normalized_intents.append({
            "id": intent_id,
            "description": description,
            "effect": effect,
            "confirmation": confirmation,
            "parameters": normalized_parameters,
            "examples": normalized_examples,
        })

    return {
        "schema": MANIFEST_SCHEMA,
        "appId": app_id,
        "appVersion": app_version,
        "authority": "none",
        "execution": "declarative-only",
        "instructions": normalized_instructions,
        "intents": normalized_intents,
    }


def load_app_intelligence_registry(path: Path | None = None) -> dict[str, Any]:
    registry_path = path or _default_registry_path()
    if not registry_path.is_file():
        return {
            "schema": REGISTRY_SCHEMA,
            "available": False,
            "source": None,
            "apps": [],
            "by_id": {},
        }
    if registry_path.is_symlink():
        raise ValueError("App Intelligence registry must not be a symlink")
    raw = registry_path.read_bytes()
    if not raw or len(raw) > MAX_REGISTRY_BYTES:
        raise ValueError("App Intelligence registry size is outside bounds")
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as error:
        raise ValueError("App Intelligence registry is not valid UTF-8 JSON") from error
    if not isinstance(value, dict) or set(value) != {"schema", "authority", "source", "apps"}:
        raise ValueError("App Intelligence registry fields are not canonical")
    if value.get("schema") != REGISTRY_SCHEMA or value.get("authority") != "none":
        raise ValueError("App Intelligence registry authority/schema is invalid")

    source = value.get("source")
    if not isinstance(source, dict) or set(source) != {"repository", "commit"}:
        raise ValueError("App Intelligence registry source is invalid")
    normalized_source = {
        "repository": _bounded_text(source.get("repository"), "App Intelligence source repository", 240),
        "commit": _bounded_text(source.get("commit"), "App Intelligence source commit", 64),
    }

    apps = value.get("apps")
    if not isinstance(apps, list) or not 1 <= len(apps) <= MAX_APPS:
        raise ValueError("App Intelligence registry apps are outside bounds")
    normalized_apps: list[dict[str, Any]] = []
    by_id: dict[str, dict[str, Any]] = {}
    for app in apps:
        if not isinstance(app, dict) or set(app) != {"id", "title", "version", "manifest"}:
            raise ValueError("App Intelligence registry app is invalid")
        app_id = _bounded_text(app.get("id"), "App Intelligence app id", 128)
        if not _APP_ID_RE.fullmatch(app_id) or app_id in by_id:
            raise ValueError("App Intelligence app id is invalid or duplicated")
        title = _bounded_text(app.get("title"), "App Intelligence app title", 160)
        version = _bounded_text(app.get("version"), "App Intelligence app version", 80)
        manifest = _validate_manifest(app.get("manifest"), app_id=app_id, app_version=version)
        normalized = {
            "id": app_id,
            "title": title,
            "version": version,
            "manifest": manifest,
        }
        normalized_apps.append(normalized)
        by_id[app_id] = normalized
    return {
        "schema": REGISTRY_SCHEMA,
        "available": True,
        "source": normalized_source,
        "apps": normalized_apps,
        "by_id": by_id,
    }


class ApplicationIntelligenceActions:
    def _initialize_application_intelligence(self) -> None:
        self._app_intelligence_registry = load_app_intelligence_registry()

    def intelligence_app_catalog(self, _payload: dict[str, Any]) -> ActionResult:
        registry = self._app_intelligence_registry
        apps = [
            {
                "app_id": app["id"],
                "title": app["title"],
                "app_version": app["version"],
                "intent_ids": [intent["id"] for intent in app["manifest"]["intents"]],
            }
            for app in registry["apps"]
        ]
        return ActionResult(
            True,
            "App Intelligence catalog loaded" if registry["available"] else "App Intelligence catalog is not packaged",
            {
                "schema": "ordax.app-intelligence-catalog/1",
                "available": registry["available"],
                "source": registry["source"],
                "apps": apps,
                "authority": "none",
                "tool_execution": False,
            },
        )

    def intelligence_app_detail(self, payload: dict[str, Any]) -> ActionResult:
        app_id = str(payload.get("app_id") or "").strip()
        if not _APP_ID_RE.fullmatch(app_id):
            return ActionResult(
                False,
                "App Intelligence app id is invalid",
                {"error_code": "app_intelligence_app_id_invalid"},
            )
        app = self._app_intelligence_registry["by_id"].get(app_id)
        if app is None:
            return ActionResult(
                False,
                f"App Intelligence detail is unavailable: {app_id}",
                {"error_code": "app_intelligence_app_not_found", "app_id": app_id},
            )
        return ActionResult(
            True,
            f"App Intelligence detail loaded: {app_id}",
            {
                "schema": "ordax.app-intelligence-detail/1",
                "app_id": app["id"],
                "title": app["title"],
                "app_version": app["version"],
                "source": self._app_intelligence_registry["source"],
                "manifest": app["manifest"],
                "authority": "none",
                "tool_execution": False,
            },
        )
