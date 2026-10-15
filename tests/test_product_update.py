import unittest

from ordax_dev_agent.product_update import evaluate_release, parse_version


class ProductUpdateTests(unittest.TestCase):
    def release(self, version: str, *, draft=False, prerelease=False, assets=True):
        names = []
        if assets:
            names = [
                {"name": f"ORDAX-Studio-Setup-{version}-x64.exe"},
                {"name": "SHA256SUMS.txt"},
            ]
        return {
            "tag_name": f"v{version}",
            "draft": draft,
            "prerelease": prerelease,
            "assets": names,
        }

    def test_version_parser_is_strict(self):
        self.assertEqual(parse_version("0.15.1"), (0, 15, 1))
        for value in ("v0.15.1", "0.15", "01.2.3", "1.2.3-beta"):
            with self.assertRaises(ValueError):
                parse_version(value)

    def test_newer_complete_release_is_discovery_only(self):
        result = evaluate_release("0.14.1", self.release("0.15.1"))
        self.assertEqual(result["state"], "update-available")
        self.assertEqual(result["availableVersion"], "0.15.1")
        self.assertFalse(result["applyAllowed"])
        self.assertEqual(
            result["verificationRequired"],
            ["sha256", "authenticode-authorized-signer", "install-smoke"],
        )

    def test_equal_release_is_current(self):
        self.assertEqual(evaluate_release("0.15.1", self.release("0.15.1"))["state"], "current")

    def test_older_release_never_downgrades(self):
        result = evaluate_release("0.15.1", self.release("0.14.1"))
        self.assertEqual(result["state"], "installed-newer")
        self.assertFalse(result["applyAllowed"])

    def test_draft_and_prerelease_do_not_enter_production_channel(self):
        self.assertEqual(evaluate_release("0.14.1", self.release("0.15.1", draft=True))["state"], "no-production-release")
        self.assertEqual(evaluate_release("0.14.1", self.release("0.15.1", prerelease=True))["state"], "no-production-release")

    def test_incomplete_release_is_not_installable(self):
        result = evaluate_release("0.14.1", self.release("0.15.1", assets=False))
        self.assertEqual(result["state"], "release-incomplete")
        self.assertIn("SHA256SUMS.txt", result["missingAssets"])

    def test_invalid_release_tag_fails_closed(self):
        bad = self.release("0.15.1")
        bad["tag_name"] = "latest"
        with self.assertRaises(ValueError):
            evaluate_release("0.14.1", bad)


if __name__ == "__main__":
    unittest.main()
