from __future__ import annotations

import concurrent.futures
import tempfile
import unittest
from pathlib import Path

from ordax_dev_agent.persistent_worker_generation import (
    MODULE_DIR, MODULES, inventory, prepare_generation,
)


class WorkerGenerationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name)
        self.source = base / "runtime"
        self.root = base / "generations"
        (self.source / MODULE_DIR).mkdir(parents=True)
        for name in ("python.exe", "python3.dll", "python312.dll", "python312.zip", "python312._pth"):
            (self.source / name).write_bytes(name.encode())
        for name in MODULES:
            (self.source / MODULE_DIR / name).write_bytes(name.encode())
        (self.source / "not-a-worker.py").write_text("excluded", encoding="utf-8")

    def test_same_content_reused_and_only_worker_files_copied(self):
        descriptor = inventory(self.source)
        first = prepare_generation(self.source, self.root, descriptor)
        second = prepare_generation(self.source, self.root, descriptor)
        self.assertEqual(first, second)
        self.assertEqual(first.name, descriptor["generation"])
        self.assertFalse((first / "not-a-worker.py").exists())
        self.assertEqual([first], list(self.root.iterdir()))

    def test_concurrent_starts_share_one_generation(self):
        descriptor = inventory(self.source)
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            paths = list(pool.map(lambda _: prepare_generation(self.source, self.root, descriptor), range(8)))
        self.assertEqual(len(set(paths)), 1)
        self.assertEqual(list(self.root.iterdir()), paths[:1])

    def test_generation_is_not_mutated_on_source_change(self):
        old = inventory(self.source)
        first = prepare_generation(self.source, self.root, old)
        (self.source / "python.exe").write_bytes(b"new-version")
        second = prepare_generation(self.source, self.root, inventory(self.source))
        self.assertNotEqual(first, second)
        self.assertEqual((first / "python.exe").read_bytes(), b"python.exe")

    def test_tampered_existing_generation_rejected(self):
        descriptor = inventory(self.source)
        target = prepare_generation(self.source, self.root, descriptor)
        (target / "python.exe").write_bytes(b"tampered")
        with self.assertRaisesRegex(ValueError, "integrity_mismatch"):
            prepare_generation(self.source, self.root, descriptor)

    def test_manifest_path_escape_rejected(self):
        descriptor = inventory(self.source)
        descriptor["files"]["../escaped.exe"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "invalid_path"):
            prepare_generation(self.source, self.root, descriptor)

    def test_source_mutation_cannot_publish_generation(self):
        descriptor = inventory(self.source)
        (self.source / "python.exe").write_bytes(b"tampered")
        with self.assertRaisesRegex(ValueError, "source_mismatch"):
            prepare_generation(self.source, self.root, descriptor)
        self.assertEqual([], list(self.root.iterdir()))


if __name__ == "__main__":
    unittest.main()
