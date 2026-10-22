"""Cross-boundary Device/Product workspace proof: disposable Git, no live grants.

Exercises real Runtime port + in-memory Owner selection + existing Product
HTTP client against a simulated PostgreSQL verifier, including postscan
revocation. No real notebook, OAuth token, cloud writes, or MCP action.
"""
from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import httpx

from ordax_dev_agent.product_remote_client import ProductRemoteClient
from ordax_dev_agent.project_workspace_device_live import DeviceWorkspaceLiveAuthority
from ordax_dev_agent.project_workspace_port import ProjectWorkspaceDenied, WorkspaceActor
from ordax_dev_agent.workspace_resource_selection import (
    OwnerCanonicalProjectScope, WorkspaceResourceSelection,
)


def uid():
    return str(uuid.uuid4())


class WorkspaceDeviceLiveAuthorityTests(unittest.TestCase):
    REPOSITORY = "ordaxsystems/ordax-live-proof-fixture"

    def setUp(self):
        self.owner, self.client, self.device, self.space, self.project = (
            uid() for _ in range(5)
        )
        self.actor = WorkspaceActor(uid(), self.client, "session_one", "lab_one")
        self.grant = uid()
        self.device_secret = "local-enrolled-device-token-" + uuid.uuid4().hex
        self.current = datetime.now(timezone.utc)
        self.enabled = True
        self.revoke_at = None
        self.requests = []
        self.root_temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.root_temp.cleanup)
        self.root = Path(self.root_temp.name) / "registered"
        self.root.mkdir()
        (self.root / "README.md").write_text("VESTE synthetic fixture\n", encoding="utf-8")
        for argv in (
            ["git", "-C", str(self.root), "init", "-q"],
            ["git", "-C", str(self.root), "add", "README.md"],
            ["git", "-C", str(self.root), "-c", "user.name=Test",
             "-c", "user.email=test@example.invalid", "commit", "-q", "-m", "fixture"],
            ["git", "-C", str(self.root), "remote", "add", "origin",
             "git@github.com:ordaxsystems/ordax-live-proof-fixture.git"],
        ):
            subprocess.run(argv, check=True, capture_output=True, timeout=10)
        self.scope = OwnerCanonicalProjectScope(
            self.owner, self.client, self.space, self.project, self.device,
            self.REPOSITORY,
        )
        client = httpx.Client(transport=httpx.MockTransport(self.handle))
        self.addCleanup(client.close)
        self.remote = ProductRemoteClient("https://product.example.invalid", http=client)
        config = SimpleNamespace(projects={
            "fixture": {"path": str(self.root), "apps": [], "allowed_branches": []},
        })
        self.selected = {}
        self.live = DeviceWorkspaceLiveAuthority(
            remote=self.remote,
            device_id=self.device,
            device_token=lambda: self.device_secret,
            scope_for_resource=lambda rid: (
                self.selected["picker"].trusted_scope_for_resource(rid)
                if "picker" in self.selected else None
            ),
        )
        self.picker = WorkspaceResourceSelection(
            config=config, device_id=self.device,
            choose_directory=lambda project: project.root,
            confirm_owner=lambda project, root: True,
            canonical_project_scope=lambda repo, device: self.scope,
            live_authority=self.live,
        )
        self.selected["picker"] = self.picker
        self.port = self.picker.as_port(local_policy=lambda root: True)

    def choose(self):
        return self.picker.choose_for_owner(self.REPOSITORY)

    def handle(self, request: httpx.Request):
        self.requests.append(request)
        if not self.enabled:
            return httpx.Response(404, json={"ok": False, "error": "not_found"})
        self.assertEqual(request.method, "POST")
        self.assertEqual(request.url.path, "/v3/device/transport/workspace-proof")
        self.assertEqual(request.headers["x-ordax-device-id"], self.device)
        self.assertEqual(request.headers["x-ordax-device-token"], self.device_secret)
        self.assertNotIn("authorization", request.headers)
        self.assertEqual(request.headers["accept"], "application/json")
        payload = json.loads(request.content)
        self.assertEqual(set(payload), {
            "owner_user_id", "actor_id", "client_id", "lab_project_id",
            "workspace_session_id", "resource_id",
        })
        self.assertNotIn(str(self.root), request.content.decode("utf-8"))
        self.assertNotIn(self.device_secret, request.content.decode("utf-8"))
        self.assertEqual(payload["owner_user_id"], self.owner)
        self.assertEqual(payload["client_id"], self.client)
        self.assertEqual(payload["actor_id"], self.actor.actor_id)
        if self.revoke_at is not None and len(self.requests) >= self.revoke_at:
            return httpx.Response(403, json={"ok": False,
                                             "error": "workspace_authorization_required"})
        issued = (self.current - timedelta(minutes=2)).isoformat()
        valid = (self.current + timedelta(hours=1)).isoformat()
        return httpx.Response(200, json={"ok": True,
            "schema": "ordax.project-workspace-resource/1",
            "grant": {
                "grant_id": self.grant, "owner_user_id": self.owner,
                "space_id": self.space, "project_id": self.project,
                "lab_project_id": payload["lab_project_id"],
                "actor_id": payload["actor_id"], "client_id": self.client,
                "workspace_session_id": payload["workspace_session_id"],
                "device_id": self.device, "resource_id": payload["resource_id"],
                "allowed_actions": ["read"], "issued_by": "trusted-runtime-picker",
                "issued_at": issued, "valid_until": valid,
                "revoked_at": None, "active": True,
            },
        })

    def test_fresh_product_proof_is_required_for_every_snapshot_and_verify(self):
        resource = self.choose()
        self.assertEqual(self.requests, [])
        self.assertEqual(self.port.verify_binding(self.actor, self.device, resource)["authorized"],True)
        self.assertEqual(len(self.requests),1)
        summary = self.port.snapshot(self.actor, self.device, resource)
        self.assertEqual(summary["authorized"],True)
        self.assertEqual(summary["fileCount"],1)
        self.assertIn("revision",summary)
        self.assertNotIn(str(self.root),repr(summary))
        self.assertEqual(len(self.requests),3,
                         "verify plus snapshot before and after scan")
        self.assertEqual(self.port.verify_binding(self.actor,self.device,resource)["authorized"],True)
        self.assertEqual(len(self.requests),4,"no cached grant proof")

    def test_postscan_revoke_denies_summary_without_cached_positive(self):
        resource = self.choose()
        self.revoke_at = 2
        with self.assertRaisesRegex(ProjectWorkspaceDenied,"workspace_grant_missing"):
            self.port.snapshot(self.actor,self.device,resource)
        self.assertEqual(len(self.requests),2,"fresh server proof postscan")

    def test_revoked_or_unavailable_product_always_denies(self):
        resource = self.choose()
        self.revoke_at = 1
        with self.assertRaises(ProjectWorkspaceDenied):
            self.port.verify_binding(self.actor,self.device,resource)
        self.revoke_at = None
        self.enabled = False
        with self.assertRaises(ProjectWorkspaceDenied):
            self.port.verify_binding(self.actor,self.device,resource)
        self.enabled = True
        self.device_secret = "short"
        with self.assertRaises(ProjectWorkspaceDenied):
            self.port.verify_binding(self.actor,self.device,resource)
        self.assertEqual(len(self.requests),2,"invalid credential blocked before HTTP")

    def test_device_actor_and_client_confusion_fail_before_product(self):
        resource = self.choose()
        self.assertIsNone(self.live(self.actor,uid(),resource))
        self.assertIsNone(self.live(WorkspaceActor(self.actor.actor_id,uid(),
                           "session_one","lab_one"),self.device,resource))
        self.assertIsNone(self.live(WorkspaceActor("not-uuid",self.client,
                           "session_one","lab_one"),self.device,resource))
        self.assertIsNone(self.live(self.actor,self.device,"resource_does_not_exist"))
        self.assertEqual(self.requests,[])

    def test_discard_prevents_resurrection_and_no_local_selection_is_a_grant(self):
        resource = self.choose()
        self.assertIsNotNone(self.picker.trusted_scope_for_resource(resource))
        self.picker.discard_local_selection(resource)
        self.assertIsNone(self.picker.trusted_scope_for_resource(resource))
        with self.assertRaises(ProjectWorkspaceDenied):
            self.port.verify_binding(self.actor,self.device,resource)
        self.assertEqual(self.requests,[])
        self.assertIsNone(self.picker.trusted_scope_for_resource("resource_unknown"))

    def test_no_grant_issuer_or_token_cache_in_resolver(self):
        self.assertFalse(hasattr(self.live,"__dict__"))
        self.assertFalse(hasattr(self.live,"device_token"))
        self.assertNotIn(self.device_secret,repr(self.live))
        self.assertNotIn(str(self.root),repr(self.live))


if __name__ == "__main__":
    unittest.main()
