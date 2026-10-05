from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ordax_dev_agent.actions import ActionRegistry
from ordax_dev_agent.config import AgentConfig


class OrchestratorActionsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.project = self.root / "project"
        self.project.mkdir()
        self.db = self.root / "state.db"
        self.env = patch.dict(os.environ, {"ORDAX_MEMORY_DB": str(self.db)})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.registry = ActionRegistry(
            AgentConfig(
                agent_name="test",
                poll_seconds=0.25,
                state_dir=self.root / "state",
                agent_repo_path=self.root / "agent",
                hordax_path=self.root / "hordax",
                bridge_path=self.root / "bridge",
                projects={"demo": {"path": str(self.project), "apps": []}},
                default_project="demo",
            )
        )

    def test_registry_can_create_prime_worker_goal_and_rotate_session(self) -> None:
        prime = self.registry.execute(
            "orchestrator.agent_create",
            {"project": "demo", "name": "Prime", "role": "coordinator"},
        )
        self.assertTrue(prime.ok)

        worker = self.registry.execute(
            "orchestrator.agent_create",
            {
                "project": "demo",
                "name": "QA",
                "role": "quality",
                "parent_agent_id": prime.data["id"],
            },
        )
        self.assertTrue(worker.ok)

        goal = self.registry.execute(
            "orchestrator.goal_create",
            {
                "project": "demo",
                "agent_id": prime.data["id"],
                "title": "Finish MVP",
                "description": "Keep CI green",
            },
        )
        self.assertTrue(goal.ok)

        session = self.registry.execute(
            "orchestrator.session_start",
            {
                "agent_id": prime.data["id"],
                "goal_id": goal.data["id"],
                "provider": "openai",
                "model": "gpt",
                "context_window_tokens": 10000,
                "rollover_ratio": 0.80,
            },
        )
        self.assertTrue(session.ok)

        checkpoint = self.registry.execute(
            "orchestrator.session_checkpoint",
            {
                "session_id": session.data["id"],
                "summary": "Mid-session checkpoint",
                "next_action": "Continue implementation",
                "completed": ["bootstrap"],
                "blockers": ["review pending"],
                "changed_paths": ["src/bootstrap.py"],
            },
        )
        self.assertTrue(checkpoint.ok)
        continuity = self.registry.execute("continuity.get", {"project": "demo"})
        self.assertTrue(continuity.ok)
        state = continuity.data["state"]
        self.assertEqual("Mid-session checkpoint", state["summary"])
        self.assertEqual("Continue implementation", state["next_action"])
        self.assertEqual(["bootstrap"], state["completed"])
        self.assertEqual(["review pending"], state["blockers"])
        self.assertEqual(["src/bootstrap.py"], state["changed_paths"])
        self.assertEqual("orchestrator", state["source"])
        self.assertEqual(checkpoint.data["id"], state["source_ref"])

        usage = self.registry.execute(
            "orchestrator.session_usage",
            {
                "session_id": session.data["id"],
                "input_tokens": 7000,
                "output_tokens": 1200,
            },
        )
        self.assertTrue(usage.ok)
        self.assertTrue(usage.data["should_rollover"])

        rotated = self.registry.execute(
            "orchestrator.session_rotate",
            {
                "session_id": session.data["id"],
                "summary": "Large context checkpoint",
                "next_action": "Continue from tests",
                "completed": ["initial implementation"],
                "changed_paths": ["src/app.py"],
            },
        )
        self.assertTrue(rotated.ok)
        self.assertEqual(
            rotated.data["continuation"]["latest_checkpoint"]["next_action"],
            "Continue from tests",
        )
        continuity = self.registry.execute("continuity.get", {"project": "demo"})
        state = continuity.data["state"]
        self.assertEqual("Large context checkpoint", state["summary"])
        self.assertEqual("Continue from tests", state["next_action"])
        self.assertEqual(["initial implementation"], state["completed"])
        self.assertEqual(["src/app.py"], state["changed_paths"])
        self.assertEqual("orchestrator", state["source"])
        self.assertEqual(rotated.data["checkpoint"]["id"], state["source_ref"])
        self.assertEqual(state, rotated.data["project_continuity"])

        sent = self.registry.execute(
            "orchestrator.message_send",
            {
                "sender_agent_id": prime.data["id"],
                "recipient_agent_id": worker.data["id"],
                "kind": "task",
                "content": "Validate the new session engine",
            },
        )
        self.assertTrue(sent.ok)

        inbox = self.registry.execute(
            "orchestrator.inbox",
            {"agent_id": worker.data["id"]},
        )
        self.assertTrue(inbox.ok)
        self.assertEqual(
            inbox.data["messages"][0]["content"],
            "Validate the new session engine",
        )

        status = self.registry.execute("orchestrator.status", {"project": "demo"})
        self.assertTrue(status.ok)
        self.assertEqual(len(status.data["agents"]), 2)
        self.assertEqual(len(status.data["active_sessions"]), 1)

    def test_rotation_requires_threshold_without_force(self) -> None:
        prime = self.registry.execute(
            "orchestrator.agent_create",
            {"project": "demo", "name": "Prime", "role": "coordinator"},
        )
        session = self.registry.execute(
            "orchestrator.session_start",
            {
                "agent_id": prime.data["id"],
                "provider": "openai",
                "model": "gpt",
                "context_window_tokens": 10000,
            },
        )
        rotated = self.registry.execute(
            "orchestrator.session_rotate",
            {
                "session_id": session.data["id"],
                "summary": "Too early",
                "next_action": "Continue",
            },
        )
        self.assertFalse(rotated.ok)
        self.assertIn("rollover threshold", rotated.summary)


if __name__ == "__main__":
    unittest.main()
