from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .product_gateway import PRODUCT_ACTIONS, ProductActionSpec


TRUSTED_STUDIO_SOURCE = "embedded-studio"


@dataclass(frozen=True, slots=True)
class LocalStudioInvocationContext:
    """Trusted host provenance supplied by the Windows Studio host.

    Values in this context are created by the host process, not accepted from
    portable UI payloads or provider connectors.
    """

    source: str
    accepted_device_ids: frozenset[str]
    available_local_actions: frozenset[str]
    known_projects: frozenset[str]


@dataclass(frozen=True, slots=True)
class NormalizedStudioActionRequest:
    """Contract-neutral normalized request used after schema validation.

    The public JS contract remains authoritative for wire/schema validation.
    This Python value deliberately models only fields needed for local
    authorization, so the Runtime does not fork the public schema definition.
    """

    capability: str
    device_id: str
    actor_kind: str
    actor_subject_id: str | None
    project_id: str | None
    space_id: str | None
    parameters: dict[str, Any]


@dataclass(frozen=True, slots=True)
class LocalStudioAuthorization:
    allowed: bool
    code: str
    spec: ProductActionSpec | None = None


def authorize_local_studio_action(
    request: NormalizedStudioActionRequest,
    context: LocalStudioInvocationContext,
) -> LocalStudioAuthorization:
    """Authorize a local Studio action without executing it.

    This boundary is intentionally distinct from remote Product OAuth/grants.
    It never mints or fabricates a ProductGrant. The eventual dispatcher must
    still execute through the canonical typed action path and the concrete
    action implementation remains responsible for local Computer policy.
    """

    if context.source != TRUSTED_STUDIO_SOURCE:
        return LocalStudioAuthorization(False, "untrusted_source")

    if request.actor_kind != "device-owner" or request.actor_subject_id is not None:
        return LocalStudioAuthorization(False, "actor_not_local_device_owner")

    if not request.device_id or request.device_id not in context.accepted_device_ids:
        return LocalStudioAuthorization(False, "device_mismatch")

    spec = PRODUCT_ACTIONS.get(request.capability)
    if spec is None:
        return LocalStudioAuthorization(False, "capability_not_typed")
    if spec.local_action not in context.available_local_actions:
        return LocalStudioAuthorization(False, "capability_unavailable")

    if request.space_id is not None:
        return LocalStudioAuthorization(False, "device_owner_space_scope_forbidden")

    if spec.project_required:
        if not request.project_id or request.project_id not in context.known_projects:
            return LocalStudioAuthorization(False, "project_required")
    elif request.project_id is not None and request.project_id not in context.known_projects:
        return LocalStudioAuthorization(False, "project_unknown")

    allowed_parameter_fields = set(spec.allowed_fields)
    allowed_parameter_fields.discard("project")
    unexpected = set(request.parameters) - allowed_parameter_fields
    if unexpected:
        return LocalStudioAuthorization(False, "unsupported_parameters")

    return LocalStudioAuthorization(True, "allowed", spec)
