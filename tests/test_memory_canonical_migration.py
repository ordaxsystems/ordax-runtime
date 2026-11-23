from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ordax_core.memory import canonical_memory_db, resolve_memory_db
from ordax_core.memory_migration import migrate_memory_database


class CanonicalMemoryMigrationTests(unittest.TestCase):
    def make_source(self, root: Path) -> Path:
        source = root / "OrdaX" / "Studio" / "state.db"
        source.parent.mkdir(parents=True)
        conn = sqlite3.connect(source)
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("CREATE TABLE projects(id INTEGER PRIMARY KEY, name TEXT)")
            conn.execute("CREATE TABLE events(id INTEGER PRIMARY KEY, event TEXT)")
            conn.executemany("INSERT INTO projects(name) VALUES(?)", [(f"p{i}",) for i in range(6)])
            conn.executemany("INSERT INTO events(event) VALUES(?)", [(f"e{i}",) for i in range(6)])
            conn.commit()
        finally:
            conn.close()
        (source.parent / "contexts").mkdir()
        (source.parent / "contexts" / "demo.md").write_text("original context", encoding="utf-8")
        (source.parent / "BOOT_CONTEXT.md").write_text("original boot context", encoding="utf-8")
        return source

    def test_dry_run_does_not_create_target(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            source = self.make_source(root)
            target = root / "OrdaX" / "Runtime" / "memory" / "state.db"
            result = migrate_memory_database(source, target)
            self.assertEqual(result["status"], "ready_for_explicit_migration")
            self.assertEqual(result["tables"]["projects"], 6)
            self.assertEqual(result["tables"]["events"], 6)
            self.assertFalse(target.parent.exists())
            self.assertTrue(source.exists())

    def test_sqlite_backup_preserves_rows_context_and_source(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            source = self.make_source(root)
            target = root / "OrdaX" / "Runtime" / "memory" / "state.db"
            result = migrate_memory_database(source, target, apply=True)
            self.assertEqual(result["status"], "verified_canonical_created")
            self.assertTrue(source.exists())
            self.assertTrue(target.exists())
            conn = sqlite3.connect(target)
            try:
                self.assertEqual(conn.execute("PRAGMA quick_check").fetchone()[0], "ok")
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM projects").fetchone()[0], 6)
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM events").fetchone()[0], 6)
            finally:
                conn.close()
            self.assertEqual((target.parent / "contexts" / "demo.md").read_text(encoding="utf-8"), "original context")
            self.assertEqual((target.parent / "BOOT_CONTEXT.md").read_text(encoding="utf-8"), "original boot context")
            proof = json.loads((target.parent / "migration-evidence.json").read_text(encoding="utf-8"))
            self.assertEqual(proof["tables"]["projects"], 6)
            with self.assertRaisesRegex(RuntimeError, "canonical_destination_already_exists"):
                migrate_memory_database(source, target, apply=True)

    def test_rejects_existing_target_even_for_dry_run(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            source = self.make_source(root)
            target = root / "OrdaX" / "Runtime" / "memory" / "state.db"
            target.parent.mkdir(parents=True)
            target.write_bytes(b"unverified")
            with self.assertRaisesRegex(RuntimeError, "canonical_destination_already_exists"):
                migrate_memory_database(source, target)
            self.assertEqual(target.read_bytes(), b"unverified")

    def test_live_sqlite_writer_blocks_migration_without_destination(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            source = self.make_source(root)
            target = root / "OrdaX" / "Runtime" / "memory" / "state.db"
            conn = sqlite3.connect(source, isolation_level=None)
            try:
                conn.execute("BEGIN IMMEDIATE")
                with self.assertRaises(sqlite3.OperationalError):
                    migrate_memory_database(source, target, apply=True)
                self.assertTrue(source.exists())
                self.assertFalse(target.exists())
            finally:
                conn.rollback()
                conn.close()

    def test_corrupted_source_fails_closed(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            source = root / "OrdaX" / "Studio" / "state.db"
            source.parent.mkdir(parents=True)
            source.write_bytes(b"not sqlite")
            target = root / "OrdaX" / "Runtime" / "memory" / "state.db"
            with self.assertRaises(sqlite3.DatabaseError):
                migrate_memory_database(source, target, apply=True)
            self.assertFalse(target.parent.exists())

    @unittest.skipUnless(os.name == "nt", "Windows LOCALAPPDATA selector")
    def test_resolver_uses_legacy_until_verified_cutover(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            with patch.dict(os.environ, {"LOCALAPPDATA": str(root)}, clear=False):
                source = self.make_source(root)
                canonical = canonical_memory_db()
                self.assertEqual(canonical, root / "OrdaX" / "Runtime" / "memory" / "state.db")
                self.assertEqual(resolve_memory_db(), source)
                migrate_memory_database(source, canonical, apply=True)
                self.assertEqual(resolve_memory_db(), canonical)


if __name__ == "__main__":
    unittest.main()
