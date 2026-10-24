"""Security regressions for the existing read-only project workspace port.

Only disposable fixtures are used. No Product grant, real user folder, App,
provider or remote service is accessed by this suite.
"""
from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from ordax_dev_agent.project_workspace_port import (
    ProjectWorkspaceDenied,
    ProjectWorkspacePort,
    VerifiedProjectResource,
    WorkspaceActor,
    _git,
)


class ProjectWorkspacePortTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "project"
        self.root.mkdir()
        self.actor = WorkspaceActor("actor_one", "client_one", "session_one", "project_one")
        identity = self.root.stat()
        self.grant = VerifiedProjectResource(
            actor_id=self.actor.actor_id,
            client_id=self.actor.client_id,
            workspace_session_id=self.actor.workspace_session_id,
            project_id=self.actor.project_id,
            device_id="device_one",
            resource_id="resource_abcdefgh1234",
            root=self.root,
            root_device=identity.st_dev,
            root_inode=identity.st_ino,
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
            allowed_actions=frozenset({"read"}),
        )

    def port(self, resolver=None, local_policy=None) -> ProjectWorkspacePort:
        return ProjectWorkspacePort(
            device_id="device_one",
            grants=resolver or (lambda _actor, _device, _resource: self.grant),
            local_policy=local_policy or (lambda _root: True),
        )

    def snapshot(self, port=None) -> dict:
        return (port or self.port()).snapshot(
            self.actor, "device_one", "resource_abcdefgh1234"
        )

    def test_plain_directory_snapshot_contains_no_path_or_content(self) -> None:
        (self.root / "index.html").write_text("private fixture", encoding="utf-8")
        result = self.snapshot()
        self.assertEqual(result["fileCount"], 1)
        self.assertEqual(result["changedCount"], 0)
        self.assertEqual(result["git"], {"isRepository": False})
        self.assertTrue(result["revision"].startswith("rev_"))
        self.assertNotIn(str(self.root), str(result))
        self.assertNotIn("private fixture", str(result))

    def test_directory_entries_are_bounded_even_without_regular_files(self) -> None:
        from ordax_dev_agent import project_workspace_port

        for index in range(4):
            (self.root / f"empty-{index}").mkdir()
        with patch.object(project_workspace_port, "_MAX_ENTRIES", 3):
            with self.assertRaisesRegex(ProjectWorkspaceDenied, "workspace_scan_limit"):
                self.snapshot()

    def test_git_pointer_file_cannot_escape_resource_root(self) -> None:
        (self.root / ".git").write_text("gitdir: ../../outside", encoding="utf-8")
        with self.assertRaisesRegex(ProjectWorkspaceDenied, "workspace_git_boundary_unverified"):
            self.snapshot()

    def test_git_symlink_cannot_escape_resource_root(self) -> None:
        outside = Path(self.temporary.name) / "external_git"
        outside.mkdir()
        try:
            (self.root / ".git").symlink_to(outside, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("symlink creation needs Windows developer privileges")
        with self.assertRaisesRegex(ProjectWorkspaceDenied, "workspace_git_boundary_unverified"):
            self.snapshot()

    def test_nested_symlink_does_not_leave_selected_root(self) -> None:
        outside = Path(self.temporary.name) / "outside"
        outside.mkdir()
        (outside / "secret").write_text("not accessible", encoding="utf-8")
        try:
            (self.root / "linked").symlink_to(outside, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("symlink creation needs Windows developer privileges")
        (self.root / "visible").write_text("ok", encoding="utf-8")
        self.assertEqual(self.snapshot()["fileCount"], 1)

    def test_scope_mismatch_does_not_reuse_another_project_grant(self) -> None:
        other = replace(self.actor, project_id="project_two")
        with self.assertRaisesRegex(ProjectWorkspaceDenied, "workspace_grant_scope_mismatch"):
            self.port().verify_binding(other, "device_one", "resource_abcdefgh1234")

    def test_revocation_during_scan_blocks_snapshot_result(self) -> None:
        calls = 0

        def resolver(_actor, _device, _resource):
            nonlocal calls
            calls += 1
            return self.grant if calls == 1 else None

        with self.assertRaisesRegex(ProjectWorkspaceDenied, "workspace_grant_missing"):
            self.snapshot(self.port(resolver=resolver))
        self.assertEqual(calls, 2)

    def test_root_identity_and_local_policy_both_required(self) -> None:
        changed = replace(self.grant, root_inode=self.grant.root_inode + 1)
        with self.assertRaisesRegex(ProjectWorkspaceDenied, "workspace_root_changed"):
            self.snapshot(self.port(resolver=lambda *_: changed))
        with self.assertRaisesRegex(ProjectWorkspaceDenied, "workspace_local_policy_denied"):
            self.snapshot(self.port(local_policy=lambda _root: False))

    def test_inherited_git_environment_cannot_redirect_snapshot(self) -> None:
        inherited = {
            "GIT_DIR": "other/.git",
            "GIT_WORK_TREE": "other",
            "GIT_INDEX_FILE": "other/index",
            "GIT_CONFIG_COUNT": "1",
            "GIT_CONFIG_KEY_0": "core.fsmonitor",
            "GIT_CONFIG_VALUE_0": "true",
            "GIT_ALTERNATE_OBJECT_DIRECTORIES": "other/objects",
        }
        with patch.dict(os.environ, inherited):
            with patch("ordax_dev_agent.project_workspace_port.subprocess.run") as run:
                run.return_value = SimpleNamespace(returncode=0, stdout=b"ok")
                self.assertEqual(_git(self.root, "status"), b"ok")
                args, options = run.call_args
                self.assertEqual(args[0][0], "git")
                self.assertIs(options["shell"], False)
                for name in inherited:
                    self.assertNotIn(name, options["env"])
                self.assertEqual(options["env"]["GIT_CONFIG_NOSYSTEM"], "1")
                self.assertEqual(options["env"]["GIT_TERMINAL_PROMPT"], "0")
                self.assertEqual(options["env"]["GIT_OPTIONAL_LOCKS"], "0")

    def test_normal_local_git_repository_remains_supported(self) -> None:
        (self.root / "index.html").write_text("fixture", encoding="utf-8")
        for arguments in [
            ("init", "-q"),
            ("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
             "add", "index.html"),
            ("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
             "commit", "-q", "-m", "fixture"),
        ]:
            subprocess.run(
                ["git", "-C", str(self.root), *arguments],
                check=True, capture_output=True, timeout=10,
            )
        result = self.snapshot()
        self.assertTrue(result["git"]["isRepository"])
        self.assertEqual(len(result["git"]["head"]), 40)
        self.assertEqual(result["fileCount"], 1)
        self.assertEqual(result["changedCount"], 0)


if __name__ == "__main__":
    unittest.main()
