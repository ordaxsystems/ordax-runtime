"""Branch retirement must preserve original commits even after squash merges."""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/branch-hygiene.sh"
SOURCE = "a" * 40
MERGE = "b" * 40
OTHER = "c" * 40

MOCK_GIT = r'''#!/usr/bin/env python3
import os
import pathlib
import sys

args = sys.argv[1:]
state = pathlib.Path(os.environ["MOCK_STATE"])
sha = os.environ["MOCK_SOURCE"]
merge = os.environ["MOCK_MERGE"]
branch = os.environ.get("MOCK_BRANCH", "fix/test")
if args[0] == "fetch":
    sys.exit(0)
if args[0] == "show-ref":
    sys.exit(0 if os.getenv("MOCK_BRANCH_EXISTS", "1") == "1" else 1)
if args[0] == "rev-parse":
    print(sha)
    sys.exit(0)
if args[0] == "merge-base":
    if args[3] != "origin/main":
        sys.exit(2)
    if args[2] == merge:
        sys.exit(0 if os.getenv("MOCK_MERGE_IN_MAIN", "1") == "1" else 1)
    if args[2] == sha:
        sys.exit(0 if os.getenv("MOCK_SOURCE_IN_MAIN", "0") == "1" else 1)
    sys.exit(1)
if args[0] == "for-each-ref":
    if args[-1] == "refs/remotes/origin/archive/":
        sys.exit(0)
    print("origin/" + branch)
    sys.exit(0)
if args[0] == "ls-remote":
    ref = args[-1]
    if ref.startswith("refs/tags/"):
        tag = state / "archive_sha"
        if tag.exists():
            print(tag.read_text().strip() + "\t" + ref)
        sys.exit(0)
    if ref == "refs/heads/" + branch:
        live = os.getenv("MOCK_LIVE_SHA", sha)
        print(live + "\t" + ref)
        sys.exit(0)
    sys.exit(2)
if args[0] == "push":
    if args[2] == "--delete":
        (state / "deleted").write_text(args[3])
        sys.exit(0)
    if args[2].endswith("refs/tags/archive/ordax-runtime/" + branch):
        if os.getenv("MOCK_FAIL_ARCHIVE", "0") == "1":
            sys.exit(1)
        (state / "archive_sha").write_text(sha)
        sys.exit(0)
    sys.exit(2)
print("Unexpected git command: " + repr(args), file=sys.stderr)
sys.exit(2)
'''

MOCK_GH = r'''#!/usr/bin/env python3
import os
import sys

args = sys.argv[1:]
if args[0] != "api":
    sys.exit(2)
if "state=open" in " ".join(args):
    if os.getenv("MOCK_FAIL_OPEN", "0") == "1":
        sys.exit(1)
    print(os.getenv("MOCK_OPEN", "0"))
    sys.exit(0)
if "state=closed" in " ".join(args):
    if os.getenv("MOCK_FAIL_CLOSED", "0") == "1":
        sys.exit(1)
    if os.getenv("MOCK_MERGED", "1") != "1":
        sys.exit(0)
    print("|".join((
        os.getenv("MOCK_PR_HEAD", os.environ["MOCK_SOURCE"]),
        os.environ["MOCK_MERGE"],
        os.getenv("MOCK_BASE", "main"),
        os.getenv("MOCK_HEAD_REPO", "ordaxsystems/ordax-runtime"),
        "ordaxsystems/ordax-runtime",
    )))
    sys.exit(0)
sys.exit(2)
'''


class BranchHygienePolicyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.script = SCRIPT.read_text(encoding="utf-8")

    def test_source_contract(self):
        source = self.script
        self.assertIn('main|archive/*) return 0', source)
        self.assertIn("exact_merged_pr_proof()", source)
        self.assertIn("has_exact_archive_snapshot()", source)
        self.assertIn("ensure_archived_if_divergent()", source)
        self.assertIn("git merge-base --is-ancestor", source)
        self.assertIn("refs/tags/$archive_tag", source)
        self.assertIn("git push origin --delete", source)
        self.assertIn('if ! open_count="$(open_pr_count "$branch")"', source)
        self.assertIn("source history archive is unverified", source)
        self.assertLess(source.index('ensure_archived_if_divergent "$branch"'),
                        source.index('git push origin --delete "$branch"'))

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.dir = Path(self.temp.name)
        bindir = self.dir / "bin"
        bindir.mkdir()
        for name, source in (("git", MOCK_GIT), ("gh", MOCK_GH)):
            file = bindir / name
            file.write_text(source, encoding="utf-8")
            file.chmod(0o755)
        self.env = {
            **os.environ,
            "PATH": f"{bindir}{os.pathsep}{os.environ.get('PATH', '')}",
            "GITHUB_REPOSITORY": "ordaxsystems/ordax-runtime",
            "MOCK_STATE": str(self.dir),
            "MOCK_SOURCE": SOURCE,
            "MOCK_MERGE": MERGE,
        }

    def run_cleanup(self, **overrides):
        env = {**self.env, **overrides}
        return subprocess.run(
            ["bash", str(getattr(self, "script_path", SCRIPT)), "branch", env.get("MOCK_BRANCH", "fix/test")],
            env=env, text=True, capture_output=True, timeout=10
        )

    def assert_no_delete(self, **overrides):
        result = self.run_cleanup(**overrides)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertFalse((self.dir / "deleted").exists(), result.stdout)

    def test_merged_squash_archives_exact_tip_before_delete(self):
        result = self.run_cleanup()
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertEqual((self.dir / "archive_sha").read_text(), SOURCE)
        self.assertEqual((self.dir / "deleted").read_text(), "fix/test")

    def test_unmerged_or_mismatched_pr_keeps_branch(self):
        self.assert_no_delete(MOCK_MERGED="0")
        self.assert_no_delete(MOCK_PR_HEAD=OTHER)
        self.assert_no_delete(MOCK_BASE="other")
        self.assert_no_delete(MOCK_HEAD_REPO="wrong/repo")

    def test_unknown_or_untrusted_state_keeps_branch(self):
        self.assert_no_delete(MOCK_OPEN="1")
        self.assert_no_delete(MOCK_FAIL_OPEN="1")
        self.assert_no_delete(MOCK_FAIL_CLOSED="1")
        self.assert_no_delete(MOCK_MERGE_IN_MAIN="0")
        self.assert_no_delete(MOCK_BRANCH_EXISTS="0")

    def test_divergent_archive_collision_keeps_branch(self):
        (self.dir / "archive_sha").write_text(OTHER)
        self.assert_no_delete()
        self.assertEqual((self.dir / "archive_sha").read_text(), OTHER)

    def test_archive_failure_or_branch_movement_keeps_branch(self):
        self.assert_no_delete(MOCK_FAIL_ARCHIVE="1")
        self.assert_no_delete(MOCK_LIVE_SHA=OTHER)

    def test_already_preserved_commit_can_be_retired(self):
        (self.dir / "archive_sha").write_text(SOURCE)
        result = self.run_cleanup()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.dir / "deleted").read_text(), "fix/test")

    def test_linear_merge_needs_no_extra_archive(self):
        result = self.run_cleanup(MOCK_SOURCE_IN_MAIN="1")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((self.dir / "archive_sha").exists())
        self.assertEqual((self.dir / "deleted").read_text(), "fix/test")

    def configure_superseded_owner_branch(self):
        import shutil
        origin_branch = "feat/owner-central-session-selection-cli-20261027"
        original = "bd995234d3d4cd6f3aace5a1fa2bc287ea75e80d"
        replacement = "8a444032b6917132ef285afaa4eb7d69b1bade7f"
        (self.dir / "scripts").mkdir()
        (self.dir / "docs").mkdir()
        self.script_path = self.dir / "scripts" / "branch-hygiene.sh"
        shutil.copy2(SCRIPT, self.script_path)
        shutil.copy2(
            ROOT / "docs" / "approved-branch-supersessions.json",
            self.dir / "docs" / "approved-branch-supersessions.json",
        )
        self.env.update({
            "MOCK_BRANCH": origin_branch,
            "MOCK_SOURCE": original,
            "MOCK_MERGE": replacement,
            "MOCK_MERGED": "0",
        })
        return origin_branch, original, replacement

    def test_reviewed_supersession_preserves_original_commit_before_retirement(self):
        branch, original, _replacement = self.configure_superseded_owner_branch()
        result = self.run_cleanup()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual((self.dir / "archive_sha").read_text(), original)
        self.assertEqual((self.dir / "deleted").read_text(), branch)
        self.assertIn("superseded-by-", result.stdout)

    def test_supersession_never_uses_failed_merged_pr_lookup_as_permission(self):
        self.configure_superseded_owner_branch()
        self.assert_no_delete(MOCK_FAIL_CLOSED="1")

    def test_supersession_requires_exact_source_tip_and_trusted_main_replacement(self):
        self.configure_superseded_owner_branch()
        self.assert_no_delete(MOCK_SOURCE=OTHER)
        self.assert_no_delete(MOCK_MERGE_IN_MAIN="0")
        self.assert_no_delete(MOCK_OPEN="1")
        self.assert_no_delete(MOCK_FAIL_OPEN="1")

    def test_supersession_does_not_delete_after_archive_failure_or_race(self):
        self.configure_superseded_owner_branch()
        self.assert_no_delete(MOCK_FAIL_ARCHIVE="1")
        self.assert_no_delete(MOCK_LIVE_SHA=OTHER)
        (self.dir / "archive_sha").write_text(OTHER)
        self.assert_no_delete()

    def test_supersession_invalid_manifest_fails_closed(self):
        branch, _original, _replacement = self.configure_superseded_owner_branch()
        file = self.dir / "docs" / "approved-branch-supersessions.json"
        import json
        data = json.loads(file.read_text())
        data["retirements"].append(dict(data["retirements"][0]))
        file.write_text(json.dumps(data))
        self.assert_no_delete()

    def test_wrong_repo_is_refused_before_any_remote_operation(self):
        result = self.run_cleanup(GITHUB_REPOSITORY="another/repo")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.dir / "archive_sha").exists())
        self.assertFalse((self.dir / "deleted").exists())


if __name__ == "__main__":
    unittest.main()
