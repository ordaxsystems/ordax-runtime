from __future__ import annotations
import unittest
from pathlib import Path
class OrdaxStudioInstallTests(unittest.TestCase):
    def test_windows_installer_creates_product_app_over_managed_runtime(self):
        root = Path(__file__).resolve().parents[1]
        script = (root / "scripts" / "windows" / "ordax-studio-install.ps1").read_text(encoding="utf-8")
        self.assertIn("OrdaX Dev Agent", script)
        self.assertIn("ordax-device-agent-setup.ps1", script)
        self.assertIn("pythonw.exe", script)
        self.assertIn("ordax_studio.product_web_desktop", script)
        self.assertIn("ORDAX Dev.lnk", script)
        self.assertIn("GetFolderPath('Programs')", script)
        self.assertNotIn("blendmcp_legacy", script)
if __name__ == "__main__": unittest.main()
