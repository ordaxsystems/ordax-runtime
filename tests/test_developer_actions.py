from __future__ import annotations

import hashlib
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from ordax_dev_agent.actions import ActionRegistry
from ordax_dev_agent.config import AgentConfig


class DeveloperActionsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.project = self.root / "project"
        self.project.mkdir()
        self.registry = ActionRegistry(
            AgentConfig(
                agent_name="test",
                poll_seconds=0.25,
                state_dir=self.root / "state",
                agent_repo_path=self.root / "agent",
                hordax_path=self.root / "hordax",
                bridge_path=self.root / "bridge",
                projects={"project": {"path": str(self.project), "apps": []}},
                default_project="project",
            )
        )

    def test_workspace_actions_are_not_limited_to_legacy_top_level_folders(self) -> None:
        target = self.project / "custom" / "deep" / "settings.ini"
        target.parent.mkdir(parents=True)
        target.write_bytes(b"mode=one\n")

        read = self.registry.execute(
            "workspace.text_read",
            {"project": "project", "path": "custom/deep/settings.ini"},
        )
        self.assertTrue(read.ok)
        self.assertEqual("mode=one\n", read.data["content"])

        patch = self.registry.execute(
            "workspace.text_patch",
            {
                "project": "project",
                "path": "custom/deep/settings.ini",
                "expected_sha256": read.data["sha256"],
                "replacements": [{"old": "mode=one", "new": "mode=two"}],
            },
        )
        self.assertTrue(patch.ok)
        self.assertEqual(b"mode=two\n", target.read_bytes())

    def test_workspace_write_move_and_remove_are_sha_guarded(self) -> None:
        created = self.registry.execute(
            "workspace.text_write",
            {
                "project": "project",
                "path": "feature/new.txt",
                "content": "hello\n",
                "create": True,
            },
        )
        self.assertTrue(created.ok)
        original_sha = hashlib.sha256(b"hello\n").hexdigest()
        self.assertEqual(original_sha, created.data["sha256"])

        moved = self.registry.execute(
            "workspace.path_move",
            {
                "project": "project",
                "source": "feature/new.txt",
                "destination": "feature/renamed.txt",
                "expected_sha256": original_sha,
            },
        )
        self.assertTrue(moved.ok)

        stale_remove = self.registry.execute(
            "workspace.path_remove",
            {
                "project": "project",
                "path": "feature/renamed.txt",
                "expected_sha256": "0" * 64,
            },
        )
        self.assertFalse(stale_remove.ok)
        self.assertTrue((self.project / "feature" / "renamed.txt").is_file())

        removed = self.registry.execute(
            "workspace.path_remove",
            {
                "project": "project",
                "path": "feature/renamed.txt",
                "expected_sha256": original_sha,
            },
        )
        self.assertTrue(removed.ok)
        self.assertFalse((self.project / "feature" / "renamed.txt").exists())

    def test_dot_git_internals_and_project_root_deletion_are_blocked(self) -> None:
        (self.project / ".git").mkdir()
        internal = self.registry.execute(
            "workspace.directory_list",
            {"project": "project", "path": ".git"},
        )
        self.assertFalse(internal.ok)

        root_remove = self.registry.execute(
            "workspace.path_remove",
            {"project": "project", "path": ".", "recursive": True},
        )
        self.assertFalse(root_remove.ok)
        self.assertTrue(self.project.is_dir())

    def test_terminal_exec_supports_real_argv_and_requires_explicit_shell_mode(self) -> None:
        result = self.registry.execute(
            "terminal.exec",
            {
                "project": "project",
                "argv": [sys.executable, "-c", "from pathlib import Path; Path('made.txt').write_text('ok', encoding='utf-8'); print('done')"],
                "timeout_seconds": 30,
            },
        )
        self.assertTrue(result.ok)
        self.assertIn("done", result.data["stdout"])
        self.assertEqual("ok", (self.project / "made.txt").read_text(encoding="utf-8"))

        refused = self.registry.execute(
            "terminal.exec",
            {"project": "project", "command": "echo unsafe-by-accident"},
        )
        self.assertFalse(refused.ok)
        self.assertIn("shell=true", refused.summary)

    def test_git_command_blocks_provider_credential_and_config_access(self) -> None:
        for args in (
            ["credential", "fill"],
            ["credential-store", "get"],
            ["config", "--list"],
        ):
            with self.subTest(args=args):
                refused = self.registry.execute(
                    "git.command",
                    {"project": "project", "args": args},
                )
                self.assertFalse(refused.ok)
                self.assertIn("not allowed through the remote boundary", refused.summary)

    def test_git_command_redacts_authenticated_remote_urls(self) -> None:
        subprocess.run(
            ["git", "-C", str(self.project), "init"],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        subprocess.run(
            [
                "git",
                "-C",
                str(self.project),
                "remote",
                "add",
                "origin",
                "https://user:super-secret@github.com/example/project.git",
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        result = self.registry.execute(
            "git.command",
            {"project": "project", "args": ["remote", "get-url", "origin"]},
        )
        self.assertTrue(result.ok)
        self.assertNotIn("super-secret", result.data["stdout"])
        self.assertNotIn("user:", result.data["stdout"])
        self.assertIn("https://***@github.com/example/project.git", result.data["stdout"])

    def test_git_command_cannot_redirect_git_to_another_worktree(self) -> None:
        refused = self.registry.execute(
            "git.command",
            {"project": "project", "args": ["-C", "..", "status"]},
        )
        self.assertFalse(refused.ok)
        self.assertIn("escape project scope", refused.summary)


if __name__ == "__main__":
    unittest.main()
