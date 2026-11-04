"""No Studio ownership of the canonical OrdaX MCP console command."""
from pathlib import Path
import sys
import tomllib
import unittest

ROOT = Path(__file__).resolve().parents[1]
# The packaged Windows Python excludes cwd from import paths: test the checkout.
sys.path.insert(0, str(ROOT))


class CanonicalMcpEntrypointTests(unittest.TestCase):
    def test_ordax_mcp_is_product_server_not_studio(self):
        project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        scripts = project["project"]["scripts"]
        self.assertEqual(
            scripts["ordax-mcp"],
            "ordax_dev_agent.product_mcp_server:main",
        )
        self.assertNotIn("ordax_studio", scripts["ordax-mcp"])
        self.assertNotIn("ordax-product-mcp", scripts)
        self.assertNotIn("ordax-studio-remote-mcp", scripts)

    def test_canonical_remote_server_identifies_as_ordax_mcp(self):
        from ordax_dev_agent.product_mcp_server import mcp
        self.assertEqual(mcp.name, "ordax-mcp")


if __name__ == "__main__":
    unittest.main()
