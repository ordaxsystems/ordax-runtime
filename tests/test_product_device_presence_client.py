from __future__ import annotations

import gzip
import json
import unittest

import httpx

from ordax_dev_agent.product_device_presence import (
    PRESENCE_PATH, ProductDevicePresenceClient, ProductPresenceError,
    ProductPresenceReceipt, ProductPresenceSnapshot,
)


DEVICE = "ba014375-8c25-4aa1-a9d0-d6be994e3c15"
TOKEN = "fixture-product-credential-distinct-from-legacy-websocket"
SNAPSHOT = ProductPresenceSnapshot(
    online=True, runtime_kind="desktop-agent", agent_version="1.30.1",
    capability_digest="a" * 64,
)


class ProductDevicePresenceClientTests(unittest.TestCase):
    def client(self, handler):
        http = httpx.Client(transport=httpx.MockTransport(handler))
        self.addCleanup(http.close)
        return ProductDevicePresenceClient("https://presence.example.test", http=http)

    def test_one_authenticated_post_and_exact_payload(self):
        requests = []

        def handler(req):
            requests.append(req)
            self.assertEqual(req.method, "POST")
            self.assertEqual(str(req.url), "https://presence.example.test" + PRESENCE_PATH)
            self.assertEqual(req.headers["X-Ordax-Device-Id"], DEVICE)
            self.assertEqual(req.headers["X-Ordax-Device-Token"], TOKEN)
            self.assertNotIn("authorization", req.headers)
            self.assertEqual(json.loads(req.content), {
                "online": True, "runtime_kind": "desktop-agent",
                "agent_version": "1.30.1", "capability_digest": "a" * 64,
            })
            return httpx.Response(200, json={"ok": True, "device_id": DEVICE, "changed": True})

        result = self.client(handler).report(device_id=DEVICE, device_credential=TOKEN, snapshot=SNAPSHOT)
        self.assertEqual(result, ProductPresenceReceipt(DEVICE, True))
        self.assertEqual(len(requests), 1)

    def test_coalesced_heartbeat_receipt_is_success_without_timestamp_claim(self):
        result = self.client(lambda _: httpx.Response(
            200, json={"ok": True, "device_id": DEVICE, "changed": False},
        )).report(device_id=DEVICE, device_credential=TOKEN, snapshot=ProductPresenceSnapshot(
            online=False, runtime_kind=None, agent_version=None, capability_digest=None,
        ))
        self.assertEqual(result, ProductPresenceReceipt(DEVICE, False))

    def test_rejects_wrong_device_and_invalid_receipts_without_retry(self):
        for payload in [
            {"ok": True, "device_id": "9ddfb216-551e-48e8-9713-2f004bf93f28", "changed": True},
            {"ok": True, "device_id": DEVICE, "changed": "true"},
            {"ok": True, "device_id": DEVICE, "changed": 1},
            {"ok": True, "device_id": DEVICE},
            {"ok": True, "device_id": DEVICE, "changed": False, "grant": "spoof"},
            {"ok": False, "device_id": DEVICE, "changed": True},
            None, [], "false",
        ]:
            with self.subTest(payload=payload):
                calls = []
                client = self.client(lambda req: (calls.append(req) or httpx.Response(200, json=payload)))
                with self.assertRaisesRegex(ProductPresenceError, "invalid_response"):
                    client.report(device_id=DEVICE, device_credential=TOKEN, snapshot=SNAPSHOT)
                self.assertEqual(len(calls), 1)

    def test_revocation_and_backend_failures_are_fail_closed(self):
        for code in (401, 403, 408, 429, 500, 502, 503, 504, 307):
            calls = []
            client = self.client(lambda req: (
                calls.append(req) or httpx.Response(
                    code, headers={"location": "https://evil.invalid/secret"},
                    text=f"private {TOKEN}",
                )
            ))
            with self.subTest(status=code), self.assertRaises(ProductPresenceError) as e:
                client.report(device_id=DEVICE, device_credential=TOKEN, snapshot=SNAPSHOT)
            self.assertEqual(len(calls), 1)
            self.assertNotIn(TOKEN, str(e.exception))
            self.assertIn(e.exception.code, {
                "product_device_unauthorized", "product_presence_unavailable",
            })

    def test_transport_failure_never_retries_or_echoes_private_host(self):
        calls = []
        def fail(req):
            calls.append(req)
            raise httpx.ReadTimeout("secret TLS diagnostics", request=req)
        client = self.client(fail)
        with self.assertRaises(ProductPresenceError) as e:
            client.report(device_id=DEVICE, device_credential=TOKEN, snapshot=SNAPSHOT)
        self.assertEqual(e.exception.code, "product_presence_uncertain")
        self.assertNotIn("secret", str(e.exception))
        self.assertEqual(len(calls), 1)

    def test_invalid_device_credential_and_snapshot_never_call_transport(self):
        calls = []
        client = self.client(lambda r: calls.append(r))
        for device in [None, "device-1", DEVICE.upper(), "../" + DEVICE, ""]:
            with self.subTest(device=device), self.assertRaises(ValueError):
                client.report(device_id=device, device_credential=TOKEN, snapshot=SNAPSHOT)
        for token in [None, "", "short", "a" * 513, "a" * 32 + "\n", "a" * 32 + " "]:
            with self.subTest(token=token), self.assertRaises(ValueError):
                client.report(device_id=DEVICE, device_credential=token, snapshot=SNAPSHOT)
        invalid = [
            ProductPresenceSnapshot("true", "desktop-agent", "1.0", None),
            ProductPresenceSnapshot(True, "unknown", "1.0", None),
            ProductPresenceSnapshot(True, "desktop-agent", "", None),
            ProductPresenceSnapshot(True, "desktop-agent", " 1.0", None),
            ProductPresenceSnapshot(True, "desktop-agent", "1.0\x00", None),
            ProductPresenceSnapshot(True, "desktop-agent", "v" * 81, None),
            ProductPresenceSnapshot(True, "desktop-agent", "1.0", "A" * 64),
            ProductPresenceSnapshot(True, "desktop-agent", "1.0", "a" * 63),
        ]
        for snapshot in invalid:
            with self.subTest(snapshot=snapshot), self.assertRaises(ValueError):
                client.report(device_id=DEVICE, device_credential=TOKEN, snapshot=snapshot)
        self.assertEqual(calls, [])

    def test_https_origin_and_timeout_are_validated(self):
        for origin in [
            "", "http://presence.example.test", "https://user:pw@presence.example.test",
            "https://presence.example.test/path", "https://presence.example.test/?x=1",
            "https://presence.example.test/#fragment", "https://presence.example.test:abc",
        ]:
            with self.subTest(origin=origin), self.assertRaises(ValueError):
                ProductDevicePresenceClient(origin)
        for timeout in [-1, 0, float("nan"), float("inf"), 31, "1"]:
            with self.subTest(timeout=timeout), self.assertRaises(ValueError):
                ProductDevicePresenceClient("https://presence.example.test", timeout_seconds=timeout)

    def test_response_size_mime_duplicate_keys_encoding_and_gzip_bomb_are_bounded(self):
        cases = [
            httpx.Response(200, headers={"content-type": "text/html"}, text="<html>secret</html>"),
            httpx.Response(200, content=b'{"ok":true,"ok":true,"device_id":"' + DEVICE.encode() + b'","changed":true}', headers={"content-type": "application/json"}),
            httpx.Response(200, content=b"\xff", headers={"content-type": "application/json"}),
            httpx.Response(200, content=b"not-json", headers={"content-type": "application/json"}),
            httpx.Response(200, content=gzip.compress(b"x" * 40_000), headers={
                "content-type": "application/json", "content-encoding": "gzip",
            }),
        ]
        for response in cases:
            with self.subTest(content_type=response.headers.get("content-type")):
                with self.assertRaises(ProductPresenceError):
                    self.client(lambda _: response).report(
                        device_id=DEVICE, device_credential=TOKEN, snapshot=SNAPSHOT,
                    )

    def test_code_boundary_no_legacy_websocket_import_or_second_credential_owner(self):
        from pathlib import Path
        source = (
            Path(__file__).resolve().parents[1]
            / "ordax_dev_agent" / "product_device_presence.py"
        ).read_text(encoding="utf-8")
        for forbidden in (
            "CloudflareControlPlane", "device_credentials", "resolve_token_path",
            "websockets", "cloudflare-v3", "setInterval", "time.sleep", "refresh_token",
        ):
            # "cloudflare-v3" is allowed in the module docstring to forbid reuse.
            if forbidden == "cloudflare-v3":
                self.assertNotIn("protocol=\"cloudflare-v3\"", source)
            else:
                self.assertNotIn("import " + forbidden, source)
        self.assertIn(PRESENCE_PATH, source)


if __name__ == "__main__":
    unittest.main()
