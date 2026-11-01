from __future__ import annotations

import json
import unittest

import httpx

from ordax_dev_agent.product_central_session import (
    ProductCentralSessionClient, ProductCentralSessionStatus,
)


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

    def test_central_device_token_never_follows_redirects_with_injected_client(self):
        # The injected HTTP client may enable redirects globally. Both signed
        # device calls MUST override that option per request.
        for redirected_method in ("GET", "POST"):
            with self.subTest(method=redirected_method):
                requests = []
                token = "a" * 64

                def handler(request: httpx.Request) -> httpx.Response:
                    requests.append((request.method, request.url.host,
                                     request.headers.get("X-Ordax-Device-Token")))
                    if request.url.host != "mcp.ordax.com.br":
                        return httpx.Response(200, json={"ok": True})
                    if request.method == "GET" and redirected_method == "POST":
                        return httpx.Response(200, json={
                            "ok": True, "pending": True,
                            "challenge_id": CHALLENGE_ID,
                        })
                    return httpx.Response(
                        307,
                        headers={"Location": "https://attacker.example/steal-device-token"},
                        json={"ok": False, "error": "redirect_not_authorized"},
                    )

                client_http = httpx.Client(
                    transport=httpx.MockTransport(handler),
                    follow_redirects=True,
                )
                self.addCleanup(client_http.close)
                client = ProductCentralSessionClient(
                    "https://mcp.ordax.com.br",
                    DEVICE_ID,
                    lambda: token,
                    http=client_http,
                )
                status = client.tick(now=10.0)
                self.assertFalse(status.confirmed)
                self.assertEqual(status.last_error, "product_central_session_unavailable")
                self.assertEqual(
                    requests,
                    [("GET", "mcp.ordax.com.br", token)]
                    + ([("POST", "mcp.ordax.com.br", token)]
                       if redirected_method == "POST" else []),
                    "No redirected HTTP request may carry the Product Device token",
                )

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

    def test_prior_confirmation_is_observed_after_empty_poll_and_outage(self):
        # Central tickets are short-lived. The next heartbeat often polls an
        # empty queue, which must not erase evidence of the prior confirmation.
        requests = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request.method)
            if request.method == "POST":
                return httpx.Response(200, json={
                    "ok": True, "status": "bound",
                    "device_id": DEVICE_ID, "request_id": CHALLENGE_ID,
                })
            if len(requests) == 1:
                return httpx.Response(200, json={
                    "ok": True, "pending": True, "challenge_id": CHALLENGE_ID,
                })
            if len(requests) == 3:
                return httpx.Response(200, json={"ok": True, "pending": False})
            return httpx.Response(503, json={
                "ok": False, "error": "central_session_unavailable",
            })

        client = self.make_client(["a" * 64], handler)
        initial = client.status.public_observation()
        self.assertEqual(initial, {
            "checked": False, "last_confirmation_observed": False,
            "last_error_code": None,
        })
        confirmed = client.tick(now=10.0)
        self.assertTrue(confirmed.confirmed)
        self.assertTrue(confirmed.public_observation()["last_confirmation_observed"])
        self.assertIsNotNone(confirmed.last_success_at)

        empty = client.tick(now=30.0)
        self.assertFalse(empty.confirmed)
        self.assertIsNone(empty.challenge_id)
        self.assertTrue(empty.public_observation()["last_confirmation_observed"])
        self.assertIsNone(empty.public_observation()["last_error_code"])

        failed = client.tick(now=50.0)
        self.assertFalse(failed.confirmed)
        self.assertEqual(failed.last_error, "product_central_session_unavailable")
        self.assertTrue(failed.public_observation()["last_confirmation_observed"])
        self.assertEqual(requests, ["GET", "POST", "GET", "GET"])

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

    def test_revoked_central_grant_does_not_require_device_reenrollment(self):
        for error_code in ("central_device_grant_revoked", "central_session_revoked"):
            with self.subTest(error_code=error_code):
                calls = []
                def handler(request: httpx.Request) -> httpx.Response:
                    calls.append(request.method)
                    if request.method == "GET":
                        return httpx.Response(200, json={
                            "ok": True, "pending": True, "challenge_id": CHALLENGE_ID,
                        })
                    return httpx.Response(403, json={"ok": False, "error": error_code})
                status = self.make_client(["a" * 64], handler).tick(now=10.0)
                self.assertEqual(calls, ["GET", "POST"])
                self.assertEqual(status.last_error, error_code)
                self.assertFalse(status.confirmed)
                self.assertIsNone(status.challenge_id)
                public = status.public_observation()
                self.assertEqual(public["last_error_code"], error_code)
                self.assertNotIn(CHALLENGE_ID, json.dumps(public))

    def test_nonjson_401_is_device_auth_denial_before_body_parsing(self):
        for failure_method in ("GET", "POST"):
            with self.subTest(failure_method=failure_method):
                def handler(request: httpx.Request) -> httpx.Response:
                    if request.method == "GET" and failure_method == "POST":
                        return httpx.Response(200, json={
                            "ok": True, "pending": True, "challenge_id": CHALLENGE_ID,
                        })
                    return httpx.Response(401, content=b"not valid JSON")
                status = self.make_client(["a" * 64], handler).tick(now=10.0)
                self.assertEqual(status.last_error, "product_identity_reenrollment_required")
                self.assertFalse(status.confirmed)
                self.assertIsNone(status.challenge_id)

    def test_unknown_remote_denials_and_challenge_are_not_exposed(self):
        def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "GET":
                return httpx.Response(200, json={
                    "ok": True, "pending": True, "challenge_id": CHALLENGE_ID,
                })
            return httpx.Response(403, json={
                "ok": False, "error": "SECRET_PRIVATE_AUTHORITY_MESSAGE",
            })
        status = self.make_client(["a" * 64], handler).tick(now=10.0)
        self.assertEqual(status.last_error, "product_central_session_unavailable")
        self.assertIsNone(status.challenge_id)
        observed = ProductCentralSessionStatus(
            True, False, "not-public", "SECRET_PRIVATE_AUTHORITY_MESSAGE", None,
        ).public_observation()
        self.assertEqual(observed["last_error_code"], "product_central_session_unavailable")
        self.assertNotIn("not-public", json.dumps(observed))
        self.assertNotIn("SECRET_PRIVATE_AUTHORITY_MESSAGE", json.dumps(observed))

    def test_malformed_nonstring_revocation_error_fails_closed(self):
        for bad_error in ([], {"private": "not-public"}):
            with self.subTest(error=bad_error):
                def handler(request: httpx.Request) -> httpx.Response:
                    if request.method == "GET":
                        return httpx.Response(200, json={
                            "ok": True, "pending": True, "challenge_id": CHALLENGE_ID,
                        })
                    return httpx.Response(403, json={
                        "ok": False, "error": bad_error,
                    })
                status = self.make_client(["a" * 64], handler).tick(now=10.0)
                self.assertFalse(status.confirmed)
                self.assertEqual(status.last_error, "product_central_session_unavailable")
                self.assertIsNone(status.challenge_id)

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

    def test_default_cadence_reuses_existing_five_second_heartbeat(self):
        token = ["a" * 64]
        calls = 0

        def handler(_request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            return httpx.Response(200, json={"ok": True, "pending": False})

        http = httpx.Client(transport=httpx.MockTransport(handler))
        self.addCleanup(http.close)
        client = ProductCentralSessionClient(
            "https://mcp.ordax.com.br", DEVICE_ID, lambda: token[0], http=http,
        )
        client.tick(now=10.0)
        client.tick(now=14.9)
        self.assertEqual(calls, 1)
        client.tick(now=15.0)
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
