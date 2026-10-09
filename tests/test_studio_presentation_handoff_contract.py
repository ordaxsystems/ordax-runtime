"""Contract: Runtime stages the *one* canonical Apps Electron presentation.

The staged folder is deliberately not part of the signed installer until
the Runtime ↔ Electron bridge, upgrade and final installed-EXE smoke exist.
"""
from __future__ import annotations

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class StudioPresentationHandoffContractTests(unittest.TestCase):
    def test_handoff_has_single_external_source_and_no_legacy_execution(self) -> None:
        source = (ROOT / "scripts/windows/verify-studio-presentation-handoff.ps1").read_text("utf-8")
        self.assertIn("git -C $source rev-parse HEAD", source)
        self.assertIn("status --porcelain -- apps/studio tools/assistant-host", source)
        self.assertIn('apps\\studio\\conversation\\src\\index.html', source)
        self.assertIn('apps\\studio\\app.json', source)
        self.assertIn('verify-windows-candidate.mjs', source)
        self.assertIn('build:windows', source)
        self.assertIn('setup:native', source)
        self.assertIn('appManifest.version -ne $hostPackage.version', source)
        self.assertIn('sourceRepository -ne "ordaxsystems/ordax-apps"', source)
        self.assertIn('entrypoint -ne "apps/studio/conversation/src/index.html"', source)
        self.assertIn("Get-FileHash -Algorithm SHA256", source)
        self.assertIn("ORDAX_STUDIO_PRESENTATION_HANDOFF=PASS", source)
        self.assertIn("ORDAX_STUDIO_PRESENTATION_RELEASE=NOT_PROMOTED", source)
        for forbidden in (
            "studio-source.lock.json",  # independent candidate, not official release lock
            "CreateProcess", "Start-Process", "Invoke-RestMethod",
            "gh release", "upload-artifact", "Set-ItemProperty",
            "Remove-Item",
        ):
            self.assertNotIn(forbidden, source)

    def test_ci_builds_on_real_windows_from_apps_checkout_without_release_upload(self) -> None:
        workflow = (ROOT / ".github/workflows/studio-presentation-handoff.yml").read_text("utf-8")
        self.assertIn("runs-on: windows-2025", workflow)
        self.assertIn("repository: ordaxsystems/ordax-apps", workflow)
        self.assertIn("ref: main", workflow)
        self.assertIn("persist-credentials: false", workflow)
        self.assertIn("node-version: '24'", workflow)
        self.assertIn("setup-node@249970729cb0ef3589644e2896645e5dc5ba9c38", workflow)
        self.assertIn("verify-studio-presentation-handoff.ps1", workflow)
        self.assertIn("SOURCE_TO_STAGE_ONLY", workflow)
        self.assertNotIn("upload-artifact", workflow)
        self.assertNotIn("publish-release", workflow)
        self.assertNotIn("create-release", workflow)
        self.assertNotIn("github.event_name == 'release'", workflow)

    def test_official_windows_release_still_requires_explicit_cutover(self) -> None:
        build = (ROOT / "scripts/windows/build-ordax-studio-product.ps1").read_text("utf-8")
        launcher = (ROOT / "packaging/windows/ordax_launcher.c").read_text("utf-8")
        setup = (ROOT / "packaging/windows/ordax-studio.iss").read_text("utf-8")
        self.assertIn('Join-Path $studioAppSource "src\\index.html"', build)
        self.assertIn('L"%ls\\\\workbench\\\\ORDAX Workbench.exe"', launcher)
        self.assertIn('ORDAX Studio.exe', setup)
        self.assertNotIn("verify-studio-presentation-handoff.ps1", build)
        self.assertNotIn("presentation\\ORDAX Studio.exe", launcher)
        self.assertNotIn("presentation\\ORDAX Studio.exe", setup)


if __name__ == "__main__":
    unittest.main()
