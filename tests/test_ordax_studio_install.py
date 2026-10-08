from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class OrdaxStudioSingleInstallerTests(unittest.TestCase):
    def test_legacy_python_git_clone_installer_is_retired(self) -> None:
        self.assertFalse((ROOT / "scripts" / "windows" / "ordax-studio-install.ps1").exists())

    def test_windows_distribution_uses_only_canonical_inno_product(self) -> None:
        installer = (ROOT / "packaging" / "windows" / "ordax-studio.iss").read_text(encoding="utf-8")
        build = (ROOT / "scripts" / "windows" / "build-ordax-studio-product.ps1").read_text(encoding="utf-8")
        self.assertIn("AppId={{0D31F22D-8451-4CF4-9E34-F0D4D857F55F}", installer)
        self.assertIn("#define AppName \"ORDAX Studio\"", installer)
        self.assertIn("ORDAX_STUDIO_APP_SOURCE is required", build)
        self.assertIn("ORDAX_STUDIO_PACKAGE_SOURCE_VERIFIED", build)
        self.assertNotIn("Join-Path $repoRoot \"ordax_studio\\assets\"", build)

    def test_install_shortcut_launcher_uses_canonical_registry_not_git_or_python(self) -> None:
        script = (ROOT / "scripts" / "windows" / "ordax-studio-start.ps1").read_text(encoding="utf-8")
        self.assertIn("$product.InstallLocation", script)
        self.assertIn('Join-Path $installLocation "ORDAX Studio.exe"', script)
        self.assertNotIn("ordax_studio.desktop", script)
        self.assertNotIn("ordax_studio.product_web_desktop", script)
        self.assertNotIn("pythonw.exe", script)
        self.assertNotIn("ORDAX Dev.lnk", script)


if __name__ == "__main__":
    unittest.main()
