"""Windows-only negative E2E tests for the existing production bootstrap.

All cases use isolated per-test LOCALAPPDATA, no installer executables, no
network access and no production signing credential. This proves fail-closed
journal binding before any installation can begin.
"""
from __future__ import annotations

import ctypes
import json
import os
import subprocess
import tempfile
import unittest
import uuid
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BOOTSTRAP = ROOT / "scripts" / "windows" / "apply-ordax-production-update.ps1"


@unittest.skipUnless(os.name == "nt", "Windows PowerShell bootstrap is Windows-only")
class ProductUpdateBootstrapGuardTests(unittest.TestCase):
    def test_installer_pid_completion_contract(self) -> None:
        # The existing Windows packaging CI already runs this unittest class.
        # This isolated fixture never executes an Inno installer or modifies
        # the real ORDAX installation; it exercises the executor's real function.
        script = ROOT / "scripts" / "windows" / "test-updater-installer-completion.ps1"
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive",
             "-ExecutionPolicy", "Bypass", "-File", str(script)],
            capture_output=True, text=True, timeout=30, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("ORDAX_INSTALLER_COMPLETION_CONTRACT=PASS", result.stdout)

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.local = Path(self.tmp.name) / "Local"
        self.install = self.local / "Programs" / "ORDAX"
        self.install.mkdir(parents=True)
        (self.install / "product-manifest.json").write_text(
            json.dumps({
                "schema": "ordax.windows-product/1",
                "product": "ORDAX Studio",
                "version": "0.15.6",
            }),
            encoding="utf-8",
        )
        self.txid = uuid.uuid4().hex
        self.txroot = self.local / "OrdaX" / "updates" / "0.15.6-to-0.15.9" / self.txid
        (self.txroot / "target").mkdir(parents=True)
        (self.txroot / "rollback").mkdir()
        self.verifier = self.txroot / "assert-release-authenticode.ps1"
        self.verifier.write_text("throw 'VERIFIER_MUST_NOT_RUN'\n", encoding="utf-8")
        self.target = self.txroot / "target" / "ORDAX-Studio-Setup-0.15.9-x64.exe"
        self.rollback = self.txroot / "rollback" / "ORDAX-Studio-Setup-0.15.6-x64.exe"
        self.target_sha = "a" * 64
        self.rollback_sha = "b" * 64
        self.journal = {
            "schema": "ordax.windows-update-transaction/1",
            "authority": "local-privileged-updater",
            "transactionId": self.txid,
            "phase": "handoff-launching",
            "installedVersion": "0.15.6",
            "target": {
                "version": "0.15.9",
                "tag": "v0.15.9",
                "installer": self.target.name,
                "installerSha256": self.target_sha,
                "checksums": "SHA256SUMS.txt",
            },
            "rollback": {
                "version": "0.15.6",
                "tag": "v0.15.6",
                "installer": self.rollback.name,
                "installerSha256": self.rollback_sha,
                "checksums": "SHA256SUMS.txt",
            },
        }

    def invoke(self, *, argv_override: dict[str, str] | None = None, expected_phase: str = "blocked"):
        (self.txroot / "transaction.json").write_text(
            json.dumps(self.journal), encoding="utf-8"
        )
        options = {
            "TransactionRoot": str(self.txroot),
            "TargetInstaller": str(self.target),
            "TargetSha256": self.target_sha,
            "TargetVersion": "0.15.9",
            "RollbackInstaller": str(self.rollback),
            "RollbackSha256": self.rollback_sha,
            "RollbackVersion": "0.15.6",
            "InstallRoot": str(self.install),
            "TrustedSignerThumbprints": "C" * 40,
            "VerifierScript": str(self.verifier),
        }
        options.update(argv_override or {})
        command = [
            "powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy",
            "Bypass", "-File", str(BOOTSTRAP),
        ]
        for key, value in options.items():
            command.extend(("-" + key, value))
        env = {**os.environ, "LOCALAPPDATA": str(self.local)}
        result = subprocess.run(
            command, env=env, capture_output=True, text=True,
            timeout=30, check=False,
        )
        after = json.loads((self.txroot / "transaction.json").read_text(encoding="utf-8"))
        self.assertEqual(result.returncode, 30, result.stdout + result.stderr)
        self.assertEqual(after["phase"], expected_phase, after)
        self.assertFalse(self.target.exists(), "target installer must never execute")
        self.assertFalse(self.rollback.exists(), "rollback installer must never execute")
        self.assertNotIn("VERIFIER_MUST_NOT_RUN", result.stdout + result.stderr)
        return after.get("detail", "")

    def test_bootstrap_rechecks_both_installer_digests_at_last_handoff(self):
        content = BOOTSTRAP.read_text(encoding="utf-8")
        self.assertLess(
            content.index("Write-TransactionPhase -Phase 'installing-target'"),
            content.index("Assert-StagedInstaller -Path $TargetInstaller -ExpectedSha256 $TargetSha256",
                          content.index("Write-TransactionPhase -Phase 'installing-target'")),
        )
        self.assertLess(
            content.index("Assert-StagedInstaller -Path $TargetInstaller -ExpectedSha256 $TargetSha256",
                          content.index("Write-TransactionPhase -Phase 'installing-target'")),
            content.index("Invoke-Installer -Path $TargetInstaller -LogName 'target-install.log'"),
        )
        self.assertIn("if (-not $targetAttempted)", content)
        self.assertLess(
            content.index("$targetAttempted = $true"),
            content.index("Invoke-Installer -Path $TargetInstaller -LogName 'target-install.log'"),
        )
        self.assertLess(
            content.index("Assert-StagedInstaller -Path $RollbackInstaller -ExpectedSha256 $RollbackSha256",
                          content.index("Write-TransactionPhase -Phase 'rolling-back'")),
            content.index("Invoke-Installer -Path $RollbackInstaller -LogName 'rollback-install.log'"),
        )

    def test_valid_handoff_passes_binding_but_stops_on_missing_installer(self):
        detail = self.invoke()
        self.assertIn("Staged installer is missing", detail)

    def test_terminal_transaction_cannot_be_replayed(self):
        self.journal["phase"] = "committed"
        detail = self.invoke(expected_phase="committed")
        self.assertEqual(detail, "", "Refused replay must not overwrite committed receipt")

    def test_interrupted_installer_and_rollback_replays_preserve_evidence(self):
        # After a power loss or abandoned handoff, "blocked" would misleadingly
        # claim that no installer had been started. Refusal must not rewrite it.
        for phase in ("installing-target", "rolling-back"):
            with self.subTest(phase=phase):
                self.journal["phase"] = phase
                self.assertEqual(self.invoke(expected_phase=phase), "")

    def test_parallel_bootstrap_cannot_mutate_active_journal(self):
        sid = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
             "[Security.Principal.WindowsIdentity]::GetCurrent().User.Value"],
            capture_output=True, text=True, check=True, timeout=15,
        ).stdout.strip()
        self.assertTrue(sid.startswith("S-1-"), sid)
        # Hold exactly the OS mutex used by the canonical bootstrap, from
        # a different process/thread. This does not simulate an installer.
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        create = kernel32.CreateMutexW
        create.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p]
        create.restype = ctypes.c_void_p
        release = kernel32.ReleaseMutex
        release.argtypes = [ctypes.c_void_p]
        release.restype = ctypes.c_int
        close = kernel32.CloseHandle
        close.argtypes = [ctypes.c_void_p]
        close.restype = ctypes.c_int
        handle = create(None, True, "Global\\ORDAX_WindowsProductUpdate_" + sid)
        self.assertTrue(handle, f"CreateMutexW error {ctypes.get_last_error()}")
        try:
            self.assertEqual(self.invoke(expected_phase="handoff-launching"), "")
        finally:
            self.assertTrue(release(handle))
            self.assertTrue(close(handle))

    def test_forged_local_owner_authority_is_refused(self):
        self.journal["authority"] = "model-generated-owner"
        self.assertIn("handoff authority, journal identity or phase", self.invoke())

    def test_wrong_rollback_checksum_is_refused_before_installer_execution(self):
        self.journal["rollback"]["installerSha256"] = "c" * 64
        self.assertIn("rollback handoff path or SHA-256", self.invoke())

    def test_wrong_target_path_is_refused_before_installer_execution(self):
        other = self.txroot / "target" / "other-installer.exe"
        self.assertIn(
            "target handoff path or SHA-256",
            self.invoke(argv_override={"TargetInstaller": str(other)}),
        )

    def test_missing_staged_verifier_is_journaled_as_blocked(self):
        self.verifier.unlink()
        self.assertIn("Staged Authenticode verifier is missing", self.invoke())

    def test_changed_installed_version_is_refused(self):
        (self.install / "product-manifest.json").write_text(
            json.dumps({
                "schema": "ordax.windows-product/1",
                "product": "ORDAX Studio",
                "version": "0.15.8",
            }),
            encoding="utf-8",
        )
        self.assertIn("Installed product changed", self.invoke())


if __name__ == "__main__":
    unittest.main()
