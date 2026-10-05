from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from ordax_dev_agent.terminal_outbox import TerminalOutbox


DEVICE_ID = "22222222-2222-4222-8222-222222222222"
JOB_ID = "33333333-3333-4333-8333-333333333333"
REPORT_ID = "77777777-7777-4777-8777-777777777777"


def report(*, summary: str = "ok") -> dict:
    return {
        "job_id": JOB_ID,
        "effect_id": "44444444-4444-4444-8444-444444444444",
        "attempt_id": "55555555-5555-4555-8555-555555555555",
        "lease_id": "66666666-6666-4666-8666-666666666666",
        "execution_epoch": 1,
        "agent_instance_id": "88888888-8888-4888-8888-888888888888",
        "boot_id": "99999999-9999-4999-8999-999999999999",
        "report_id": REPORT_ID,
        "status": "succeeded",
        "result": {"ok": True, "summary": summary, "data": {}},
        "result_sha256": "a" * 64,
        "error_code": None,
    }


class TerminalOutboxTests(unittest.TestCase):
    def test_persist_read_and_acknowledge_are_atomic_by_job(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            state = Path(raw)
            outbox = TerminalOutbox(state, "cloudflare-v3", DEVICE_ID)
            expected = report()

            path = outbox.persist(expected)

            self.assertTrue(path.is_file())
            self.assertEqual(outbox.pending(), [(path, expected)])
            envelope = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(envelope["schema"], 1)
            self.assertEqual(envelope["device_id"], DEVICE_ID)

            outbox.acknowledge(path)
            self.assertEqual(outbox.pending(), [])

    def test_same_report_is_idempotent_but_divergent_report_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            state = Path(raw)
            outbox = TerminalOutbox(state, "cloudflare-v3", DEVICE_ID)
            first = report()
            first_path = outbox.persist(first)

            second_path = outbox.persist(json.loads(json.dumps(first)))
            self.assertEqual(first_path, second_path)

            with self.assertRaisesRegex(RuntimeError, "outbox conflict"):
                outbox.persist(report(summary="different"))

            self.assertEqual(outbox.pending(), [(first_path, first)])

    def test_malformed_entry_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            state = Path(raw)
            root = state / "terminal-outbox" / "cloudflare-v3"
            root.mkdir(parents=True)
            (root / f"{JOB_ID}.json").write_text("{broken", encoding="utf-8")

            outbox = TerminalOutbox(state, "cloudflare-v3", DEVICE_ID)
            with self.assertRaisesRegex(RuntimeError, "entry is invalid"):
                outbox.pending()


if __name__ == "__main__":
    unittest.main()
