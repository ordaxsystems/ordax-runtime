from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
GATE = ROOT / "scripts" / "windows" / "assert-release-main-ancestry.ps1"
GOVERNANCE = ROOT / "scripts" / "windows" / "assert-github-main-governance.ps1"
OWNER_WORKFLOW = ROOT / ".github" / "workflows" / "owner-linux-recovery.yml"


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

    def test_github_release_uses_single_concrete_main_governance_ssot(self) -> None:
        gate = GATE.read_text(encoding="utf-8")
        governance = GOVERNANCE.read_text(encoding="utf-8")

        self.assertIn('assert-github-main-governance.ps1', gate)
        self.assertIn('-ExpectedMainCommit $mainCommit', gate)
        self.assertNotIn('Invoke-RestMethod', gate)
        self.assertNotIn('/rules/branches/main', gate)

        self.assertIn("$env:GITHUB_REPOSITORY", governance)
        self.assertIn('$headers.Authorization = "Bearer $env:GH_TOKEN"', governance)
        self.assertIn('default_branch -ne "main"', governance)
        self.assertIn('/branches/main', governance)
        self.assertIn('[bool]$githubMain.protected', governance)
        self.assertIn('Blocked publication: canonical main is not protected', governance)
        self.assertIn('/rules/branches/main?per_page=100', governance)
        self.assertIn('"deletion", "non_fast_forward", "pull_request", "required_status_checks"', governance)
        self.assertIn('required_review_thread_resolution', governance)
        self.assertIn('strict_required_status_checks_policy', governance)
        for context in (
            "packaging-contracts",
            "build-installer",
            "install-smoke",
            "product-mcp-minimum-sdk",
            "runtime-contracts",
        ):
            self.assertIn(f'"{context}"', governance)
        self.assertIn('ORDAX_REPOSITORY_GOVERNANCE=PASS', governance)
        self.assertIn('ORDAX_MAIN_RULESET_POLICY=PASS', governance)
        self.assertIn('Canonical GitHub main moved after the trusted publication commit was resolved', governance)
        self.assertNotIn('continue-on-error', governance)

    def test_owner_prerelease_separates_read_only_governance_from_write_publication(self) -> None:
        workflow = OWNER_WORKFLOW.read_text(encoding="utf-8")
        governance = workflow.split("  governance:", 1)[1].split("  publish:", 1)[0]
        publish = workflow.split("  publish:", 1)[1]

        self.assertIn("contents: read", governance)
        self.assertIn("assert-github-main-governance.ps1", governance)
        self.assertIn('-ExpectedMainCommit "${{ github.sha }}"', governance)
        self.assertNotIn("contents: write", governance)
        self.assertIn("GH_TOKEN: ${{ github.token }}", governance)
        self.assertNotIn("gh release create", governance)

        self.assertIn("needs: [build, governance]", publish)
        self.assertIn("contents: write", publish)
        self.assertIn("gh release create", publish)
        self.assertNotIn("assert-github-main-governance.ps1", publish)
        self.assertNotIn("actions/checkout@", publish)


if __name__ == "__main__":
    unittest.main()
