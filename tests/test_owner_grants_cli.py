from __future__ import annotations

import http.client
from datetime import datetime, timedelta, timezone
import threading
import unittest
from urllib.parse import parse_qs, urlparse
from unittest.mock import patch

import httpx

from ordax_core.owner_oauth import (
    OwnerOAuthError, browser_authorization_code, exchange_owner_code,
)
from ordax_dev_agent.owner_grants_cli import parse_args, perform, diagnose_owner_mcp_readiness, eligible_full_control_grants, recoverable_discovery_clients, retain_existing_full_control_grant, _DIAGNOSTIC_ACTIONS
from ordax_dev_agent.product_gateway import PRODUCT_ACTIONS

DEVICE = "24630b00-e9c0-4d93-b38a-2367048cd9d1"
CLIENT = "202fd3e1-b058-4531-8750-82c57e4b6ca0"


class FakeOwner:
    def __init__(self):
        self.authority = {"device_id": DEVICE, "clients": [{"client_id": CLIENT}], "grant_groups": [
            {"grant_group_id": DEVICE, "client_id": CLIENT}
        ]}
        self.calls = []

    def owner_remote_authority(self, token, *, device_id):
        self.calls.append(("authority", token, device_id))
        return self.authority

    def replace_owner_remote_grant_group(self, token, **kwargs):
        self.calls.append(("grant", token, kwargs))
        return {"ok": True, "group": kwargs}

    def revoke_owner_remote_grant_group(self, token, **kwargs):
        self.calls.append(("revoke", token, kwargs))
        return {"ok": True}


def canonical_diag_grants():
    return [
        {"capability": name,
         "access_mode": "read" if PRODUCT_ACTIONS[name].effect == "read" else "write"}
        for name in _DIAGNOSTIC_ACTIONS
    ]


