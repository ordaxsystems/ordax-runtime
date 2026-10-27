from __future__ import annotations

import json
import unittest

import httpx

from ordax_dev_agent.product_remote_client import ProductRemoteClient, ProductRemoteError

CLIENT = "11111111-1111-4111-8111-111111111111"
TICKET = "22222222-2222-4222-8222-222222222222"
DESKTOP = "33333333-3333-4333-8333-333333333333"
NOTEBOOK = "44444444-4444-4444-8444-444444444444"


class OwnerCentralDeviceSelectionClientTests(unittest.TestCase):
    def test_owner_can_inspect_then_explicitly_select_without_grants(self):
        calls = []

        def respond(req: httpx.Request) -> httpx.Response:
            calls.append((req.method, req.url.path, dict(req.url.params)))
            self.assertEqual(req.headers["authorization"], "Bearer signed-owner")
            if req.url.path.endswith("central-session-targets"):
                self.assertEqual(req.method, "GET")
                self.assertEqual(req.url.params["client_id"], CLIENT)
                return httpx.Response(200, json={
                    "ok": True, "client_id": CLIENT, "eligible_device_count": 2,
                    "targets": [{"device_id": NOTEBOOK}, {"device_id": DESKTOP}],
                    "state": "owner_selection_required", "attested": False,
                    "dispatch_authorized": False, "owner_choice_performed": False,
                })
            if req.method == "GET":
                return httpx.Response(200, json={
                    "ok": True, "count": 1, "attested": False,
                    "dispatch_authorized": False, "pending_selections": [{
                        "challenge_id": TICKET, "created_at": "2026-10-27T04:00:00Z",
                        "expires_at": "2026-10-27T04:05:00Z",
                    }],
                })
            self.assertEqual(req.method, "POST")
            self.assertEqual(json.loads(req.content), {
                "client_id": CLIENT, "challenge_id": TICKET, "device_id": DESKTOP,
            })
            return httpx.Response(200, json={
                "ok": True, "status": "pending", "challenge_id": TICKET,
                "device_id": DESKTOP, "attested": False, "dispatch_authorized": False,
            })

        remote = ProductRemoteClient(
            "https://mcp.example.test",
            http=httpx.Client(transport=httpx.MockTransport(respond)),
        )
        view = remote.owner_central_session_targets("signed-owner", client_id=CLIENT)
        self.assertEqual(view["state"], "owner_selection_required")
        tickets = remote.owner_central_session_selections("signed-owner", client_id=CLIENT)
        self.assertEqual(tickets["pending_selections"][0]["challenge_id"], TICKET)
        chosen = remote.owner_select_central_device(
            "signed-owner", client_id=CLIENT, challenge_id=TICKET, device_id=DESKTOP)
        self.assertEqual(chosen["status"], "pending")
        self.assertFalse(chosen["attested"])
        self.assertEqual([x[0] for x in calls], ["GET", "GET", "POST"])
        self.assertFalse(hasattr(remote, "access_token"))

    def test_bounded_malformed_responses_are_not_trusted(self):
        cases = [
            ("targets", {"ok": True, "client_id": NOTEBOOK, "eligible_device_count": 0,
                         "targets": [], "attested": False,
                         "dispatch_authorized": False, "owner_choice_performed": False}),
            ("targets", {"ok": True, "client_id": CLIENT, "eligible_device_count": 2,
                         "targets": [{"device_id": DESKTOP}], "attested": False,
                         "dispatch_authorized": False, "owner_choice_performed": False}),
            ("selections", {"ok": True, "count": 1, "pending_selections": [{
                "challenge_id": TICKET, "created_at": "now", "expires_at": "later",
                "session_sha256": "secret",
            }], "attested": False, "dispatch_authorized": False}),
            ("select", {"ok": True, "status": "bound", "challenge_id": TICKET,
                        "device_id": DESKTOP, "attested": True, "dispatch_authorized": True}),
        ]
        for kind, payload in cases:
            with self.subTest(kind=kind, payload=payload):
                remote = ProductRemoteClient(
                    "https://mcp.example.test",
                    http=httpx.Client(transport=httpx.MockTransport(
                        lambda _req: httpx.Response(200, json=payload))),
                )
                with self.assertRaises(ProductRemoteError) as err:
                    if kind == "targets":
                        remote.owner_central_session_targets("jwt", client_id=CLIENT)
                    elif kind == "selections":
                        remote.owner_central_session_selections("jwt", client_id=CLIENT)
                    else:
                        remote.owner_select_central_device(
                            "jwt", client_id=CLIENT, challenge_id=TICKET, device_id=DESKTOP)
                self.assertEqual(err.exception.error_code, "product_remote_invalid_response")

    def test_invalid_ids_fail_before_network_or_owner_action(self):
        observed = []
        remote = ProductRemoteClient("https://mcp.example.test",
            http=httpx.Client(transport=httpx.MockTransport(
                lambda req: observed.append(req) or httpx.Response(500))))
        for call in [
            lambda: remote.owner_central_session_targets("jwt", client_id="not-uuid"),
            lambda: remote.owner_central_session_selections("jwt", client_id="not-uuid"),
            lambda: remote.owner_select_central_device(
                "jwt", client_id=CLIENT, challenge_id="not-uuid", device_id=DESKTOP),
            lambda: remote.owner_select_central_device(
                "jwt", client_id=CLIENT, challenge_id=TICKET, device_id="not-uuid"),
        ]:
            with self.assertRaises(ValueError):
                call()
        self.assertEqual(observed, [])


if __name__ == "__main__":
    unittest.main()
