from __future__ import annotations

import hashlib
import json
import os
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from ordax_dev_agent.actions import ActionRegistry
from ordax_dev_agent.config import AgentConfig
from ordax_dev_agent.execution_lock import ExecutionLock
from ordax_dev_agent.computer_filesystem_actions import load_computer_access_policy, _readable_installed_product_manifest


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

    def test_patch_preserves_crlf_when_fragment_uses_lf(self):
        target = self.allowed / "windows.py"
        before = b"def hello():\r\n    return 'old'\r\n"
        target.write_bytes(before)
        result = self.registry.execute("computer.text_patch", {
            "path": str(target),
            "expected_sha256": hashlib.sha256(before).hexdigest(),
            "replacements": [{"old": "def hello():\n    return 'old'",
                              "new": "def hello():\n    return 'new'"}],
        })
        self.assertTrue(result.ok, result.summary)
        expected = b"def hello():\r\n    return 'new'\r\n"
        self.assertEqual(target.read_bytes(), expected)
        self.assertEqual(result.data["sha256"], hashlib.sha256(expected).hexdigest())

    def test_patch_preserves_utf8_bom_and_crlf(self):
        target = self.allowed / "bom.txt"
        before = b"\xef\xbb\xbf" + "Olá\r\nMundo\r\n".encode("utf-8")
        target.write_bytes(before)
        result = self.registry.execute("computer.text_patch", {
            "path": str(target),
            "expected_sha256": hashlib.sha256(before).hexdigest(),
            "replacements": [{"old": "Olá\nMundo", "new": "Olá\nOrdaX"}],
        })
        self.assertTrue(result.ok, result.summary)
        expected = b"\xef\xbb\xbf" + "Olá\r\nOrdaX\r\n".encode("utf-8")
        self.assertEqual(target.read_bytes(), expected)
        self.assertEqual(result.data["size_bytes"], len(expected))
        self.assertEqual(result.data["sha256"], hashlib.sha256(expected).hexdigest())

    def test_patch_accepts_crlf_fragment_against_lf_file(self):
        target = self.allowed / "posix.txt"
        before = b"alpha\nbeta\n"
        target.write_bytes(before)
        result = self.registry.execute("computer.text_patch", {
            "path": str(target),
            "expected_sha256": hashlib.sha256(before).hexdigest(),
            "replacements": [{"old": "alpha\r\nbeta", "new": "alpha\r\ngamma"}],
        })
        self.assertTrue(result.ok, result.summary)
        self.assertEqual(target.read_bytes(), b"alpha\ngamma\n")

    def test_patch_mixed_newlines_remain_exact(self):
        target = self.allowed / "mixed.txt"
        before = b"alpha\r\nbeta\ngamma\r\n"
        target.write_bytes(before)
        rejected = self.registry.execute("computer.text_patch", {
            "path": str(target),
            "expected_sha256": hashlib.sha256(before).hexdigest(),
            "replacements": [{"old": "alpha\nbeta", "new": "changed"}],
        })
        self.assertFalse(rejected.ok)
        self.assertEqual(target.read_bytes(), before)
        accepted = self.registry.execute("computer.text_patch", {
            "path": str(target),
            "expected_sha256": hashlib.sha256(before).hexdigest(),
            "replacements": [{"old": "alpha\r\nbeta\n", "new": "x\r\ny\n"}],
        })
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertEqual(target.read_bytes(), b"x\r\ny\ngamma\r\n")

    def test_patch_sha_precondition_rejects_stale_files(self):
        target = self.allowed / "conflict.txt"
        target.write_bytes(b"current\r\n")
        result = self.registry.execute("computer.text_patch", {
            "path": str(target),
            "expected_sha256": hashlib.sha256(b"stale\r\n").hexdigest(),
            "replacements": [{"old": "current", "new": "bad"}],
        })
        self.assertFalse(result.ok)
        self.assertEqual(target.read_bytes(), b"current\r\n")

    def test_owner_full_access_can_read_only_canonical_installed_product_manifest(self):
        local = self.root / "Local"
        install = local / "Programs" / "ORDAX"
        install.mkdir(parents=True)
        product = install / "product-manifest.json"
        product.write_text('{"schema":"ordax.windows-product/1","version":"0.15.9"}\n', encoding="utf-8")
        secret = install / "runtime-secret.txt"
        secret.write_text("private-runtime-state", encoding="utf-8")
        (self.state / "agent-settings.json").write_text(json.dumps({
            "computer_access": {"enabled": True, "full_access": True,
                                "full_filesystem": True, "allowed_roots": []}
        }), encoding="utf-8")
        registry = ActionRegistry(replace(self.registry.config, agent_repo_path=install))
        fixture = {"version": "0.15.9", "installLocation": str(install)}
        with patch.dict(os.environ, {"LOCALAPPDATA": str(local)}), patch(
            "ordax_dev_agent.computer_filesystem_actions.installed_windows_product",
            return_value=fixture,
        ):
            policy = load_computer_access_policy(registry.config)
            self.assertTrue(policy.full_access and policy.full_filesystem, policy.public())
            self.assertTrue(
                _readable_installed_product_manifest(product, product.resolve(), policy),
                f"candidate={str(product.absolute())!r}, resolved={str(product.resolve())!r}, "
                f"canonical={str((local / 'Programs' / 'ORDAX').resolve())!r}, "
                f"registered={str(install.resolve())!r}, file={product.is_file()}",
            )
            read = registry.execute("computer.text_read", {"path": str(product)})
            self.assertTrue(read.ok, read.summary)
            self.assertIn('"version":"0.15.9"', read.data["content"])
            stat = registry.execute("computer.file_stat", {"path": str(product)})
            self.assertTrue(stat.ok, stat.summary)
            self.assertEqual(stat.data["kind"], "file")

            # Full Access cannot read arbitrary Runtime state or modify the manifest.
            for operation, payload in (
                ("computer.text_read", {"path": str(secret)}),
                ("computer.file_stat", {"path": str(secret)}),
                ("computer.directory_list", {"path": str(install)}),
                ("computer.text_write", {"path": str(product), "content": "tampered"}),
                ("computer.text_patch", {"path": str(product), "expected_sha256": "a" * 64,
                                         "replacements": []}),
                ("computer.path_move", {"source": str(product),
                                        "destination": str(self.allowed / "stolen.json")}),
                ("computer.path_remove", {"path": str(product)}),
            ):
                with self.subTest(operation=operation):
                    denied = registry.execute(operation, payload)
                    self.assertFalse(denied.ok)
                    self.assertIn("protected", denied.summary)
            self.assertEqual(json.loads(product.read_text(encoding="utf-8"))["version"], "0.15.9")
            denied = registry.execute("computer.text_read", {"path": str(install / "other.json")})
            self.assertFalse(denied.ok)
            self.assertIn("protected", denied.summary)
            with patch(
                "ordax_dev_agent.computer_filesystem_actions.installed_windows_product",
                return_value={"version": "0.15.9", "installLocation": str(self.outside)},
            ):
                denied = registry.execute("computer.text_read", {"path": str(product)})
                self.assertFalse(denied.ok)
                self.assertIn("protected", denied.summary)

            # A path alias is not the manifest at the expected canonical spelling.
            alias = self.allowed / "manifest-alias.json"
            try:
                alias.symlink_to(product)
            except (OSError, NotImplementedError):
                pass
            else:
                denied = registry.execute("computer.text_read", {"path": str(alias)})
                self.assertFalse(denied.ok)
            # Removing the opt-in Full Access status closes the special read.
            (self.state / "agent-settings.json").write_text(json.dumps({
                "computer_access": {"enabled": True, "full_access": False,
                                    "full_filesystem": False, "allowed_roots": [str(self.allowed)]}
            }), encoding="utf-8")
            denied = registry.execute("computer.text_read", {"path": str(product)})
            self.assertFalse(denied.ok)
            self.assertIn("protected", denied.summary)

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
