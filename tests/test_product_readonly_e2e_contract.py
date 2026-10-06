from __future__ import annotations

import unittest
from pathlib import Path


class ProductRuntimeDispatchContractTests(unittest.TestCase):
    def setUp(self):
        root = Path(__file__).resolve().parents[1]
        self.main = (root / "ordax_dev_agent/main.py").read_text(encoding="utf-8")

    def test_agent_product_jobs_do_not_execute_directly_in_registry(self):
        self.assertIn("PRODUCT_REMOTE_CAPABILITY", self.main)
        self.assertIn("PRODUCT_REMOTE_LEGACY_CAPABILITY", self.main)
        self.assertIn("execute_product_invocation", self.main)


if __name__ == "__main__":
    unittest.main()
