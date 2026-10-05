from __future__ import annotations

import tomllib
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class HeadlessRuntimeProfileTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        cls.windows_build = (
            ROOT / "scripts" / "windows" / "build-ordax-studio-product.ps1"
        ).read_text(encoding="utf-8")
        cls.agent_main = (ROOT / "ordax_dev_agent" / "main.py").read_text(encoding="utf-8")
        cls.compat_main = (ROOT / "ordax_device_agent" / "main.py").read_text(encoding="utf-8")

    def test_headless_base_does_not_require_pywebview(self) -> None:
        base = self.project["project"]["dependencies"]
        self.assertFalse(any(item.lower().startswith("pywebview") for item in base))

    def test_desktop_extra_owns_pywebview(self) -> None:
        desktop = self.project["project"]["optional-dependencies"]["desktop"]
        self.assertTrue(any(item.lower().startswith("pywebview") for item in desktop))

    def test_device_agent_entrypoints_remain_headless(self) -> None:
        for source in (self.agent_main, self.compat_main):
            self.assertNotIn("import webview", source)
            self.assertNotIn("from webview", source)
            self.assertNotIn("pywebview", source.lower())

    def test_windows_product_explicitly_installs_desktop_extra(self) -> None:
        self.assertIn("$desktopPackage = ('{0}[desktop]' -f $repoRoot)", self.windows_build)
        self.assertIn("--target $sitePackages $desktopPackage", self.windows_build)
        self.assertIn(
            "import ordax_studio, ordax_dev_agent, ordax_device_agent, webview",
            self.windows_build,
        )


if __name__ == "__main__":
    unittest.main()
