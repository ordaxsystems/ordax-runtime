from __future__ import annotations

import json
import os
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest import mock
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

    def test_two_writers_never_overwrite_a_terminal_result(self) -> None:
        # Force both writers past the absent-target check before publishing.
        # A concurrent divergent receipt must not replace the winner; two
        # identical receipts should remain idempotent.
        for summaries, divergent in (
            (("same", "same"), False),
            (("first", "second"), True),
        ):
            with self.subTest(divergent=divergent), tempfile.TemporaryDirectory() as raw:
                state = Path(raw)
                outbox = TerminalOutbox(state, "cloudflare-v3", DEVICE_ID)
                candidates = [report(summary=value) for value in summaries]
                barrier = threading.Barrier(2, timeout=10)
                real_link = os.link

                def link_at_same_time(source, destination):
                    barrier.wait()
                    return real_link(source, destination)

                def write(candidate):
                    return TerminalOutbox(state, "cloudflare-v3", DEVICE_ID).persist(candidate)

                with mock.patch(
                    "ordax_dev_agent.terminal_outbox.os.link",
                    side_effect=link_at_same_time,
                ):
                    with ThreadPoolExecutor(max_workers=2) as workers:
                        futures = [workers.submit(write, item) for item in candidates]
                        accepted = []
                        conflicts = []
                        for item, future in zip(candidates, futures):
                            try:
                                accepted.append((item, future.result(timeout=15)))
                            except RuntimeError as exc:
                                conflicts.append(str(exc))

                self.assertEqual(len(accepted), 1 if divergent else 2)
                self.assertEqual(len(conflicts), 1 if divergent else 0)
                if conflicts:
                    self.assertIn("outbox conflict", conflicts[0])
                entries = outbox.pending()
                self.assertEqual(len(entries), 1)
                self.assertEqual(entries[0][1], accepted[0][0])
                self.assertEqual(
                    {file.name for file in entries[0][0].parent.iterdir()},
                    {f"{JOB_ID}.json"},
                    "no losing or orphaned temporary receipt may remain",
                )

    def test_concurrent_ack_during_pending_scan_is_benign(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            outbox = TerminalOutbox(Path(raw), "cloudflare-v3", DEVICE_ID)
            expected = report()
            path = outbox.persist(expected)
            original = outbox._read_path

            def acknowledged_before_read(candidate: Path):
                candidate.unlink()
                return original(candidate)

            with mock.patch.object(
                outbox, "_read_path", side_effect=acknowledged_before_read,
            ):
                self.assertEqual(outbox.pending(), [])

            # Recovery and the completing job may both acknowledge the same
            # server-accepted receipt.
            outbox.acknowledge(path)
            outbox.acknowledge(path)

    def test_native_outbox_flushes_new_receipt_directory_entry(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            outbox = TerminalOutbox(Path(raw), "cloudflare-v3", DEVICE_ID)
            with mock.patch(
                "ordax_dev_agent.terminal_outbox.os.fsync", wraps=os.fsync,
            ) as fsync:
                receipt = outbox.persist(report())
            self.assertTrue(receipt.is_file())
            # The file is fsynced before publication, and on Native POSIX
            # the final directory entry is fsynced after link + unlink.
            self.assertEqual(fsync.call_count, 1 if os.name == "nt" else 2)
            self.assertEqual(outbox.pending()[0][1], report())

    def test_symlinked_terminal_receipt_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            state = Path(raw)
            outbox = TerminalOutbox(state, "cloudflare-v3", DEVICE_ID)
            receipt = outbox.persist(report())
            external = state / "external-report.json"
            external.write_bytes(receipt.read_bytes())
            receipt.unlink()
            try:
                receipt.symlink_to(external)
            except (NotImplementedError, OSError) as error:
                self.skipTest(f"symlinks are unavailable on this filesystem: {error}")
            with self.assertRaisesRegex(RuntimeError, "symlink"):
                outbox.pending()
            with self.assertRaisesRegex(RuntimeError, "symlink"):
                outbox.persist(report())

    def test_broken_symlinked_receipt_is_not_treated_as_acknowledged(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            state = Path(raw)
            outbox = TerminalOutbox(state, "cloudflare-v3", DEVICE_ID)
            receipt = outbox.persist(report())
            receipt.unlink()
            try:
                receipt.symlink_to(state / "missing-outside.json")
            except (NotImplementedError, OSError) as error:
                self.skipTest(f"symlinks are unavailable on this filesystem: {error}")
            with self.assertRaisesRegex(RuntimeError, "symlink"):
                outbox.pending()

    def test_read_is_bounded_even_if_file_grows_after_size_probe(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            state = Path(raw)
            outbox = TerminalOutbox(state, "cloudflare-v3", DEVICE_ID)
            receipt = outbox.persist(report())
            with receipt.open("wb") as stream:
                stream.write(b"x" * (1024 * 1024 + 100))
            original_fstat = os.fstat
            def stale_size(fd):
                actual = original_fstat(fd)
                return mock.Mock(st_mode=actual.st_mode, st_size=1)
            with mock.patch(
                "ordax_dev_agent.terminal_outbox.os.fstat", side_effect=stale_size,
            ):
                with self.assertRaisesRegex(RuntimeError, "invalid size"):
                    outbox._read_path(receipt)

    @unittest.skipUnless(hasattr(os, "O_NOFOLLOW"), "POSIX O_NOFOLLOW is required")
    def test_symlink_swap_between_check_and_open_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            state = Path(raw)
            outbox = TerminalOutbox(state, "cloudflare-v3", DEVICE_ID)
            receipt = outbox.persist(report())
            other = state / "other.json"
            other.write_bytes(receipt.read_bytes())
            real_open = os.open
            def swap_before_open(path, flags, *args, **kwargs):
                if Path(path) == receipt:
                    receipt.unlink()
                    receipt.symlink_to(other)
                return real_open(path, flags, *args, **kwargs)
            with mock.patch(
                "ordax_dev_agent.terminal_outbox.os.open",
                side_effect=swap_before_open,
            ):
                with self.assertRaisesRegex(RuntimeError, "cannot be opened"):
                    outbox._read_path(receipt)

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
