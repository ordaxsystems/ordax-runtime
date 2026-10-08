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

    def test_only_host_adapter_owns_native_webview_transport(self) -> None:
        self.assertIn("window.chrome?.webview", self.host)
        self.assertIn("window.pywebview?.api", self.host)
        self.assertIn("pywebviewready", self.host)
        self.assertIn("Object.defineProperty(window,'ordaxStudioHost'", self.host)
        for method in (
            "projectsCatalog:", "aiSessionsStatus:", "connectProductAccount:",
            "remoteAppIntelligenceGrants:", "authorizeRemoteAppIntelligenceGrant:",
            "revokeRemoteAppIntelligenceGrant:",
        ):
            self.assertIn(method, self.host)
        for unsafe in ("call:", "execute:", "deviceAgent:"):
            self.assertNotIn(unsafe, self.host)

    def test_native_workbench_allows_every_host_surface_method(self) -> None:
        bridge = (ROOT / "ordax_studio" / "workbench_bridge.py").read_text(encoding="utf-8")
        for method in (
            '"ai_sessions_status"', '"connect_product_account"',
            '"blender_prepare"', '"computer_access_settings"',
            '"remote_app_intelligence_grants"',
            '"authorize_remote_app_intelligence_grant"',
            '"revoke_remote_app_intelligence_grant"',
        ):
            self.assertIn(method, bridge)

    def test_privileged_studio_webview_rejects_foreign_navigation_and_messages(self) -> None:
        native = (ROOT / "native" / "ordax-workbench" / "MainWindow.xaml.cs").read_text(encoding="utf-8")
        self.assertIn('TrustedStudioDocument = "https://ordax.local/studio_product.html"', native)
        self.assertIn("StudioView.CoreWebView2.NavigationStarting +=", native)
        self.assertIn("StudioView.CoreWebView2.NewWindowRequested +=", native)
        self.assertIn("args.Cancel = true", native)
        self.assertIn("args.Handled = true", native)
        self.assertIn("IsTrustedStudioSource(args.Uri)", native)
        self.assertIn("IsTrustedStudioSource(e.Source)", native)
        self.assertIn('uri.UserInfo.Length == 0', native)
        self.assertIn('uri.Query.Length == 0', native)
        self.assertIn('"/studio_product.html"', native)
        self.assertLess(
            native.index("if (!IsTrustedStudioSource(e.Source))"),
            native.index("JsonDocument.Parse(e.WebMessageAsJson)"),
            "untrusted messages must be rejected before parsing or dispatch",
        )
        self.assertIn("!double.IsFinite(viewportWidth)", native)
        self.assertIn("!double.IsFinite(width)", native)

    def test_package_contains_only_host_adapter_not_portable_source(self) -> None:
        project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        package_data = project["tool"]["setuptools"]["package-data"]["ordax_studio"]
        self.assertIn("host_bridge.js", package_data)
        self.assertNotIn("*.html", package_data)
        self.assertNotIn("assets/*.js", package_data)
        self.assertNotIn("assets/*.css", package_data)
        package = ROOT / "ordax_studio"
        self.assertFalse((package / "assets").exists())
        self.assertFalse((package / "studio.html").exists())
        self.assertFalse((package / "studio_product.html").exists())

    @unittest.skipUnless(shutil.which("node"), "node is required for JS syntax validation")
    def test_javascript_host_adapter_parses(self) -> None:
        subprocess.run(
            [shutil.which("node") or "node", "--check", str(ROOT / "ordax_studio" / "host_bridge.js")],
            check=True,
            capture_output=True,
            text=True,
        )


if __name__ == "__main__":
    unittest.main()
