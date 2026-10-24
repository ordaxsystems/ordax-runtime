import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch

from ordax_dev_agent.config import DEFAULT_PRODUCT_MCP_URL
from ordax_dev_agent import product_mcp_handlers


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
        self.assertNotIn('settings.get("control_plane_url")', config)
        self.assertNotIn('settings["control_plane_url"]', config)

    def test_product_mcp_uses_shared_default_and_keeps_explicit_override(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(CANONICAL, product_mcp_handlers._base_url())
        with patch.dict(
            os.environ,
            {"ORDAX_PRODUCT_CONTROL_PLANE_URL": "https://control.example.test"},
            clear=True,
        ):
            self.assertEqual(
                "https://control.example.test",
                product_mcp_handlers._base_url(),
            )

    def test_product_mcp_example_does_not_pin_a_separate_ingress(self):
        example = json.loads(
            Path("config/product-mcp.example.json").read_text(encoding="utf-8")
        )
        env = example["mcpServers"]["ordax-studio"]["env"]
        self.assertNotIn("ORDAX_PRODUCT_CONTROL_PLANE_URL", env)
        self.assertIn("ORDAX_PRODUCT_ACCESS_TOKEN", env)

    def test_read_and_bootstrap_paths_cannot_regress_to_legacy_workers_dev(self):
        for raw_path in CANONICAL_READ_OR_BOOTSTRAP_PATHS:
            source = Path(raw_path).read_text(encoding="utf-8-sig")
            with self.subTest(path=raw_path):
                self.assertNotIn(LEGACY, source)
                self.assertIn("mcp.ordax.com.br", source)


if __name__ == "__main__":
    unittest.main()
