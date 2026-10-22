from __future__ import annotations

import json
import unittest

import httpx

from ordax_dev_agent.product_remote_client import ProductRemoteClient, ProductRemoteError


DEVICE_ID = "24630b00-e9c0-4d93-b38a-2367048cd9d1"
CLIENT_ID = "11111111-1111-4111-8111-111111111111"
GROUP_ID = "22222222-2222-4222-8222-222222222222"
SPACE_ID = "55555555-5555-4555-8555-555555555555"
PROJECT_ID = "66666666-6666-4666-8666-666666666666"
OWNER_ID = "77777777-7777-4777-8777-777777777777"
DEVICE_TOKEN = "d" * 64
RESOURCE_ID = "resource_0123456789abcdefghijklmnopqrstuvwxyz"


class ProductRemoteClientTests(unittest.TestCase):
    def test_workspace_resource_lifecycle_uses_owner_and_device_authority_per_call(self):
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            if request.method == "POST" and request.url.path == "/v3/owner/project-workspace-resources":
                return httpx.Response(200, json={"ok": True, "schema": "ordax.project-workspace-resource/1"})
            if request.method == "POST" and request.url.path == "/v3/device/transport/workspace-proof":
                return httpx.Response(200, json={"ok": True, "schema": "ordax.project-workspace-resource/1"})
            if request.method == "DELETE" and request.url.path.endswith("/" + RESOURCE_ID):
                return httpx.Response(200, json={
                    "ok": True, "resource_id": RESOURCE_ID, "revoked": True,
                })
            return httpx.Response(404, json={"ok": False, "error": "not_found"})

        client = ProductRemoteClient(
            "https://mcp.example.test",
            http=httpx.Client(transport=httpx.MockTransport(handler)),
        )
        self.assertTrue(client.register_owner_project_workspace_resource(
            "owner-token", device_id=DEVICE_ID, device_token=DEVICE_TOKEN,
            actor_id=OWNER_ID, client_id=CLIENT_ID, lab_project_id="lab_veste",
            repository_full_name="ordaxsystems/veste", resource_id=RESOURCE_ID,
            workspace_session_id="session_veste",
        )["ok"])
        self.assertTrue(client.verify_device_project_workspace_resource(
            owner_user_id=OWNER_ID, actor_id=OWNER_ID, client_id=CLIENT_ID,
            device_id=DEVICE_ID, device_token=DEVICE_TOKEN,
            lab_project_id="lab_veste", resource_id=RESOURCE_ID,
            workspace_session_id="session_veste",
        )["ok"])
        self.assertTrue(client.revoke_owner_project_workspace_resource(
            "owner-token", client_id=CLIENT_ID, device_id=DEVICE_ID,
            device_token=DEVICE_TOKEN, resource_id=RESOURCE_ID,
        )["revoked"])

        register, verify, revoke = seen
        self.assertEqual(register.method, "POST")
        self.assertEqual(register.headers["authorization"], "Bearer owner-token")
        self.assertEqual(register.headers["x-ordax-device-id"], DEVICE_ID)
        self.assertEqual(register.headers["x-ordax-device-token"], DEVICE_TOKEN)
        self.assertEqual(set(json.loads(register.content)), {
            "actor_id", "client_id", "lab_project_id", "repository_full_name",
            "resource_id", "workspace_session_id",
        })
        self.assertNotIn("path", json.loads(register.content))

        self.assertEqual(verify.method, "POST")
        self.assertNotIn("authorization", verify.headers)
        self.assertEqual(verify.headers["x-ordax-device-token"], DEVICE_TOKEN)
        self.assertEqual(set(json.loads(verify.content)), {
            "owner_user_id", "actor_id", "client_id", "lab_project_id",
            "resource_id", "workspace_session_id",
        })

        self.assertEqual(revoke.method, "DELETE")
        self.assertEqual(revoke.headers["authorization"], "Bearer owner-token")
        self.assertEqual(revoke.url.params.get("client_id"), CLIENT_ID)
        self.assertFalse(hasattr(client, "access_token"))
        self.assertFalse(hasattr(client, "device_token"))

    def test_workspace_resource_rejects_invalid_reference_and_device_credential_before_network(self):
        calls = []
        client = ProductRemoteClient(
            "https://mcp.example.test",
            http=httpx.Client(transport=httpx.MockTransport(lambda request: calls.append(request))),
        )
        with self.assertRaisesRegex(ValueError, "resource_id is invalid"):
            client.verify_device_project_workspace_resource(
                owner_user_id=OWNER_ID, actor_id=OWNER_ID, client_id=CLIENT_ID,
                device_id=DEVICE_ID, device_token=DEVICE_TOKEN,
                lab_project_id="lab_veste", resource_id="C:\\Users\\test",
                workspace_session_id="session_veste",
            )
        with self.assertRaisesRegex(ValueError, "Product device credential is invalid"):
            client.verify_device_project_workspace_resource(
                owner_user_id=OWNER_ID, actor_id=OWNER_ID, client_id=CLIENT_ID,
                device_id=DEVICE_ID, device_token="short",
                lab_project_id="lab_veste", resource_id=RESOURCE_ID,
                workspace_session_id="session_veste",
            )
        self.assertEqual(calls, [])

    def test_session_targets_and_action_flow_do_not_store_token(self):
        seen: list[tuple[str, str, str]] = []
        polls = {"count": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append((request.method, request.url.path, request.headers.get("authorization", "")))
            if request.url.path == "/v3/product/session":
                return httpx.Response(200, json={"ok": True, "session": {"subject_id": "user:1"}})
            if request.url.path == "/v3/product/targets":
                return httpx.Response(200, json={"ok": True, "targets": [{"device_id": DEVICE_ID}]})
            if request.url.path == "/v3/product/actions" and request.method == "POST":
                body = json.loads(request.content.decode("utf-8"))
                self.assertEqual(body["device_id"], DEVICE_ID)
                self.assertEqual(body["action"], "git.status")
                return httpx.Response(202, json={"ok": True, "request_id": "req-1", "status": "queued"})
            if request.url.path == "/v3/product/actions/req-1":
                polls["count"] += 1
                status = "running" if polls["count"] == 1 else "succeeded"
                return httpx.Response(200, json={"ok": True, "action": {
                    "request_id": "req-1", "status": status, "result": {"ok": True},
                }})
            return httpx.Response(404, json={"ok": False, "error": "not_found"})

        client = ProductRemoteClient(
            "https://mcp.example.test",
            http=httpx.Client(transport=httpx.MockTransport(handler)),
        )
        self.assertEqual(client.session("jwt-one")["subject_id"], "user:1")
        self.assertEqual(client.targets("jwt-two")[0]["device_id"], DEVICE_ID)
        request_id = client.submit_action(
            "jwt-three", device_id=DEVICE_ID, action="git.status", project="demo",
        )
        self.assertEqual(request_id, "req-1")
        result = client.wait_action(
            "jwt-four", request_id, timeout_seconds=2, poll_interval_seconds=0.1,
        )
        self.assertEqual(result["status"], "succeeded")
        self.assertFalse(hasattr(client, "access_token"))
        self.assertEqual([item[2] for item in seen], [
            "Bearer jwt-one", "Bearer jwt-two", "Bearer jwt-three",
            "Bearer jwt-four", "Bearer jwt-four",
        ])

    def test_owner_remote_authority_is_device_bound(self):
        def handler(request: httpx.Request) -> httpx.Response:
            self.assertEqual(request.url.path, "/v3/owner/remote-authority")
            self.assertEqual(request.url.params.get("device_id"), DEVICE_ID)
            self.assertEqual(request.headers["authorization"], "Bearer jwt-owner")
            return httpx.Response(200, json={
                "ok": True,
                "device_id": DEVICE_ID,
                "clients": [{"client_id": CLIENT_ID, "client_name": "ChatGPT"}],
                "grant_groups": [{
                    "grant_group_id": GROUP_ID,
                    "client_id": CLIENT_ID,
                    "profile_key": "computer-observe",
                    "device_id": DEVICE_ID,
                    "space_id": None,
                    "project_id": None,
                    "capabilities": [{"capability": "computer.windows", "access_mode": "read"}],
                }],
            })

        client = ProductRemoteClient(
            "https://mcp.example.test",
            http=httpx.Client(transport=httpx.MockTransport(handler)),
        )
        authority = client.owner_remote_authority("jwt-owner", device_id=DEVICE_ID)
        self.assertEqual(authority["device_id"], DEVICE_ID)
        self.assertEqual(authority["clients"][0]["client_id"], CLIENT_ID)
        self.assertEqual(authority["grant_groups"][0]["profile_key"], "computer-observe")

    def test_owner_remote_authority_rejects_cross_device_response(self):
        other = "33333333-3333-4333-8333-333333333333"
        client = ProductRemoteClient(
            "https://mcp.example.test",
            http=httpx.Client(transport=httpx.MockTransport(lambda _request: httpx.Response(200, json={
                "ok": True, "device_id": other, "clients": [], "grant_groups": [],
            }))),
        )
        with self.assertRaises(ProductRemoteError) as caught:
            client.owner_remote_authority("jwt", device_id=DEVICE_ID)
        self.assertEqual(caught.exception.error_code, "product_remote_invalid_response")

    def test_owner_project_scope_is_bound_to_requested_device_and_repository(self):
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            self.assertEqual(request.method, "GET")
            self.assertEqual(request.url.path, "/v3/owner/project-scope")
            self.assertEqual(request.url.params.get("device_id"), DEVICE_ID)
            self.assertEqual(request.url.params.get("repository_full_name"), "ordaxsystems/veste")
            self.assertEqual(request.headers["authorization"], "Bearer owner-token")
            return httpx.Response(200, json={
                "ok": True,
                "space_id": SPACE_ID,
                "project_id": PROJECT_ID,
                "repository_id": 17,
                "repository_full_name": "ordaxsystems/veste",
            })

        client = ProductRemoteClient(
            "https://mcp.example.test",
            http=httpx.Client(transport=httpx.MockTransport(handler)),
        )
        scope = client.owner_project_scope(
            "owner-token", device_id=DEVICE_ID,
            repository_full_name="ordaxsystems/veste",
        )
        self.assertEqual(scope, {
            "space_id": SPACE_ID,
            "project_id": PROJECT_ID,
            "repository_id": 17,
            "repository_full_name": "ordaxsystems/veste",
        })
        self.assertEqual(len(seen), 1)

    def test_owner_project_scope_rejects_repository_mismatch_and_invalid_ids(self):
        payloads = [
            {"ok": True, "space_id": SPACE_ID, "project_id": PROJECT_ID,
             "repository_id": 17, "repository_full_name": "ordaxsystems/other"},
            {"ok": True, "space_id": "not-a-uuid", "project_id": PROJECT_ID,
             "repository_id": 17, "repository_full_name": "ordaxsystems/veste"},
            {"ok": True, "space_id": SPACE_ID, "project_id": PROJECT_ID,
             "repository_id": True, "repository_full_name": "ordaxsystems/veste"},
        ]
        for payload in payloads:
            client = ProductRemoteClient(
                "https://mcp.example.test",
                http=httpx.Client(transport=httpx.MockTransport(
                    lambda _request, body=payload: httpx.Response(200, json=body)
                )),
            )
            with self.subTest(payload=payload), self.assertRaises(ProductRemoteError) as caught:
                client.owner_project_scope(
                    "owner-token", device_id=DEVICE_ID,
                    repository_full_name="ordaxsystems/veste",
                )
            self.assertEqual(caught.exception.error_code, "product_remote_invalid_response")

    def test_replace_and_revoke_owner_grant_group_use_canonical_routes(self):
        seen: list[tuple[str, str, str]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append((request.method, request.url.path, request.headers.get("authorization", "")))
            if request.method == "POST":
                self.assertEqual(request.url.path, "/v3/owner/remote-grant-groups")
                body = json.loads(request.content.decode("utf-8"))
                self.assertEqual(body, {
                    "client_id": CLIENT_ID,
                    "device_id": DEVICE_ID,
                    "profile_key": "computer-observe",
                    "space_id": None,
                    "project_id": None,
                    "valid_until": "2026-11-05T00:00:00+00:00",
                })
                return httpx.Response(200, json={
                    "ok": True, "grant_group_id": GROUP_ID, "profile_key": "computer-observe",
                })
            self.assertEqual(request.method, "DELETE")
            self.assertEqual(request.url.path, f"/v3/owner/remote-grant-groups/{GROUP_ID}")
            self.assertEqual(request.url.params.get("client_id"), CLIENT_ID)
            return httpx.Response(200, json={"ok": True, "grant_group_id": GROUP_ID, "revoked": True})

        client = ProductRemoteClient(
            "https://mcp.example.test",
            http=httpx.Client(transport=httpx.MockTransport(handler)),
        )
        created = client.replace_owner_remote_grant_group(
            "jwt-create",
            client_id=CLIENT_ID,
            device_id=DEVICE_ID,
            profile_key="computer-observe",
            valid_until="2026-11-05T00:00:00+00:00",
        )
        self.assertEqual(created["grant_group_id"], GROUP_ID)
        revoked = client.revoke_owner_remote_grant_group(
            "jwt-revoke", client_id=CLIENT_ID, grant_group_id=GROUP_ID,
        )
        self.assertTrue(revoked["revoked"])
        self.assertEqual([item[2] for item in seen], ["Bearer jwt-create", "Bearer jwt-revoke"])

    def test_retired_owner_routes_are_not_implemented(self):
        for name in (
            "claim_device_pairing", "device_links", "revoke_device_link",
            "device_computer_grants", "create_device_computer_grant",
            "revoke_device_computer_grant", "device_intelligence_grants",
            "create_device_intelligence_grant", "revoke_device_intelligence_grant",
            "project_capability_grants", "create_project_capability_grant",
            "revoke_project_capability_grant",
        ):
            self.assertFalse(hasattr(ProductRemoteClient, name), name)

    def test_product_errors_are_typed(self):
        client = ProductRemoteClient(
            "https://mcp.example.test",
            http=httpx.Client(transport=httpx.MockTransport(
                lambda _request: httpx.Response(403, json={
                    "ok": False, "error": "product_grant_not_resolved",
                })
            )),
        )
        with self.assertRaises(ProductRemoteError) as caught:
            client.targets("jwt")
        self.assertEqual(caught.exception.error_code, "product_grant_not_resolved")
        self.assertEqual(caught.exception.status_code, 403)

    def test_requires_https_and_bounded_wait_parameters(self):
        with self.assertRaises(ValueError):
            ProductRemoteClient("http://mcp.example.test")
        client = ProductRemoteClient(
            "https://mcp.example.test",
            http=httpx.Client(transport=httpx.MockTransport(
                lambda _request: httpx.Response(200, json={"ok": True})
            )),
        )
        with self.assertRaises(ValueError):
            client.wait_action("jwt", "req", timeout_seconds=0)
        with self.assertRaises(ValueError):
            client.wait_action("jwt", "req", poll_interval_seconds=0.01)
        with self.assertRaises(ValueError):
            client.owner_remote_authority("jwt", device_id="not-a-uuid")


if __name__ == "__main__":
    unittest.main()
