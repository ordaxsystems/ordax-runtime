from __future__ import annotations

import json
import unittest
from pathlib import Path


class ProductMcpConnectivityContractTests(unittest.TestCase):
    def setUp(self):
        root = Path(__file__).resolve().parents[1]
        self.config = json.loads(
            (root / "config" / "product-mcp.example.json").read_text(encoding="utf-8")
        )
        self.smoke = (root / "scripts" / "product_mcp_smoke.py").read_text(encoding="utf-8")
        self.env = (root / ".env.example").read_text(encoding="utf-8")

    def test_example_uses_product_entrypoint_without_real_secret(self):
        server = self.config["mcpServers"]["ordax-studio"]
        self.assertEqual(server["command"], "ordax-product-mcp")
        token = server["env"]["ORDAX_PRODUCT_ACCESS_TOKEN"]
        self.assertIn("provide-at-runtime", token)
        self.assertNotIn("eyJ", token)

    def test_env_example_keeps_product_token_blank(self):
        line = next(
            item for item in self.env.splitlines()
            if item.startswith("ORDAX_PRODUCT_ACCESS_TOKEN=")
        )
        self.assertEqual(line, "ORDAX_PRODUCT_ACCESS_TOKEN=")

    def test_smoke_is_identity_and_target_discovery_only(self):
        self.assertIn("client.session(token)", self.smoke)
        self.assertIn("client.targets(token)", self.smoke)
        self.assertNotIn("submit_action", self.smoke)
        self.assertNotIn("wait_action", self.smoke)
        self.assertNotIn("/v3/product/actions", self.smoke)


if __name__ == "__main__":
    unittest.main()
