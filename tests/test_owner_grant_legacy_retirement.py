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
    "device_links",
    "device_computer_grants",
    "create_device_computer_grant",
    "revoke_device_computer_grant",
    "device_intelligence_grants",
    "create_device_intelligence_grant",
    "revoke_device_intelligence_grant",
    "project_capability_grants",
    "create_project_capability_grant",
    "revoke_project_capability_grant",
    "link_id",
    "sign_in_with_password",
)


class OwnerGrantLegacyRetirementTests(unittest.TestCase):
    def test_owner_authorization_has_no_retired_routes_or_link_authority(self):
        source = "\n".join(path.read_text(encoding="utf-8-sig") for path in PRODUCTION_PATHS)
        for forbidden in FORBIDDEN:
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, source)

    def test_canonical_owner_grant_authority_is_present(self):
        client = Path("ordax_dev_agent/product_remote_client.py").read_text(encoding="utf-8")
        studio = Path("ordax_studio/product_web_desktop.py").read_text(encoding="utf-8")
        self.assertIn("/v3/owner/remote-authority", client)
        self.assertIn("/v3/owner/remote-grant-groups", client)
        self.assertIn("owner_remote_authority", studio)
        self.assertIn("replace_owner_remote_grant_group", studio)
        self.assertIn("revoke_owner_remote_grant_group", studio)
        self.assertIn('"computer-observe"', studio)
        self.assertIn("browser_oauth_session", studio)


if __name__ == "__main__":
    unittest.main()
