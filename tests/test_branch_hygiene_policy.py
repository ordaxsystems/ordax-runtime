import unittest
from pathlib import Path


class BranchHygienePolicyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        root = Path(__file__).resolve().parents[1]
        cls.script = (root / "scripts" / "branch-hygiene.sh").read_text(
            encoding="utf-8"
        )

    def test_archive_snapshot_is_a_distinct_safe_retirement_proof(self) -> None:
        self.assertIn("has_exact_archive_snapshot()", self.script)
        self.assertIn(
            'refs/remotes/origin/archive/',
            self.script,
        )
        archive_check = self.script.index(
            'if has_exact_archive_snapshot "$branch_sha"; then'
        )
        merged_check = self.script.index(
            'if ! has_merged_pr "$branch"; then'
        )
        ancestry_check = self.script.index(
            'if ! git merge-base --is-ancestor "origin/$branch" origin/main; then'
        )
        self.assertLess(archive_check, merged_check)
        self.assertLess(archive_check, ancestry_check)
        self.assertIn(
            "exact protected archive snapshot preserves branch history",
            self.script,
        )

    def test_unproven_divergent_branch_remains_fail_closed(self) -> None:
        self.assertIn(
            "no merged PR and no exact archive snapshot proves safe retirement",
            self.script,
        )
        self.assertIn(
            "merged branch still contains commits outside main",
            self.script,
        )
        self.assertIn(
            'main|archive/*) return 0',
            self.script,
        )
        self.assertIn(
            'if has_open_pr "$branch"; then',
            self.script,
        )

    def test_archive_namespace_is_never_transient(self) -> None:
        transient_case = self.script[
            self.script.index("is_transient_branch()"):
            self.script.index("is_protected_branch()")
        ]
        self.assertNotIn("archive/*", transient_case)


if __name__ == "__main__":
    unittest.main()
