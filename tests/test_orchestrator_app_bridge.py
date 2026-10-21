import base64
from dataclasses import replace
import hashlib
import hmac
import json
from pathlib import Path
import subprocess
import sys
import time
import unittest
import uuid

from ordax_dev_agent.orchestrator_app_bridge import (
    OrchestratorAppReadClient, OrchestratorBridgeUnavailable, ExpectedOrchestratorIdentity,
    VerifiedOrchestratorReadAuthorization, InheritedOrchestratorPipe, READ_OPERATIONS,
    READ_CAPABILITY, SCHEMA, CONTRACT_SHA256, LEASE_RENEWAL, _canonical,
)


class OrchestratorHostTests(unittest.TestCase):
    def setUp(self):
        self.now = 100000
        self.authorization = VerifiedOrchestratorReadAuthorization(
            subject_id=str(uuid.uuid4()), client_id="oauth-fixture-client",
            device_id=str(uuid.uuid4()), grant_id=str(uuid.uuid4()), expires_at_ms=160000,
        )
        self.expected = ExpectedOrchestratorIdentity("a" * 40, "b" * 64, "c" * 64)
        self.calls = []
        self.bootstrap = None

    def exchange(self, message):
        self.calls.append(message)
        if message.get("type") == "bootstrap":
            self.bootstrap = message
            self.wire_key = base64.b64decode(message["key"])
            return {"schema": SCHEMA, "type": "ready", "ok": True,
                    "sessionId": message["sessionId"], "sourceCommit": self.expected.source_commit,
                    "contentDigest": self.expected.content_digest,
                    "profileFingerprint": self.expected.profile_fingerprint,
                    "capability": READ_CAPABILITY, "operations": sorted(READ_OPERATIONS),
                    "leaseRenewal": LEASE_RENEWAL}
        signed = dict(message)
        signature = signed.pop("signature")
        expected = hmac.new(self.wire_key, _canonical(signed), hashlib.sha256).hexdigest()
        self.assertTrue(hmac.compare_digest(expected, signature))
        self.assertEqual(message["sessionId"], self.bootstrap["sessionId"])
        if message["operation"] == "$lease_renew":
            self.wire_key = base64.b64decode(message["input"]["key"])
            return {"schema": SCHEMA, "id": message["id"], "ok": True,
                    "result": {"leaseRenewal": LEASE_RENEWAL, "expiresAt": message["input"]["expiresAt"]}}
        return {"schema": SCHEMA, "id": message["id"], "ok": True, "result": {"projects": []}}

    def client(self, exchange=None):
        return OrchestratorAppReadClient(exchange=exchange or self.exchange,
            authorize=lambda: self.authorization, expected=self.expected, clock=lambda: self.now)

    def test_contract_is_pinned_to_owner_projection_and_has_exactly_seven_read_operations(self):
        root = Path(__file__).resolve().parents[1]
        lock = json.loads((root / "contracts/orchestrator-app.lock.json").read_text())
        self.assertEqual(lock["repository"], "ordaxsystems/ordax-apps")
        self.assertEqual(lock["projectionSha256"], CONTRACT_SHA256)
        self.assertNotIn(b"\r\n", (root / "ordax_dev_agent/assets/orchestrator_app_contract.json").read_bytes())
        self.assertEqual(len(READ_OPERATIONS), 7)
        self.assertNotIn("work_send", READ_OPERATIONS)
        self.assertNotIn("orchestrator_chat", READ_OPERATIONS)

    def test_happy_path_reauthorizes_and_uses_distinct_signed_request_ids(self):
        client = self.client()
        self.assertEqual(client.read("projects_list"), {"projects": []})
        self.assertEqual(client.read("status"), {"projects": []})
        self.assertNotEqual(self.calls[1]["id"], self.calls[2]["id"])
        self.assertNotIn("key", self.calls[1])
        self.assertNotIn("grant_id", self.calls[1]["input"])

    def test_generic_computer_authority_cannot_bootstrap_this_channel(self):
        self.authorization = replace(self.authorization, capability="full-computer-control")
        with self.assertRaises(OrchestratorBridgeUnavailable):
            self.client()
        self.assertEqual(self.calls, [])

    def test_missing_or_expired_authorization_never_writes_a_frame(self):
        for authorization in [None, replace(self.authorization, expires_at_ms=self.now)]:
            self.authorization = authorization
            with self.assertRaises(OrchestratorBridgeUnavailable):
                self.client()
            self.assertEqual(self.calls, [])

    def test_app_profile_or_build_mismatch_poison_session_before_reads(self):
        def mismatch(frame):
            result = self.exchange(frame)
            result["profileFingerprint"] = "d" * 64
            return result
        with self.assertRaises(OrchestratorBridgeUnavailable):
            self.client(mismatch)
        self.assertEqual(len(self.calls), 1)

    def test_write_operation_cannot_borrow_a_read_authorization(self):
        client = self.client()
        with self.assertRaises(OrchestratorBridgeUnavailable):
            client.read("work_send", {"content": "No"})
        self.assertEqual(len(self.calls), 1)

    def test_revocation_and_new_subject_or_client_stop_before_any_subsequent_exchange(self):
        for field in ["subject_id", "client_id", "device_id", "grant_id"]:
            original = self.authorization
            client = self.client()
            previous = len(self.calls)
            self.authorization = replace(original, **{field: "different-client" if field == "client_id" else str(uuid.uuid4())})
            with self.assertRaises(OrchestratorBridgeUnavailable):
                client.read("status")
            self.assertEqual(len(self.calls), previous)
            self.authorization = original

    def test_late_reply_after_revocation_is_withheld(self):
        def revoke(frame):
            result = self.exchange(frame)
            if frame.get("type") != "bootstrap":
                self.authorization = None
            return result
        client = self.client(revoke)
        with self.assertRaises(OrchestratorBridgeUnavailable):
            client.read("projects_list")
        self.assertTrue(client._closed)

    def test_reply_after_request_deadline_is_withheld_even_while_session_is_valid(self):
        for elapsed in (30000, 30001):
            with self.subTest(elapsed=elapsed):
                self.now = 100000
                before = len(self.calls)
                def late(frame):
                    reply = self.exchange(frame)
                    if frame.get("type") != "bootstrap":
                        self.now += elapsed
                    return reply
                client = self.client(late)
                with self.assertRaisesRegex(OrchestratorBridgeUnavailable, "^host_read_unavailable$"):
                    client.read("status")
                self.assertLess(self.now, client._expires_at)
                self.assertEqual(bytes(client._key), b"\0" * 32)
                with self.assertRaises(OrchestratorBridgeUnavailable):
                    client.read("status")
                self.assertEqual(len(self.calls) - before, 2)

    def test_uncertain_exchange_is_not_retried_and_secret_does_not_appear_in_error(self):
        def fail(frame):
            if frame.get("type") == "bootstrap":
                return self.exchange(frame)
            self.calls.append(frame)
            raise TimeoutError("private provider content must not escape")
        client = self.client(fail)
        with self.assertRaisesRegex(OrchestratorBridgeUnavailable, "^host_read_unavailable$"):
            client.read("status")
        with self.assertRaises(OrchestratorBridgeUnavailable):
            client.read("status")
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(bytes(client._key), b"\0" * 32)

    def test_response_id_must_match_the_exact_inflight_read(self):
        def wrong(frame):
            reply = self.exchange(frame)
            if frame.get("type") != "bootstrap":
                reply["id"] = str(uuid.uuid4())
            return reply
        client = self.client(wrong)
        with self.assertRaises(OrchestratorBridgeUnavailable):
            client.read("status")

    def test_renewal_rotates_key_and_keeps_same_session_and_read_scope(self):
        client = self.client()
        old_key = bytes(client._key)
        self.now += 30000
        self.authorization = replace(self.authorization, expires_at_ms=self.now + 600000)
        self.assertEqual(client.renew(), self.now + 300000)
        self.assertNotEqual(bytes(client._key), old_key)
        self.assertEqual(client._session_id, self.bootstrap["sessionId"])
        self.assertEqual(self.calls[-1]["expiresAt"], 160000)
        self.assertEqual(client.read("status"), {"projects": []})
        with self.assertRaises(OrchestratorBridgeUnavailable):
            client.read("work_send")

    def test_renewal_requires_fresh_same_caller_authority_before_any_frame(self):
        original = self.authorization
        for field in ("subject_id", "client_id", "device_id", "grant_id", "capability", "app_id"):
            self.authorization = original
            client = self.client()
            before = len(self.calls)
            value = str(uuid.uuid4()) if field.endswith("_id") and field != "client_id" else "different-value"
            self.authorization = replace(original, **{field: value})
            with self.assertRaises(OrchestratorBridgeUnavailable):
                client.renew()
            self.assertEqual(len(self.calls), before)
            self.assertTrue(client._closed)
            self.assertEqual(bytes(client._key), bytes(32))

    def test_expired_or_revoked_lease_cannot_be_resurrected(self):
        for revoke in (False, True):
            self.now = 100000
            self.authorization = replace(self.authorization, expires_at_ms=160000)
            client = self.client()
            if revoke:
                self.authorization = None
            else:
                self.now = 160000
                self.authorization = replace(self.authorization, expires_at_ms=760000)
            before = len(self.calls)
            with self.assertRaises(OrchestratorBridgeUnavailable):
                client.renew()
            self.assertEqual(len(self.calls), before)
            self.assertEqual(bytes(client._key), bytes(32))
            if revoke:
                self.authorization = VerifiedOrchestratorReadAuthorization(
                    str(uuid.uuid4()), "oauth-fixture-client", str(uuid.uuid4()), str(uuid.uuid4()), 160000)

    def test_uncertain_or_late_renewal_never_retries_or_releases_authority(self):
        for failure in ("timeout", "late", "revoke", "shorten", "wrong-id", "wrong-expiry"):
            with self.subTest(failure=failure):
                self.now = 100000
                self.authorization = replace(self.authorization, expires_at_ms=160000)
                def exchange(frame):
                    reply = self.exchange(frame)
                    if frame.get("operation") == "$lease_renew":
                        if failure == "timeout":
                            raise TimeoutError("sensitive transport details")
                        if failure == "late":
                            self.now += 30000
                        if failure == "revoke":
                            self.authorization = None
                        if failure == "shorten":
                            self.authorization = replace(self.authorization, expires_at_ms=self.now + 1000)
                        if failure == "wrong-id":
                            reply["id"] = str(uuid.uuid4())
                        if failure == "wrong-expiry":
                            reply["result"]["expiresAt"] += 1
                    return reply
                client = self.client(exchange)
                self.now += 1000
                original = self.authorization
                self.authorization = replace(original, expires_at_ms=self.now + 600000)
                before = len(self.calls)
                with self.assertRaisesRegex(OrchestratorBridgeUnavailable, '^host_renewal_unavailable$'):
                    client.renew()
                self.assertEqual(bytes(client._key), bytes(32))
                with self.assertRaises(OrchestratorBridgeUnavailable):
                    client.renew()
                self.assertEqual(len(self.calls), before + 1)
                self.authorization = original

    def test_older_app_can_still_read_but_does_not_receive_a_renewal(self):
        def exchange(frame):
            reply = self.exchange(frame)
            reply.pop("leaseRenewal", None)
            return reply
        client = self.client(exchange)
        client.read("status")
        before = len(self.calls)
        with self.assertRaises(OrchestratorBridgeUnavailable):
            client.renew()
        self.assertEqual(len(self.calls), before)

    def test_explicit_close_during_exchange_cannot_restore_a_key_or_return_data(self):
        for operation in ('read', 'renew'):
            with self.subTest(operation=operation):
                self.now = 100000
                self.authorization = replace(self.authorization, expires_at_ms=160000)
                def exchange(frame):
                    reply = self.exchange(frame)
                    if frame.get('type') != 'bootstrap':
                        client.close()
                    return reply
                client = self.client(exchange)
                self.now += 1000
                self.authorization = replace(self.authorization, expires_at_ms=self.now + 600000)
                with self.assertRaises(OrchestratorBridgeUnavailable):
                    client.read('status') if operation == 'read' else client.renew()
                self.assertTrue(client._closed)
                self.assertEqual(bytes(client._key), bytes(32))

    def test_cross_language_canonical_json_is_unambiguous_for_unicode(self):
        self.assertEqual(_canonical({"z": "ação", "a": [True, None, 2]}).decode(), '{"a":[true,null,2],"z":"ação"}')
        for value in [float("nan"), 0.5, 9007199254740992]:
            with self.assertRaises(OrchestratorBridgeUnavailable):
                _canonical({"x": value})

    def test_private_child_pipe_has_a_deadline_and_never_resends(self):
        code = "import sys,time; sys.stdin.buffer.readline(); time.sleep(2)"
        child = subprocess.Popen([sys.executable, "-c", code], stdin=subprocess.PIPE, stdout=subprocess.PIPE)
        try:
            pipe = InheritedOrchestratorPipe(child, request_pipe=child.stdin,
                response_pipe=child.stdout, timeout_seconds=0.02)
            started = time.monotonic()
            with self.assertRaises(OrchestratorBridgeUnavailable):
                pipe({"test": True})
            self.assertLess(time.monotonic() - started, 1)
            with self.assertRaises(OrchestratorBridgeUnavailable):
                pipe({"test": True})
        finally:
            child.terminate()
            child.wait(timeout=5)
            child.stdin.close()
            child.stdout.close()


if __name__ == "__main__":
    unittest.main()

