"""Owner-driven development access: local opt-in and canonical grants only."""
from __future__ import annotations

import unittest
from types import SimpleNamespace

from ordax_dev_agent.owner_grants_cli import parse_args, perform

DEVICE = "0a563924-e4d1-4328-bcb0-f73820c92e1b"
DESKTOP = "24630b00-e9c0-4d93-b38a-2367048cd9d1"
CLIENT = "202fd3e1-b058-4531-8750-82c57e4b6ca0"


class OwnerRemote:
    def __init__(self):
        self.calls = []

    def owner_remote_authority(self, token, *, device_id):
        self.calls.append(("authority", token, device_id))
        return {"device_id": device_id, "clients": [{"client_id": CLIENT}], "grant_groups": []}

    def replace_owner_remote_grant_group(self, token, **kwargs):
        self.calls.append(("grant", token, kwargs))
        return {"ok": True, "receipt": "remote-owner-authorized"}


class OwnerDevelopmentAccessTests(unittest.TestCase):
    def setUp(self):
        self.args = parse_args(["dev-enable", "--device", DEVICE, "--client", CLIENT, "--days", "3"])
        self.owner = OwnerRemote()
        self.config = SimpleNamespace(device_id=DEVICE)
        self.updates = []

    def update(self, config, payload):
        self.updates.append((config, payload))
        return {"enabled": True, "full_access": True, "full_filesystem": True}

    @staticmethod
    def status(config):
        return {"enabled": False, "full_access": False, "full_filesystem": False,
                "revision": "previous-sha256"}

    def perform(self, *, config=None, checker=None, updater=None):
        return perform(
            self.args, "owner-token-in-memory", self.owner,
            config=self.config if config is None else config,
            policy_checker=self.status if checker is None else checker,
            policy_updater=self.update if updater is None else updater,
        )

    def test_dev_enable_requires_owner_authority_and_exact_client_and_local_opt_in(self):
        result = self.perform()
        self.assertTrue(result["ok"])
        self.assertEqual(result["device_id"], DEVICE)
        self.assertEqual(result["profile_key"], "full-computer-control")
        self.assertTrue(result["local_policy"]["full_filesystem"])
        self.assertFalse(result["end_to_end_proven"])
        self.assertEqual(len(self.updates), 1)
        config, payload = self.updates[0]
        self.assertIs(config, self.config)
        self.assertEqual(payload, {
            "enabled": True, "full_access": True, "full_filesystem": True,
            "expected_revision": "previous-sha256",
        })
        self.assertEqual([call[0] for call in self.owner.calls], ["authority", "grant"])
        token, grant = self.owner.calls[1][1:]
        self.assertEqual(token, "owner-token-in-memory")
        self.assertEqual(grant["client_id"], CLIENT)
        self.assertEqual(grant["device_id"], DEVICE)
        self.assertEqual(grant["profile_key"], "full-computer-control")
        self.assertIsNone(grant["space_id"])
        self.assertIsNone(grant["project_id"])
        self.assertIn("+00:00", grant["valid_until"])

    def test_cannot_change_another_computers_local_settings(self):
        with self.assertRaisesRegex(ValueError, "development_access_must_run_on_target_device"):
            self.perform(config=SimpleNamespace(device_id=DESKTOP))
        self.assertFalse(self.updates)
        self.assertEqual([call[0] for call in self.owner.calls], ["authority"])

    def test_missing_local_device_identity_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "development_access_must_run_on_target_device"):
            self.perform(config=SimpleNamespace(device_id=None))
        self.assertFalse(self.updates)

    def test_missing_revision_never_changes_settings_or_grants(self):
        with self.assertRaisesRegex(ValueError, "computer_access_revision_unavailable"):
            self.perform(checker=lambda _cfg: {})
        self.assertFalse(self.updates)
        self.assertFalse(any(row[0] == "grant" for row in self.owner.calls))

    def test_environment_override_must_allow_effective_full_access(self):
        def ignored(_cfg, _payload):
            return {"enabled": True, "full_access": False, "full_filesystem": True}
        with self.assertRaisesRegex(ValueError, "development_local_access_not_enabled"):
            self.perform(updater=ignored)
        self.assertFalse(any(row[0] == "grant" for row in self.owner.calls))

    def test_client_consent_required_before_local_settings_change(self):
        self.args = parse_args([
            "dev-enable", "--device", DEVICE,
            "--client", DESKTOP,
        ])
        with self.assertRaisesRegex(ValueError, "no consented OAuth client"):
            self.perform()
        self.assertFalse(self.updates)

    def test_explicit_bounded_expiry_is_required(self):
        for invalid in ("0", "366", "-1"):
            with self.assertRaises(SystemExit):
                parse_args(["dev-enable", "--device", DEVICE, "--days", invalid])
        self.assertEqual(parse_args(["dev-enable", "--device", DEVICE]).days, 30)


if __name__ == "__main__":
    unittest.main()
