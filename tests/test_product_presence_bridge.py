from __future__ import annotations

import hashlib
import json
import unittest
from unittest.mock import patch

import httpx

from ordax_dev_agent.cloudflare_control_plane import CloudflareControlPlane
from ordax_dev_agent.control_plane import ProductAwareCloudflareControlPlane
from ordax_dev_agent.product_device_presence import ProductPresenceError
from ordax_dev_agent.product_presence_bridge import ProductPresenceBridge


DEVICE_ID = "22222222-2222-4222-8222-222222222222"
BINDING = "b" * 64


class RecordingPresence:
    def __init__(self, sink: list[dict], *, error: str | None = None):
        self.sink = sink
        self.error = error

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def report(self, *, device_id, device_credential, snapshot):
        if self.error:
            raise ProductPresenceError(self.error)
        self.sink.append(
            {
                "device_id": device_id,
                "credential_len": len(device_credential),
                "snapshot": snapshot,
            }
        )


class FakeBridge:
    def __init__(self, events: list[str]):
        self.events = events

    def report(self, _actions, *, agent_version, online):
        self.events.append(f"product:{agent_version}:{online}")


class ProductPresenceBridgeTests(unittest.TestCase):
    def make_bridge(self, token_box, *, identity=None, presence_error=None, verify_headers=None):
        identity_calls: list[dict] = []
        presence_calls: list[dict] = []

        def verify(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content)
            identity_calls.append(
                {
                    "path": request.url.path,
                    "body": body,
                    "token": request.headers.get("X-Ordax-Device-Token"),
                }
            )
            payload = identity or {
                "ok": True,
                "device_id": DEVICE_ID,
                "protocol": "cloudflare-v3",
            }
            return httpx.Response(200, json=payload)

        verify_http = httpx.Client(
            transport=httpx.MockTransport(verify),
            headers=verify_headers,
        )

        bridge = ProductPresenceBridge(
            "https://control.example",
            DEVICE_ID,
            lambda: token_box[0],
            verify_http=verify_http,
            binding_provider=lambda: BINDING,
            presence_client_factory=lambda _base: RecordingPresence(
                presence_calls, error=presence_error
            ),
        )
        return bridge, identity_calls, presence_calls, verify_http

    def test_identity_is_proven_once_and_presence_uses_canonical_snapshot(self):
        token = ["a" * 64]
        bridge, identity_calls, presence_calls, client = self.make_bridge(token)
        self.addCleanup(client.close)

        first = bridge.report(["agent.status"], agent_version="1.30.2", online=True)
        second = bridge.report(["agent.status"], agent_version="1.30.2", online=True)

        self.assertTrue(first.verified)
        self.assertTrue(first.online)
        self.assertIsNone(first.last_error)
        self.assertTrue(second.verified)
        self.assertEqual(len(identity_calls), 1)
        self.assertEqual(identity_calls[0]["path"], "/v3/device/setup")
        self.assertEqual(
            identity_calls[0]["body"],
            {"operation": "identify", "machine_binding_sha256": BINDING},
        )
        self.assertEqual(identity_calls[0]["token"], token[0])
        self.assertEqual(len(presence_calls), 2)
        snapshot = presence_calls[0]["snapshot"]
        self.assertTrue(snapshot.online)
        self.assertEqual(snapshot.runtime_kind, "desktop-agent")
        self.assertEqual(snapshot.agent_version, "1.30.2")
        self.assertRegex(snapshot.capability_digest, r"^[0-9a-f]{64}$")

    def test_rotated_credential_forces_new_identity_proof_without_storing_raw_secret(self):
        token = ["a" * 64]
        bridge, identity_calls, _presence_calls, client = self.make_bridge(token)
        self.addCleanup(client.close)
        bridge.report(["agent.status"], agent_version="1.30.2", online=True)
        old = token[0]
        token[0] = "c" * 64
        bridge.report(["agent.status"], agent_version="1.30.2", online=True)

        self.assertEqual(len(identity_calls), 2)
        self.assertEqual(
            bridge._verified_credential_sha256,
            hashlib.sha256(token[0].encode()).hexdigest(),
        )
        state = repr(vars(bridge))
        self.assertNotIn(old, state)
        self.assertNotIn(token[0], state)

    def test_identity_mismatch_fails_closed_before_presence(self):
        token = ["a" * 64]
        bridge, _identity_calls, presence_calls, client = self.make_bridge(
            token,
            identity={
                "ok": True,
                "device_id": "33333333-3333-4333-8333-333333333333",
                "protocol": "cloudflare-v3",
            },
        )
        self.addCleanup(client.close)

        status = bridge.report(["agent.status"], agent_version="1.30.2", online=True)

        self.assertFalse(status.verified)
        self.assertIsNone(status.online)
        self.assertEqual(status.last_error, "product_identity_mismatch")
        self.assertEqual(presence_calls, [])

    def test_account_authorization_header_is_rejected_before_identity_request(self):
        token = ["a" * 64]
        bridge, identity_calls, presence_calls, client = self.make_bridge(
            token,
            verify_headers={"Authorization": "Bearer account-secret"},
        )
        self.addCleanup(client.close)

        status = bridge.report(["agent.status"], agent_version="1.30.2", online=True)

        self.assertEqual(status.last_error, "product_identity_transport_has_account_auth")
        self.assertEqual(identity_calls, [])
        self.assertEqual(presence_calls, [])

    def test_presence_failure_is_contained_and_revocation_requires_reverification(self):
        token = ["a" * 64]
        bridge, identity_calls, presence_calls, client = self.make_bridge(
            token,
            presence_error="product_device_unauthorized",
        )
        self.addCleanup(client.close)

        status = bridge.report(["agent.status"], agent_version="1.30.2", online=True)

        self.assertFalse(status.verified)
        self.assertEqual(status.last_error, "product_identity_reenrollment_required")
        self.assertEqual(len(identity_calls), 1)
        self.assertEqual(presence_calls, [])
        self.assertIsNone(bridge._verified_credential_sha256)

    def test_capability_digest_is_order_independent_and_bound_to_canonical_snapshot(self):
        one = ProductPresenceBridge.capability_digest(["agent.status", "computer.screenshot"])
        two = ProductPresenceBridge.capability_digest(["computer.screenshot", "agent.status"])
        self.assertEqual(one, two)
        self.assertRegex(one, r"^[0-9a-f]{64}$")


