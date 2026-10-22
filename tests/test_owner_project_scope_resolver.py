"""Disposable authenticated Owner project-scope tests, no live tokens or grants."""
from __future__ import annotations

import subprocess
import tempfile
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import httpx

from ordax_dev_agent.owner_project_scope_resolver import (
    AuthenticatedOwnerProjectScopeResolver,
)
from ordax_dev_agent.product_remote_client import ProductRemoteClient
from ordax_dev_agent.project_workspace_port import ProjectWorkspaceDenied, WorkspaceActor
from ordax_dev_agent.workspace_resource_selection import WorkspaceResourceSelection


def _uuid() -> str:
    return str(uuid.uuid4())


class AuthenticatedOwnerProjectScopeResolverTests(unittest.TestCase):
    REPO = "ordaxsystems/ordax-picker-fixture"

    def setUp(self):
        self.owner, self.client, self.device = _uuid(), _uuid(), _uuid()
        self.space, self.project = _uuid(), _uuid()
        self.token = ["owner.private.bearer.fixture"]
        self.device_token = "d" * 64
        self.consent_active = True
        self.clients = [self.client]
        self.token_subject = self.owner
        self.expiry = 1_900_000_000
        self.repository = self.REPO
        self.scope_project = self.project
        self.scope_space = self.space
        self.fail_project = False
        self.fail_session = False
        self.calls: list[tuple[str, str, str]] = []
        self.on_call = None
        self.workspace_grants: dict[str, str] = {}
        transport = httpx.MockTransport(self.handle)
        http = httpx.Client(transport=transport)
        self.addCleanup(http.close)
        self.remote = ProductRemoteClient("https://product.example.invalid", http=http)
        self.resolver = self.make_resolver()

    def make_resolver(self, **kwargs):
        return AuthenticatedOwnerProjectScopeResolver(
            remote=self.remote,
            owner_user_id=kwargs.get("owner_user_id", self.owner),
            client_id=kwargs.get("client_id", self.client),
            device_id=kwargs.get("device_id", self.device),
            access_token=kwargs.get("access_token", lambda: self.token[0]),
            device_token=kwargs.get("device_token", lambda: self.device_token),
            clock=lambda: 1_800_000_000,
        )

    def workspace_proof(self, body):
        issued = datetime.now(timezone.utc)
        grant_id = self.workspace_grants.setdefault(body["resource_id"], _uuid())
        grant = {
            "grant_id": grant_id,
            "owner_user_id": self.owner,
            "space_id": self.scope_space,
            "project_id": self.scope_project,
            "lab_project_id": body["lab_project_id"],
            "actor_id": body["actor_id"],
            "client_id": body["client_id"],
            "workspace_session_id": body["workspace_session_id"],
            "device_id": self.device,
            "resource_id": body["resource_id"],
            "allowed_actions": ["read"],
            "issued_by": "trusted-runtime-picker",
            "issued_at": issued.isoformat(),
            "valid_until": (issued + timedelta(hours=1)).isoformat(),
            "revoked_at": None,
            "active": True,
        }
        return {"ok": True, "schema": "ordax.project-workspace-resource/1", "grant": grant}

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.calls.append((request.method, request.url.path,
                           request.headers.get("authorization", "")))
        if callable(self.on_call):
            self.on_call(request, len(self.calls))
        owner_auth = request.headers.get("authorization") == "Bearer " + self.token[0]
        device_auth = (
            request.headers.get("x-ordax-device-id") == self.device
            and request.headers.get("x-ordax-device-token") == self.device_token
        )
        if request.url.path == "/v3/device/transport/workspace-proof":
            if request.method != "POST" or not device_auth:
                return httpx.Response(401, json={"ok": False, "error": "device_auth_required"})
            return httpx.Response(200, json=self.workspace_proof(httpx.Response(200, content=request.content).json()))
        if request.url.path == "/v3/owner/project-workspace-resources":
            if request.method != "POST" or not owner_auth or not device_auth:
                return httpx.Response(401, json={"ok": False, "error": "workspace_authorization_required"})
            return httpx.Response(200, json=self.workspace_proof(httpx.Response(200, content=request.content).json()))
        if request.url.path.startswith("/v3/owner/project-workspace-resources/"):
            resource = request.url.path.rsplit("/", 1)[-1]
            if (request.method != "DELETE" or not owner_auth or not device_auth
                    or request.url.params.get("client_id") != self.client):
                return httpx.Response(401, json={"ok": False, "error": "workspace_authorization_required"})
            return httpx.Response(200, json={"ok": True, "resource_id": resource, "revoked": True})
        if not owner_auth:
            return httpx.Response(401, json={"ok": False, "error": "product_auth_required"})
        if request.method != "GET":
            return httpx.Response(405, json={"ok": False, "error": "method_not_allowed"})
        if request.url.path == "/v3/product/session":
            if self.fail_session:
                return httpx.Response(401, json={"ok": False, "error": "product_auth_required"})
            return httpx.Response(200, json={"ok": True, "session": {
                "subject_id": self.token_subject, "expires_at_unix": self.expiry,
            }})
        if request.url.path == "/v3/owner/remote-authority":
            if request.url.params.get("device_id") != self.device:
                return httpx.Response(403, json={"ok": False, "error": "device_not_owned"})
            return httpx.Response(200, json={"ok": True, "device_id": self.device,
                "clients": [{"client_id": i} for i in self.clients]
                    if self.consent_active else [],
                "grant_groups": []})
        if request.url.path == "/v3/owner/project-scope":
            if self.fail_project or request.url.params.get("device_id") != self.device:
                return httpx.Response(403, json={"ok": False, "error": "project_not_owned"})
            return httpx.Response(200, json={"ok": True,
                "space_id": self.scope_space,
                "project_id": self.scope_project,
                "repository_id": 42,
                "repository_full_name": self.repository})
        raise AssertionError("Unexpected Product authority route: " + request.url.path)

    def test_live_product_owner_subject_consent_and_repo_are_required(self):
        scope = self.resolver(self.REPO, self.device)
        self.assertEqual((scope.owner_user_id, scope.client_id, scope.device_id),
                         (self.owner, self.client, self.device))
        self.assertEqual((scope.space_id, scope.project_id),
                         (self.space, self.project))
        self.assertEqual(scope.repository_full_name, self.REPO)
        self.assertEqual([c[1] for c in self.calls], [
            "/v3/product/session", "/v3/owner/remote-authority",
            "/v3/owner/project-scope", "/v3/product/session",
            "/v3/owner/remote-authority",
        ])
        self.assertTrue(all(method == "GET" for method, _, _ in self.calls))
        self.assertNotIn(self.token[0], repr(self.resolver))
        self.assertFalse(hasattr(self.resolver, "__dict__"))

    def test_unknown_device_or_untrusted_repo_never_hits_product(self):
        for repo, device in (
            ("ordaxsystems/other", _uuid()),
            ("https://github.com/ordaxsystems/ordax-picker-fixture", self.device),
            ("ordaxsystems/ORDAX-picker-fixture", self.device),
            ("../registered", self.device),
            (None, self.device),
        ):
            with self.subTest(repo=repo), self.assertRaisesRegex(
                ProjectWorkspaceDenied, "^workspace_project_scope_unavailable$",
            ):
                self.resolver(repo, device)
        self.assertEqual(self.calls, [])

    def test_signed_session_owner_and_expiry_fail_closed(self):
        for change in (
            {"token_subject": _uuid()}, {"token_subject": "untrusted"},
            {"expiry": 1_799_999_999}, {"expiry": True},
            {"expiry": "1900000000"}, {"fail_session": True},
        ):
            self.setUp()
            for key, value in change.items():
                setattr(self, key, value)
            with self.subTest(change=change), self.assertRaisesRegex(
                ProjectWorkspaceDenied, "^workspace_project_scope_unavailable$",
            ):
                self.resolver(self.REPO, self.device)

    def test_client_consent_must_match_exact_active_mcp_client(self):
        for clients, consent in (
            ([], True), ([_uuid()], True), ([self.client, self.client], True),
            ([self.client], False),
        ):
            self.clients, self.consent_active = clients, consent
            with self.subTest(clients=clients, consent=consent), self.assertRaises(
                ProjectWorkspaceDenied
            ):
                self.resolver(self.REPO, self.device)
            self.calls.clear()
        self.clients, self.consent_active = [self.client], True

    def test_revocation_after_scope_request_is_detected_without_cache(self):
        self.on_call = lambda request, n: setattr(
            self, "consent_active", False
        ) if n == 3 else None
        with self.assertRaises(ProjectWorkspaceDenied):
            self.resolver(self.REPO, self.device)
        self.assertEqual(self.calls[-1][1], "/v3/owner/remote-authority")
        self.on_call = None
        self.consent_active = True
        self.resolver(self.REPO, self.device)
        self.assertEqual(len(self.calls), 10)

    def test_logout_during_project_resolution_is_denied(self):
        self.on_call = lambda request, n: setattr(
            self, "fail_session", True
        ) if n == 3 else None
        with self.assertRaises(ProjectWorkspaceDenied):
            self.resolver(self.REPO, self.device)
        self.assertEqual(self.calls[-1][1], "/v3/product/session")

    def test_project_rejected_or_wrong_repository_never_returns_scope(self):
        for key, value in (
            ("fail_project", True),
            ("repository", "ordaxsystems/different"),
            ("scope_space", "untrusted-space"),
            ("scope_project", "not-uuid"),
        ):
            original = getattr(self, key)
            setattr(self, key, value)
            with self.subTest(key=key), self.assertRaises(ProjectWorkspaceDenied):
                self.resolver(self.REPO, self.device)
            setattr(self, key, original)

    def test_missing_token_or_transport_error_never_falls_back(self):
        for token in ("", "a\rb", "a\nb", "a" * 16001):
            self.token[0] = token
            with self.assertRaises(ProjectWorkspaceDenied):
                self.resolver(self.REPO, self.device)
        self.assertEqual(self.calls, [])
        self.token[0] = "owner.private.bearer.fixture"
        self.remote.http.close()
        with self.assertRaises(ProjectWorkspaceDenied):
            self.resolver(self.REPO, self.device)

    def test_owner_callback_is_required_and_never_auto_grants(self):
        for mutation in (
            {"owner_user_id": "invalid"},
            {"client_id": _uuid()},
            {"device_id": "not-uuid"},
            {"access_token": None},
        ):
            with self.subTest(mutation=mutation):
                if "client_id" in mutation:
                    other = self.make_resolver(**mutation)
                    with self.assertRaises(ProjectWorkspaceDenied):
                        other(self.REPO, self.device)
                else:
                    with self.assertRaises(ValueError):
                        self.make_resolver(**mutation)
        self.assertEqual([path for _method, path, _auth in self.calls], [
            "/v3/product/session", "/v3/owner/remote-authority",
        ])
        self.assertTrue(all(method == "GET" for method, _, _ in self.calls))

    def test_broker_composition_uses_single_registered_root_and_does_not_read_without_grant(self):
        with tempfile.TemporaryDirectory() as home:
            root = Path(home).joinpath("registered")
            root.mkdir()
            subprocess.run(["git", "-C", str(root), "init", "-q"],
                           check=True, capture_output=True, timeout=10)
            subprocess.run(["git", "-C", str(root), "remote", "add", "origin",
                "git@github.com:ordaxsystems/ordax-picker-fixture.git"],
                check=True, capture_output=True, timeout=10)
            config = SimpleNamespace(projects={"fixture": {
                "path": str(root), "apps": [], "allowed_branches": []}})
            picks = []
            broker = WorkspaceResourceSelection(
                config=config, device_id=self.device,
                choose_directory=lambda project: (
                    picks.append(project.root) or project.root
                ),
                confirm_owner=lambda project, path: True,
                canonical_project_scope=self.resolver,
                live_authority=lambda *args: None,
            )
            opaque = broker.choose_for_owner(self.REPO)
            self.assertTrue(opaque.startswith("resource_"))
            self.assertNotIn(str(root), opaque)
            self.assertEqual(len(picks), 1)
            with self.assertRaisesRegex(ProjectWorkspaceDenied,"workspace_grant_missing"):
                broker.as_port(local_policy=lambda _root: True).verify_binding(
                    WorkspaceActor(_uuid(), self.client, "session_a", "lab_a"),
                    self.device, opaque)

    def test_resource_registration_live_verification_and_revoke_use_existing_owner_and_device_auth(self):
        scope = self.resolver(self.REPO, self.device)
        actor = WorkspaceActor(self.owner, self.client, "session_veste", "lab_veste")
        resource_id = "resource_0123456789abcdefghijklmnopqrstuvwxyz"

        registered = self.resolver.register_workspace_resource(scope, actor, resource_id)
        self.assertEqual((registered.owner_user_id, registered.canonical_project_id),
                         (self.owner, self.project))
        self.assertEqual(registered.resource_id, resource_id)
        live = self.resolver.live_workspace_authority(actor, self.device, resource_id, scope)
        self.assertIsNotNone(live)
        self.assertEqual(live.grant_id, registered.grant_id)
        self.resolver.revoke_workspace_resource(scope, actor, resource_id)

        paths = [path for _method, path, _auth in self.calls]
        self.assertIn("/v3/owner/project-workspace-resources", paths)
        self.assertIn("/v3/device/transport/workspace-proof", paths)
        self.assertTrue(any(method == "DELETE" and path.endswith("/" + resource_id)
                            for method, path, _auth in self.calls))
        self.assertNotIn(self.device_token, repr(self.resolver))

    def test_resource_registration_requires_device_credential_provider(self):
        scope = self.resolver(self.REPO, self.device)
        actor = WorkspaceActor(self.owner, self.client, "session_veste", "lab_veste")
        without_device_token = self.make_resolver(device_token=None)
        with self.assertRaisesRegex(ProjectWorkspaceDenied,
                                    "workspace_resource_registration_unavailable"):
            without_device_token.register_workspace_resource(
                scope, actor, "resource_0123456789abcdefghijklmnopqrstuvwxyz",
            )


if __name__ == "__main__":
    unittest.main()
