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
        self.assertIn("GetRSAPrivateKey", text)
        self.assertIn("GetECDsaPrivateKey", text)
        self.assertIn("CngExportPolicies]::None", text)
        self.assertIn("CspKeyContainerInfo.Exportable", text)
        self.assertIn("AllowedKeyProviders", text)
        self.assertIn("approved cryptographic provider", text)
        self.assertIn("/fd SHA256", text)
        self.assertIn("/tr $timestampUri.AbsoluteUri", text)
        self.assertIn("/td SHA256", text)
        self.assertIn("assert-release-authenticode.ps1", text)
        self.assertIn("Self-signed certificates are not accepted", text)
        self.assertIn("absolute HTTPS URL", text)
        self.assertNotIn("Set-AuthenticodeSignature", text)
        self.assertNotIn("New-SelfSignedCertificate", text)

    def test_release_job_uses_only_dedicated_signing_runner_key(self) -> None:
        text = WORKFLOW.read_text(encoding="utf-8")
        release = text.split("  publish-release:", 1)[1]
        self.assertIn("environment: windows-production-release", release)
        self.assertIn("runs-on: [self-hosted, windows, x64, ordax-release-signing]", release)
        self.assertIn("ORDAX_RELEASE_SIGNING_CERT_THUMBPRINT", release)
        self.assertIn("ORDAX_RELEASE_SIGNING_KEY_PROVIDERS", release)
        self.assertIn("ORDAX_RELEASE_TIMESTAMP_URL", release)
        self.assertIn("sign-release-authenticode.ps1", release)
        self.assertNotIn("Import-PfxCertificate", text)
        self.assertNotIn("ORDAX_RELEASE_SIGNING_PFX", text)
        self.assertNotIn("ConvertTo-SecureString", text)

    def test_release_job_has_no_private_key_material_or_unsigned_fallback(self) -> None:
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertNotIn("continue-on-error: true", text)
        self.assertNotIn("ORDAX_ALLOW_UNSIGNED", text)
        self.assertNotIn("New-SelfSignedCertificate", text)
        self.assertNotIn("Remove ephemeral signing certificate", text)
        self.assertIn("Verify production Authenticode signature and authorized publisher", text)


if __name__ == "__main__":
    unittest.main()
