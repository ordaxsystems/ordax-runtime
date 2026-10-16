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

    def test_hsm_runner_only_signs_and_hands_off_immutable_artifact(self) -> None:
        text = WORKFLOW.read_text(encoding="utf-8")
        signing = text.split("  sign-release:", 1)[1].split("  signed-release-smoke:", 1)[0]
        self.assertIn("environment: windows-production-release", signing)
        self.assertIn("runs-on: [self-hosted, windows, x64, ordax-release-signing]", signing)
        self.assertIn("contents: read", signing)
        self.assertNotIn("contents: write", signing)
        self.assertIn("ORDAX_RELEASE_SIGNING_CERT_THUMBPRINT", signing)
        self.assertIn("ORDAX_RELEASE_SIGNING_KEY_PROVIDERS", signing)
        self.assertIn("ORDAX_RELEASE_TIMESTAMP_URL", signing)
        self.assertIn("sign-release-authenticode.ps1", signing)
        self.assertIn("ordax-studio-windows-x64-signed", signing)
        self.assertNotIn("test-ordax-studio-install-smoke.ps1", signing)
        self.assertNotIn("gh release create", signing)
        self.assertNotIn("GH_TOKEN", signing)
        self.assertNotIn("GITHUB_OUTPUT", signing)

    def test_signed_product_executes_only_on_clean_hosted_runner(self) -> None:
        text = WORKFLOW.read_text(encoding="utf-8")
        smoke = text.split("  signed-release-smoke:", 1)[1].split("  publish-release:", 1)[0]
        self.assertIn("environment: windows-production-release", smoke)
        self.assertIn("runs-on: windows-latest", smoke)
        self.assertIn("needs: sign-release", smoke)
        self.assertIn("ordax-studio-windows-x64-signed", smoke)
        self.assertIn("Verify production Authenticode signature on clean hosted runner", smoke)
        self.assertIn("ORDAX_RELEASE_SIGNER_THUMBPRINTS: ${{ vars.ORDAX_RELEASE_SIGNER_THUMBPRINTS }}", smoke)
        self.assertIn("test-ordax-studio-install-smoke.ps1", smoke)
        self.assertNotIn("needs.sign-release.outputs", smoke)
        self.assertNotIn("sign-release-authenticode.ps1", smoke)
        self.assertNotIn("ORDAX_RELEASE_SIGNING_KEY_PROVIDERS", smoke)
        self.assertNotIn("ORDAX_RELEASE_SIGNING_CERT_THUMBPRINT", smoke)

    def test_publication_is_hosted_and_has_release_write_without_signing_key(self) -> None:
        text = WORKFLOW.read_text(encoding="utf-8")
        publish = text.split("  publish-release:", 1)[1]
        self.assertIn("environment: windows-production-release", publish)
        self.assertIn("runs-on: windows-latest", publish)
        self.assertIn("contents: write", publish)
        self.assertIn("signed-release-smoke", publish)
        self.assertIn("ordax-studio-windows-x64-signed", publish)
        self.assertIn("ORDAX_RELEASE_SIGNER_THUMBPRINTS: ${{ vars.ORDAX_RELEASE_SIGNER_THUMBPRINTS }}", publish)
        self.assertIn("Verify tagged Runtime commit still belongs to protected canonical main before publication", publish)
        self.assertIn("assert-release-main-ancestry.ps1", publish)
        self.assertIn("gh release create", publish)
        self.assertNotIn("needs.sign-release.outputs", publish)
        self.assertNotIn("sign-release-authenticode.ps1", publish)
        self.assertNotIn("ORDAX_RELEASE_SIGNING_KEY_PROVIDERS", publish)
        self.assertNotIn("ORDAX_RELEASE_SIGNING_CERT_THUMBPRINT", publish)
        self.assertNotIn("test-ordax-studio-install-smoke.ps1", publish)

    def test_release_pipeline_has_no_private_key_material_or_unsigned_fallback(self) -> None:
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertNotIn("continue-on-error: true", text)
        self.assertNotIn("ORDAX_ALLOW_UNSIGNED", text)
        self.assertNotIn("New-SelfSignedCertificate", text)
        self.assertNotIn("Import-PfxCertificate", text)
        self.assertNotIn("ORDAX_RELEASE_SIGNING_PFX", text)
        self.assertNotIn("ConvertTo-SecureString", text)
        self.assertNotIn("Remove ephemeral signing certificate", text)
        self.assertNotIn("trusted_signer_thumbprints:", text)
        self.assertIn("assert-release-authenticode.ps1", text)
        self.assertEqual(1, text.count("sign-release-authenticode.ps1 -Path"))


if __name__ == "__main__":
    unittest.main()
