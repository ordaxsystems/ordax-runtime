from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]


class StudioComputerAccessSurfaceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.product_html = (ROOT / "ordax_studio" / "studio_product.html").read_text(encoding="utf-8")
        self.dev_html = (ROOT / "ordax_studio" / "studio.html").read_text(encoding="utf-8")
        self.studio_js = (ROOT / "ordax_studio" / "assets" / "studio.js").read_text(encoding="utf-8")
        self.access_js = (ROOT / "ordax_studio" / "assets" / "computer_access.js").read_text(encoding="utf-8")
        self.bridge = (ROOT / "ordax_studio" / "workbench_bridge.py").read_text(encoding="utf-8")
        self.product_mcp = (ROOT / "ordax_dev_agent" / "product_mcp.py").read_text(encoding="utf-8")

    def test_shared_surface_has_first_class_computer_access_view(self) -> None:
        for html in (self.product_html, self.dev_html):
            self.assertIn('data-view="computer"', html)
            self.assertIn('id="computerCanvas"', html)
            self.assertIn('assets/computer_access.css', html)
            self.assertIn('assets/computer_access.js', html)
        self.assertIn("computer:'Acesso ao computador'", self.studio_js)
        self.assertIn("view==='computer'", self.studio_js)
        self.assertIn("await loadComputerAccess()", self.studio_js)

    def test_policy_editor_is_local_owner_control_with_revision_guard(self) -> None:
        self.assertIn("computer_access_settings", self.access_js)
        self.assertIn("save_computer_access_settings", self.access_js)
        self.assertIn("expected_revision", self.access_js)
        self.assertIn("full_access", self.access_js)
        self.assertIn("Full Access", self.access_js)
        self.assertIn("window.confirm", self.access_js)
        self.assertIn("O cliente remoto não pode ativar este modo", self.access_js)
        self.assertIn("Windows/UAC", self.access_js)
        self.assertIn('"computer_access_settings"', self.bridge)
        self.assertIn('"save_computer_access_settings"', self.bridge)

    def test_remote_product_mcp_cannot_mutate_local_policy(self) -> None:
        self.assertNotIn("computer.access_update", self.product_mcp)
        self.assertNotIn("full_access", self.product_mcp)
        self.assertNotIn("save_computer_access_settings", self.product_mcp)


if __name__ == "__main__":
    unittest.main()
