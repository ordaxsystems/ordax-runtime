from __future__ import annotations

import tempfile
import threading
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from ordax_dev_agent.cloudflare_control_plane import CloudflareControlPlane
from ordax_dev_agent.product_audit_outbox import ProductAuditOutbox


def event_dict(request_id: str, phase: str = "decision") -> dict:
    return {
        "request_id": request_id,
        "subject_id": str(uuid.uuid4()),
        "grant_id": str(uuid.uuid4()),
        "action": "computer.mouse_move",
        "project": None,
        "phase": phase,
        "decision": "allow",
        "reason": "grant_validated" if phase == "decision" else "local_action_completed",
        "payload_fields": ["x", "y"],
        "result_ok": None if phase == "decision" else True,
    }


class ProductAuditOutboxTests(unittest.TestCase):
    def test_persist_is_durable_idempotent_and_acknowledgeable(self):
        with tempfile.TemporaryDirectory() as tmp:
            box = ProductAuditOutbox(Path(tmp), "cloudflare-v3", str(uuid.uuid4()))
            body = event_dict(str(uuid.uuid4()))
            first = box.persist(body)
            second = box.persist(dict(body))
            self.assertEqual(first, second)
            self.assertEqual(box.pending(), [(first, body)])
            box.acknowledge(first)
            self.assertEqual(box.pending(), [])

    def test_conflicting_same_request_phase_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            box = ProductAuditOutbox(Path(tmp), "cloudflare-v3", str(uuid.uuid4()))
            body = event_dict(str(uuid.uuid4()))
            box.persist(body)
            changed = dict(body)
            changed["reason"] = "changed"
            with self.assertRaises(RuntimeError):
                box.persist(changed)


class CloudflareProductAuditTests(unittest.TestCase):
    def test_record_persists_locally_and_only_kicks_async_delivery(self):
        control = object.__new__(CloudflareControlPlane)
        control._product_audit_outbox = Mock()
        control._kick_product_audit_flush = Mock()
        event = SimpleNamespace(**event_dict(str(uuid.uuid4())))

        control.record_product_audit(event)

        control._product_audit_outbox.persist.assert_called_once()
        control._kick_product_audit_flush.assert_called_once_with()

    def test_flush_preserves_event_when_remote_delivery_fails(self):
        control = object.__new__(CloudflareControlPlane)
        path = Path("audit.json")
        body = event_dict(str(uuid.uuid4()))
        control._product_audit_outbox = Mock()
        control._product_audit_outbox.pending.return_value = [(path, body)]
        control._deliver_product_audit_body = Mock(side_effect=RuntimeError("offline"))
        control._product_audit_flush_lock = threading.Lock()
        control._product_audit_flush_thread = Mock()

        control._flush_product_audit_outbox()

        control._product_audit_outbox.acknowledge.assert_not_called()
        self.assertIsNone(control._product_audit_flush_thread)


if __name__ == "__main__":
    unittest.main()
