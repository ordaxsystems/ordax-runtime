from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / "scripts" / "windows" / "build-ordax-studio-product.ps1"
PROBE = ROOT / "scripts" / "windows" / "assert-runtime-package-capabilities.ps1"


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

    def test_lifecycle_probe_keeps_agent_and_distribution_version_authorities_distinct(self):
        source = PROBE.read_text(encoding="utf-8")
        self.assertIn('metadata.version("ordax-runtime")', source)
        self.assertIn("ORDAX_DEVICE_AGENT_VERSION=", source)
        self.assertIn("ORDAX_RUNTIME_PACKAGE_VERSION=", source)
        self.assertIn("transport.DEVICE_AGENT_VERSION != ordax_dev_agent.__version__", source)
        self.assertNotIn("ORDAX_RUNTIME_PACKAGE_VERSION={ordax_dev_agent.__version__", source)
        for token in (
            "$manifest.runtime_version -ne $runtimePackageVersion",
            "$versionInfo.FileVersion -ne $runtimePackageVersion",
            "$versionInfo.ProductVersion -ne $runtimePackageVersion",
        ):
            self.assertIn(token, source)


if __name__ == "__main__":
    unittest.main()
