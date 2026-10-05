from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from ordax_dev_agent.actions import ActionRegistry
from ordax_dev_agent.config import AgentConfig
from ordax_dev_agent.execution_lock import ExecutionLock


class ComputerFilesystemActionsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.allowed = self.root / "allowed"
        self.allowed.mkdir()
        self.outside = self.root / "outside"
        self.outside.mkdir()
        self.state = self.root / "state"
        self.state.mkdir()
        (self.state / "agent-settings.json").write_text(
            json.dumps(
                {
                    "computer_access": {
                        "enabled": True,
                        "full_filesystem": False,
                        "allowed_roots": [str(self.allowed.resolve())],
                    }
                }
            ),
            encoding="utf-8",
        )
        self.registry = ActionRegistry(
            AgentConfig(
                agent_name="test",
                poll_seconds=0.25,
                state_dir=self.state,
                agent_repo_path=self.root / "agent",
                hordax_path=self.root / "hordax",
                bridge_path=self.root / "bridge",
                workspace_root=self.allowed,
                projects={},
                default_project="missing",
            )
        )

    def test_policy_status_and_escape_rejection(self):
        status = self.registry.execute("computer.access_status", {})
        self.assertTrue(status.ok)
        self.assertFalse(status.data["full_filesystem"])
        self.assertEqual([str(self.allowed.resolve())], status.data["allowed_roots"])
        self.assertEqual([], status.data["allowed_applications"])

        blocked = self.registry.execute(
            "computer.file_stat", {"path": str(self.outside)}
        )
        self.assertFalse(blocked.ok)
        self.assertIn("outside local ORDAX computer access roots", blocked.summary)

    def test_read_write_patch_move_search_and_remove(self):
        file = self.allowed / "notes.txt"
        created = self.registry.execute(
            "computer.text_write",
            {"path": str(file), "content": "hello renderer\nsecond line\n", "create": True},
        )
        self.assertTrue(created.ok, created.summary)
        sha = created.data["sha256"]

        read = self.registry.execute(
            "computer.text_read",
            {"path": str(file), "start_line": 1, "end_line": 1},
        )
        self.assertTrue(read.ok)
        self.assertEqual("hello renderer\n", read.data["content"])
        self.assertEqual(sha, read.data["sha256"])

        patched = self.registry.execute(
            "computer.text_patch",
            {
                "path": str(file),
                "expected_sha256": sha,
                "replacements": [
                    {"old": "renderer", "new": "preview", "expected_count": 1}
                ],
            },
        )
        self.assertTrue(patched.ok, patched.summary)
        self.assertNotEqual(sha, patched.data["sha256"])

        search = self.registry.execute(
            "computer.search",
            {
                "root": str(self.allowed),
                "query": "preview",
                "mode": "content",
                "max_results": 20,
            },
        )
        self.assertTrue(search.ok)
        self.assertTrue(Path(search.data["results"][0]["path"]).samefile(file))

        moved = self.allowed / "archive" / "notes.txt"
        move = self.registry.execute(
            "computer.path_move",
            {"source": str(file), "destination": str(moved)},
        )
        self.assertTrue(move.ok, move.summary)
        self.assertTrue(moved.is_file())

        remove = self.registry.execute(
            "computer.path_remove", {"path": str(moved)}
        )
        self.assertTrue(remove.ok, remove.summary)
        self.assertFalse(moved.exists())

    def test_existing_write_requires_matching_sha(self):
        file = self.allowed / "safe.txt"
        file.write_text("before", encoding="utf-8")
        denied = self.registry.execute(
            "computer.text_write",
            {"path": str(file), "content": "after"},
        )
        self.assertFalse(denied.ok)
        self.assertIn("expected_sha256", denied.summary)

        expected = hashlib.sha256(b"before").hexdigest()
        written = self.registry.execute(
            "computer.text_write",
            {
                "path": str(file),
                "content": "after",
                "expected_sha256": expected,
            },
        )
        self.assertTrue(written.ok, written.summary)
        self.assertEqual("after", file.read_text(encoding="utf-8"))

    def test_directory_list_does_not_follow_symlink_outside_policy(self):
        target = self.outside / "secret"
        target.mkdir()
        (target / "secret.txt").write_text("secret", encoding="utf-8")
        link = self.allowed / "external-link"
        try:
            link.symlink_to(target, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("symlink creation unavailable")

        result = self.registry.execute(
            "computer.directory_list",
            {"path": str(self.allowed), "max_depth": 3, "max_entries": 100},
        )
        self.assertTrue(result.ok)
        linked = next(item for item in result.data["entries"] if item["name"] == "external-link")
        self.assertFalse(linked["allowed"])
        self.assertFalse(any(item["name"] == "secret.txt" for item in result.data["entries"]))

    def test_observation_read_is_not_blocked_by_process_execution_lock(self):
        held = ExecutionLock(self.state)
        self.assertTrue(held.acquire())
        self.addCleanup(held.release)

        result = self.registry.execute(
            "computer.directory_list",
            {"path": str(self.allowed), "max_depth": 1, "max_entries": 20},
        )
        self.assertTrue(result.ok, result.summary)

    def test_observation_read_is_not_blocked_by_registry_execution_lock(self):
        self.assertTrue(self.registry._execution_lock.acquire(blocking=False))
        try:
            result = self.registry.execute(
                "computer.file_stat", {"path": str(self.allowed)}
            )
        finally:
            self.registry._execution_lock.release()
        self.assertTrue(result.ok, result.summary)

    def test_mutating_write_remains_serialized_by_process_lock(self):
        held = ExecutionLock(self.state)
        self.assertTrue(held.acquire())
        self.addCleanup(held.release)

        result = self.registry.execute(
            "computer.text_write",
            {"path": str(self.allowed / "blocked.txt"), "content": "x", "create": True},
        )
        self.assertFalse(result.ok)
        self.assertTrue(result.data.get("retryable"))
        self.assertFalse((self.allowed / "blocked.txt").exists())

    def test_configured_root_cannot_be_removed_or_moved(self):
        remove = self.registry.execute(
            "computer.path_remove",
            {"path": str(self.allowed), "recursive": True},
        )
        self.assertFalse(remove.ok)
        self.assertIn("configured computer access root", remove.summary)

        move = self.registry.execute(
            "computer.path_move",
            {
                "source": str(self.allowed),
                "destination": str(self.root / "moved-root"),
            },
        )
        self.assertFalse(move.ok)


if __name__ == "__main__":
    unittest.main()
