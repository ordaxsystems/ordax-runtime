from __future__ import annotations

import json
import unittest

import httpx

from ordax_dev_agent.product_remote_client import (
    ProductRemoteClient,
    ProductRemoteError,
)


class ProductRemoteClientTests(unittest.TestCase):
    def test_session_targets_and_action_flow_do_not_store_token(self):
        seen: list[tuple[str, str, str]] = []
        polls = {"count": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append((
                request.method,
                request.url.path,
                request.headers.get("authorization", ""),
            ))
            if request.url.path == "/v3/product/session":
                return httpx.Response(200, json={
                    "ok": True,
                    "session": {"subject_id": "user:1"},
                })
            if request.url.path == "/v3/product/targets":
                return httpx.Response(200, json={
                    "ok": True,
                    "targets": [{"device_id": "dev-1", "name": "Workstation", "grants": []}],
                })
            if request.url.path == "/v3/product/actions" and request.method == "POST":
                body = json.loads(request.content.decode("utf-8"))
                self.assertEqual(body["device_id"], "dev-1")
                self.assertEqual(body["action"], "git.status")
                self.assertEqual(body["project"], "demo")
                return httpx.Response(202, json={
                    "ok": True,
                    "request_id": "req-1",
                    "status": "queued",
                })
            if request.url.path == "/v3/product/actions/req-1":
                polls["count"] += 1
                status = "running" if polls["count"] == 1 else "succeeded"
                return httpx.Response(200, json={
                    "ok": True,
                    "action": {
                        "request_id": "req-1",
                        "status": status,
                        "result": {"ok": True},
                    },
                })
            return httpx.Response(404, json={"ok": False, "error": "not_found"})

        http = httpx.Client(
            transport=httpx.MockTransport(handler),
            base_url="https://unused.test",
        )
        client = ProductRemoteClient("https://control.example.test", http=http)

        self.assertEqual(client.session("jwt-one")["subject_id"], "user:1")
        self.assertEqual(client.targets("jwt-two")[0]["device_id"], "dev-1")
        request_id = client.submit_action(
            "jwt-three",
            device_id="dev-1",
            action="git.status",
            project="demo",
            arguments={"project": "demo"},
        )
        self.assertEqual(request_id, "req-1")
        result = client.wait_action(
            "jwt-four",
            request_id,
            timeout_seconds=2,
            poll_interval_seconds=0.1,
        )
        self.assertEqual(result["status"], "succeeded")
        self.assertFalse(hasattr(client, "access_token"))
        self.assertEqual(
            [authorization for _, _, authorization in seen],
            [
                "Bearer jwt-one",
                "Bearer jwt-two",
                "Bearer jwt-three",
                "Bearer jwt-four",
                "Bearer jwt-four",
            ],
        )

    def test_device_pairing_methods_are_explicit_and_product_authenticated(self):
        seen = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append((request.method, request.url.path, request.headers.get("authorization", "")))
            if request.url.path == "/v3/product/device-links" and request.method == "POST":
                body = json.loads(request.content.decode("utf-8"))
                self.assertEqual(body["pairing_id"], "pair-1")
                self.assertEqual(body["pairing_secret"], "secret-1")
                return httpx.Response(201, json={
                    "ok": True,
                    "link": {"link_id": "link-1", "device_id": "dev-1"},
                })
            if request.url.path == "/v3/product/device-links" and request.method == "GET":
                return httpx.Response(200, json={
                    "ok": True,
                    "links": [{"link_id": "link-1", "device_id": "dev-1"}],
                })
            if request.url.path == "/v3/product/device-links/link-1" and request.method == "DELETE":
                return httpx.Response(200, json={
                    "ok": True,
                    "link_id": "link-1",
                    "revoked_at": "2026-09-28T12:00:00Z",
                })
            return httpx.Response(404, json={"ok": False, "error": "not_found"})

        client = ProductRemoteClient(
            "https://control.example.test",
            http=httpx.Client(transport=httpx.MockTransport(handler)),
        )
        link = client.claim_device_pairing(
            "jwt-claim",
            pairing_id="pair-1",
            pairing_secret="secret-1",
        )
        self.assertEqual(link["link_id"], "link-1")
        self.assertEqual(client.device_links("jwt-list")[0]["device_id"], "dev-1")
        self.assertEqual(
            client.revoke_device_link("jwt-revoke", "link-1")["link_id"],
            "link-1",
        )
        self.assertEqual(
            [item[2] for item in seen],
            ["Bearer jwt-claim", "Bearer jwt-list", "Bearer jwt-revoke"],
        )

    def test_control_plane_errors_are_typed(self):
        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                403,
                json={"ok": False, "error": "product_grant_not_resolved"},
            )

        client = ProductRemoteClient(
            "https://control.example.test",
            http=httpx.Client(transport=httpx.MockTransport(handler)),
        )
        with self.assertRaises(ProductRemoteError) as caught:
            client.targets("jwt")
        self.assertEqual(caught.exception.error_code, "product_grant_not_resolved")
        self.assertEqual(caught.exception.status_code, 403)

    def test_requires_https_and_bounded_wait_parameters(self):
        with self.assertRaises(ValueError):
            ProductRemoteClient("http://control.example.test")

        client = ProductRemoteClient(
            "https://control.example.test",
            http=httpx.Client(transport=httpx.MockTransport(
                lambda _request: httpx.Response(200, json={"ok": True, "action": {"status": "queued"}})
            )),
        )
        with self.assertRaises(ValueError):
            client.wait_action("jwt", "req", timeout_seconds=0)
        with self.assertRaises(ValueError):
            client.wait_action("jwt", "req", poll_interval_seconds=0.01)

    def test_owner_device_computer_grant_methods_are_product_authenticated(self):
        seen = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append((request.method, request.url.path, request.headers.get("authorization", ""), str(request.url.query)))
            if request.url.path == "/v3/product/device-computer-grants" and request.method == "GET":
                self.assertIn("link_id=link-1", str(request.url.query))
                return httpx.Response(200, json={
                    "ok": True,
                    "grants": [{"id": "grant-1", "device_id": "dev-1", "mode": "interactive-computer-control"}],
                })
            if request.url.path == "/v3/product/device-computer-grants" and request.method == "POST":
                body = json.loads(request.content.decode("utf-8"))
                self.assertEqual("link-1", body["link_id"])
                self.assertEqual("interactive-computer-control", body["mode"])
                self.assertEqual("2026-11-05T00:00:00+00:00", body["expires_at"])
                return httpx.Response(201, json={
                    "ok": True,
                    "mode": body["mode"],
                    "replayed": False,
                    "grant": {"id": "grant-1", "device_id": "dev-1", "mode": body["mode"]},
                })
            if request.url.path == "/v3/product/device-computer-grants/grant-1" and request.method == "DELETE":
                return httpx.Response(200, json={"ok": True, "revoked": True, "grant": {"id": "grant-1"}})
            return httpx.Response(404, json={"ok": False, "error": "not_found"})

        client = ProductRemoteClient(
            "https://control.example.test",
            http=httpx.Client(transport=httpx.MockTransport(handler)),
        )
        grants = client.device_computer_grants("jwt-list", link_id="link-1")
        self.assertEqual("grant-1", grants[0]["id"])
        created = client.create_device_computer_grant(
            "jwt-create",
            link_id="link-1",
            mode="interactive-computer-control",
            expires_at="2026-11-05T00:00:00+00:00",
        )
        self.assertEqual("grant-1", created["grant"]["id"])
        revoked = client.revoke_device_computer_grant("jwt-revoke", "grant-1")
        self.assertTrue(revoked["revoked"])
        self.assertFalse(hasattr(client, "access_token"))
        self.assertEqual(
            [item[2] for item in seen],
            ["Bearer jwt-list", "Bearer jwt-create", "Bearer jwt-revoke"],
        )


    def test_owner_project_browser_grant_methods_are_product_authenticated(self):
        seen = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append((request.method, request.url.path, request.headers.get("authorization", ""), str(request.url.query)))
            if request.url.path == "/v3/product/project-capability-grants" and request.method == "GET":
                self.assertIn("link_id=link-1", str(request.url.query))
                return httpx.Response(200, json={
                    "ok": True,
                    "grants": [{
                        "id": "grant-browser-1",
                        "device_id": "dev-1",
                        "mode": "project-browser-automation",
                        "projects": ["demo"],
                    }],
                })
            if request.url.path == "/v3/product/project-capability-grants" and request.method == "POST":
                body = json.loads(request.content.decode("utf-8"))
                self.assertEqual("link-1", body["link_id"])
                self.assertEqual("project-browser-automation", body["mode"])
                self.assertEqual(["demo"], body["projects"])
                return httpx.Response(201, json={
                    "ok": True,
                    "mode": body["mode"],
                    "replayed": False,
                    "grant": {
                        "id": "grant-browser-1",
                        "device_id": "dev-1",
                        "mode": body["mode"],
                        "projects": body["projects"],
                    },
                })
            if request.url.path == "/v3/product/project-capability-grants/grant-browser-1" and request.method == "DELETE":
                return httpx.Response(200, json={
                    "ok": True,
                    "revoked": True,
                    "grant": {"id": "grant-browser-1"},
                })
            return httpx.Response(404, json={"ok": False, "error": "not_found"})

        client = ProductRemoteClient(
            "https://control.example.test",
            http=httpx.Client(transport=httpx.MockTransport(handler)),
        )
        grants = client.project_capability_grants("jwt-list", link_id="link-1")
        self.assertEqual("grant-browser-1", grants[0]["id"])
        created = client.create_project_capability_grant(
            "jwt-create",
            link_id="link-1",
            mode="project-browser-automation",
            projects=["demo"],
            expires_at="2026-11-05T00:00:00+00:00",
        )
        self.assertEqual("grant-browser-1", created["grant"]["id"])
        revoked = client.revoke_project_capability_grant("jwt-revoke", "grant-browser-1")
        self.assertTrue(revoked["revoked"])
        self.assertFalse(hasattr(client, "access_token"))
        self.assertEqual(
            [item[2] for item in seen],
            ["Bearer jwt-list", "Bearer jwt-create", "Bearer jwt-revoke"],
        )


if __name__ == "__main__":
    unittest.main()
