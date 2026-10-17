from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / "scripts" / "windows" / "build-ordax-studio-product.ps1"

class WindowsLauncherVersionProvenanceTests(unittest.TestCase):
    def test_native_launchers_embed_and_verify_canonical_versioninfo(self):
        source = BUILD.read_text(encoding="utf-8")
        for token in (
            "Get-Command rc.exe",
            "Convert-ToWindowsVersionQuad",
            "Write-LauncherVersionResource",
            "VS_VERSION_INFO VERSIONINFO",
            "FileVersion",
            "ProductVersion",
            "$runtimeVersion",
            "$studioVersionInfo.FileVersion -ne $Version",
            "$runtimeVersionInfo.FileVersion -ne $runtimeVersion",
            "ORDAX_RUNTIME_LAUNCHER_VERSION=",
        ):
            self.assertIn(token, source)

    def test_runtime_version_is_derived_from_packaged_distribution(self):
        source = BUILD.read_text(encoding="utf-8")
        version_read = "metadata.version('ordax-runtime')"
        self.assertIn(version_read, source)
        self.assertLess(source.index(version_read), source.index('Write-LauncherVersionResource $runtimeRc $runtimeVersion'))

if __name__ == "__main__":
    unittest.main()