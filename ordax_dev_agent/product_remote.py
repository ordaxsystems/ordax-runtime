from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from .models import ActionResult
from .product_gateway import (
    ProductActionGateway,
    ProductAuditEvent,
    ProductAuditSink,
    ProductGrant,
    ProductRequestContext,
)


PRODUCT_REMOTE_CAPABILITY = "ordax.product.invoke"
PRODUCT_REMOTE_LEGACY_CAPABILITY = "ordax.product.read.invoke"


class ProductAuditTransport(Protocol):
    def record_product_audit(self, event: ProductAuditEvent) -> None: ...


class RemoteProductAuditSink(ProductAuditSink):
    def __init__(self, transport: ProductAuditTransport):
        self.transport = transport

    def record(self, event: ProductAuditEvent) -> None:
        self.transport.record_product_audit(event)


@dataclass(frozen=True)
class ProductInvocation:
    action: str
    arguments: dict[str, Any]
    context: ProductRequestContext
    grant: ProductGrant


def parse_product_invocation(payload: dict[str, Any]) -> ProductInvocation:
    allowed = {"action", "arguments", "context", "grant"}
    if set(payload) - allowed:
        raise ValueError("product invocation contains unsupported fields")

    action = payload.get("action")
    arguments = payload.get("arguments")
    context_raw = payload.get("context")
    grant_raw = payload.get("grant")
    if not isinstance(action, str) or not isinstance(arguments, dict):
        raise ValueError("product invocation action/arguments are invalid")
    if not isinstance(context_raw, dict) or not isinstance(grant_raw, dict):
        raise ValueError("product invocation context/grant are invalid")

    request_id = context_raw.get("request_id")
    subject_id = context_raw.get("subject_id")
    device_id = context_raw.get("device_id")
    space_id = context_raw.get("space_id")
    if not all(isinstance(item, str) for item in (request_id, subject_id, device_id)):
        raise ValueError("product invocation verified context is incomplete")
    if space_id is not None and not isinstance(space_id, str):
        raise ValueError("product invocation space is invalid")

    grant_id = grant_raw.get("grant_id")
    grant_subject = grant_raw.get("subject_id")
    actions = grant_raw.get("actions")
    projects = grant_raw.get("projects", [])
    grant_space = grant_raw.get("space_id")
    grant_device = grant_raw.get("device_id")
    expires = grant_raw.get("expires_at_unix")
    if not isinstance(grant_id, str) or not isinstance(grant_subject, str):
        raise ValueError("product invocation grant identity is invalid")
    if not isinstance(actions, list) or not all(isinstance(item, str) for item in actions):
        raise ValueError("product invocation grant actions are invalid")
    if not isinstance(projects, list) or not all(isinstance(item, str) for item in projects):
        raise ValueError("product invocation grant projects are invalid")
    if grant_space is not None and not isinstance(grant_space, str):
        raise ValueError("product invocation grant space is invalid")
    if not isinstance(grant_device, str) or not grant_device:
        raise ValueError("product invocation grant device is required")
    if expires is not None and type(expires) is not int:
        raise ValueError("product invocation grant expiry is invalid")

    return ProductInvocation(
        action=action,
        arguments=dict(arguments),
        context=ProductRequestContext(
            request_id=request_id,
            subject_id=subject_id,
            device_id=device_id,
            space_id=space_id,
        ),
        grant=ProductGrant(
            grant_id=grant_id,
            subject_id=grant_subject,
            actions=frozenset(actions),
            projects=frozenset(projects),
            space_id=grant_space,
            device_id=grant_device,
            expires_at_unix=expires,
        ),
    )


def execute_product_invocation(
    executor,
    transport: ProductAuditTransport,
    payload: dict[str, Any],
) -> ActionResult:
    invocation = parse_product_invocation(payload)
    gateway = ProductActionGateway(executor, RemoteProductAuditSink(transport))
    return gateway.execute(
        invocation.action,
        invocation.arguments,
        context=invocation.context,
        grant=invocation.grant,
    )