class OwnerGrantsCliTests(unittest.TestCase):
    def test_device_scoped_grant_does_not_need_project_or_space(self):
        args = parse_args(["authorize", "--device", DEVICE, "--profile", "interactive-computer-control"])
        self.assertEqual((args.space, args.project, args.days), (None, None, 30))
        remote = FakeOwner()
        result = perform(args, "opaque-owner-token", remote)
        self.assertTrue(result["ok"])
        self.assertEqual(result["group"]["profile_key"], "interactive-computer-control")
        self.assertIsNone(result["group"]["project_id"])
        self.assertIsNone(result["group"]["space_id"])

    def test_full_control_requires_local_full_access(self):
        args = parse_args(["authorize", "--device", DEVICE, "--profile", "full-computer-control"])
        remote = FakeOwner()
        with self.assertRaisesRegex(ValueError, "local_full_access_required"):
            perform(args, "token", remote, policy_checker=lambda _: {"enabled": True, "full_access": False},
                    config=object())
        self.assertEqual(len(remote.calls), 1)
        perform(args, "token", remote, policy_checker=lambda _: {"enabled": True, "full_access": True},
                config=object())
        self.assertEqual(remote.calls[-1][0], "grant")

    def test_device_terminal_grant_requires_local_full_access(self):
        args = parse_args(["authorize", "--device", DEVICE, "--profile", "device-native-terminal"])
        self.assertIsNone(args.project)
        self.assertIsNone(args.space)
        remote = FakeOwner()
        for policy in ({"enabled": False, "full_access": True},
                       {"enabled": True, "full_access": False}):
            with self.assertRaisesRegex(ValueError, "local_full_access_required"):
                perform(args, "token", remote, policy_checker=lambda _: policy, config=object())
            self.assertFalse(any(call[0] == "grant" for call in remote.calls))
        result = perform(args, "token", remote,
                         policy_checker=lambda _: {"enabled": True, "full_access": True},
                         config=object())
        self.assertTrue(result["ok"])
        self.assertEqual(result["group"]["profile_key"], "device-native-terminal")
        self.assertIsNone(result["group"]["space_id"])
        self.assertIsNone(result["group"]["project_id"])

    def test_device_process_grant_requires_full_access_and_device_scope(self):
        args = parse_args(["authorize", "--device", DEVICE, "--profile", "device-native-process"])
        remote = FakeOwner()
        with self.assertRaisesRegex(ValueError, "local_full_access_required"):
            perform(args, "token", remote,
                    policy_checker=lambda _: {"enabled": True, "full_access": False},
                    config=object())
        self.assertFalse(any(call[0] == "grant" for call in remote.calls))
        result = perform(args, "token", remote,
                         policy_checker=lambda _: {"enabled": True, "full_access": True},
                         config=object())
        self.assertTrue(result["ok"])
        self.assertIsNone(result["group"]["space_id"])
        self.assertIsNone(result["group"]["project_id"])

    def test_project_grants_require_exact_project_scope(self):
        # The CLI validates paired identifiers, while the Control Plane
        # alone decides which named profiles require a project.
        with self.assertRaises(SystemExit):
            parse_args(["authorize", "--device", DEVICE, "--profile", "project-native-terminal", "--space", DEVICE])
        with self.assertRaises(SystemExit):
            parse_args(["authorize", "--device", DEVICE, "--profile", "computer-filesystem", "--project", DEVICE])
        args = parse_args(["authorize", "--device", DEVICE, "--profile", "project-native-terminal",
                           "--space", DEVICE, "--project", DEVICE, "--days", "14"])
        remote = FakeOwner()
        result = perform(args, "token", remote)
        self.assertEqual(result["group"]["space_id"], DEVICE)
        self.assertEqual(result["group"]["project_id"], DEVICE)

    def test_profile_names_are_owned_by_control_plane(self):
        # New/removed server profiles must never require updating a
        # duplicate client-side catalog. The authoritative API refuses
        # unrecognized names and inappropriate scope combinations.
        class RejectingOwner(FakeOwner):
            def replace_owner_remote_grant_group(self, token, **kwargs):
                raise ValueError("owner_grant_invalid")

        args = parse_args(["authorize", "--device", DEVICE, "--profile", "new-device-profile"])
        with self.assertRaisesRegex(ValueError, "owner_grant_invalid"):
            perform(args, "token", RejectingOwner())
        with self.assertRaises(SystemExit):
            parse_args(["authorize", "--device", DEVICE, "--profile", "../invalid"])

    def test_expiry_invalid_client_and_uuid_fail_closed(self):
        for days in ("0", "366", "1.5", "NaN"):
            with self.assertRaises(SystemExit):
                parse_args(["authorize", "--device", DEVICE, "--profile", "computer-observe", "--days", days])
        with self.assertRaises(SystemExit):
            parse_args(["list", "--device", "../../escape"])
        remote = FakeOwner()
        remote.authority["clients"] = [
            {"client_id": CLIENT}, {"client_id": DEVICE},
        ]
        args = parse_args([
            "authorize", "--device", DEVICE, "--profile", "computer-observe",
        ])
        with self.assertRaisesRegex(ValueError, "owner_mcp_client_ambiguous"):
            perform(args, "token", remote)
        self.assertEqual([call[0] for call in remote.calls], ["authority"])

        selected = parse_args([
            "authorize", "--device", DEVICE,
            "--profile", "computer-observe", "--client", CLIENT,
        ])
        result = perform(selected, "token", remote)
        self.assertTrue(result["ok"])
        self.assertEqual(remote.calls[-1][2]["client_id"], CLIENT)

    def test_owner_authority_wrong_device_blocks_every_cli_entrypoint(self):
        # The same trusted response may not be replayed from another device,
        # even for inventory or diagnosis, which could expose another owner's
        # grants and guide a mistaken authorization.
        operation_args = (
            ["list", "--device", DEVICE],
            ["diagnose", "--device", DEVICE],
            ["authorize", "--device", DEVICE, "--profile", "computer-observe"],
            ["revoke", "--device", DEVICE, "--group", DEVICE],
            ["central-targets", "--device", DEVICE],
            ["central-selections", "--device", DEVICE],
            ["dev-enable", "--device", DEVICE],
        )
        for argv in operation_args:
            with self.subTest(operation=argv[0]):
                remote = FakeOwner()
                remote.authority["device_id"] = (
                    "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
                )
                with self.assertRaisesRegex(ValueError, "owner_mcp_device_mismatch"):
                    perform(parse_args(argv), "token", remote, config=object())
                self.assertEqual([call[0] for call in remote.calls], ["authority"])

    def test_invalid_owner_authority_never_selects_or_grants(self):
        for clients in (
            [],
            [{"client_id": "not-a-uuid"}],
            [{"client_id": CLIENT}, {"client_id": "not-a-uuid"}],
        ):
            with self.subTest(clients=clients):
                remote = FakeOwner()
                remote.authority["clients"] = clients
                with self.assertRaisesRegex(
                    ValueError, "owner_mcp_no_consented_client|owner_mcp_authority_invalid",
                ):
                    perform(parse_args([
                        "authorize", "--device", DEVICE,
                        "--profile", "computer-observe",
                    ]), "token", remote)
                self.assertEqual([call[0] for call in remote.calls], ["authority"])

    def test_revocation_matches_client_and_group(self):
        remote = FakeOwner()
        args = parse_args(["revoke", "--device", DEVICE, "--group", DEVICE])
        self.assertTrue(perform(args, "token", remote)["ok"])
        self.assertEqual(remote.calls[-1][0], "revoke")
        remote = FakeOwner()
        remote.authority["grant_groups"][0]["client_id"] = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
        with self.assertRaisesRegex(ValueError, "not_found"):
            perform(args, "token", remote)
        self.assertEqual(len(remote.calls), 1)

    def test_list_is_read_only(self):
        remote = FakeOwner()
        result = perform(parse_args(["list", "--device", DEVICE]), "token", remote)
        self.assertEqual(result["clients"][0]["client_id"], CLIENT)
        self.assertEqual(len(remote.calls), 1)

    def test_diagnose_is_read_only_and_distinguishes_grants_from_e2e(self):
        remote = FakeOwner()
        remote.authority["grant_groups"] = [{
            "client_id": CLIENT, "client_consent_active": True,
            "scope_kind": "device", "device_id": DEVICE,
            "space_id": None, "project_id": None,
            "valid_until": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
            "capabilities": canonical_diag_grants(),
        }]
        args = parse_args(["diagnose", "--device", DEVICE, "--client", CLIENT])
        result = perform(args, "token", remote, config=object(),
                         policy_checker=lambda _: {"enabled": True, "full_access": True})
        self.assertEqual(result["state"], "preflight-ready-unproven")
        self.assertFalse(result["endToEndProven"])
        self.assertFalse(result["grantMutationPerformed"])
        self.assertEqual(result["missingClientGrants"], [])
        self.assertEqual([call[0] for call in remote.calls], ["authority"])

    def test_diagnose_skips_scoped_expired_and_other_client_grants(self):
        past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        authority = {
            "device_id": DEVICE,
            "grant_groups": [
                {"client_id": CLIENT, "client_consent_active": True,
                 "scope_kind": "project", "device_id": DEVICE,
                 "project_id": DEVICE, "capabilities": canonical_diag_grants()},
                {"client_id": CLIENT, "client_consent_active": True,
                 "scope_kind": "device", "device_id": DEVICE,
                 "valid_until": past, "capabilities": canonical_diag_grants()},
                {"client_id": DEVICE, "client_consent_active": True,
                 "scope_kind": "device", "device_id": DEVICE, "capabilities": canonical_diag_grants()},
                {"client_id": CLIENT, "client_consent_active": True,
                 "scope_kind": "device", "device_id": DEVICE,
                 "valid_until": "bad-date", "capabilities": canonical_diag_grants()},
            ],
        }
        result = diagnose_owner_mcp_readiness(authority, CLIENT,
                                             {"enabled": True, "full_access": True})
        self.assertEqual(result["state"], "blocked")
        self.assertEqual(set(result["missingClientGrants"]), set(_DIAGNOSTIC_ACTIONS))

    def test_diagnose_does_not_mistake_full_access_for_oauth_consent(self):
        remote = FakeOwner()
        remote.authority["grant_groups"] = []
        result = perform(parse_args(["diagnose", "--device", DEVICE]), "token", remote,
                         config=object(), policy_checker=lambda _: {
                             "enabled": True, "full_access": True,
                         })
        self.assertEqual(result["state"], "blocked")
        self.assertTrue(result["localFullAccess"])
        self.assertIn("computer.terminal_exec", result["missingClientGrants"])
        self.assertFalse(result["endToEndProven"])
        self.assertEqual(len(remote.calls), 1)

    def test_diagnose_rejects_wrong_grant_access_mode(self):
        capabilities = canonical_diag_grants()
        for item in capabilities:
            if item["capability"] == "computer.click":
                item["access_mode"] = "read"
        result = diagnose_owner_mcp_readiness(
            {"device_id": DEVICE, "grant_groups": [{
                "client_id": CLIENT, "client_consent_active": True, "scope_kind": "device",
                "device_id": DEVICE, "capabilities": capabilities,
            }]},
            CLIENT, {"enabled": True, "full_access": True},
        )
        self.assertEqual(result["state"], "blocked")
        self.assertIn("computer.click", result["missingClientGrants"])

    def test_diagnose_never_inherits_revoked_or_other_device_grants(self):
        valid_group = {
            "client_id": CLIENT, "client_consent_active": True,
            "scope_kind": "device", "device_id": DEVICE,
            "space_id": None, "project_id": None,
            "capabilities": canonical_diag_grants(),
        }
        for tamper in (
            {"client_consent_active": False},
            {"client_consent_active": None},
            {"device_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"},
            {"scope_kind": "project"},
            {"space_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"},
        ):
            with self.subTest(tamper=tamper):
                group = {**valid_group, **tamper}
                result = diagnose_owner_mcp_readiness(
                    {"device_id": DEVICE, "grant_groups": [group]},
                    CLIENT, {"enabled": True, "full_access": True},
                )
                self.assertEqual(result["state"], "blocked")
                self.assertEqual(set(result["missingClientGrants"]), set(_DIAGNOSTIC_ACTIONS))

    def test_diagnose_fails_closed_without_owner_device_identity(self):
        with self.assertRaisesRegex(ValueError, "owner_authority_device_invalid"):
            diagnose_owner_mcp_readiness(
                {"grant_groups": []}, CLIENT, {"enabled": True, "full_access": True},
            )

    def test_diagnose_denies_disabled_local_policy_even_with_grants(self):
        authority = {"device_id": DEVICE, "grant_groups": [{
            "client_id": CLIENT, "client_consent_active": True, "scope_kind": "device",
            "device_id": DEVICE, "capabilities": canonical_diag_grants(),
        }]}
        result = diagnose_owner_mcp_readiness(
            authority, CLIENT, {"enabled": False, "full_access": True},
        )
        self.assertEqual(result["state"], "blocked")
        self.assertEqual(result["missingClientGrants"], [])
        self.assertFalse(result["localPolicyEnabled"])

    def test_existing_full_control_expiry_requires_exact_consent_and_device(self):
        from types import SimpleNamespace
        now = datetime(2026, 11, 2, tzinfo=timezone.utc)
        base_group = {
            "grant_group_id": DEVICE, "device_id": DEVICE,
            "client_id": CLIENT, "client_consent_active": True,
            "scope_kind": "device", "space_id": None, "project_id": None,
            "profile_key": "full-computer-control",
            "valid_until": (now + timedelta(days=1)).isoformat(),
        }
        authority = {
            "device_id": DEVICE, "clients": [{"client_id": CLIENT}],
            "grant_groups": [base_group],
        }
        entries = eligible_full_control_grants(authority, device_id=DEVICE, now=now)
        self.assertEqual(entries, [(CLIENT, DEVICE, now + timedelta(days=1))])
        for invalid in (
            {"client_consent_active": False},
            {"profile_key": "computer-observe"},
            {"device_id": "11111111-1111-4111-8111-111111111111"},
            {"scope_kind": "project"},
            {"project_id": DEVICE},
            {"valid_until": (now - timedelta(minutes=1)).isoformat()},
            {"valid_until": "not-a-date"},
        ):
            with self.subTest(invalid=invalid):
                filtered = eligible_full_control_grants(
                    {**authority, "grant_groups": [{**base_group, **invalid}]},
                    device_id=DEVICE, now=now,
                )
                self.assertEqual(filtered, [])
        permanent = {**base_group, "valid_until": None}
        self.assertEqual(
            eligible_full_control_grants(
                {**authority, "grant_groups": [permanent]},
                device_id=DEVICE, now=now,
            ),
            [(CLIENT, DEVICE, None)],
        )
        missing_expiry = dict(base_group)
        del missing_expiry["valid_until"]
        self.assertEqual(
            eligible_full_control_grants(
                {**authority, "grant_groups": [missing_expiry]},
                device_id=DEVICE, now=now,
            ), [],
        )
        # A real, finite grant remains eligible even if it does not
        # expire within seven days; no periodic renewal schedule is needed.
        long_lived = {**base_group,
                      "valid_until": (now + timedelta(days=30)).isoformat()}
        self.assertEqual(
            len(eligible_full_control_grants(
                {**authority, "grant_groups": [long_lived]},
                device_id=DEVICE, now=now,
            )), 1,
        )
        with self.assertRaises(ValueError):
            eligible_full_control_grants(
                {**authority, "device_id": CLIENT}, device_id=DEVICE, now=now,
            )

    def test_full_control_retention_only_reuses_authorized_scope(self):
        from types import SimpleNamespace
        now = datetime(2026, 11, 2, tzinfo=timezone.utc)
        authority = {
            "device_id": DEVICE, "clients": [{"client_id": CLIENT}],
            "grant_groups": [{
                "grant_group_id": DEVICE, "device_id": DEVICE,
                "client_id": CLIENT, "client_consent_active": True,
                "scope_kind": "device", "space_id": None, "project_id": None,
                "profile_key": "full-computer-control",
                "valid_until": (now + timedelta(days=1)).isoformat(),
            }],
        }
        class StoredOwner(FakeOwner):
            def __init__(self):
                super().__init__()
                self.authority = authority

            def replace_owner_remote_grant_group(self, token, **kwargs):
                super().replace_owner_remote_grant_group(token, **kwargs)
                self.authority = {
                    **authority,
                    "grant_groups": [
                        {**authority["grant_groups"][0], "valid_until": None},
                    ],
                }
                return {"ok": True, "grant_group_id": DEVICE}

        remote = StoredOwner()
        policy = {"enabled": True, "full_access": True, "full_filesystem": True}
        result = retain_existing_full_control_grant(
            authority, device_id=DEVICE, client_id=CLIENT,
            token="opaque", remote=remote, config=SimpleNamespace(device_id=DEVICE),
            now=now, policy_checker=lambda _cfg: policy,
        )
        self.assertTrue(result["ok"])
        self.assertFalse(result["endToEndProven"])
        self.assertTrue(result["grantMutationPerformed"])
        self.assertIsNone(result["valid_until"])
        self.assertEqual([x[0] for x in remote.calls], ["grant", "authority"])
        args = remote.calls[0][2]
        self.assertEqual(args, {
            "client_id": CLIENT, "device_id": DEVICE,
            "profile_key": "full-computer-control",
            "space_id": None, "project_id": None,
            "valid_until": None,
        })
        for invalid in (
            {"enabled": False, "full_access": True, "full_filesystem": True},
            {"enabled": True, "full_access": False, "full_filesystem": True},
            {"enabled": True, "full_access": True, "full_filesystem": False},
        ):
            prior = len(remote.calls)
            with self.subTest(invalid=invalid), self.assertRaisesRegex(
                ValueError, "owner_retention_local_full_access_required"
            ):
                retain_existing_full_control_grant(
                    authority, device_id=DEVICE, client_id=CLIENT,
                    token="opaque", remote=remote,
                    config=SimpleNamespace(device_id=DEVICE),
                    now=now, policy_checker=lambda _cfg: invalid,
                )
            self.assertEqual(len(remote.calls), prior)
        for client, device in (
            (DEVICE, DEVICE),
            (CLIENT, "11111111-1111-4111-8111-111111111111"),
        ):
            count = len(remote.calls)
            with self.subTest(client=client, device=device):
                with self.assertRaises(ValueError):
                    retain_existing_full_control_grant(
                        authority, device_id=device, client_id=client,
                        token="opaque", remote=remote,
                        config=SimpleNamespace(device_id=DEVICE), now=now,
                        policy_checker=lambda _: policy,
                    )
                self.assertEqual(len(remote.calls), count)

    def test_indefinite_grant_requires_authoritative_readback_before_success(self):
        from types import SimpleNamespace
        now = datetime(2026, 11, 2, tzinfo=timezone.utc)
        original = {
            "grant_group_id": DEVICE, "device_id": DEVICE,
            "client_id": CLIENT, "client_consent_active": True,
            "scope_kind": "device", "space_id": None, "project_id": None,
            "profile_key": "full-computer-control",
            "valid_until": (now + timedelta(days=1)).isoformat(),
        }
        authority = {
            "device_id": DEVICE, "clients": [{"client_id": CLIENT}],
            "grant_groups": [original],
        }

        class WriteAcceptedOwner(FakeOwner):
            def __init__(self, readback):
                super().__init__()
                self.authority = readback

            def replace_owner_remote_grant_group(self, token, **kwargs):
                super().replace_owner_remote_grant_group(token, **kwargs)
                return {"ok": True, "grant_group_id": DEVICE}

        invalid_readbacks = (
            authority,  # The finite expiry persisted despite HTTP 200.
            {**authority, "grant_groups": [{**original, "valid_until": None,
                                            "client_consent_active": False}]},
            {**authority, "grant_groups": [{**original, "valid_until": None,
                                            "client_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"}]},
            {**authority, "grant_groups": [{**original, "valid_until": None,
                                            "device_id": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"}]},
            {**authority, "grant_groups": [{**original, "valid_until": None,
                                            "profile_key": "computer-observe"}]},
            {**authority, "grant_groups": [{**original, "valid_until": None,
                                            "grant_group_id": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"}]},
            {**authority, "grant_groups": [{**original, "valid_until": None,
                                            "scope_kind": "project"}]},
            {**authority, "device_id": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"},
        )
        for readback in invalid_readbacks:
            with self.subTest(readback=readback):
                remote = WriteAcceptedOwner(readback)
                with self.assertRaises(ValueError):
                    retain_existing_full_control_grant(
                        authority, device_id=DEVICE, client_id=CLIENT,
                        token="opaque", remote=remote,
                        config=SimpleNamespace(device_id=DEVICE),
                        now=now, policy_checker=lambda _: {
                            "enabled": True, "full_access": True,
                            "full_filesystem": True,
                        },
                    )
                # Never repeat a potentially committed write upon an uncertain
                # readback; Owner must verify identity manually.
                self.assertEqual(
                    [event[0] for event in remote.calls].count("grant"), 1,
                )
                self.assertEqual(
                    [event[0] for event in remote.calls].count("authority"), 1,
                )

    def test_retention_refuses_duplicate_or_revoked_grants(self):
        from types import SimpleNamespace
        now = datetime(2026, 11, 2, tzinfo=timezone.utc)
        group = {
            "grant_group_id": DEVICE, "device_id": DEVICE,
            "client_id": CLIENT, "client_consent_active": True,
            "scope_kind": "device", "space_id": None, "project_id": None,
            "profile_key": "full-computer-control",
            "valid_until": (now + timedelta(days=1)).isoformat(),
        }
        auth = {"device_id": DEVICE, "clients": [{"client_id": CLIENT}],
                "grant_groups": [group, group]}
        with self.assertRaisesRegex(ValueError, "duplicate"):
            eligible_full_control_grants(auth, device_id=DEVICE, now=now)
        remote = FakeOwner()
        for grants in ([], [{**group, "client_consent_active": False}]):
            before = len(remote.calls)
            with self.assertRaisesRegex(ValueError, "existing_grant_required"):
                retain_existing_full_control_grant(
                    {**auth, "grant_groups": grants}, device_id=DEVICE,
                    client_id=CLIENT, token="opaque", remote=remote,
                    config=SimpleNamespace(device_id=DEVICE), now=now,
                    policy_checker=lambda _: {
                        "enabled": True, "full_access": True, "full_filesystem": True,
                    },
                )
            self.assertEqual(len(remote.calls), before)

    def test_expired_owner_access_recovers_only_from_preexisting_mcp_discovery(self):
        from types import SimpleNamespace
        now = datetime(2026, 11, 2, tzinfo=timezone.utc)
        discovery = {
            "grant_group_id": DEVICE, "device_id": DEVICE,
            "client_id": CLIENT, "client_consent_active": True,
            "scope_kind": "device", "space_id": None, "project_id": None,
            "profile_key": "mcp-discovery", "valid_until": None,
        }
        authority = {
            "device_id": DEVICE,
            "clients": [{"client_id": CLIENT, "client_name": "ChatGPT OrdaX"}],
            # Expired full-control groups are absent from canonical inventory.
            "grant_groups": [discovery],
        }
        self.assertEqual(
            recoverable_discovery_clients(authority, device_id=DEVICE, now=now),
            [(CLIENT, "ChatGPT OrdaX")],
        )

        class RecoveringOwner(FakeOwner):
            def __init__(self):
                super().__init__()
                self.authority = authority

            def replace_owner_remote_grant_group(self, token, **kwargs):
                self.calls.append(("grant", token, kwargs))
                self.authority = {
                    **authority,
                    "grant_groups": [
                        discovery, {
                            **discovery,
                            "grant_group_id": CLIENT,
                            "profile_key": "full-computer-control",
                        },
                    ],
                }
                return {"ok": True, "grant_group_id": CLIENT}

        remote = RecoveringOwner()
        result = retain_existing_full_control_grant(
            authority, device_id=DEVICE, client_id=CLIENT, token="owner-token",
            remote=remote, config=SimpleNamespace(device_id=DEVICE),
            now=now, restore_from_discovery=True,
            policy_checker=lambda _: {
                "enabled": True, "full_access": True, "full_filesystem": True,
            },
        )
        self.assertTrue(result["ok"])
        self.assertIsNone(result["valid_until"])
        self.assertEqual([call[0] for call in remote.calls], ["grant", "authority"])
        self.assertEqual(remote.calls[0][2], {
            "client_id": CLIENT, "device_id": DEVICE,
            "profile_key": "full-computer-control",
            "space_id": None, "project_id": None, "valid_until": None,
        })

    def test_recovery_never_converts_oauth_consent_alone_into_computer_access(self):
        from types import SimpleNamespace
        now = datetime(2026, 11, 2, tzinfo=timezone.utc)
        discovery = {
            "grant_group_id": DEVICE, "device_id": DEVICE,
            "client_id": CLIENT, "client_consent_active": True,
            "scope_kind": "device", "space_id": None, "project_id": None,
            "profile_key": "mcp-discovery", "valid_until": None,
        }
        authority = {
            "device_id": DEVICE,
            "clients": [{"client_id": CLIENT, "client_name": "ChatGPT"}],
            "grant_groups": [discovery],
        }
        denied_catalogs = [
            {**authority, "grant_groups": []},
            {**authority, "grant_groups": [{**discovery,
                                            "client_consent_active": False}]},
            {**authority, "grant_groups": [{**discovery,
                                            "profile_key": "computer-observe"}]},
            {**authority, "grant_groups": [{**discovery,
                                            "scope_kind": "project"}]},
            {**authority, "grant_groups": [{**discovery,
                                            "device_id": CLIENT}]},
            {**authority, "grant_groups": [{**discovery,
                                            "valid_until": (now - timedelta(seconds=1)).isoformat()}]},
            {**authority, "grant_groups": [{k: v for k, v in discovery.items()
                                              if k != "valid_until"}]},
            {**authority, "clients": []},
        ]
        for denied in denied_catalogs:
            with self.subTest(denied=denied):
                remote = FakeOwner()
                with self.assertRaises(ValueError):
                    retain_existing_full_control_grant(
                        denied, device_id=DEVICE, client_id=CLIENT,
                        token="owner-token", remote=remote,
                        config=SimpleNamespace(device_id=DEVICE),
                        now=now, restore_from_discovery=True,
                        policy_checker=lambda _: {
                            "enabled": True, "full_access": True,
                            "full_filesystem": True,
                        },
                    )
                self.assertFalse(any(event[0] == "grant" for event in remote.calls))
        remote = FakeOwner()
        with self.assertRaises(ValueError):
            retain_existing_full_control_grant(
                authority, device_id=DEVICE, client_id=CLIENT,
                token="owner-token", remote=remote,
                config=SimpleNamespace(device_id=DEVICE),
                now=now, restore_from_discovery="yes",
                policy_checker=lambda _: {"enabled": True,
                                          "full_access": True,
                                          "full_filesystem": True},
            )
        self.assertEqual(remote.calls, [])

    def test_recovery_excludes_existing_full_access_even_with_discovery(self):
        now = datetime(2026, 11, 2, tzinfo=timezone.utc)
        group = {
            "grant_group_id": DEVICE, "device_id": DEVICE,
            "client_id": CLIENT, "client_consent_active": True,
            "scope_kind": "device", "space_id": None, "project_id": None,
            "valid_until": None,
        }
        authority = {
            "device_id": DEVICE,
            "clients": [{"client_id": CLIENT, "client_name": "ChatGPT"}],
            "grant_groups": [
                {**group, "profile_key": "mcp-discovery"},
                {**group, "profile_key": "full-computer-control"},
            ],
        }
        self.assertEqual(
            recoverable_discovery_clients(authority, device_id=DEVICE, now=now),
            [],
        )

    def test_discovery_client_name_keeps_normal_letters_and_removes_controls(self):
        now = datetime(2026, 11, 2, tzinfo=timezone.utc)
        authority = {
            "device_id": DEVICE,
            "clients": [{"client_id": CLIENT, "client_name": "OrdaX Runtime\nChatGPT"}],
            "grant_groups": [{
                "grant_group_id": DEVICE, "device_id": DEVICE,
                "client_id": CLIENT, "client_consent_active": True,
                "scope_kind": "device", "space_id": None, "project_id": None,
                "valid_until": None, "profile_key": "mcp-discovery",
            }],
        }
        self.assertEqual(
            recoverable_discovery_clients(authority, device_id=DEVICE, now=now),
            [(CLIENT, "OrdaX RuntimeChatGPT")],
        )

    def test_owner_oauth_pkce_exact_loopback_and_state(self):
        def open_browser(url, **kwargs):
            q = parse_qs(urlparse(url).query)
            self.assertEqual(q["code_challenge_method"], ["S256"])
            self.assertEqual(kwargs, {"new": 1, "autoraise": True})
            self.assertEqual(len(q["code_challenge"][0]), 43)
            def callback():
                conn = http.client.HTTPConnection("127.0.0.1", 8766, timeout=5)
                from urllib.parse import urlencode
                conn.request("GET", "/oauth/callback?" + urlencode({"state": q["state"][0], "code": "good-code"}),
                             headers={"Host": "127.0.0.1:8766"})
                self.assertEqual(conn.getresponse().status, 200)
                conn.close()
            threading.Thread(target=callback, daemon=True).start()
            return True
        code, verifier = browser_authorization_code(open_browser=open_browser, timeout_seconds=5)
        self.assertEqual(code, "good-code")
        self.assertGreaterEqual(len(verifier), 43)

    def test_owner_oauth_never_exchanges_failed_browser_session(self):
        with self.assertRaisesRegex(OwnerOAuthError, "browser_unavailable"):
            browser_authorization_code(open_browser=lambda *_a, **_kw: False, timeout_seconds=1)

    def test_owner_oauth_exchange_uses_registered_public_client(self):
        def handler(req):
            form = parse_qs(req.content.decode())
            self.assertEqual(form["code_verifier"], ["v" * 64])
            self.assertNotIn("client_secret", form)
            return httpx.Response(200, json={"access_token": "opaque-test-token-value"})
        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            token = exchange_owner_code("authorization-code", "v" * 64, http=client)
        self.assertEqual(token, "opaque-test-token-value")


if __name__ == "__main__":
    unittest.main()
