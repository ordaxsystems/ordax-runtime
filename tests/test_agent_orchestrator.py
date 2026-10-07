from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from ordax_core import OrchestratorStore


class OrchestratorStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.db = self.root / "state.db"
        self.store = OrchestratorStore(self.db)

    def test_agent_goal_and_session_survive_reopen(self) -> None:
        prime = self.store.create_agent("demo", "Prime", "coordinator")
        goal = self.store.create_goal(prime["id"], "Finish MVP", "Keep tests green")
        session = self.store.start_session(
            prime["id"],
            goal_id=goal["id"],
            provider="openai",
            model="gpt",
            context_window_tokens=10000,
            rollover_ratio=0.80,
        )
        self.store.checkpoint(
            session["id"],
            summary="Foundation ready",
            next_action="Run tests",
            completed=["created agent runtime"],
            changed_paths=["src/runtime.py"],
            git_state={"branch": "main", "head": "abc123"},
        )

        reopened = OrchestratorStore(self.db)
        status = reopened.status("demo")
        self.assertEqual(status["agents"][0]["name"], "Prime")
        self.assertEqual(status["goals"][0]["title"], "Finish MVP")
        self.assertEqual(status["active_sessions"][0]["id"], session["id"])

    def test_context_usage_triggers_rollover_and_successor_continuity(self) -> None:
        prime = self.store.create_agent("demo", "Prime", "coordinator")
        goal = self.store.create_goal(prime["id"], "Finish MVP")
        session = self.store.start_session(
            prime["id"],
            goal_id=goal["id"],
            provider="openai",
            model="gpt",
            context_window_tokens=10000,
            rollover_ratio=0.80,
        )
        usage = self.store.record_usage(
            session["id"],
            input_tokens=6500,
            output_tokens=1600,
        )
        self.assertTrue(usage["should_rollover"])
        self.assertEqual(usage["remaining_before_rollover"], 0)

        rotated = self.store.rotate_session(
            session["id"],
            summary="Session reached context threshold",
            next_action="Continue implementation",
            completed=["tests inspected", "runtime changed"],
            blockers=[],
            changed_paths=["a.py", "b.py"],
            git_state={"branch": "feature", "head": "deadbee"},
        )
        successor = rotated["session"]
        self.assertEqual(successor["predecessor_session_id"], session["id"])
        self.assertEqual(successor["estimated_tokens"], 0)
        self.assertEqual(
            rotated["continuation"]["latest_checkpoint"]["next_action"],
            "Continue implementation",
        )
        self.assertEqual(
            rotated["continuation"]["latest_checkpoint"]["changed_paths"],
            ["a.py", "b.py"],
        )
        previous = self.store.session(session["id"])
        self.assertEqual(previous["state"], "rotated")
        self.assertEqual(previous["end_reason"], "context_rollover")

    def test_workers_report_through_coordinator_not_each_other(self) -> None:
        prime = self.store.create_agent("demo", "Prime", "coordinator")
        backend = self.store.create_agent(
            "demo", "Backend", "backend worker", parent_agent_id=prime["id"]
        )
        qa = self.store.create_agent(
            "demo", "QA", "quality worker", parent_agent_id=prime["id"]
        )

        report = self.store.send_message(
            backend["id"],
            prime["id"],
            "Backend fix ready",
            kind="result",
            correlation_id="task-1",
        )
        self.assertGreater(report["id"], 0)
        inbox = self.store.inbox(prime["id"])
        self.assertEqual(inbox[0]["content"], "Backend fix ready")
        self.assertTrue(self.store.mark_message_read(prime["id"], inbox[0]["id"]))
        self.assertEqual(self.store.inbox(prime["id"]), [])

        with self.assertRaisesRegex(ValueError, "route through the coordinator"):
            self.store.send_message(backend["id"], qa["id"], "Run tests")

        instruction = self.store.send_message(
            prime["id"], qa["id"], "Run tests for task-1", kind="task"
        )
        self.assertGreater(instruction["id"], report["id"])

    def test_assistant_conversations_are_durable_and_project_scoped(self) -> None:
        chat = self.store.create_assistant_conversation(
            "demo",
            title="Chat UI",
            account_id="ordax",
            provider_id="openai",
            model_id="gpt-4o",
        )
        self.store.add_assistant_message(
            chat["id"],
            role="user",
            content="Revise o layout do Studio",
        )
        self.store.add_assistant_message(
            chat["id"],
            role="assistant",
            content="Vou revisar o shell IDE.",
        )
        updated = self.store.update_assistant_conversation(
            chat["id"],
            provider_id="xai",
            model_id="grok",
        )
        self.assertEqual(updated["provider_id"], "xai")
        self.assertEqual(updated["model_id"], "grok")

        reopened = OrchestratorStore(self.db)
        chats = reopened.assistant_conversations("demo")
        self.assertEqual([item["id"] for item in chats], [chat["id"]])
        messages = reopened.assistant_messages(chat["id"])
        self.assertEqual([item["role"] for item in messages], ["user", "assistant"])
        self.assertEqual(messages[0]["content"], "Revise o layout do Studio")
        self.assertEqual(reopened.assistant_conversations("other"), [])

        archived = reopened.archive_assistant_conversation(chat["id"])
        self.assertIsNotNone(archived["archived_at"])
        self.assertEqual(reopened.assistant_conversations("demo"), [])
        self.assertEqual(
            reopened.assistant_conversations("demo", include_archived=True)[0]["id"],
            chat["id"],
        )

    def test_worker_cannot_create_nested_workers(self) -> None:
        prime = self.store.create_agent("demo", "Prime", "coordinator")
        worker = self.store.create_agent(
            "demo", "Worker", "implementation", parent_agent_id=prime["id"]
        )
        with self.assertRaisesRegex(ValueError, "cannot own other workers"):
            self.store.create_agent(
                "demo", "Nested", "nested", parent_agent_id=worker["id"]
            )


if __name__ == "__main__":
    unittest.main()