class ProductAwareControlPlaneTests(unittest.TestCase):
    def make_plane(self, events: list[str]):
        plane = ProductAwareCloudflareControlPlane.__new__(ProductAwareCloudflareControlPlane)
        plane.product_presence = FakeBridge(events)
        return plane

    def test_product_online_follows_successful_command_heartbeat(self):
        events: list[str] = []
        plane = self.make_plane(events)

        def command(*_args, **_kwargs):
            events.append("command")

        with patch.object(CloudflareControlPlane, "heartbeat", side_effect=command):
            plane.heartbeat(["agent.status"], agent_version="1.30.2", status="online")

        self.assertEqual(events, ["command", "product:1.30.2:True"])

    def test_command_heartbeat_failure_never_advertises_online(self):
        events: list[str] = []
        plane = self.make_plane(events)

        def command(*_args, **_kwargs):
            events.append("command")
            raise RuntimeError("transport_down")

        with patch.object(CloudflareControlPlane, "heartbeat", side_effect=command):
            with self.assertRaisesRegex(RuntimeError, "transport_down"):
                plane.heartbeat(["agent.status"], agent_version="1.30.2", status="online")

        self.assertEqual(events, ["command"])

    def test_offline_presence_is_attempted_even_when_command_socket_is_gone(self):
        events: list[str] = []
        plane = self.make_plane(events)

        def command(*_args, **_kwargs):
            events.append("command")
            raise RuntimeError("socket_gone")

        with patch.object(CloudflareControlPlane, "heartbeat", side_effect=command):
            with self.assertRaisesRegex(RuntimeError, "socket_gone"):
                plane.heartbeat(["agent.status"], agent_version="1.30.2", status="offline")

        self.assertEqual(events, ["command", "product:1.30.2:False"])


if __name__ == "__main__":
    unittest.main()
