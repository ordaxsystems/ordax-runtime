from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]


class StudioComputerAccessHostTests(unittest.TestCase):
    def setUp(self) -> None:
        self.bridge = (ROOT / "ordax_studio" / "workbench_bridge.py").read_text(encoding="utf-8")
        self.product_mcp = (ROOT / "ordax_dev_agent" / "product_mcp.py").read_text(encoding="utf-8")

    def test_policy_editor_requires_local_owner_bridge_and_revision_guard(self) -> None:
        self.assertIn('"computer_access_settings"', self.bridge)
        self.assertIn('"save_computer_access_settings"', self.bridge)
        self.assertIn("expected_revision", self.bridge)

    def test_remote_product_mcp_cannot_mutate_local_policy(self) -> None:
        self.assertNotIn("computer.access_update", self.product_mcp)
        self.assertNotIn("full_access", self.product_mcp)
        self.assertNotIn("save_computer_access_settings", self.product_mcp)


if __name__ == "__main__":
    unittest.main()
