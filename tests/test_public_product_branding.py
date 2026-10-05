from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class PublicProductBrandingTests(unittest.TestCase):
    def test_control_plane_surfaces_use_generic_ordax_identity(self) -> None:
        public_pages = (ROOT / "control-plane" / "cloudflare" / "src" / "public_pages.ts").read_text(encoding="utf-8")
        consent = (ROOT / "control-plane" / "cloudflare" / "src" / "oauth_consent.ts").read_text(encoding="utf-8")
        mcp = (ROOT / "control-plane" / "cloudflare" / "src" / "mcp_http.ts").read_text(encoding="utf-8")

        self.assertIn('const PRODUCT_NAME = "ORDAX"', public_pages)
        self.assertIn("Autorizar acesso ao ORDAX", consent)
        self.assertIn('serverInfo: { name: "ORDAX Control Plane", version: "0.4.2" }', mcp)
        self.assertNotIn("ORDAX Studio", public_pages)
        self.assertNotIn("ORDAX Studio", consent)
        self.assertNotIn("Conecte o ChatGPT", consent)
        self.assertNotIn("ORDAX Dev", public_pages)
        self.assertNotIn("ORDAX Dev", consent)

    def test_removed_chat_app_brand_does_not_return_to_canonical_runtime_or_architecture(self) -> None:
        orchestrator = (ROOT / "ordax_core" / "orchestrator.py").read_text(encoding="utf-8")
        architecture = (ROOT / "docs" / "ORDAX_STUDIO_MCP_ARCHITECTURE.md").read_text(encoding="utf-8")

        self.assertNotIn("ORDAX Chat App", orchestrator)
        self.assertNotIn("ORDAX Chat App", architecture)
        self.assertIn("ORDAX Studio", orchestrator)
        self.assertIn("ORDAX Studio", architecture)

    def test_historical_windows_binary_name_remains_explicit_compatibility(self) -> None:
        document = (ROOT / "docs" / "ORDAX_STUDIO_WINDOWS_PRODUCT.md").read_text(encoding="utf-8")
        self.assertIn("ORDAX Studio para Windows", document)
        self.assertIn("ORDAX Dev.exe", document)
        self.assertIn("legado/compatível", document)
        self.assertIn("compatibilidade", document)


if __name__ == "__main__":
    unittest.main()
