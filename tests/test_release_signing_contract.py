from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "windows-product-build.yml"
SIGNER = ROOT / "scripts" / "windows" / "sign-release-authenticode.ps1"


class ReleaseSigningContractTests(unittest.TestCase):
    def test_signing_script_is_single_production_signing_ssot(self) -> None:
        text = SIGNER.read_text(encoding="utf-8")
        self.assertIn("Cert:\\CurrentUser\\My\\$normalizedThumbprint", text)
        self.assertIn("1.3.6.1.5.5.7.3.3", text)
        self.assertIn("/fd SHA256", text)
        self.assertIn("/tr $timestampUri.AbsoluteUri", text)
        self.assertIn("/td SHA256", text)
        self.assertIn("assert-release-authenticode.ps1", text)
        self.assertIn("Self-signed certificates are not accepted", text)
        self.assertIn("absolute HTTPS URL", text)
        self.assertNotIn("Set-AuthenticodeSignature", text)
        self.assertNotIn("New-SelfSignedCertificate", text)

    def test_release_job_imports_ephemeral_private_key_before_signing(self) -> None:
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("environment: windows-production-release", text)
        self.assertIn("ORDAX_RELEASE_SIGNING_PFX_BASE64: ${{ secrets.ORDAX_RELEASE_SIGNING_PFX_BASE64 }}", text)
        self.assertIn("ORDAX_RELEASE_SIGNING_PFX_PASSWORD: ${{ secrets.ORDAX_RELEASE_SIGNING_PFX_PASSWORD }}", text)
        self.assertIn("Import-PfxCertificate", text)
        self.assertIn("-Exportable:$false", text)
        self.assertIn("sign-release-authenticode.ps1", text)
        self.assertIn("ORDAX_RELEASE_SIGNING_CERT_THUMBPRINT", text)
        self.assertIn("ORDAX_RELEASE_TIMESTAMP_URL", text)

    def test_release_job_cleans_signing_material_even_after_failure(self) -> None:
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("name: Remove ephemeral signing certificate", text)
        self.assertIn("if: always()", text)
        self.assertIn("Remove-Item -LiteralPath $certificatePath", text)
        self.assertIn("Remove-Item -LiteralPath $pfxPath", text)

    def test_unsigned_or_unconfigured_release_has_no_fallback(self) -> None:
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertNotIn("continue-on-error: true", text)
        self.assertNotIn("ORDAX_ALLOW_UNSIGNED", text)
        self.assertNotIn("self-signed", text.lower())
        self.assertIn("Verify production Authenticode signature and authorized publisher", text)


if __name__ == "__main__":
    unittest.main()
