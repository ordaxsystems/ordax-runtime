from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class WindowsProductInstallRootContractTests(unittest.TestCase):
    def test_installer_updater_and_docs_share_canonical_install_root(self):
        installer = (ROOT / "packaging/windows/ordax-studio.iss").read_text(encoding="utf-8")
        updater = (ROOT / "scripts/windows/apply-ordax-production-update.ps1").read_text(encoding="utf-8")
        docs = (ROOT / "docs/ORDAX_STUDIO_WINDOWS_PRODUCT.md").read_text(encoding="utf-8")

        self.assertIn(r"DefaultDirName={localappdata}\Programs\ORDAX", installer)
        self.assertIn("UsePreviousAppDir=no", installer)
        self.assertIn(r"Programs\ORDAX", updater)
        self.assertIn(r"%LOCALAPPDATA%\Programs\ORDAX", docs)
        self.assertNotIn(r"`Programs\ORDAX Studio`", docs)


if __name__ == "__main__":
    unittest.main()
