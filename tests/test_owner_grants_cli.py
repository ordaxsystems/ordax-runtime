from __future__ import annotations

import http.client
import threading
import unittest
from urllib.parse import parse_qs, urlparse
from unittest.mock import patch

import httpx

from ordax_core.owner_oauth import (
    OwnerOAuthError, browser_authorization_code, exchange_owner_code,
)
from ordax_dev_agent.owner_grants_cli import parse_args, perform, select_client

DEVICE = "24630b00-e9c0-4d93-b38a-2367048cd9d1"
CLIENT = "202fd3e1-b058-4531-8750-82c57e4b6ca0"


class FakeOwner:
    def __init__(self):
        self.authority = {"clients": [{"client_id": CLIENT}], "grant_groups": [
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

    def test_project_grants_require_exact_project_scope(self):
        with self.assertRaises(SystemExit):
            parse_args(["authorize", "--device", DEVICE, "--profile", "project-native-terminal"])
        with self.assertRaises(SystemExit):
            parse_args(["authorize", "--device", DEVICE, "--profile", "computer-filesystem", "--space", DEVICE])
        args = parse_args(["authorize", "--device", DEVICE, "--profile", "project-native-terminal",
                           "--space", DEVICE, "--project", DEVICE, "--days", "14"])
        remote = FakeOwner()
        result = perform(args, "token", remote)
        self.assertEqual(result["group"]["space_id"], DEVICE)
        self.assertEqual(result["group"]["project_id"], DEVICE)

    def test_expiry_invalid_client_and_uuid_fail_closed(self):
        for days in ("0", "366", "1.5", "NaN"):
            with self.assertRaises(SystemExit):
                parse_args(["authorize", "--device", DEVICE, "--profile", "computer-observe", "--days", days])
        with self.assertRaises(SystemExit):
            parse_args(["list", "--device", "../../escape"])
        authority = {"clients": [{"client_id": CLIENT}, {"client_id": DEVICE}]}
        with self.assertRaisesRegex(ValueError, "select an exact"):
            select_client(authority, None)
        self.assertEqual(select_client(authority, CLIENT), CLIENT)

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
