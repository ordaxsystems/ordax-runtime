"""First-party-informed Unity capability registry for OrdaX.

This module does not require Codex, Claude, Grok, or Unity's agent plugin at
runtime. It mirrors only capability metadata and project-detection rules
derived from Unity's public documentation/repository; implementation/execution
remains native to the OrdaX Dev Agent.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


UNITY_AGENT_PLUGIN_REPO = "https://github.com/Unity-Technologies/unity-agent-plugin"
UNITY_AGENT_PLUGIN_DOCS = "https://docs.unity.com/en-us/ai/unity-plugin/about-unity-plugin"

# Metadata only: no Unity skill bodies are copied into OrdaX.
OFFICIAL_SKILLS: tuple[dict[str, Any], ...] = (
    {"id": "physics-3d-collision", "category": "physics", "signals": []},
    {"id": "initialize-ai-navigation", "category": "navigation", "signals": ["com.unity.ai.navigation"]},
    {"id": "ui-uitk", "category": "ui", "signals": []},
    {"id": "ui-ugui", "category": "ui", "signals": ["com.unity.ugui"]},
    {"id": "ui-imgui", "category": "ui", "signals": []},
    {"id": "localization", "category": "localization", "signals": ["com.unity.localization"]},
    {"id": "2d-pixel-perfect", "category": "2d", "signals": ["com.unity.2d.pixel-perfect"]},
    {"id": "sprite-editor", "category": "2d", "signals": ["com.unity.2d.sprite"]},
    {"id": "manage-sprite-atlas", "category": "2d", "signals": ["com.unity.2d.sprite"]},
    {"id": "tilemap-palette-create", "category": "2d", "signals": ["com.unity.2d.tilemap"]},
    {"id": "tilemap-ruletile-createempty", "category": "2d", "signals": ["com.unity.2d.tilemap.extras"]},
    {"id": "tilemap-ruletile-createfromsegment", "category": "2d", "signals": ["com.unity.2d.tilemap.extras"]},
    {"id": "audio-setup-mixers", "category": "audio", "signals": []},
    {"id": "optimize-audio", "category": "audio", "signals": []},
    {"id": "optimize-text-mesh-pro", "category": "text", "signals": ["com.unity.ugui"]},
    {"id": "migrate-birp-to-urp", "category": "rendering", "signals": []},
    {"id": "urp-postprocessing", "category": "rendering", "signals": ["com.unity.render-pipelines.universal"]},
    {"id": "validate-urp-render-graph-renderer-feature", "category": "rendering", "signals": ["com.unity.render-pipelines.universal"]},
    {"id": "shader-graph-create-custom-node", "category": "rendering", "signals": ["com.unity.shadergraph"]},
    {"id": "setup-multiplayer-services", "category": "multiplayer", "signals": ["com.unity.services.multiplayer"]},
    {"id": "setup-vivox-voice-chat", "category": "multiplayer", "signals": ["com.unity.services.vivox"]},
    {"id": "build-live-game", "category": "services", "signals": ["com.unity.services.authentication"]},
    {"id": "implement-in-app-purchases", "category": "monetization", "signals": ["com.unity.purchasing"]},
    {"id": "levelplay-unity-integration", "category": "monetization", "signals": ["com.unity.services.levelplay"]},
    {"id": "unity-package-management", "category": "tooling", "signals": []},
    {"id": "unity-cli", "category": "tooling", "signals": []},
    {"id": "generate-editor-search-query", "category": "editor", "signals": []},
    {"id": "new-unity-project", "category": "project", "signals": []},
    {"id": "optimize-web", "category": "platform", "signals": []},
)


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _unity_version(project_root: Path) -> str | None:
    path = project_root / "ProjectSettings" / "ProjectVersion.txt"
    if not path.is_file():
        return None
    text = path.read_text(encoding="utf-8-sig", errors="replace")
    match = re.search(r"m_EditorVersion:\s*([^\r\n]+)", text)
    return match.group(1).strip() if match else None


def _major_version(version: str | None) -> int | None:
    if not version:
        return None
    match = re.match(r"(\d+)", version)
    return int(match.group(1)) if match else None


def project_profile(project_root: Path) -> dict[str, Any]:
    root = project_root.resolve()
    manifest = _read_json(root / "Packages" / "manifest.json")
    dependencies = manifest.get("dependencies")
    if not isinstance(dependencies, dict):
        dependencies = {}

    lock = _read_json(root / "Packages" / "packages-lock.json")
    lock_deps = lock.get("dependencies")
    if not isinstance(lock_deps, dict):
        lock_deps = {}

    packages = {
        str(name): str(version)
        for name, version in dependencies.items()
        if isinstance(name, str)
    }

    version = _unity_version(root)
    major = _major_version(version)

    if "com.unity.render-pipelines.universal" in packages:
        render_pipeline = "URP"
    elif "com.unity.render-pipelines.high-definition" in packages:
        render_pipeline = "HDRP"
    else:
        render_pipeline = "Built-in/unknown"

    scenes: list[str] = []
    assets = root / "Assets"
    if assets.is_dir():
        try:
            scenes = sorted(
                str(path.relative_to(root)).replace("\\", "/")
                for path in assets.rglob("*.unity")
            )[:250]
        except OSError:
            scenes = []

    return {
        "unity_version": version,
        "unity_major": major,
        "unity_6_or_newer": bool(major is not None and major >= 6000),
        "render_pipeline": render_pipeline,
        "packages": packages,
        "package_lock_entries": len(lock_deps),
        "scene_count": len(scenes),
        "scenes": scenes,
        "has_assets": assets.is_dir(),
        "has_project_settings": (root / "ProjectSettings").is_dir(),
    }


def capability_report(project_root: Path) -> dict[str, Any]:
    profile = project_profile(project_root)
    packages: dict[str, str] = profile["packages"]

    skills: list[dict[str, Any]] = []
    categories: dict[str, dict[str, Any]] = {}

    for entry in OFFICIAL_SKILLS:
        signals = list(entry["signals"])
        installed = [signal for signal in signals if signal in packages]
        # Empty signal list means the workflow is editor/core knowledge and can
        # be considered generally applicable to Unity 6+.
        applicable = bool(not signals or installed)
        item = {
            "id": entry["id"],
            "category": entry["category"],
            "applicable": applicable,
            "package_signals": signals,
            "installed_signals": installed,
            "source": f"{UNITY_AGENT_PLUGIN_REPO}/tree/main/skills/{entry['id']}",
        }
        skills.append(item)

        category = categories.setdefault(
            entry["category"],
            {"applicable_skills": [], "detected_packages": []},
        )
        if applicable:
            category["applicable_skills"].append(entry["id"])
        for signal in installed:
            if signal not in category["detected_packages"]:
                category["detected_packages"].append(signal)

    return {
        "profile": profile,
        "unity_plugin_runtime_dependency": False,
        "codex_dependency": False,
        "official_source_repo": UNITY_AGENT_PLUGIN_REPO,
        "official_docs": UNITY_AGENT_PLUGIN_DOCS,
        "skills": skills,
        "categories": categories,
        "applicable_skill_ids": [item["id"] for item in skills if item["applicable"]],
    }


def skill_catalog() -> dict[str, Any]:
    return {
        "source_repo": UNITY_AGENT_PLUGIN_REPO,
        "docs": UNITY_AGENT_PLUGIN_DOCS,
        "runtime_dependency": False,
        "license_note": (
            "OrdaX stores capability metadata/source references only; "
            "Unity skill bodies are not vendored by this module."
        ),
        "skills": [
            {
                "id": entry["id"],
                "category": entry["category"],
                "package_signals": list(entry["signals"]),
                "source": f"{UNITY_AGENT_PLUGIN_REPO}/tree/main/skills/{entry['id']}",
            }
            for entry in OFFICIAL_SKILLS
        ],
    }
