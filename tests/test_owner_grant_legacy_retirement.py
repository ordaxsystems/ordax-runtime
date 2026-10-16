from pathlib import Path
import unittest


PRODUCTION_PATHS = (
    Path("ordax_dev_agent/product_remote_client.py"),
    Path("ordax_studio/product_web_desktop.py"),
)

FORBIDDEN = (
    "/v3/product/device-links",
    "/v3/product/device-computer-grants",
    "/v3/product/device-intelligence-grants",
    "/v3/product/project-capability-grants",
    "claim_device_pairing",
)


class OwnerGrantLegacyRetirementTests(unittest.TestCase):
    def test_owner_authorization_has_no_retired_routes(self):
        source = "\n".join(path.read_text(encoding="utf-8-sig") for path in PRODUCTION_PATHS)
        for forbidden in FORBIDDEN:
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, source)

    def test_canonical_owner_grant_authority_is_present(self):
        client = Path("ordax_dev_agent/product_remote_client.py").read_text(encoding="utf-8")
        self.assertIn("/v3/owner/remote-authority", client)
        self.assertIn("/v3/owner/remote-grant-groups", client)


if __name__ == "__main__":
    unittest.main()
