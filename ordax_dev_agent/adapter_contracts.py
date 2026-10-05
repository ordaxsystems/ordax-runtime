"""Typed adapter contracts shared by action dispatch and agent status."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


ADAPTER_CONTRACT_SCHEMA = "ordax.device-adapter-contracts/1"


@dataclass(frozen=True)
class AdapterContract:
    name: str
    action_prefix: str
    project_app: str
    source: str
    component_id: str | None = None
    global_actions: frozenset[str] = frozenset()

    def public(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "action_prefix": self.action_prefix,
            "project_app": self.project_app,
            "source": self.source,
            "component_id": self.component_id,
            "project_scoped": True,
            "global_actions": sorted(self.global_actions),
        }


def builtin_adapter_contracts() -> dict[str, AdapterContract]:
    """Return built-in app adapters without importing their implementations."""
    return {
        "blender": AdapterContract(
            name="blender",
            action_prefix="blender.",
            project_app="blender",
            source="builtin",
            component_id="adapter-blender",
            # Version discovery is intentionally machine-global and has always
            # been callable without a registered project.
            global_actions=frozenset({"blender.version", "blender.instances", "blender.adoption_install"}),
        ),
        "unity": AdapterContract(
            name="unity",
            action_prefix="unity.",
            project_app="unity",
            source="builtin",
            component_id="adapter-unity",
        ),
    }


def external_adapter_contract(name: str) -> AdapterContract:
    """Create the default project-scoped contract for an entry-point adapter."""
    return AdapterContract(
        name=name,
        action_prefix=f"{name}.",
        project_app=name,
        source="entry-point",
    )


def adapter_contract_catalog(
    contracts: dict[str, AdapterContract] | None = None,
) -> dict[str, Any]:
    selected = contracts if contracts is not None else builtin_adapter_contracts()
    return {
        "schema": ADAPTER_CONTRACT_SCHEMA,
        "adapters": [
            selected[name].public()
            for name in sorted(selected)
        ],
    }
