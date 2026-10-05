from __future__ import annotations

from typing import Protocol

from .local_device_identity import WindowsRuntimeDeviceIdentity
from .studio_local_authorizer import (
    LocalStudioInvocationContext,
    TRUSTED_STUDIO_SOURCE,
)


class LocalStudioRegistryView(Protocol):
    @property
    def names(self) -> list[str]: ...

    @property
    def projects(self): ...


def build_local_studio_invocation_context(
    registry: LocalStudioRegistryView,
    identity: WindowsRuntimeDeviceIdentity,
) -> LocalStudioInvocationContext:
    """Build trusted local Studio authorization context from Runtime-owned state.

    Portable UI does not supply the source, accepted target ids, available
    actions, or known projects. Those values are derived from the host Runtime
    and its persistent local identity immediately before authorization.
    """

    return LocalStudioInvocationContext(
        source=TRUSTED_STUDIO_SOURCE,
        accepted_device_ids=identity.local_studio_target_ids,
        available_local_actions=frozenset(str(name) for name in registry.names),
        known_projects=frozenset(str(slug) for slug in registry.projects),
    )
