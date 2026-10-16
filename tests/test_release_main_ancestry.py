from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
GATE = ROOT / "scripts" / "windows" / "assert-release-main-ancestry.ps1"
GOVERNANCE_GATE = ROOT / "scripts" / "windows" / "assert-release-repository-governance.ps1"


@unittest.skipUnless(
    shutil.which("powershell") and shutil.which("git"),
    "Windows PowerShell and Git are required for release ancestry contracts",
)
class ReleaseMainAncestryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name) / "runtime"
        subprocess.run(
            ["git", "init", "-b", "main", str(self.repo)],
            check=True, capture_output=True, text=True, timeout=15,
        )
        self.git("config", "user.name", "ORDAX CI")
        self.git("config", "user.email", "ci@ordax.invalid")
        self.git("config", "commit.gpgsign", "false")
        self.write_commit("base")
        self.first = self.git("rev-parse", "HEAD").stdout.strip()
        self.git("tag", "v0.5.8")
        self.write_commit("main-next")
        self.main = self.git("rev-parse", "HEAD").stdout.strip()
        self.git("update-ref", "refs/remotes/origin/main", self.main)
        self.git("checkout", "-b", "unmerged", self.first)
        self.write_commit("branch-only")
        self.foreign = self.git("rev-parse", "HEAD").stdout.strip()
        self.git("tag", "v0.5.9")
        self.git("checkout", "--detach", self.first)

    def git(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", "-C", str(self.repo), *args],
            check=True, capture_output=True, text=True, timeout=15,
        )

    def write_commit(self, message: str) -> None:
        (self.repo / "history.txt").write_text(message, encoding="utf-8")
        self.git("add", "history.txt")
        self.git("commit", "-m", message)

    def verify(
        self, tag: str = "v0.5.8", expected_commit: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        # Git-history unit contracts must not depend on the live repository's
        # administrative state. The real release job keeps GITHUB_REPOSITORY.
        env.pop("GITHUB_REPOSITORY", None)
        return subprocess.run(
            [
                "powershell", "-NoProfile", "-NonInteractive", "-File", str(GATE),
                "-Tag", tag, "-ExpectedCommit", expected_commit or self.first,
                "-RepositoryRoot", str(self.repo),
            ],
            check=False, capture_output=True, text=True, timeout=30, env=env,
        )

    def test_tag_on_previous_main_commit_is_allowed(self) -> None:
        result = self.verify()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("ORDAX_RELEASE_MAIN_ANCESTRY_VALID", result.stdout)
        self.assertIn(self.first, result.stdout)

    def test_unmerged_branch_tag_is_rejected(self) -> None:
        self.git("checkout", "--detach", self.foreign)
        result = self.verify(tag="v0.5.9", expected_commit=self.foreign)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("not an ancestor", result.stdout + result.stderr)

    def test_tag_and_checkout_disagree(self) -> None:
        result = self.verify(tag="v0.5.9")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("disagree", result.stdout + result.stderr)

    def test_github_event_commit_mismatch_is_rejected(self) -> None:
        result = self.verify(expected_commit=self.main)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("disagree", result.stdout + result.stderr)

    def test_missing_canonical_main_remote_ref_is_rejected(self) -> None:
        self.git("update-ref", "-d", "refs/remotes/origin/main")
        self.assertNotEqual(self.verify().returncode, 0)

    def test_malformed_version_tag_is_rejected(self) -> None:
        result = self.verify(tag="v0.5.8;Write-Host UNTRUSTED")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("exact stable version", result.stdout + result.stderr)

    def test_malformed_event_sha_is_rejected(self) -> None:
        self.assertNotEqual(self.verify(expected_commit="0" * 39).returncode, 0)

    def test_github_release_requires_protected_canonical_main(self) -> None:
        ancestry = GATE.read_text(encoding="utf-8")
        governance = GOVERNANCE_GATE.read_text(encoding="utf-8")
        self.assertIn("$env:GITHUB_REPOSITORY", ancestry)
        self.assertIn('assert-release-repository-governance.ps1', ancestry)
        self.assertIn('-Repository $env:GITHUB_REPOSITORY', ancestry)
        self.assertIn('default_branch -ne "main"', governance)
        self.assertIn('/branches/main', governance)
        self.assertIn('[bool]$main.protected', governance)
        self.assertIn('Blocked public release: canonical main is not protected', governance)
        self.assertIn('ORDAX_RELEASE_REPOSITORY_GOVERNANCE=PASS', governance)
        self.assertNotIn('continue-on-error', governance)

    def test_repository_governance_gate_has_valid_powershell_syntax(self) -> None:
        command = (
            "$tokens=$null; $errors=$null; "
            f"[System.Management.Automation.Language.Parser]::ParseFile('{GOVERNANCE_GATE}', "
            "[ref]$tokens, [ref]$errors) | Out-Null; "
            "if (@($errors).Count -gt 0) { $errors | ForEach-Object { Write-Error $_.Message }; exit 1 }"
        )
        result = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", command],
            check=False, capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
