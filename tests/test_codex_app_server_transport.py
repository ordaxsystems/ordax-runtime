"""Protocol-level fake Codex process; never relies on ChatGPT credentials."""

from __future__ import annotations

import os
import sys
import unittest

from ordax_studio.codex_app_server import CodexAppServerTransport, CodexTransportError


FAKE_CODEX = r'''
import json, sys, time
for line in sys.stdin:
    data = json.loads(line)
    method = data.get("method", "")
    if method == "initialize":
        print(json.dumps({"id": data["id"], "result": {"userAgent": "fake-test"}}), flush=True)
    elif method == "thread/start":
        print(json.dumps({"id": data["id"], "result": {"thread": {"id": "thread-test"}}}), flush=True)
    elif method == "turn/start":
        print(json.dumps({"id": data["id"], "result": {"turn": {"id": "turn-test"}}}), flush=True)
        print(json.dumps({"method": "item/agentMessage/delta", "params": {"threadId": "thread-test", "delta": "Olá"}}), flush=True)
        print(json.dumps({"method": "turn/completed", "params": {"turn": {"status": "completed"}}}), flush=True)
    elif method == "fake/approval":
        print(json.dumps({"id": "approval-1", "method": "item/commandExecution/requestApproval", "params": {"reason": "attempt"}}), flush=True)
        reply = json.loads(sys.stdin.readline())
        print(json.dumps({"id": data["id"], "result": {"denied": reply.get("error", {}).get("code") == -32601}}), flush=True)
    elif method == "fake/exit":
        sys.exit(0)
    elif method == "fake/invalid":
        print("not-json!", flush=True)
    elif method == "fake/overflow":
        for i in range(50):
            print(json.dumps({"method": "dummy", "params": {"i": i}}), flush=True)
    elif "id" in data:
        print(json.dumps({"id": data["id"], "error": {"code": -32601, "message": "token-secret-should-not-surface"}}), flush=True)
'''


class CodexAppServerTransportTests(unittest.TestCase):
    def transport(self, *, capacity: int = 100) -> CodexAppServerTransport:
        # The provider process is explicitly managed by the host; no inherited
        # environment and no unrelated user process is touched.
        client = CodexAppServerTransport(
            [sys.executable, "-u", "-c", FAKE_CODEX],
            environment={"PATH": os.environ.get("PATH", "")},
            timeout=3,
            notification_capacity=capacity,
        )
        self.addCleanup(client.close)
        client.start(version="0.4.4")
        return client

    def test_initialize_thread_turn_and_stream(self) -> None:
        client = self.transport()
        thread = client.request("thread/start", {"sandbox": "read-only", "approvalPolicy": "on-request"})
        self.assertEqual("thread-test", thread["thread"]["id"])
        turn = client.request("turn/start", {"threadId": "thread-test", "input": [{"type": "text", "text": "oi"}]})
        self.assertEqual("turn-test", turn["turn"]["id"])
        event = client.next_notification(timeout=3)
        self.assertEqual("item/agentMessage/delta", event["method"])
        self.assertEqual("Olá", event["params"]["delta"])
        self.assertEqual("turn/completed", client.next_notification(timeout=3)["method"])

    def test_server_initiated_tool_request_is_denied(self) -> None:
        client = self.transport()
        self.assertTrue(client.request("fake/approval")["denied"])

    def test_remote_error_does_not_leak_provider_message(self) -> None:
        client = self.transport()
        with self.assertRaises(CodexTransportError) as captured:
            client.request("not-supported")
        self.assertNotIn("token-secret-should-not-surface", str(captured.exception))

    def test_malformed_provider_frame_fails_closed(self) -> None:
        client = self.transport()
        with self.assertRaises(CodexTransportError):
            client.request("fake/invalid")
        with self.assertRaises(CodexTransportError):
            client.request("thread/start")

    def test_event_queue_overflow_closes_transport(self) -> None:
        client = self.transport(capacity=1)
        with self.assertRaises(CodexTransportError):
            client.request("fake/overflow")

    def test_environment_must_be_explicit(self) -> None:
        with self.assertRaises(TypeError):
            CodexAppServerTransport(["codex", "app-server"])

    def test_invalid_argv_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            CodexAppServerTransport([], environment={})


if __name__ == "__main__":
    unittest.main()
