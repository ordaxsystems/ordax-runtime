from __future__ import annotations

import shutil
import subprocess
import tomllib
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class StudioHostTransportBoundaryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.host = (ROOT / "ordax_studio" / "host_bridge.js").read_text(encoding="utf-8")
        self.html = (ROOT / "ordax_studio" / "studio_product.html").read_text(encoding="utf-8")
        self.legacy_html = (ROOT / "ordax_studio" / "studio.html").read_text(encoding="utf-8")
        self.portable = {
            path.name: path.read_text(encoding="utf-8")
            for path in (
                ROOT / "ordax_studio" / "assets" / "studio.js",
                ROOT / "ordax_studio" / "assets" / "computer_access.js",
                ROOT / "ordax_studio" / "assets" / "blender-connection.js",
                ROOT / "ordax_studio" / "assets" / "product_account.js",
            )
        }

    def test_only_host_adapter_knows_webview_transport(self) -> None:
        self.assertIn("window.chrome?.webview", self.host)
        self.assertIn("window.pywebview?.api", self.host)
        self.assertIn("pywebviewready", self.host)
        for name, source in self.portable.items():
            with self.subTest(name=name):
                self.assertNotIn("window.chrome", source)
                self.assertNotIn("window.pywebview", source)
                self.assertNotIn("pywebviewready", source)

    def test_host_adapter_is_explicit_and_loaded_before_portable_surface(self) -> None:
        for name, html in (("product", self.html), ("legacy", self.legacy_html)):
            with self.subTest(name=name):
                self.assertLess(
                    html.index('src="host_bridge.js"'),
                    html.index('src="assets/studio.js"'),
                )
        self.assertIn("Object.defineProperty(window,'ordaxStudioHost'", self.host)
        self.assertIn("projectsCatalog:", self.host)
        self.assertIn("aiSessionsStatus:", self.host)
        self.assertIn("connectProductAccount:", self.host)
        self.assertIn("remoteAppIntelligenceGrants:", self.host)
        self.assertIn("authorizeRemoteAppIntelligenceGrant:", self.host)
        self.assertIn("revokeRemoteAppIntelligenceGrant:", self.host)
        self.assertNotIn("call:", self.host)
        self.assertNotIn("execute:", self.host)
        self.assertNotIn("deviceAgent:", self.host)

    def test_native_workbench_allows_every_surface_method_added_by_boundary(self) -> None:
        bridge = (ROOT / "ordax_studio" / "workbench_bridge.py").read_text(encoding="utf-8")
        self.assertIn('"ai_sessions_status"', bridge)
        self.assertIn('"connect_product_account"', bridge)
        self.assertIn('"blender_prepare"', bridge)
        self.assertIn('"computer_access_settings"', bridge)
        self.assertIn('"remote_app_intelligence_grants"', bridge)
        self.assertIn('"authorize_remote_app_intelligence_grant"', bridge)
        self.assertIn('"revoke_remote_app_intelligence_grant"', bridge)

    def test_host_bridge_is_in_python_package_data(self) -> None:
        project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        package_data = project["tool"]["setuptools"]["package-data"]["ordax_studio"]
        self.assertIn("*.js", package_data)
        self.assertIn("assets/*.js", package_data)

    @unittest.skipUnless(shutil.which("node"), "node is required for JavaScript syntax validation")
    def test_javascript_boundary_files_parse(self) -> None:
        paths = [
            ROOT / "ordax_studio" / "host_bridge.js",
            *(ROOT / "ordax_studio" / "assets" / name for name in self.portable),
        ]
        for path in paths:
            with self.subTest(path=path.name):
                subprocess.run(
                    [shutil.which("node") or "node", "--check", str(path)],
                    check=True,
                    capture_output=True,
                    text=True,
                )


if __name__ == "__main__":
    unittest.main()
