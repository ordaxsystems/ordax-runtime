"""Read-only capability contract inventory exposed by agent.status."""
from __future__ import annotations

from typing import Any

from .assets.blender_modeling_contracts import modeling_schemas


def capability_contracts() -> dict[str, Any]:
    schemas = modeling_schemas()
    modeling = {}
    for name, schema in schemas.items():
        modeling[name] = {
            "status": schema.get("status"),
            "action": schema.get("action"),
            "requires_real_blender_smoke": schema.get("status") != "available",
            "runtime_requirements": list(schema.get("runtime_requirements", [])),
            "runtime_guards": dict(schema.get("runtime_guards", {})),
            "failure_policy": list(schema.get("failure_policy", [])),
            "workflow_actions": dict(schema.get("workflow_actions", {})),
        }

    return {
        "artifact_transfer": {
            "action": "artifact.read_chunk",
            "max_chunk_bytes": 32768,
            "encoding": "base64",
            "integrity": "chunk_sha256",
            "resume_requires": ["source_version", "offset"],
            "roots": ["project/Artifacts", "agent-artifacts/project"],
            "source_version_is_content_hash": False,
        },
        "blender_modeling": {
            "operations": modeling,
            "pending_variants": {
                name: sorted(
                    variant
                    for variant, variant_schema in (schema.get("type_overrides") or {}).items()
                    if variant_schema.get("status") != "available"
                )
                for name, schema in schemas.items()
                if any(
                    variant_schema.get("status") != "available"
                    for variant_schema in (schema.get("type_overrides") or {}).values()
                )
            },
            "available_actions": sorted({
                action
                for item in modeling.values()
                if item.get("status") == "available"
                for action in ([item.get("action")] + list((item.get("workflow_actions") or {}).values()))
                if action
            }),
            "pending_operations": sorted(
                name
                for name, item in modeling.items()
                if item.get("status") != "available"
            ),
        }
    }
