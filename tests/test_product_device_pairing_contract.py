from __future__ import annotations

import unittest
from pathlib import Path


class ProductDevicePairingContractTests(unittest.TestCase):
    def setUp(self):
        root = Path(__file__).resolve().parents[1]
        self.worker = (
            root / "control-plane" / "cloudflare" / "src" / "index.ts"
        ).read_text(encoding="utf-8")
        self.migration = (
            root
            / "control-plane"
            / "cloudflare"
            / "migrations"
            / "0007_product_device_pairing.sql"
        ).read_text(encoding="utf-8")
        self.mcp = (
            root / "ordax_dev_agent" / "product_mcp_server.py"
        ).read_text(encoding="utf-8")
        self.cli = (
            root / "ordax_dev_agent" / "product_pair_cli.py"
        ).read_text(encoding="utf-8")

    def test_pairing_is_device_initiated_and_product_claimed(self):
        self.assertIn('"/v3/device/product-pairings"', self.worker)
        self.assertIn("authenticateDevice(env, deviceId, token)", self.worker)
        self.assertIn('"/v3/product/device-links"', self.worker)
        self.assertIn("authenticateProductRequest(request, env)", self.worker)
        self.assertIn("10 * 60 * 1000", self.worker)
        self.assertIn("randomHex(32)", self.worker)

    def test_pairing_persistence_is_separate_from_grants(self):
        self.assertIn("ordax_product_device_pairings", self.migration)
        self.assertIn("ordax_product_device_links", self.migration)
        self.assertIn("secret_sha256", self.migration)
        self.assertNotIn("ordax_product_grants", self.migration)

    def test_claim_does_not_create_product_grant(self):
        start = self.worker.index("async function claimProductDevicePairing")
        end = self.worker.index("async function listProductDeviceLinks", start)
        claim_body = self.worker[start:end]
        self.assertIn("ordax_product_device_links", claim_body)
        self.assertNotIn("ordax_product_grants", claim_body)

    def test_pairing_secret_is_not_exposed_as_mcp_tool(self):
        self.assertNotIn("pairing_secret", self.mcp)
        self.assertNotIn("claim_device_pairing", self.mcp)

    def test_pairing_secret_is_one_time_and_cli_does_not_persist_it(self):
        start = self.worker.index("async function claimProductDevicePairing")
        end = self.worker.index("async function listProductDeviceLinks", start)
        claim_body = self.worker[start:end]
        self.assertIn("product_pairing_already_claimed", claim_body)
        self.assertIn("replayed: true", claim_body)
        self.assertNotIn("write_text", self.cli)
        self.assertNotIn("open(", self.cli)


if __name__ == "__main__":
    unittest.main()
