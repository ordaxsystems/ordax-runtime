"""Device setup uses the same pinned Product Worker, without ambiguous replay."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

from ordax_dev_agent.device_setup import SetupError, configure

PRIMARY = "https://mcp.ordax.com.br"
RESTRICTED = "https://ordax-mcp.ordaxsystems.workers.dev"
DEVICE = "11111111-1111-4111-8111-111111111111"


class DeviceSetupTrustedIngressTests(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.TemporaryDirectory()
        self.addCleanup(self.t.cleanup)
        self.state = Path(self.t.name) / "owner-device"
        self.state.mkdir()
        self.seen = []
        guard = patch("ordax_dev_agent.device_setup.private_directory",
                      side_effect=lambda p: p.mkdir(exist_ok=True))
        guard.start()
        self.addCleanup(guard.stop)

    def setup_device(self, handler):
        with httpx.Client(transport=httpx.MockTransport(handler),
                          follow_redirects=False, timeout=5) as client:
            return configure(self.state, client=client, binding="a" * 64,
                             home=Path(self.t.name) / "private-home",
                             product_mcp_url=PRIMARY, product_access_token="owner-token",
                             device_kind="desktop", channel="development",
                             device_name="OrdaX Desktop (test)")

    def test_preconnect_error_uses_official_restricted_worker_exactly_once(self):
        def handler(request):
            self.seen.append((str(request.url), bytes(request.content)))
            if request.url.host == "mcp.ordax.com.br":
                raise httpx.ConnectTimeout("no TCP connection", request=request)
            self.assertEqual(request.url.host, "ordax-mcp.ordaxsystems.workers.dev")
            body = json.loads(request.content)
            self.assertEqual(body["operation"], "enroll")
            self.assertEqual(body["device_name"], "OrdaX Desktop (test)")
            return httpx.Response(200,json={"ok":True,"device_id":DEVICE,"protocol":"cloudflare-v3"})

        result = self.setup_device(handler)
        self.assertTrue(result["ok"])
        self.assertEqual(result["device_id"], DEVICE)
        self.assertEqual([s[0] for s in self.seen],[
            PRIMARY + "/v3/device/setup", RESTRICTED + "/v3/device/setup"])
        self.assertEqual(self.seen[0][1], self.seen[1][1])
        self.assertTrue((self.state / "device-token.cloudflare-v3.txt").is_file())

    def test_ambiguous_read_timeout_never_replays_enrollment(self):
        def handler(request):
            self.seen.append(str(request.url))
            raise httpx.ReadTimeout("may have enrolled already", request=request)

        with self.assertRaisesRegex(SetupError, "CONTROL_PLANE_UNAVAILABLE"):
            self.setup_device(handler)
        self.assertEqual(self.seen,[PRIMARY + "/v3/device/setup"])
        self.assertFalse((self.state / "device-token.cloudflare-v3.txt").exists())
        self.assertTrue((self.state / "device-token.cloudflare-v3.txt.pending-setup").exists())

    def test_http_failure_never_repeats_owner_enrollment(self):
        def handler(request):
            self.seen.append(str(request.url))
            return httpx.Response(503,json={"ok":False})

        with self.assertRaisesRegex(SetupError, "CONTROL_PLANE_UNAVAILABLE"):
            self.setup_device(handler)
        self.assertEqual(self.seen,[PRIMARY + "/v3/device/setup"])

    def test_custom_origin_does_not_switch_to_another_authority(self):
        # Invalid/custom origins must not activate Product failover.
        with httpx.Client(transport=httpx.MockTransport(lambda req:
            (_ for _ in ()).throw(httpx.ConnectTimeout("connect",request=req))),
            follow_redirects=False) as client:
            with self.assertRaisesRegex(SetupError,"CONTROL_PLANE_UNAVAILABLE"):
                configure(self.state, client=client, binding="a"*64,
                          home=Path(self.t.name) / "private-home",
                          product_mcp_url="https://custom.example",
                          product_access_token="owner-token")


if __name__ == "__main__":
    unittest.main()
