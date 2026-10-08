"""Provider event projection must never become an authorization channel."""

from __future__ import annotations

import unittest

from ordax_studio.codex_events import normalize_codex_notification as normalize


class CodexEventBoundaryTests(unittest.TestCase):
    def test_bounded_message_delta(self) -> None:
        result = normalize({"method": "item/agentMessage/delta", "params": {
            "threadId": "project-thread", "turnId": "turn-1", "delta": "Olá, OrdaX",
            "secret": "must-not-leak", "command": "run-dangerous-command",
        }})
        self.assertEqual({
            "kind": "message_delta", "thread_id": "project-thread",
            "turn_id": "turn-1", "delta": "Olá, OrdaX",
        }, result)

    def test_completed_failure_never_claimed_as_success(self) -> None:
        for status in ("completed", "failed", "interrupted"):
            with self.subTest(status=status):
                result = normalize({"method": "turn/completed", "params": {
                    "threadId": "thread-1", "turn": {"id": "turn-1", "status": status},
                }})
                self.assertEqual(status, result["status"])
                self.assertEqual("turn_completed", result["kind"])
        self.assertIsNone(normalize({"method": "turn/completed", "params": {
            "threadId": "thread-1", "turn": {"id": "turn-1", "status": "pretend-success"},
        }}))

    def test_tool_item_projection_excludes_command_and_paths(self) -> None:
        event = normalize({"method": "item/started", "params": {
            "threadId": "thread-1", "turnId": "turn-1",
            "item": {
                "id": "item-1", "type": "commandExecution",
                "command": "print-token", "cwd": "C:\\Private", "output": "private",
            },
        }})
        self.assertEqual({"kind": "item_started", "thread_id": "thread-1",
                          "turn_id": "turn-1", "item_id": "item-1",
                          "item_type": "commandExecution"}, event)
        self.assertNotIn("C:\\Private", str(event))

    def test_rejects_missing_or_hostile_identity(self) -> None:
        good = {"method": "item/agentMessage/delta", "params": {
            "threadId": "t", "turnId": "r", "delta": "a"}}
        for thread_id in (None, "", 10, "x\ninjected", "a" * 257):
            with self.subTest(thread_id=str(thread_id)[:15]):
                self.assertIsNone(normalize({**good, "params": {**good["params"], "threadId": thread_id}}))
        self.assertIsNone(normalize({**good, "id": "request-1"}))

    def test_rejects_unknown_or_bloated_content(self) -> None:
        self.assertIsNone(normalize({"method": "untrusted/tool/request", "params": {"threadId": "t"}}))
        self.assertIsNone(normalize({"method": "item/agentMessage/delta", "params": {
            "threadId": "t", "turnId": "r", "delta": "x" * 65537,
        }}))
        self.assertIsNone(normalize({"method": "item/agentMessage/delta", "params": {
            "threadId": "t", "turnId": "r", "delta": {"text": "invalid"},
        }}))
        self.assertIsNone(normalize({"method": "item/agentMessage/delta", "params": []}))


if __name__ == "__main__":
    unittest.main()
