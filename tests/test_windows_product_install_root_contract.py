from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class WindowsProductInstallRootContractTests(unittest.TestCase):
    def test_required_windows_workflow_does_not_filter_pull_requests(self):
        workflow = (ROOT / ".github/workflows/windows-product-build.yml").read_text(encoding="utf-8")
        start = workflow.index("  pull_request:")
        end = workflow.index("  workflow_dispatch:", start)
        self.assertNotIn("paths:", workflow[start:end])
        self.assertIn("  pull_request:\n", workflow[start:end])

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
