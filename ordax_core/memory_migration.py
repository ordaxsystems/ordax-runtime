"""Explicit, non-destructive migration of Runtime memory to its canonical location.

Run while the Runtime and older consumers are quiescent. No automatic relocation
occurs during an ordinary read. The source remains untouched for recovery.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import uuid
from contextlib import closing
from pathlib import Path

from .memory import canonical_memory_db, legacy_memory_db


def _open_readonly(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=2)


def _digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _inventory(conn: sqlite3.Connection) -> dict[str, int]:
    if conn.execute("PRAGMA quick_check").fetchone()[0] != "ok":
        raise RuntimeError("sqlite_integrity_failed")
    tables = [str(row[0]) for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
    )]
    result: dict[str, int] = {}
    for table in tables:
        if not table.replace("_", "").isalnum() or not table[0].isalpha():
            raise RuntimeError("sqlite_invalid_table_name")
        result[table] = int(conn.execute(
            'SELECT COUNT(*) FROM "' + table + '"'
        ).fetchone()[0])
    return result


def migrate_memory_database(source: Path, target: Path, *, apply: bool = False) -> dict:
    source = source.expanduser()
    target = target.expanduser()
    if source.is_symlink() or target.is_symlink() or target.parent.is_symlink():
        raise RuntimeError("memory_path_link_not_allowed")
    source = source.resolve()
    target = target.resolve()
    if source == target:
        raise RuntimeError("source_equals_target")
    if not source.is_file():
        raise RuntimeError("legacy_source_missing_or_link")
    if target.exists() or target.parent.exists():
        # Never adopt or overwrite an unverified competing memory database,
        # directory, or partial previous migration.
        raise RuntimeError("canonical_destination_already_exists")
    with closing(_open_readonly(source)) as conn:
        counts = _inventory(conn)
    result = {
        "status": "ready_for_explicit_migration" if not apply else "pending",
        "source": str(source),
        "target": str(target),
        "tables": counts,
        "source_preserved": True,
    }
    if not apply:
        return result

    target.parent.parent.mkdir(parents=True, exist_ok=True)
    stage = target.parent.with_name(target.parent.name + ".migration-" + uuid.uuid4().hex)
    stage.mkdir(mode=0o700)
    try:
        # A write reservation blocks writers during snapshot + context transfer.
        # The SQLite backup API includes committed WAL pages correctly.
        lock = sqlite3.connect(source, timeout=1, isolation_level=None)
        try:
            lock.execute("PRAGMA busy_timeout=1000")
            lock.execute("BEGIN IMMEDIATE")
            with closing(_open_readonly(source)) as src, closing(sqlite3.connect(stage / target.name)) as dst:
                src.backup(dst)
                dst.commit()
            with closing(_open_readonly(stage / target.name)) as cloned:
                if _inventory(cloned) != counts:
                    raise RuntimeError("sqlite_snapshot_mismatch")
            parent = source.parent
            for name in ("contexts", "BOOT_CONTEXT.md"):
                original = parent / name
                if original.is_symlink():
                    raise RuntimeError("context_link_not_allowed")
                if original.is_dir():
                    if any(part.is_symlink() for part in original.rglob("*")):
                        raise RuntimeError("context_nested_link_not_allowed")
                    shutil.copytree(original, stage / name)
                elif original.is_file():
                    shutil.copy2(original, stage / name)
            # Contexts are not protected by SQLite's write lock, verify them.
            for name in ("contexts", "BOOT_CONTEXT.md"):
                original = parent / name
                if original.is_file():
                    if _digest(original) != _digest(stage / name):
                        raise RuntimeError("context_changed_during_migration")
                elif original.is_dir():
                    for f in original.rglob("*"):
                        if f.is_file() and _digest(f) != _digest(stage / name / f.relative_to(original)):
                            raise RuntimeError("context_changed_during_migration")
            # Verify again before exposing canonical state.
            with closing(_open_readonly(source)) as original:
                if _inventory(original) != counts:
                    raise RuntimeError("legacy_changed_during_migration")
            (stage / "migration-evidence.json").write_text(
                json.dumps({"source": str(source), "tables": counts,
                            "status": "verified_backup_source_retained"},
                           ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            os.rename(stage, target.parent)
            lock.commit()
        except BaseException:
            lock.rollback()
            raise
        finally:
            lock.close()
    finally:
        if stage.exists():
            shutil.rmtree(stage)
    result["status"] = "verified_canonical_created"
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Migrate OrdaX Runtime memory safely")
    parser.add_argument("--apply", action="store_true", help="Perform verified copy; never delete source")
    options = parser.parse_args()
    source = legacy_memory_db()
    if source is None:
        raise SystemExit("legacy_source_not_found")
    result = migrate_memory_database(source, canonical_memory_db(), apply=options.apply)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
