from __future__ import annotations

import unittest

import httpx

from ordax_dev_agent.cloudflare_control_plane import CloudflareControlPlane
from ordax_dev_agent.remote_protocol import DeviceAuthorizationError


class ProductPairingTransportTests(unittest.TestCase):
    def test_device_pairing_uses_authenticated_device_http_client(self):
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["method"] = request.method
            seen["path"] = request.url.path
            seen["device_id"] = request.headers.get("X-Ordax-Device-Id")
            seen["device_token"] = request.headers.get("X-Ordax-Device-Token")
            return httpx.Response(201, json={
                "ok": True,
                "pairing": {
                    "pairing_id": "11111111-1111-4111-8111-111111111111",
                    "pairing_secret": "a" * 64,
                    "expires_at": "2026-09-28T12:10:00Z",
                },
            })

        control = CloudflareControlPlane.__new__(CloudflareControlPlane)
        control.base_http_url = "https://control.example.test"
        control.http = httpx.Client(
            transport=httpx.MockTransport(handler),
            headers={
                "X-Ordax-Device-Id": "22222222-2222-4222-8222-222222222222",
                "X-Ordax-Device-Token": "t" * 64,
            },
        )
        try:
            pairing = control.create_product_pairing()
        finally:
            control.http.close()

        self.assertEqual(pairing["pairing_secret"], "a" * 64)
        self.assertEqual(seen["method"], "POST")
        self.assertEqual(seen["path"], "/v3/device/product-pairings")
        self.assertEqual(
            seen["device_id"],
            "22222222-2222-4222-8222-222222222222",
        )
        self.assertEqual(seen["device_token"], "t" * 64)

    def test_device_pairing_fails_closed_on_rejected_device_credentials(self):
        control = CloudflareControlPlane.__new__(CloudflareControlPlane)
        control.base_http_url = "https://control.example.test"
        control.http = httpx.Client(
            transport=httpx.MockTransport(
                lambda _request: httpx.Response(
                    401,
                    json={"ok": False, "error": "invalid_device_token"},
                )
            )
        )
        try:
            with self.assertRaises(DeviceAuthorizationError):
                control.create_product_pairing()
        finally:
            control.http.close()


if __name__ == "__main__":
    unittest.main()
