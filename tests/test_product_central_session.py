from __future__ import annotations

import json
import unittest

import httpx

from ordax_dev_agent.product_central_session import ProductCentralSessionClient


DEVICE_ID = "22222222-2222-4222-8222-222222222222"
CHALLENGE_ID = "33333333-3333-4333-8333-333333333333"


class ProductCentralSessionClientTests(unittest.TestCase):
    def make_client(self, token_box, handler, *, min_poll_seconds=20.0):
        http = httpx.Client(transport=httpx.MockTransport(handler))
        client = ProductCentralSessionClient(
            "https://mcp.ordax.com.br",
            DEVICE_ID,
            lambda: token_box[0],
            http=http,
            min_poll_seconds=min_poll_seconds,
        )
        self.addCleanup(http.close)
        return client

    def test_poll_without_challenge_is_bounded_and_does_not_post(self):
        token = ["a" * 64]
        calls: list[tuple[str, str]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append((request.method, request.url.path))
            self.assertEqual(request.headers["X-Ordax-Device-Id"], DEVICE_ID)
            self.assertEqual(request.headers["X-Ordax-Device-Token"], token[0])
            return httpx.Response(200, json={"ok": True, "pending": False})

        client = self.make_client(token, handler)
        status = client.tick(now=10.0)
        self.assertTrue(status.checked)
        self.assertFalse(status.confirmed)
        self.assertIsNone(status.last_error)
        self.assertEqual(calls, [("GET", "/v3/product/device/central-session")])

    def test_pending_challenge_is_confirmed_for_exact_device(self):
        token = ["a" * 64]
        calls: list[tuple[str, dict | None]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content) if request.content else None
            calls.append((request.method, body))
            self.assertEqual(request.headers["X-Ordax-Device-Id"], DEVICE_ID)
            self.assertEqual(request.headers["X-Ordax-Device-Token"], token[0])
            if request.method == "GET":
                return httpx.Response(200, json={
                    "ok": True,
                    "pending": True,
                    "challenge_id": CHALLENGE_ID,
                    "expires_at": "2026-10-15T13:32:00Z",
                })
            return httpx.Response(200, json={
                "ok": True,
                "status": "bound",
                "device_id": DEVICE_ID,
                "request_id": CHALLENGE_ID,
                "expires_at": "2026-10-15T14:00:00Z",
            })

        client = self.make_client(token, handler)
        status = client.tick(now=10.0)
        self.assertTrue(status.confirmed)
        self.assertEqual(status.challenge_id, CHALLENGE_ID)
        self.assertIsNone(status.last_error)
        self.assertEqual(calls, [
            ("GET", None),
            ("POST", {"challenge_id": CHALLENGE_ID}),
        ])

    def test_receipt_for_another_device_fails_closed(self):
        token = ["a" * 64]

        def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "GET":
                return httpx.Response(200, json={
                    "ok": True, "pending": True, "challenge_id": CHALLENGE_ID,
                })
            return httpx.Response(200, json={
                "ok": True,
                "status": "bound",
                "device_id": "44444444-4444-4444-8444-444444444444",
            })

        client = self.make_client(token, handler)
        status = client.tick(now=10.0)
        self.assertFalse(status.confirmed)
        self.assertEqual(status.last_error, "product_central_session_receipt_mismatch")

    def test_unauthorized_device_requires_reenrollment(self):
        token = ["a" * 64]

        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(401, json={"ok": False, "error": "product_device_unauthorized"})

        client = self.make_client(token, handler)
        status = client.tick(now=10.0)
        self.assertEqual(status.last_error, "product_identity_reenrollment_required")

    def test_cadence_reuses_existing_heartbeat_without_second_scheduler(self):
        token = ["a" * 64]
        calls = 0

        def handler(_request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            return httpx.Response(200, json={"ok": True, "pending": False})

        client = self.make_client(token, handler, min_poll_seconds=20.0)
        client.tick(now=10.0)
        client.tick(now=15.0)
        client.tick(now=29.9)
        self.assertEqual(calls, 1)
        client.tick(now=30.0)
        self.assertEqual(calls, 2)

    def test_raw_credential_is_not_retained_by_client(self):
        token = ["a" * 64]

        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"ok": True, "pending": False})

        client = self.make_client(token, handler)
        client.tick(now=10.0)
        state = repr(vars(client))
        self.assertNotIn(token[0], state)

    def test_malformed_or_oversized_authority_response_fails_closed(self):
        token = ["a" * 64]
        responses = iter([
            httpx.Response(200, content=b"not-json"),
            httpx.Response(200, content=b"{" + b"x" * (16 * 1024 + 1) + b"}"),
        ])

        def handler(_request: httpx.Request) -> httpx.Response:
            return next(responses)

        client = self.make_client(token, handler)
        first = client.tick(now=10.0)
        self.assertEqual(first.last_error, "product_central_session_response_invalid")
        second = client.tick(now=30.0)
        self.assertEqual(second.last_error, "product_central_session_response_oversized")


if __name__ == "__main__":
    unittest.main()
