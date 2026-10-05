from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class AgentJob:
    id: str
    action: str
    payload: dict[str, Any] = field(default_factory=dict)
    project_slug: str | None = None
    lease_token: str | None = None
    effect_id: str | None = None
    attempt_id: str | None = None
    execution_epoch: int | None = None
    agent_instance_id: str | None = None
    boot_id: str | None = None
    payload_sha256: str | None = None

    def action_payload(self) -> dict[str, Any]:
        payload = dict(self.payload)
        if self.project_slug:
            if payload.get("project") not in (None, self.project_slug):
                raise ValueError("job project_slug conflicts with payload.project")
            payload["project"] = self.project_slug
        return payload


@dataclass(slots=True)
class ActionResult:
    ok: bool
    summary: str
    data: dict[str, Any] = field(default_factory=dict)
