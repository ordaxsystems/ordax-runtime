from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

from ordax_dev_agent.device_setup import SetupError, configure


class ProductEndpointSetupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.state = Path(self.temp.name) / "state"
        self.home = Path(self.temp.name) / "home"
        self.state.mkdir()
        self.home.mkdir()
        self.calls: list[httpx.Request] = []
        self.client = httpx.Client(transport=httpx.MockTransport(self.server))
        self.addCleanup(self.client.close)
        self.acl = patch(
            "ordax_dev_agent.device_setup.private_directory",
            side_effect=lambda p: p.mkdir(exist_ok=True),
        )
        self.acl.start()
        self.addCleanup(self.acl.stop)

    def server(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        body = json.loads(request.content)
        self.assertEqual("enroll", body["operation"])
        self.assertEqual("Bearer product-session", request.headers["Authorization"])
        return httpx.Response(200, json={
            "ok": True,
            "protocol": "cloudflare-v3",
            "device_id": "22222222-2222-4222-8222-222222222222",
        })

    def test_enrollment_uses_product_host_but_persists_operational_control_plane_separately(self):
        result = configure(
            self.state,
            client=self.client,
            binding="a" * 64,
            home=self.home,
            control_plane_url="https://commands.example.test",
            product_mcp_url="https://product.example.test",
            product_access_token="product-session",
        )
        self.assertEqual(1, len(self.calls))
        self.assertEqual("product.example.test", self.calls[0].url.host)
        self.assertEqual("/v3/device/setup", self.calls[0].url.path)
        settings = json.loads((self.state / "agent-settings.json").read_text())
        self.assertEqual("https://commands.example.test", settings["control_plane_url"])
        self.assertEqual("https://product.example.test", settings["product_mcp_url"])
        self.assertEqual("https://commands.example.test", result["control_plane_url"])
        self.assertEqual("https://product.example.test", result["product_mcp_url"])

    def test_invalid_product_endpoint_fails_before_network_or_credential_creation(self):
        with self.assertRaisesRegex(SetupError, "PRODUCT_MCP_URL_INVALID"):
            configure(
                self.state,
                client=self.client,
                binding="a" * 64,
                home=self.home,
                control_plane_url="https://commands.example.test",
                product_mcp_url="http://product.example.test",
                product_access_token="product-session",
            )
        self.assertEqual([], self.calls)
        self.assertFalse((self.state / "device-token.cloudflare-v3.txt").exists())
        self.assertFalse((self.state / "device-token.cloudflare-v3.txt.pending-setup").exists())


if __name__ == "__main__":
    unittest.main()
