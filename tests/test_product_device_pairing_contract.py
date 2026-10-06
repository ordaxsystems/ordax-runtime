from __future__ import annotations

import unittest
from pathlib import Path


class ProductPairingLocalContractTests(unittest.TestCase):
    def setUp(self) -> None:
        root = Path(__file__).resolve().parents[1]
        self.mcp = (
            root / "ordax_dev_agent" / "product_mcp_server.py"
        ).read_text(encoding="utf-8")
        self.cli = (
            root / "ordax_dev_agent" / "product_pair_cli.py"
        ).read_text(encoding="utf-8")

    def test_pairing_secret_is_not_exposed_as_mcp_tool(self) -> None:
        self.assertNotIn("pairing_secret", self.mcp)
        self.assertNotIn("claim_device_pairing", self.mcp)

    def test_pairing_cli_does_not_persist_secret_or_credentials(self) -> None:
        self.assertNotIn("write_text", self.cli)
        self.assertNotIn("open(", self.cli)
        self.assertNotIn("access_token", self.cli)


if __name__ == "__main__":
    unittest.main()
