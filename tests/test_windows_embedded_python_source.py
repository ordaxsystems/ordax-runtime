"""Canonical Windows embedded-Python provenance for BOTH package owners.

This is a source contract, not a replacement for hashing the binary at build time.
"""
from __future__ import annotations

import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOCK = ROOT / "packaging/windows/embedded-python.lock.json"
VERIFY = ROOT / "scripts/windows/get-locked-embedded-python.ps1"
STUDIO = ROOT / "scripts/windows/build-ordax-studio-product.ps1"
MCP = ROOT / "scripts/windows/build-ordax-mcp.ps1"


class EmbeddedPythonSourceTests(unittest.TestCase):
    def test_official_immutable_expected_archive(self):
        data = json.loads(LOCK.read_text(encoding="utf-8"))
        self.assertEqual(data["schema"], "ordax.windows-embedded-python-lock/1")
        self.assertEqual(data["architecture"], "amd64")
        self.assertEqual(data["version"], "3.12.10")
        name = f'python-{data["version"]}-embeddable-amd64.zip'
        base = f'https://www.python.org/ftp/python/{data["version"]}/'
        self.assertEqual(data["filename"], name)
        self.assertEqual(data["url"], base + name)
        self.assertEqual(data["source_manifest"], base + f'windows-{data["version"]}.json')
        self.assertEqual(len(data["sha256"]), 64)
        self.assertTrue(all(char in "0123456789abcdef" for char in data["sha256"]))

    def test_builds_share_single_verify_before_extract(self):
        common = VERIFY.read_text(encoding="utf-8")
        self.assertIn("Get-FileHash -LiteralPath $Path -Algorithm SHA256", common)
        self.assertIn("ORDAX_EMBEDDED_PYTHON_SHA256_MISMATCH", common)
        self.assertIn("Assert-ArchiveHash $archive", common)
        self.assertIn("Assert-ArchiveHash $temporary", common)
        self.assertIn("Move-Item -LiteralPath $temporary -Destination $archive", common)
        self.assertIn("MaximumRedirection 0", common)
        for path in (STUDIO, MCP):
            source = path.read_text(encoding="utf-8")
            self.assertIn("get-locked-embedded-python.ps1", source)
            self.assertIn("Expand-Archive -LiteralPath", source)
            self.assertLess(source.index("get-locked-embedded-python.ps1"),
                            source.index("Expand-Archive -LiteralPath"))
            self.assertNotIn("Invoke-WebRequest -Uri", source)
            self.assertNotIn("python-$PythonVersion-embed-amd64.zip", source)

    def test_no_fallback_to_dynamic_source_or_cache_trust(self):
        source = VERIFY.read_text(encoding="utf-8")
        for required in (
            "ORDAX_EMBEDDED_PYTHON_SOURCE_LOCK_INVALID",
            "ORDAX_EMBEDDED_PYTHON_CACHE_INVALID",
            "ORDAX_EMBEDDED_PYTHON_VERSION_INVALID",
            "source_manifest",
            "$lock.sha256",
            "Set-StrictMode -Version Latest",
        ):
            self.assertIn(required, source)


if __name__ == "__main__":
    unittest.main()
