"""Read-only component/update actions for the Device Agent."""
from __future__ import annotations

from typing import Any

from .component_updates import component_catalog, plan_component_update
from .models import ActionResult


class ComponentActions:
    def agent_component_catalog(self, payload: dict[str, Any]) -> ActionResult:
        if payload:
            return ActionResult(False, "component catalog does not accept arguments")
        return ActionResult(True, "component catalog ready", component_catalog())

    def agent_component_update_plan(self, payload: dict[str, Any]) -> ActionResult:
        unsupported = sorted(set(payload) - {"changed_paths", "install_contract_changed"})
        if unsupported:
            return ActionResult(False, "unsupported field(s): " + ", ".join(unsupported))

        changed_paths = payload.get("changed_paths", [])
        if not isinstance(changed_paths, list) or not all(
            isinstance(path, str) for path in changed_paths
        ):
            return ActionResult(False, "changed_paths must be a list of strings")

        try:
            plan = plan_component_update(
                changed_paths,
                install_contract_changed=payload.get("install_contract_changed") is True,
            )
        except ValueError as error:
            return ActionResult(False, str(error))

        return ActionResult(True, "component update plan ready", plan)
