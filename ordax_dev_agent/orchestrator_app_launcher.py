"""Candidate managed Lab launch, not a registry writer or Product MCP action.

Only the trusted installation/authorization owners may call this boundary.
Never deserialize this receipt from model input or trust App self-attestation
as the source of its own hashes. No registration or grant is created here.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import ntpath
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import time
from typing import Callable

from .orchestrator_app_bridge import (
    ExpectedOrchestratorIdentity, OrchestratorAppReadClient, OrchestratorBridgeUnavailable,
    VerifiedOrchestratorReadAuthorization, require_orchestrator_read_authorization,
)
from .orchestrator_windows_pipe import WindowsApi, WindowsOrchestratorPipe

EXE = "OrdaX Orchestrator Lab.exe"
_REQUIRED = frozenset({EXE, "resources/app.asar", "resources/runtime-build.json"})
_HASH = re.compile(r"[a-f0-9]{64}\Z")


@dataclass(frozen=True, slots=True)
class VerifiedOrchestratorInstallation:
    """Trusted installation-owner result, not an App-authored manifest/token.

    Hashes cover EVERY installed file and come from verified publisher/build
    provenance. This module cannot mint or persist that provenance. An unsigned
    CI fixture is allowed only inside a disposable test, never a live registry.
    """
    root: Path
    file_hashes: tuple[tuple[str, str], ...]
    expected: ExpectedOrchestratorIdentity


def validate_orchestrator_installation(receipt):
    if not isinstance(receipt, VerifiedOrchestratorInstallation) or not isinstance(receipt.root, Path) or not receipt.root.is_absolute() or str(receipt.root).startswith("\\\\") or not isinstance(receipt.expected, ExpectedOrchestratorIdentity) or type(receipt.expected.source_commit) is not str or not re.fullmatch(r"[a-f0-9]{40}", receipt.expected.source_commit) or not all(type(v) is str and _HASH.fullmatch(v) for v in (receipt.expected.content_digest, receipt.expected.profile_fingerprint)) or type(receipt.file_hashes) is not tuple or not 3 <= len(receipt.file_hashes) <= 4096:
        raise OrchestratorBridgeUnavailable("host_verified_installation_required")
    names, folded = set(), set()
    for item in receipt.file_hashes:
        if type(item) is not tuple or len(item) != 2 or not all(type(v) is str for v in item):
            raise OrchestratorBridgeUnavailable("host_verified_installation_required")
        name, digest = item
        path = PurePosixPath(name)
        if not name or path.is_absolute() or path.as_posix() != name or ".." in path.parts or any(any(ord(c) < 32 or c in '<>:"\\|?*' for c in part) or part.endswith((".", " ")) for part in path.parts) or not _HASH.fullmatch(digest) or name.casefold() in folded:
            raise OrchestratorBridgeUnavailable("host_verified_installation_required")
        names.add(name)
        folded.add(name.casefold())
    if not _REQUIRED.issubset(names):
        raise OrchestratorBridgeUnavailable("host_verified_installation_required")
    return receipt


class _PinnedInstallation:
    def __init__(self, receipt, api):
        self.api, self.handles = api, []
        try:
            # Pin ancestors against replacement/junction swaps as well as files.
            directories = set(receipt.root.parents) | {receipt.root}
            for name, _ in receipt.file_hashes:
                directories.update((receipt.root / name).parents)
            for directory in sorted(directories, key=lambda p: (len(p.parts), str(p))):
                self.handles.append(api.pin_file(directory, directory=True))
            actual, total = set(), 0
            for member in receipt.root.rglob("*"):
                info = member.lstat()
                if member.is_symlink() or getattr(info, "st_file_attributes", 0) & 0x400:
                    raise OrchestratorBridgeUnavailable("host_installation_unsafe")
                if member.is_file():
                    actual.add(member.relative_to(receipt.root).as_posix())
                    total += info.st_size
            if actual != {name for name, _ in receipt.file_hashes} or total > 2 * 1024 ** 3:
                raise OrchestratorBridgeUnavailable("host_installation_inventory_mismatch")
            for name, expected_hash in receipt.file_hashes:
                handle = api.pin_file(receipt.root / name)
                self.handles.append(handle)
                if api.hash_handle(handle) != expected_hash:
                    raise OrchestratorBridgeUnavailable("host_installation_digest_mismatch")
        except Exception:
            self.close()
            raise

    def close(self):
        while self.handles:
            self.api.kernel.CloseHandle(self.handles.pop())


class ManagedOrchestratorSession:
    def __init__(self, child, pipe, pins, client):
        self._child, self._pipe, self._pins, self._client = child, pipe, pins, client
        self._closed = False

    @property
    def pid(self):
        return self._child.pid

    def read(self, operation, arguments=None):
        if self._closed:
            raise OrchestratorBridgeUnavailable("host_session_closed")
        try:
            if self._child.poll() is not None:
                raise OrchestratorBridgeUnavailable("host_app_exited")
            return self._client.read(operation, arguments)
        except Exception:
            self.close()
            raise

    def close(self):
        if not self._closed:
            self._closed = True
            self._client.close()
            self._pipe.close()
            self._pins.close()
            # Closing remote read access must not kill the human App.


def launch_verified_orchestrator(*, installation: VerifiedOrchestratorInstallation,
        authorize: Callable[[], VerifiedOrchestratorReadAuthorization | None],
        clock=lambda: int(time.time() * 1000)) -> ManagedOrchestratorSession:
    authority = require_orchestrator_read_authorization(authorize, clock)
    receipt = validate_orchestrator_installation(installation)
    if os.name != "nt":
        raise OrchestratorBridgeUnavailable("host_windows_required")
    local_app_data = os.environ.get("LOCALAPPDATA", "")
    if not ntpath.isabs(local_app_data):
        raise OrchestratorBridgeUnavailable("host_profile_binding_required")
    profile = ntpath.normpath(ntpath.join(local_app_data, "OrdaX", "OrchestratorLab")).lower()
    if hashlib.sha256(profile.encode("utf-8")).hexdigest() != receipt.expected.profile_fingerprint:
        raise OrchestratorBridgeUnavailable("host_profile_binding_required")
    pins, pipe = None, None
    try:
        pins = _PinnedInstallation(receipt, WindowsApi())
        current = require_orchestrator_read_authorization(authorize, clock)
        if (current.subject_id, current.client_id, current.device_id, current.grant_id) != (authority.subject_id, authority.client_id, authority.device_id, authority.grant_id):
            raise OrchestratorBridgeUnavailable("host_authority_changed")
        pipe = WindowsOrchestratorPipe()
        environment = dict(os.environ)
        for name in ("NODE_OPTIONS", "NODE_PATH", "ELECTRON_RUN_AS_NODE", "ELECTRON_ENABLE_LOGGING", "ORDAX_DESKTOP_DEBUG", "GROQ_API_KEY"):
            environment.pop(name, None)
        environment.update(ORDAX_LAB_RUNTIME_BRIDGE_ENABLE="1", ORDAX_LAB_SUPERVISOR_ENABLE="0")
        child = subprocess.Popen([str(receipt.root / EXE), "--ordax-runtime-bridge-pipe=" + pipe.name],
            cwd=receipt.root, env=environment, shell=False, close_fds=True,
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        pipe.bind_child(child)
        def same_caller():
            value = require_orchestrator_read_authorization(authorize, clock)
            if (value.subject_id, value.client_id, value.device_id, value.grant_id) != (authority.subject_id, authority.client_id, authority.device_id, authority.grant_id):
                raise OrchestratorBridgeUnavailable("host_authority_changed")
            return value
        client = OrchestratorAppReadClient(exchange=pipe, authorize=same_caller, expected=receipt.expected, clock=clock)
        return ManagedOrchestratorSession(child, pipe, pins, client)
    except Exception:
        if pipe is not None:
            pipe.close()
        if pins is not None:
            pins.close()
        raise OrchestratorBridgeUnavailable("host_managed_launch_unavailable") from None
