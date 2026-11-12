"""Physical-disk transfer invariants without cloud or installed Runtime dependencies."""
from __future__ import annotations

import base64
import hashlib
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from ordax_dev_agent.computer_transfer_actions import (
    ComputerTransferActions, MAX_CHUNK_BYTES, MAX_TRANSFER_BYTES,
)


class Harness(ComputerTransferActions):
    def __init__(self, root: Path):
        self.allowed = root / "allowed"
        self.allowed.mkdir(exist_ok=True)
        self.config = SimpleNamespace(state_dir=root / "state")

    def _computer_path(self, path, *, must_exist, mutating):
        dest = Path(path).resolve()
        if not dest.is_relative_to(self.allowed.resolve()):
            raise PermissionError("outside roots")
        if must_exist and not dest.exists():
            raise FileNotFoundError(dest)
        return dest, None


class ComputerTransferTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.h = Harness(self.root)
        self.file = self.h.allowed / "sample.bin"

    def begin(self, content: bytes, *, create=True, expected=None, digest=None):
        args = {
            "path": str(self.file), "size_bytes": len(content),
            "sha256": digest or hashlib.sha256(content).hexdigest(), "create": create,
        }
        if expected is not None:
            args["expected_sha256"] = expected
        return self.h.computer_transfer_begin(args)

    def chunk(self, transfer_id: str, offset: int, chunk: bytes):
        return self.h.computer_transfer_chunk({
            "transfer_id": transfer_id, "offset": offset,
            "data_base64": base64.b64encode(chunk).decode("ascii"),
        })

    def test_large_binary_resumes_replays_and_commits_atomically(self):
        data = (b"hello\x00\xff" + "caf\u00e9".encode("utf-8")) * 6200
        self.assertGreater(len(data), MAX_CHUNK_BYTES)
        started = self.begin(data)
        self.assertTrue(started.ok, started.summary)
        tid = started.data["transfer_id"]
        first = data[:MAX_CHUNK_BYTES]
        self.assertTrue(self.chunk(tid, 0, first).ok)
        self.assertFalse(self.file.exists())
        replay = self.chunk(tid, 0, first)
        self.assertTrue(replay.ok)
        self.assertTrue(replay.data["replayed"])
        resumed = Harness(self.root).computer_transfer_status({"transfer_id": tid})
        self.assertEqual(resumed.data["offset"], len(first))
        bad = self.chunk(tid, len(first) + 1, b"x")
        self.assertFalse(bad.ok)
        self.assertEqual(bad.data["expected_offset"], len(first))
        self.assertFalse(self.h.computer_transfer_commit({"transfer_id": tid}).ok)
        self.assertTrue(self.chunk(tid, len(first), data[len(first):]).ok)
        committed = self.h.computer_transfer_commit({"transfer_id": tid})
        self.assertTrue(committed.ok, committed.summary)
        self.assertEqual(self.file.read_bytes(), data)
        self.assertTrue(self.h.computer_transfer_status({"transfer_id": tid}).data["committed"])
        self.assertTrue(self.h.computer_transfer_commit({"transfer_id": tid}).data["replayed"])

    def test_plain_utf8_chunk_no_base64_overhead(self):
        text = ("Café, bolo e código — 🧪\n" * 100)
        data = text.encode("utf-8")
        start = self.begin(data)
        self.assertTrue(start.ok)
        tid = start.data["transfer_id"]
        sent = self.h.computer_transfer_chunk({
            "transfer_id": tid, "offset": 0, "text_utf8": text,
        })
        self.assertTrue(sent.ok, sent.summary)
        self.assertEqual(sent.data["offset"], len(data))
        self.assertTrue(self.h.computer_transfer_commit({"transfer_id": tid}).ok)
        self.assertEqual(self.file.read_bytes(), data)

    def test_stale_original_never_overwritten(self):
        self.file.write_bytes(b"original")
        sha = hashlib.sha256(b"original").hexdigest()
        started = self.begin(b"replacement", expected=sha)
        self.assertTrue(started.ok)
        tid = started.data["transfer_id"]
        self.assertTrue(self.chunk(tid, 0, b"replacement").ok)
        self.file.write_bytes(b"external update")
        committed = self.h.computer_transfer_commit({"transfer_id": tid})
        self.assertFalse(committed.ok)
        self.assertEqual(self.file.read_bytes(), b"external update")
        self.assertTrue(self.h.computer_transfer_abort({"transfer_id": tid}).ok)

    def test_corrupt_digest_and_size_fail_closed(self):
        self.file.write_bytes(b"do not touch")
        sha = hashlib.sha256(self.file.read_bytes()).hexdigest()
        start = self.begin(b"replacement", expected=sha, digest="0" * 64)
        self.assertTrue(start.ok)
        tid = start.data["transfer_id"]
        self.assertFalse(self.chunk(tid, 0, b"replacement-and-extra").ok)
        self.assertTrue(self.chunk(tid, 0, b"replacement").ok)
        self.assertFalse(self.h.computer_transfer_commit({"transfer_id": tid}).ok)
        self.assertEqual(self.file.read_bytes(), b"do not touch")

    def test_limits_and_authorization(self):
        bad = self.begin(b"small", digest="not-a-hash")
        self.assertFalse(bad.ok)
        args = {"path": str(self.file), "size_bytes": MAX_TRANSFER_BYTES + 1,
                "sha256": "0" * 64, "create": True}
        self.assertFalse(self.h.computer_transfer_begin(args).ok)
        with self.assertRaises(PermissionError):
            self.h.computer_transfer_begin({
                "path": str(self.root / "outside.txt"), "size_bytes": 1,
                "sha256": "0" * 64, "create": True,
            })
        started = self.begin(b"x")
        tid = started.data["transfer_id"]
        self.assertFalse(self.h.computer_transfer_chunk({
            "transfer_id": tid, "offset": 0, "data_base64": "%%%"}).ok)
        self.assertFalse(self.chunk(tid, 0, b"x" * (MAX_CHUNK_BYTES + 1)).ok)
        self.assertTrue(self.h.computer_transfer_abort({"transfer_id": tid}).ok)
        self.assertFalse(self.file.exists())

    def test_zero_byte_document_and_commit_receipt_replay(self):
        empty = b""
        created = self.begin(empty)
        self.assertTrue(created.ok)
        tid = created.data["transfer_id"]
        self.assertTrue(self.h.computer_transfer_commit({"transfer_id": tid}).ok)
        self.assertEqual(self.file.read_bytes(), b"")
        status = self.h.computer_transfer_status({"transfer_id": tid})
        self.assertEqual(status.data["offset"], 0)
        self.assertTrue(status.data["committed"])

    def test_crash_between_atomic_replace_and_receipt_is_idempotent(self):
        original = b"initial document"
        updated = b"this is the new document"
        self.file.write_bytes(original)
        sha = hashlib.sha256(original).hexdigest()
        started = self.begin(updated, expected=sha)
        self.assertTrue(started.ok)
        tid = started.data["transfer_id"]
        self.assertTrue(self.chunk(tid, 0, updated).ok)
        # Simulate os.replace having completed before receipt persistence.
        self.file.write_bytes(updated)
        recovered = self.h.computer_transfer_commit({"transfer_id": tid})
        self.assertTrue(recovered.ok, recovered.summary)
        self.assertTrue(recovered.data["replayed"])
        self.assertEqual(self.file.read_bytes(), updated)
        self.assertTrue(self.h.computer_transfer_status({"transfer_id": tid}).data["committed"])



if __name__ == "__main__":
    unittest.main()
