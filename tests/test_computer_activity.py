from __future__ import annotations

import json
import tempfile
import threading
import unittest
from pathlib import Path

from ordax_dev_agent.actions import ActionRegistry
from ordax_dev_agent.computer_activity import ComputerActivityTracker
from ordax_dev_agent.config import AgentConfig
from ordax_dev_agent.models import ActionResult
from ordax_dev_agent.product_gateway import (
    ProductActionGateway,
    ProductAuditEvent,
    ProductGrant,
    ProductRequestContext,
)


class AuditSink:
    def __init__(self) -> None:
        self.events: list[ProductAuditEvent] = []

    def record(self, event: ProductAuditEvent) -> None:
        self.events.append(event)


class TrackingExecutor:
    def __init__(self, state_dir: Path) -> None:
        self.tracker = ComputerActivityTracker(state_dir)
        self.captured: dict | None = None
        self.calls = 0

    @property
    def names(self) -> list[str]:
        return ["computer.click"]

    def execute(self, action: str, payload: dict) -> ActionResult:
        self.calls += 1
        operation_id = self.tracker.start(action)
        try:
            self.captured = self.tracker.snapshot()
            return ActionResult(True, "clicked")
        finally:
            self.tracker.finish(operation_id, result_ok=True)


class ComputerActivityTests(unittest.TestCase):
    def make_config(self, root: Path) -> AgentConfig:
        return AgentConfig(
            agent_name="test",
            poll_seconds=0.25,
            state_dir=root / "state",
            agent_repo_path=root / "agent",
            hordax_path=root / "hordax",
            bridge_path=root / "bridge",
            workspace_root=root,
            projects={},
            default_project="missing",
        )

    def test_registry_activity_is_visible_to_another_tracker_instance(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            config = self.make_config(root)
            registry = ActionRegistry(config)
            settings = config.state_dir / "agent-settings.json"
            settings.parent.mkdir(parents=True, exist_ok=True)
            settings.write_text(json.dumps({"computer_access": {
                "enabled": True,
                "full_access": True,
            }}), encoding="utf-8")

            started = threading.Event()
            release = threading.Event()

            def blocking(_payload: dict) -> ActionResult:
                started.set()
                self.assertTrue(release.wait(5))
                return ActionResult(True, "done")

            registry._actions["computer.screen_info"] = blocking
            result_box: list[ActionResult] = []
            worker = threading.Thread(
                target=lambda: result_box.append(registry.execute("computer.screen_info", {})),
                daemon=True,
            )
            worker.start()
            self.assertTrue(started.wait(5))

            observer = ComputerActivityTracker(config.state_dir)
            snapshot = observer.snapshot()
            self.assertTrue(snapshot["active"])
            self.assertEqual(snapshot["active_count"], 1)
            operation = snapshot["active_operations"][0]
            self.assertEqual(operation["action"], "computer.screen_info")
            self.assertEqual(operation["source"], "local_runtime")
            self.assertIsNone(operation["request_id"])
            self.assertNotIn("payload", operation)

            status = registry.execute("computer.activity_status", {})
            self.assertTrue(status.ok)
            self.assertTrue(status.data["active"])

            release.set()
            worker.join(5)
            self.assertFalse(worker.is_alive())
            self.assertTrue(result_box[0].ok)

            final = observer.snapshot()
            self.assertFalse(final["active"])
            self.assertEqual(final["last_completed"]["action"], "computer.screen_info")
            self.assertTrue(final["last_completed"]["result_ok"])

    def test_verified_product_provenance_is_bound_only_for_local_execution(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            state_dir = Path(raw) / "state"
            executor = TrackingExecutor(state_dir)
            gateway = ProductActionGateway(executor, AuditSink(), clock=lambda: 2_000_000_000)
            context = ProductRequestContext(
                request_id="req-activity-1",
                subject_id="user:123",
                device_id="device-1",
                space_id="space-1",
            )
            grant = ProductGrant(
                grant_id="grant-1",
                subject_id="user:123",
                actions=frozenset({"computer.click"}),
                device_id="device-1",
                space_id="space-1",
                expires_at_unix=2_000_000_100,
            )

            result = gateway.execute(
                "computer.click",
                {"x": 10, "y": 20, "button": "left", "clicks": 1},
                context=context,
                grant=grant,
            )
            self.assertTrue(result.ok)
            self.assertEqual(executor.calls, 1)
            self.assertIsNotNone(executor.captured)
            operation = executor.captured["active_operations"][0]
            self.assertEqual(operation["source"], "product_remote")
            self.assertEqual(operation["request_id"], "req-activity-1")
            self.assertNotIn("subject_id", operation)
            self.assertEqual(operation["device_id"], "device-1")
            self.assertEqual(operation["space_id"], "space-1")
            self.assertEqual(operation["grant_id"], "grant-1")
            self.assertNotIn("x", operation)
            self.assertNotIn("y", operation)

            local_id = executor.tracker.start("computer.windows")
            local_snapshot = executor.tracker.snapshot()
            local = next(item for item in local_snapshot["active_operations"] if item["operation_id"] == local_id)
            self.assertEqual(local["source"], "local_runtime")
            self.assertIsNone(local["request_id"])
            executor.tracker.finish(local_id, result_ok=True)

    def test_dead_process_tracker_is_removed_instead_of_reported_active(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            state_dir = Path(raw) / "state"
            root = state_dir / "computer-activity"
            root.mkdir(parents=True)
            stale = root / "tracker-999999-stale.json"
            stale.write_text(json.dumps({
                "schema": "ordax.computer-activity/1",
                "process": {"pid": 999999, "identity": "dead:999999", "instance_id": "stale"},
                "updated_at_unix_ms": 1,
                "active_operations": [{
                    "operation_id": "stale-op",
                    "action": "computer.click",
                    "source": "product_remote",
                    "started_at_unix_ms": 1,
                }],
                "last_completed": None,
            }), encoding="utf-8")

            snapshot = ComputerActivityTracker(state_dir).snapshot()
            self.assertFalse(snapshot["active"])
            self.assertFalse(stale.exists())

    def test_activity_status_rejects_payload_fields(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            registry = ActionRegistry(self.make_config(Path(raw)))
            result = registry.execute("computer.activity_status", {"unexpected": True})
            self.assertFalse(result.ok)
            self.assertIn("accepts no fields", result.summary)


if __name__ == "__main__":
    unittest.main()
