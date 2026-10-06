from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PRODUCT_HOST = ROOT / "ordax_studio" / "product_web_desktop.py"
HOST_BRIDGE = ROOT / "ordax_studio" / "host_bridge.js"
ACTIONS = ROOT / "ordax_dev_agent" / "actions.py"


class StudioManagedBrowserContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.product_host = PRODUCT_HOST.read_text(encoding="utf-8")
        cls.host_bridge = HOST_BRIDGE.read_text(encoding="utf-8")
        cls.actions = ACTIONS.read_text(encoding="utf-8")

    def test_owner_browser_grant_is_exact_and_project_scoped(self) -> None:
        self.assertIn('_OWNER_PROJECT_BROWSER_GRANT_MODE = "project-browser-automation"', self.product_host)
        self.assertIn("projects=[project]", self.product_host)
        self.assertIn("create_project_capability_grant", self.product_host)

    def test_studio_host_exposes_typed_browser_operations(self) -> None:
        for action in (
            "browser.list",
            "browser.start",
            "browser.status",
            "browser.navigate",
            "browser.snapshot",
            "browser.screenshot",
            "browser.stop",
        ):
            self.assertIn(action, self.product_host)
            self.assertIn(action, self.actions)

    def test_host_bridge_exposes_browser_without_generic_dispatch(self) -> None:
        for method in (
            "remoteProjectBrowserGrants",
            "authorizeRemoteProjectBrowserGrant",
            "revokeRemoteProjectBrowserGrant",
            "browserList",
            "browserStart",
            "browserStatus",
            "browserNavigate",
            "browserSnapshot",
            "browserScreenshot",
            "browserStop",
        ):
            self.assertIn(method, self.host_bridge)
        self.assertNotIn("genericDispatch", self.host_bridge)

    def test_managed_browser_does_not_use_desktop_fallback(self) -> None:
        browser_section = self.product_host.split("def remote_project_browser_grants", 1)[1].split("def blender_prepare", 1)[0]
        for forbidden in (
            "computer.click",
            "computer.hotkey",
            "computer.type",
            "computer.launch_app",
            "chrome.exe",
        ):
            self.assertNotIn(forbidden, browser_section)


if __name__ == "__main__":
    unittest.main()
