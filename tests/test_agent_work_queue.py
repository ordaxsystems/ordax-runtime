from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

from ordax_core import OrchestratorStore


class AgentWorkQueueTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = OrchestratorStore(self.root / "state.db")
        self.agent = self.store.create_agent("demo", "Worker", "implementation")

    def test_priority_claim_complete_and_idle(self) -> None:
        low = self.store.enqueue_work(
            self.agent["id"], "Low", "Do low priority work", priority=10
        )
        high = self.store.enqueue_work(
            self.agent["id"], "High", "Do high priority work", priority=90
        )

        claimed = self.store.claim_next_work(
            self.agent["id"], "runner-1", lease_seconds=60
        )
        self.assertEqual(claimed["id"], high["id"])
        self.assertEqual(claimed["state"], "leased")
        self.assertEqual(claimed["attempts"], 1)

        completed = self.store.complete_work(
            claimed["id"], "runner-1", result="done"
        )
        self.assertEqual(completed["state"], "done")
        self.assertEqual(completed["result"], "done")

        second = self.store.claim_next_work(
            self.agent["id"], "runner-1", lease_seconds=60
        )
        self.assertEqual(second["id"], low["id"])
        self.store.complete_work(second["id"], "runner-1")
        self.assertIsNone(
            self.store.claim_next_work(self.agent["id"], "runner-1", lease_seconds=60)
        )

    def test_failure_requeues_then_stops_at_max_attempts(self) -> None:
        work = self.store.enqueue_work(
            self.agent["id"],
            "Retry",
            "Fail twice",
            max_attempts=2,
        )
        first = self.store.claim_next_work(
            self.agent["id"], "runner-1", lease_seconds=60
        )
        retried = self.store.fail_work(
            first["id"],
            "runner-1",
            error="temporary",
            retryable=True,
            retry_delay_seconds=0,
        )
        self.assertEqual(retried["state"], "queued")

        second = self.store.claim_next_work(
            self.agent["id"], "runner-2", lease_seconds=60
        )
        self.assertEqual(second["attempts"], 2)
        failed = self.store.fail_work(
            second["id"],
            "runner-2",
            error="still broken",
            retryable=True,
            retry_delay_seconds=0,
        )
        self.assertEqual(failed["state"], "failed")
        self.assertIsNone(
            self.store.claim_next_work(self.agent["id"], "runner-3", lease_seconds=60)
        )

    def test_expired_lease_is_recoverable_after_runner_crash(self) -> None:
        work = self.store.enqueue_work(
            self.agent["id"], "Recover", "Recover after crash"
        )
        claimed = self.store.claim_next_work(
            self.agent["id"], "dead-runner", lease_seconds=30
        )
        self.assertEqual(claimed["id"], work["id"])

        with self.store._connect() as connection:
            connection.execute(
                "UPDATE agent_work_items SET lease_expires_at_unix=? WHERE id=?",
                (time.time() - 1, work["id"]),
            )

        recovered = self.store.claim_next_work(
            self.agent["id"], "new-runner", lease_seconds=30
        )
        self.assertEqual(recovered["id"], work["id"])
        self.assertEqual(recovered["lease_owner"], "new-runner")
        self.assertEqual(recovered["attempts"], 2)

    def test_delayed_work_does_not_run_early(self) -> None:
        work = self.store.enqueue_work(
            self.agent["id"],
            "Later",
            "Do not run yet",
            delay_seconds=60,
        )
        self.assertIsNone(
            self.store.claim_next_work(self.agent["id"], "runner", lease_seconds=60)
        )
        listed = self.store.list_work("demo", states=["queued"])
        self.assertEqual(listed[0]["id"], work["id"])


if __name__ == "__main__":
    unittest.main()
