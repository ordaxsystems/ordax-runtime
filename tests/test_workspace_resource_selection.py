"""Disposable tests for the host-only picker seam; no real Owner/Device grants."""
from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
import uuid
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from ordax_dev_agent.project_workspace_port import (
    ProjectWorkspaceDenied, WorkspaceActor,
)
from ordax_dev_agent.workspace_resource_selection import (
    LiveWorkspaceAuthority, WorkspaceResourceSelection,
)


def _id() -> str:
    return str(uuid.uuid4())


class WorkspaceResourceSelectionTests(unittest.TestCase):
    REPOSITORY = "ordaxsystems/ordax-picker-fixture"

    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name)
        self.root = self.base / "registered"
        self.root.mkdir()
        self.root.joinpath("index.html").write_text("fixture only", encoding="utf-8")
        for command in (
            ("init", "-q"),
            ("remote", "add", "origin", "git@github.com:ordaxsystems/ordax-picker-fixture.git"),
            ("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
             "add", "index.html"),
            ("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
             "commit", "-q", "-m", "fixture"),
        ):
            subprocess.run(["git", "-C", str(self.root), *command], check=True,
                           capture_output=True, timeout=10)
        self.config = SimpleNamespace(projects={
            "local": {"path": str(self.root), "apps": [], "allowed_branches": []},
        })
        self.owner = _id()
        self.client = _id()
        self.space = _id()
        self.project = _id()
        self.grant = _id()
        self.device = _id()
        self.actor = WorkspaceActor(_id(), self.client, "workspace_a", "lab_a")
        self.clock = [100.0]
        self.selected = []
        self.confirmed = []
        self.allowed = False
        self.revoked = False
        self.calls = 0
        self.picker = self.make_picker()

    def live(self, actor, device, resource):
        self.calls += 1
        if not self.allowed or self.revoked:
            return None
        return LiveWorkspaceAuthority(
            owner_user_id=self.owner, space_id=self.space,
            canonical_project_id=self.project, lab_project_id=self.actor.project_id,
            actor_id=self.actor.actor_id, client_id=self.client,
            workspace_session_id=self.actor.workspace_session_id,
            device_id=self.device, resource_id=resource, grant_id=self.grant,
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
            allowed_actions=frozenset({"read"}),
        )

    def make_picker(self, *, choose=None, confirm=None, lookup=None):
        def select(project):
            self.selected.append(project.root)
            return project.root

        def approve(project, path):
            self.confirmed.append((project.root, path))
            return True

        return WorkspaceResourceSelection(
            config=self.config, device_id=self.device,
            choose_directory=choose if choose is not None else select,
            confirm_owner=confirm if confirm is not None else approve,
            live_authority=lookup if lookup is not None else self.live,
            clock=lambda: self.clock[0],
        )

    def choose(self, picker=None):
        return (picker or self.picker).choose_for_owner(self.REPOSITORY)

    def test_selection_is_not_authorization_and_exposes_no_local_path(self):
        resource = self.choose()
        self.assertTrue(resource.startswith("resource_"))
        self.assertNotIn(str(self.root), resource)
        self.assertEqual(self.selected, [self.root])
        self.assertEqual(self.confirmed, [(self.root, self.root)])
        port = self.picker.as_port(local_policy=lambda _root: True)
        with self.assertRaisesRegex(ProjectWorkspaceDenied, "workspace_grant_missing"):
            port.verify_binding(self.actor, self.device, resource)
        self.allowed = True
        proof = port.verify_binding(self.actor, self.device, resource)
        self.assertEqual(proof["resourceId"], resource)
        result = port.snapshot(self.actor, self.device, resource)
        self.assertTrue(result["git"]["isRepository"])
        self.assertEqual(result["fileCount"], 1)
        self.assertEqual(result["changedCount"], 0)
        self.assertNotIn(str(self.root), str(result))
        self.assertNotIn("fixture only", str(result))
        self.assertGreaterEqual(self.calls, 4)

    def test_cancel_reject_or_escape_never_create_resource(self):
        outside = self.base / "outside"
        outside.mkdir()
        for picker, code in (
            (self.make_picker(choose=lambda _project: None), "workspace_selection_cancelled"),
            (self.make_picker(choose=lambda _project: outside),
             "workspace_selection_outside_registered_project"),
            (self.make_picker(choose=lambda _project: str(self.root)),
             "workspace_selection_outside_registered_project"),
            (self.make_picker(confirm=lambda _project, _root: False),
             "workspace_selection_not_confirmed"),
        ):
            with self.assertRaisesRegex(ProjectWorkspaceDenied, code):
                self.choose(picker)
            self.assertEqual(picker._selections, {})

    def test_unknown_or_ambiguous_local_project_is_rejected(self):
        with self.assertRaisesRegex(ProjectWorkspaceDenied, "workspace_registered_project_unavailable"):
            self.picker.choose_for_owner("ordaxsystems/nonexistent")
        self.config.projects["copy"] = dict(self.config.projects["local"])
        with self.assertRaisesRegex(ProjectWorkspaceDenied, "workspace_registered_project_unavailable"):
            self.choose()
        self.assertEqual(self.picker._selections, {})

    def test_live_scope_requires_exact_actor_client_device_and_resource(self):
        resource = self.choose()
        self.allowed = True
        port = self.picker.as_port(local_policy=lambda _root: True)
        for wrong_actor in (
            replace(self.actor, actor_id=_id()),
            replace(self.actor, client_id=_id()),
            replace(self.actor, workspace_session_id="other_session"),
            replace(self.actor, project_id="other_project"),
        ):
            with self.assertRaises(ProjectWorkspaceDenied):
                port.verify_binding(wrong_actor, self.device, resource)
        for device, token in ((str(uuid.uuid4()), resource),
                              (self.device, "resource_" + "a" * 24)):
            with self.assertRaises(ProjectWorkspaceDenied):
                port.verify_binding(self.actor, device, token)

    def test_forged_or_malformed_authority_cannot_enable_port(self):
        resource = self.choose()
        self.allowed = True
        valid = self.live(self.actor, self.device, resource)
        invalid = (
            {"grant_id": self.grant, "root": str(self.root)},
            None,
            replace(valid, owner_user_id="owner_fake"),
            replace(valid, client_id=_id()),
            replace(valid, grant_id="grant_fake"),
            replace(valid, space_id="bad_space"),
            replace(valid, canonical_project_id="not_a_uuid"),
            replace(valid, allowed_actions=frozenset({"read", "write"})),
            replace(valid, expires_at=datetime.now(timezone.utc) - timedelta(seconds=1)),
            replace(valid, expires_at=datetime.now(timezone.utc) + timedelta(days=2)),
            replace(valid, expires_at=datetime.now(timezone.utc).replace(tzinfo=None)),
        )
        for authority in invalid:
            picker = self.make_picker(lookup=lambda _actor, _device, rid, value=authority:
                replace(value, resource_id=rid) if isinstance(value, LiveWorkspaceAuthority) else value)
            other = self.choose(picker)
            port = picker.as_port(local_policy=lambda _root: True)
            with self.assertRaisesRegex(ProjectWorkspaceDenied, "workspace_grant_missing"):
                port.verify_binding(self.actor, self.device, other)

    def test_selection_expiry_and_revocation_midscan_are_fail_closed(self):
        resource = self.choose()
        self.clock[0] += 301
        self.allowed = True
        port = self.picker.as_port(local_policy=lambda _root: True)
        with self.assertRaisesRegex(ProjectWorkspaceDenied, "workspace_grant_missing"):
            port.verify_binding(self.actor, self.device, resource)
        resource = self.choose()
        self.clock[0] += 10
        port.verify_binding(self.actor, self.device, resource)
        self.revoked = True
        with self.assertRaisesRegex(ProjectWorkspaceDenied, "workspace_grant_missing"):
            port.snapshot(self.actor, self.device, resource)
        self.revoked = False
        with self.assertRaisesRegex(ProjectWorkspaceDenied, "workspace_local_policy_denied"):
            self.picker.as_port(local_policy=lambda _root: False).snapshot(
                self.actor, self.device, resource)
        self.picker.discard_local_selection(resource)
        with self.assertRaisesRegex(ProjectWorkspaceDenied, "workspace_grant_missing"):
            port.verify_binding(self.actor, self.device, resource)

    def test_revocation_between_first_and_last_scan_proof_fails(self):
        resource = self.choose()
        self.allowed = True
        before = self.calls

        def transient(*args):
            proof = self.live(*args)
            return proof if self.calls == before + 1 else None

        self.picker._live = transient
        with self.assertRaisesRegex(ProjectWorkspaceDenied, "workspace_grant_missing"):
            self.picker.as_port(local_policy=lambda _root: True).snapshot(
                self.actor, self.device, resource)
        self.assertEqual(self.calls, before + 2)

    def test_root_swap_and_registry_removal_revoke_local_selection(self):
        resource = self.choose()
        self.allowed = True
        port = self.picker.as_port(local_policy=lambda _root: True)
        port.verify_binding(self.actor, self.device, resource)
        self.config.projects.clear()
        with self.assertRaisesRegex(ProjectWorkspaceDenied, "workspace_grant_missing"):
            port.verify_binding(self.actor, self.device, resource)
        self.config.projects["local"] = {"path": str(self.root), "apps": [], "allowed_branches": []}
        moved = self.base / "old"
        self.root.rename(moved)
        self.root.mkdir()
        with self.assertRaisesRegex(ProjectWorkspaceDenied, "workspace_grant_missing"):
            port.snapshot(self.actor, self.device, resource)

    def test_ambient_git_config_cannot_spoof_project_origin(self):
        fake = self.base / "other"
        fake.mkdir()
        inherited = {
            "GIT_DIR": str(fake),
            "GIT_WORK_TREE": str(fake),
            "GIT_INDEX_FILE": str(fake / "index"),
            "GIT_CONFIG_COUNT": "1",
            "GIT_CONFIG_KEY_0": "remote.origin.url",
            "GIT_CONFIG_VALUE_0": "git@github.com:attacker/wrong.git",
        }
        with patch.dict(os.environ, inherited):
            resource = self.choose()
            self.allowed = True
            proof = self.picker.as_port(local_policy=lambda _root: True).verify_binding(
                self.actor, self.device, resource)
            self.assertEqual(proof["resourceId"], resource)

    def test_limits_and_native_host_provenance_are_mandatory(self):
        with self.assertRaises(ValueError):
            WorkspaceResourceSelection(config=self.config, device_id=self.device,
                choose_directory=None, confirm_owner=lambda *_: True,live_authority=self.live)
        with self.assertRaises(ValueError):
            WorkspaceResourceSelection(config=self.config, device_id="device",
                choose_directory=lambda *_: self.root,
                confirm_owner=lambda *_: True,live_authority=self.live)
        with patch("ordax_dev_agent.workspace_resource_selection._MAX_SELECTIONS", 1):
            self.choose()
            with self.assertRaisesRegex(ProjectWorkspaceDenied, "workspace_selection_limit"):
                self.choose()


if __name__ == "__main__":
    unittest.main()
