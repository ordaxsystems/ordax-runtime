from pathlib import Path
import unittest

from ordax_dev_agent.config import DEFAULT_PRODUCT_MCP_URL


CANONICAL = "https://mcp.ordax.com.br"
LEGACY = "ordax-control-plane-v3.ordax-ac1ca1b50d09.workers.dev"
CANONICAL_READ_OR_BOOTSTRAP_PATHS = (
    "ordax_dev_agent/config.py",
    "ordax_studio/electron_account_session.py",
    "scripts/product_mcp_smoke.py",
    "scripts/windows/ordax-agent-bootstrap.ps1",
    "scripts/windows/ordax-device-agent-setup.ps1",
    "docs/ORDAX_STUDIO_PRODUCT.md",
)


class CanonicalControlPlaneDefaultsTests(unittest.TestCase):
    def test_agent_has_one_canonical_product_ingress(self):
        self.assertEqual(CANONICAL, DEFAULT_PRODUCT_MCP_URL)
        config = Path("ordax_dev_agent/config.py").read_text(encoding="utf-8")
        self.assertNotIn("DEFAULT_CONTROL_PLANE_URL", config)

    def test_read_and_bootstrap_paths_cannot_regress_to_legacy_workers_dev(self):
        for raw_path in CANONICAL_READ_OR_BOOTSTRAP_PATHS:
            source = Path(raw_path).read_text(encoding="utf-8-sig")
            with self.subTest(path=raw_path):
                self.assertNotIn(LEGACY, source)
                self.assertIn("mcp.ordax.com.br", source)


if __name__ == "__main__":
    unittest.main()
