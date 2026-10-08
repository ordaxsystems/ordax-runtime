"""Guard the canonical Windows product's UTF-8 + visible-window smoke.

Does not execute installer or mutate user machine. Real E2E installation
remains owned by test-ordax-studio-install-smoke.ps1 on Windows CI.
"""
from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / "scripts" / "windows" / "build-ordax-studio-product.ps1"
SMOKE = ROOT / "scripts" / "windows" / "test-ordax-studio-install-smoke.ps1"
UX_PROBE = ROOT / "scripts" / "windows" / "assert-installed-studio-ux.ps1"


class StudioInstalledUiContractTests(unittest.TestCase):
    def test_source_files_are_loaded_explicitly_in_utf8(self) -> None:
        source = BUILD.read_text(encoding="utf-8")
        for path in (
            "$sourceLockPath",
            "$studioAppManifestPath",
            "$studioAiManifestPath",
            "$studioActionManifestPath",
            '(Join-Path $studioAppSource "src\\index.html")',
        ):
            with self.subTest(path=path):
                self.assertIn("Get-Content " + path + " -Raw -Encoding UTF8", source)
        self.assertIn("[System.Text.UTF8Encoding]::new($false)", source)
        self.assertIn("Copy-Item (Join-Path $studioAppSource \"assets\\*\")", source)

    def test_installer_smoke_requires_real_window_and_installed_bytes(self) -> None:
        source = SMOKE.read_text(encoding="utf-8")
        self.assertIn('assert-installed-studio-ux.ps1', source)
        self.assertIn("-WorkbenchProcessId $workbench.Id", source)
        self.assertLess(
            source.index('assert-installed-studio-ux.ps1'),
            source.index('Write-Host "ORDAX_WORKBENCH_READY"'),
        )
        self.assertIn("Stop-Process -Id $studio.Id", source)

    def test_probe_is_read_only_and_fail_closed(self) -> None:
        source = UX_PROBE.read_text(encoding="utf-8")
        for required in (
            "EnumWindows", "GetWindowThreadProcessId", "IsWindowVisible",
            "IsHungAppWindow", "studio_product.html", "UTF8Encoding",
            "DecoderFallbackException", "Double-decoded text found",
            "ORDAX_STUDIO_VISIBLE_WINDOW_OK", "ORDAX_STUDIO_UTF8_ASSETS_OK",
        ):
            with self.subTest(symbol=required):
                self.assertIn(required, source)
        self.assertNotIn("Stop-Process", source)
        self.assertNotIn("Remove-Item", source)
        self.assertNotIn("Set-Content", source)
        self.assertNotIn("Start-Process", source)


if __name__ == "__main__":
    unittest.main()
