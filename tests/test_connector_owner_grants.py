from __future__ import annotations

import unittest

from ordax_connector.owner_grants import (
    OwnerMcpClientSelectionError,
    grant_consented_owner_device_profile,
    select_consented_owner_mcp_client,
)

DEVICE = "11111111-1111-4111-8111-111111111111"
OTHER_DEVICE = "22222222-2222-4222-8222-222222222222"
A = "33333333-3333-4333-8333-333333333333"
B = "44444444-4444-4444-8444-444444444444"


def authority(clients=(A, B), groups=(), device=DEVICE):
    return {
        "device_id": device,
        "clients": [{"client_id": item} for item in clients],
        "grant_groups": list(groups),
    }


def previous(client=A, *, device=DEVICE, consent=True, scope="device"):
    return {
        "client_id": client, "device_id": device,
        "profile_key": "computer-observe",
        "scope_kind": scope, "space_id": None, "project_id": None,
        "client_consent_active": consent,
    }


class FakeRemote:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def owner_remote_authority(self, token, *, device_id):
        self.calls.append(("authority", token, device_id))
        return self.response

    def replace_owner_remote_grant_group(self, token, **kwargs):
        self.calls.append(("replace", token, kwargs))
        return {"ok": True, "grant_group_id": "fake"}


class OwnerMcpClientSelectionTests(unittest.TestCase):
    def select(self, value, requested=None):
        return select_consented_owner_mcp_client(
            value, device_id=DEVICE, requested_client_id=requested,
        )

    def assert_denied(self, code, value, requested=None):
        with self.assertRaises(OwnerMcpClientSelectionError) as caught:
            self.select(value, requested)
        self.assertEqual(caught.exception.code, code)

    def test_single_consented_client(self):
        self.assertEqual(self.select(authority(clients=(A,))), A)

    def test_exact_prior_observe_grant_selects_owner_consented_client(self):
        self.assertEqual(self.select(authority(groups=[previous(A)])), A)

    def test_explicit_consented_selection_overrides_prior(self):
        self.assertEqual(self.select(authority(groups=[previous(A)]), requested=B), B)

    def test_no_grants_with_multiple_clients_requires_selection(self):
        self.assert_denied("owner_mcp_client_ambiguous", authority())

    def test_cross_device_grant_does_not_select(self):
        self.assert_denied("owner_mcp_client_ambiguous",
                           authority(groups=[previous(A, device=OTHER_DEVICE)]))

    def test_revoked_oauth_consent_does_not_select(self):
        self.assert_denied("owner_mcp_client_ambiguous",
                           authority(groups=[previous(A, consent=False)]))

    def test_project_scope_does_not_select(self):
        self.assert_denied("owner_mcp_client_ambiguous",
                           authority(groups=[previous(A, scope="project")]))

    def test_two_prior_clients_are_ambiguous(self):
        self.assert_denied("owner_mcp_client_ambiguous",
                           authority(groups=[previous(A), previous(B)]))

    def test_unknown_client_rejected(self):
        self.assert_denied("owner_mcp_client_not_consented",
                           authority(), requested=OTHER_DEVICE)

    def test_device_mismatch_rejected(self):
        self.assert_denied("owner_mcp_device_mismatch",
                           authority(device=OTHER_DEVICE))

    def test_missing_or_invalid_consent(self):
        self.assert_denied("owner_mcp_no_consented_client", authority(clients=()))
        self.assert_denied("owner_mcp_authority_invalid", {
            "device_id": DEVICE, "clients": [{"client_id": "invalid"}],
            "grant_groups": [],
        })

    def test_grant_uses_only_canonical_owner_endpoints(self):
        remote = FakeRemote(authority(groups=[previous(A)]))
        result = grant_consented_owner_device_profile(
            remote, "owner-token", device_id=DEVICE,
            profile_key="computer-filesystem", valid_until="2026-11-17T00:00:00Z",
        )
        self.assertTrue(result["ok"])
        self.assertEqual(remote.calls[0], ("authority", "owner-token", DEVICE))
        self.assertEqual(remote.calls[1][0], "replace")
        self.assertEqual(remote.calls[1][2]["client_id"], A)
        self.assertEqual(remote.calls[1][2]["profile_key"], "computer-filesystem")
        self.assertIsNone(remote.calls[1][2]["project_id"])

    def test_failed_resolution_does_not_grant(self):
        remote = FakeRemote(authority())
        with self.assertRaises(OwnerMcpClientSelectionError):
            grant_consented_owner_device_profile(
                remote, "owner-token", device_id=DEVICE,
                profile_key="computer-filesystem",
            )
        self.assertEqual([call[0] for call in remote.calls], ["authority"])

    def test_disallow_full_control(self):
        remote = FakeRemote(authority(clients=(A,)))
        with self.assertRaises(OwnerMcpClientSelectionError) as caught:
            grant_consented_owner_device_profile(
                remote, "owner-token", device_id=DEVICE,
                profile_key="full-computer-control",
            )
        self.assertEqual(caught.exception.code, "owner_mcp_profile_not_allowed")
        self.assertEqual(remote.calls, [])


if __name__ == "__main__":
    unittest.main()
