from pathlib import Path
import unittest

from ordax_dev_agent.config import DEFAULT_CONTROL_PLANE_URL, DEFAULT_PRODUCT_MCP_URL


CANONICAL = "https://mcp.ordax.com.br"
LEGACY = "ordax-control-plane-v3.ordax-ac1ca1b50d09.workers.dev"


class CanonicalControlPlaneDefaultsTests(unittest.TestCase):
    def test_agent_defaults_share_the_canonical_product_ingress(self):
        self.assertEqual(CANONICAL, DEFAULT_CONTROL_PLANE_URL)
        self.assertEqual(CANONICAL, DEFAULT_PRODUCT_MCP_URL)

    def test_agent_config_cannot_regress_to_legacy_workers_dev(self):
        source = Path("ordax_dev_agent/config.py").read_text(encoding="utf-8")
        self.assertNotIn(LEGACY, source)


if __name__ == "__main__":
    unittest.main()
