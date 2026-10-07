from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from ordax_dev_agent.actions import ActionRegistry
from ordax_dev_agent.config import AgentConfig
from ordax_dev_agent.computer_filesystem_actions import (
    computer_access_management_status, update_computer_access_policy,
)


class ComputerRuntimeIntegrityTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.state = self.root / "state"
        self.state.mkdir()
        self.source = self.root / "active-runtime"
        self.source.mkdir()
        self.code = self.source / "actions.py"
        self.code.write_text("AUTHORITY_MARKER", encoding="utf-8")
        self.secret = self.state / "device-secret.json"
        self.secret.write_text('"AUTHORITY_MARKER"', encoding="utf-8")
        self.settings = self.state / "agent-settings.json"
        self.config = AgentConfig(
            agent_name="test", poll_seconds=0.25, state_dir=self.state,
            agent_repo_path=self.source, hordax_path=self.root / "hordax",
            bridge_path=self.source, workspace_root=self.root,
            projects={}, default_project="missing",
        )
        self.policy()
        self.registry = ActionRegistry(self.config)

    def policy(self, *, full_access=False, full_filesystem=False):
        self.settings.write_text(json.dumps({"computer_access": {
            "enabled": True, "full_access": full_access,
            "full_filesystem": full_filesystem, "allowed_roots": [str(self.root)],
        }}), encoding="utf-8")

    def test_no_filesystem_mode_can_read_replace_or_delete_runtime_authority(self):
        for mode in ({}, {"full_filesystem": True}, {"full_access": True}):
            self.policy(**mode)
            before = self.settings.read_bytes()
            for target in (self.settings, self.secret, self.code):
                digest = hashlib.sha256(target.read_bytes()).hexdigest()
                calls = [
                    ("computer.text_read", {"path": str(target)}),
                    ("computer.text_write", {"path": str(target), "content": "{}", "expected_sha256": digest}),
                    ("computer.text_patch", {"path": str(target), "expected_sha256": digest, "replacements": [{"old": "False", "new": "True"}]}),
                    ("computer.path_remove", {"path": str(target), "expected_sha256": digest}),
                    ("computer.path_move", {"source": str(target), "destination": str(self.root / "moved.txt")}),
                ]
                for action, args in calls:
                    with self.subTest(mode=mode, action=action, target=target.name):
                        result = self.registry.execute(action, args)
                        self.assertFalse(result.ok, result.summary)
                        self.assertIn("protected", result.summary)
                self.assertTrue(target.exists())
            self.assertEqual(before, self.settings.read_bytes())

    def test_ancestor_remove_move_and_overwrite_are_blocked_before_io(self):
        self.policy(full_access=True)
        user_file = self.root / "user.txt"
        user_file.write_text("user data", encoding="utf-8")
        for action, args in (
            ("computer.path_remove", {"path": str(self.root), "recursive": True}),
            ("computer.path_move", {"source": str(self.root), "destination": str(self.root.with_name("moved"))}),
            ("computer.path_move", {"source": str(user_file), "destination": str(self.settings), "overwrite": True}),
            ("computer.directory_create", {"path": str(self.state / "injected")}),
        ):
            result = self.registry.execute(action, args)
            self.assertFalse(result.ok, result.summary)
            self.assertIn("protected", result.summary)
        self.assertTrue(self.settings.exists())
        self.assertEqual("user data", user_file.read_text(encoding="utf-8"))

    def test_listing_and_search_never_descend_into_private_runtime_roots(self):
        self.policy(full_access=True)
        listing = self.registry.execute("computer.directory_list", {"path": str(self.root), "max_depth": 6})
        self.assertTrue(listing.ok, listing.summary)
        entries = {Path(item["path"]).name: item for item in listing.data["entries"]}
        self.assertFalse(entries["state"]["allowed"])
        self.assertFalse(entries["active-runtime"]["allowed"])
        self.assertNotIn("device-secret.json", entries)
        search = self.registry.execute("computer.search", {"root": str(self.root), "query": "AUTHORITY_MARKER", "mode": "content"})
        self.assertTrue(search.ok, search.summary)
        self.assertEqual([], search.data["results"])

    def test_symlink_alias_cannot_reach_the_policy_file(self):
        alias = self.root / "ordinary.txt"
        try:
            alias.symlink_to(self.settings)
        except OSError as error:
            self.skipTest(f"symlink creation unavailable: {error}")
        self.policy(full_access=True)
        result = self.registry.execute("computer.text_write", {
            "path": str(alias), "content": "{}",
            "expected_sha256": hashlib.sha256(self.settings.read_bytes()).hexdigest(),
        })
        self.assertFalse(result.ok)
        self.assertIn("protected", result.summary)

    def test_user_files_and_owner_policy_port_still_work(self):
        self.policy(full_filesystem=True)
        target = self.root / "user-data" / "notes.txt"
        result = self.registry.execute("computer.text_write", {"path": str(target), "create": True, "content": "ação"})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual("ação", target.read_text(encoding="utf-8"))
        status = computer_access_management_status(self.config)
        updated = update_computer_access_policy(self.config, {"full_access": True, "expected_revision": status["revision"]})
        self.assertTrue(updated["full_access"])

    def test_move_remove_enforce_sha_before_any_change(self):
        self.policy(full_access=True)
        source = self.root / "user.txt"
        destination = self.root / "existing.txt"
        source.write_bytes(b"new source")
        destination.write_bytes(b"preserved destination")
        moved = self.registry.execute("computer.path_move", {
            "source": str(source), "destination": str(destination), "overwrite": True,
            "expected_sha256": "0" * 64,
        })
        self.assertFalse(moved.ok)
        self.assertEqual(b"preserved destination", destination.read_bytes())
        removed = self.registry.execute("computer.path_remove", {"path": str(source), "expected_sha256": "0" * 64})
        self.assertFalse(removed.ok)
        self.assertEqual(b"new source", source.read_bytes())
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        self.assertTrue(self.registry.execute("computer.path_move", {
            "source": str(source), "destination": str(destination), "overwrite": True,
            "expected_sha256": digest,
        }).ok)
        self.assertTrue(self.registry.execute("computer.path_remove", {"path": str(destination), "expected_sha256": digest}).ok)
        directory = self.root / "user-folder"
        directory.mkdir()
        self.assertFalse(self.registry.execute("computer.path_remove", {
            "path": str(directory), "recursive": True, "expected_sha256": digest,
        }).ok)
        self.assertTrue(directory.exists())

    def test_removing_a_user_symlink_preserves_its_target(self):
        self.policy(full_access=True)
        target = self.root / "user.txt"
        target.write_bytes(b"preserve target")
        alias = self.root / "alias.txt"
        try:
            alias.symlink_to(target)
        except OSError as error:
            self.skipTest(f"symlink creation unavailable: {error}")
        removed = self.registry.execute("computer.path_remove", {"path": str(alias)})
        self.assertTrue(removed.ok, removed.summary)
        self.assertFalse(alias.exists())
        self.assertEqual(b"preserve target", target.read_bytes())
        alias.symlink_to(target)
        moved_alias = self.root / "moved-alias.txt"
        moved = self.registry.execute("computer.path_move", {"source": str(alias), "destination": str(moved_alias)})
        self.assertTrue(moved.ok, moved.summary)
        self.assertTrue(moved_alias.is_symlink())
        self.assertEqual(b"preserve target", target.read_bytes())
        dangling = self.root / "dangling.txt"
        dangling.symlink_to(self.root / "missing.txt")
        removed = self.registry.execute("computer.path_remove", {"path": str(dangling)})
        self.assertTrue(removed.ok, removed.summary)
        self.assertFalse(dangling.is_symlink())


if __name__ == "__main__":
    unittest.main()
