from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
GATE = ROOT / "scripts" / "windows" / "assert-release-authenticode.ps1"
TRUSTED_A = "A" * 40
TRUSTED_B = "B" * 40


@unittest.skipUnless(shutil.which("powershell"), "Windows PowerShell is required")
class ReleaseSignerIdentityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.dir = Path(self.temp.name)
        self.installer = self.dir / "ORDAX-Studio-Setup-test-x64.exe"
        self.installer.write_bytes(b"not an actual signed artifact; mocked Windows API")

    @staticmethod
    def ps_quote(value: str) -> str:
        return "'" + value.replace("'", "''") + "'"

    def verify(
        self,
        *,
        signer: str = TRUSTED_A,
        trusted: str = TRUSTED_A,
        status: str = "Valid",
        timestamp: bool = True,
        self_signed: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        certificate_issuer = "CN=ORDAX Test Publisher" if self_signed else "CN=Test Issuing CA"
        ts_value = "[pscustomobject]@{Subject='CN=Test Timestamp CA'}" if timestamp else "$null"
        entry = f"""$global:FakeSignature = [pscustomobject]@{{
    Status = [System.Management.Automation.SignatureStatus]::{status}
    StatusMessage = 'mock only'
    SignerCertificate = [pscustomobject]@{{
        Subject = 'CN=ORDAX Test Publisher'
        Issuer = {self.ps_quote(certificate_issuer)}
        Thumbprint = {self.ps_quote(signer)}
    }}
    TimeStamperCertificate = {ts_value}
}}
function Get-AuthenticodeSignature {{
    param([string]$FilePath)
    return $global:FakeSignature
}}
try {{
    & {self.ps_quote(str(GATE))} -Path {self.ps_quote(str(self.installer))} -TrustedSignerThumbprints {self.ps_quote(trusted)}
    exit 0
}} catch {{
    Write-Host $_.Exception.Message
    exit 1
}}
"""
        path = self.dir / "run-mock.ps1"
        path.write_text(entry, encoding="utf-8")
        return subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-File", str(path)],
            check=False,
            text=True,
            capture_output=True,
            timeout=30,
        )

    def test_authorized_signer_and_timestamp_are_accepted(self) -> None:
        result = self.verify()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("ORDAX_AUTHENTICODE_VALID", result.stdout)

    def test_allowed_rotation_certificate_is_accepted(self) -> None:
        result = self.verify(signer=TRUSTED_B.lower(), trusted=f"{TRUSTED_A},{TRUSTED_B}")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_wrong_publisher_is_rejected_even_when_signature_is_valid(self) -> None:
        result = self.verify(signer=TRUSTED_B)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("not an authorized ORDAX publisher", result.stdout)

    def test_missing_or_invalid_configuration_is_rejected(self) -> None:
        for trusted in ("", "NOT_A_THUMBPRINT", f"{TRUSTED_A},", f"{TRUSTED_A},{TRUSTED_A}"):
            with self.subTest(trusted=trusted):
                self.assertNotEqual(self.verify(trusted=trusted).returncode, 0)

    def test_unsigned_artifact_is_rejected_even_for_correct_publisher(self) -> None:
        self.assertNotEqual(self.verify(status="NotSigned").returncode, 0)

    def test_no_timestamp_is_rejected(self) -> None:
        result = self.verify(timestamp=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("no trusted timestamp", result.stdout)

    def test_self_signed_cert_is_rejected(self) -> None:
        self.assertNotEqual(self.verify(self_signed=True).returncode, 0)


if __name__ == "__main__":
    unittest.main()
