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
    READ_CAPABILITY, SCHEMA, CONTRACT_SHA256, _canonical,
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
            return {"schema": SCHEMA, "type": "ready", "ok": True,
                    "sessionId": message["sessionId"], "sourceCommit": self.expected.source_commit,
                    "contentDigest": self.expected.content_digest,
                    "profileFingerprint": self.expected.profile_fingerprint,
                    "capability": READ_CAPABILITY, "operations": sorted(READ_OPERATIONS)}
        signed = dict(message)
        signature = signed.pop("signature")
        expected = hmac.new(base64.b64decode(self.bootstrap["key"]), _canonical(signed), hashlib.sha256).hexdigest()
        self.assertTrue(hmac.compare_digest(expected, signature))
        self.assertEqual(message["sessionId"], self.bootstrap["sessionId"])
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

    def test_cross_language_canonical_json_is_unambiguous_for_unicode(self):
        self.assertEqual(_canonical({"z": "ação", "a": [True, None, 2]}).decode(), '{"a":[true,null,2],"z":"ação"}')
        for value in [float("nan"), 0.5, 9007199254740992]:
            with self.assertRaises(OrchestratorBridgeUnavailable):
                _canonical({"x": value})

    def test_private_child_pipe_has_a_deadline_and_never_resends(self):
        code = "import sys,time; sys.stdin.buffer.readline(); time.sleep(2)"
        child = subprocess.Popen([sys.executable, "-c", code], stdin=subprocess.PIPE, stdout=subprocess.PIPE)
        try:
            pipe = InheritedOrchestratorPipe(child, timeout_seconds=0.02)
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
