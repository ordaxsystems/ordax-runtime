from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ordax_dev_agent.actions import ActionRegistry
from ordax_dev_agent.computer_filesystem_actions import (
    _credential_path,
    _path_allowed,
    load_computer_access_policy,
)
from ordax_dev_agent.config import AgentConfig


class ComputerCredentialBoundaryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name) / "owner"
        self.home.mkdir()
        self.public = self.home / "Documents" / "project"
        self.public.mkdir(parents=True)
        self.state = self.home / "runtime-state"
        self.state.mkdir()
        (self.state / "agent-settings.json").write_text(
            json.dumps({"computer_access": {
                "enabled": True, "full_access": True,
                "full_filesystem": True, "allowed_roots": [],
                "allowed_applications": [],
            }}), encoding="utf-8"
        )
        self.config = AgentConfig(
            agent_name="credential-test", poll_seconds=0.25,
            state_dir=self.state, agent_repo_path=self.home / "agent",
            hordax_path=self.home / "hordax", bridge_path=self.home / "bridge",
            workspace_root=self.public, projects={}, default_project="missing",
        )
        home_patcher = patch.object(Path, "home", return_value=self.home)
        home_patcher.start()
        self.addCleanup(home_patcher.stop)
        self.registry = ActionRegistry(self.config)
        self.policy = load_computer_access_policy(self.config)

    def test_full_access_can_read_ordinary_files_but_not_credential_roots(self):
        normal = self.public / "brief.txt"
        normal.write_text("approved user material", encoding="utf-8")
        self.assertTrue(_path_allowed(normal.resolve(), self.policy))
        read = self.registry.execute("computer.text_read", {"path": str(normal)})
        self.assertTrue(read.ok, read.summary)

        targets = (
            ".ssh/id_ed25519",
            ".aws/credentials",
            ".gnupg/private-keys-v1.d/key",
            ".config/gcloud/application_default_credentials.json",
            "AppData/Roaming/Microsoft/Credentials/private",
            "AppData/Roaming/Microsoft/Protect/private",
            "AppData/Local/Google/Chrome/User Data/Default/Login Data",
            "AppData/Local/Microsoft/Edge/User Data/Default/Login Data",
            "AppData/Roaming/Mozilla/Firefox/Profiles/example/key4.db",
        )
        for relative in targets:
            with self.subTest(path=relative):
                secret = self.home / relative
                secret.parent.mkdir(parents=True, exist_ok=True)
                secret.write_text("do-not-expose", encoding="utf-8")
                self.assertFalse(_path_allowed(secret.resolve(), self.policy))
                for action, payload in (
                    ("computer.text_read", {"path": str(secret)}),
                    ("computer.file_stat", {"path": str(secret)}),
                    ("computer.text_write", {"path": str(secret), "content": "malicious"}),
                    ("computer.path_remove", {"path": str(secret)}),
                ):
                    response = self.registry.execute(action, payload)
                    self.assertFalse(response.ok, (action, relative, response))
                    self.assertNotIn("do-not-expose", str(response.data))

    def test_secret_file_names_blocked_even_outside_known_roots(self):
        for basename in (".env", ".env.production", ".npmrc", ".pypirc",
                         "id_ed25519", "credentials.json", "private.pem", "server.p12"):
            with self.subTest(basename=basename):
                file = self.public / basename
                file.write_text("secret", encoding="utf-8")
                self.assertFalse(_path_allowed(file.resolve(), self.policy))
                self.assertFalse(self.registry.execute(
                    "computer.text_read", {"path": str(file)}
                ).ok)
        allowed_example = self.public / ".env.example"
        allowed_example.write_text("placeholder", encoding="utf-8")
        self.assertTrue(self.registry.execute(
            "computer.text_read", {"path": str(allowed_example)}
        ).ok)

    def test_search_cannot_expose_credential_content_or_follow_symlinks(self):
        hidden = self.home / ".ssh"
        hidden.mkdir()
        (hidden / "id_ed25519").write_text("unique-secret-marker", encoding="utf-8")
        link = self.public / "shortcut"
        try:
            link.symlink_to(hidden, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("symlink creation not supported")

        denied = self.registry.execute("computer.text_read",
                                       {"path": str(link / "id_ed25519")})
        self.assertFalse(denied.ok)
        listed = self.registry.execute("computer.directory_list",
                                       {"path": str(self.public), "include_hidden": True})
        self.assertTrue(listed.ok, listed.summary)
        self.assertFalse(next(item for item in listed.data["entries"]
                              if item["name"] == "shortcut")["allowed"])
        searched = self.registry.execute("computer.search", {
            "root": str(self.home), "query": "unique-secret-marker",
            "mode": "content", "include_hidden": True, "max_depth": 5
        })
        self.assertTrue(searched.ok, searched.summary)
        self.assertEqual(searched.data["results"], [])

    def test_destructive_mutation_cannot_delete_parent_of_credentials(self):
        folder = self.home / ".ssh"
        folder.mkdir()
        denied = self.registry.execute("computer.path_remove",
                                       {"path": str(self.home), "recursive": True})
        self.assertFalse(denied.ok)
        self.assertTrue(folder.exists())
        self.assertFalse(_credential_path((self.public / "normal.txt").resolve(),
                                         self.policy.credential_roots))


if __name__ == "__main__":
    unittest.main()
