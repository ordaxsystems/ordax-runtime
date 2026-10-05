from __future__ import annotations

import unittest
from typing import Any

from ordax_dev_agent.models import ActionResult
from ordax_dev_agent.product_action_scope import DEVICE_SCOPED_ACTIONS
from ordax_dev_agent.product_gateway import (
    PRODUCT_ACTIONS,
    PRODUCT_READ_ONLY_ACTIONS,
    ProductActionGateway,
    ProductAuditEvent,
    ProductGrant,
    ProductRequestContext,
    product_action_catalog,
)


class FakeExecutor:
    def __init__(self):
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self._names = [
            "projects.list",
            "workspace.repository_catalog",
            "workspace.project_create",
            "workspace.bind_project",
            "project.inventory",
            "project.text_read",
            "handoff.get",
            "handoff.create",
            "project.search_text",
            "project.text_read_batch",
            "project.preview_status",
            "agent.project_health",
            "agent.project_briefing",
            "continuity.get",
            "continuity.update",
            "project.text_write",
            "artifacts.list",
            "git.status",
            "git.diff",
            "git.sync",
            "artifact.preview",
            "artifact.read_chunk",
            "browser.status",
            "browser.list",
            "browser.snapshot",
            "browser.screenshot",
            "browser.start",
            "browser.navigate",
            "browser.click",
            "browser.type",
            "browser.stop",
            "computer.windows",
            "computer.active_window",
            "computer.screenshot",
            "computer.focus_window",
            "computer.click",
            "computer.scroll",
            "computer.type",
            "computer.hotkey",
            "computer.access_status",
            "computer.file_stat",
            "computer.directory_list",
            "computer.text_read",
            "computer.search",
            "computer.processes",
            "computer.terminate_process",
            "computer.text_write",
            "computer.text_patch",
            "computer.directory_create",
            "computer.path_move",
            "computer.path_remove",
            "blender.live_inspect",
            "unity.scene_summary",
        ]

    @property
    def names(self) -> list[str]:
        return list(self._names)

    def execute(self, action: str, payload: dict[str, Any]) -> ActionResult:
        self.calls.append((action, payload))
        if action == "projects.list":
            return ActionResult(
                True,
                "projects",
                {
                    "default_project": "scene",
                    "projects": [
                        {
                            "slug": "scene",
                            "path": "C:/Users/example/secret/project",
                            "apps": ["blender"],
                            "available": True,
                            "allowed_branches": ["main"],
                            "unity": {"allowed_methods": ["x"]},
                            "blender": {"blend_file": "private.blend"},
                        }
                    ],
                },
            )
        if action == "workspace.project_create":
            return ActionResult(True, "created", {
                "slug": "new-app",
                "project_path": "C:/Users/example/secret/new-app",
                "workspace_root": "C:/Users/example/secret",
                "git_initialized": True,
                "active_project": "new-app",
            })
        if action == "workspace.bind_project":
            return ActionResult(True, "bound", {
                "slug": "existing-app",
                "project_path": "C:/Users/example/secret/existing-app",
                "workspace_root": "C:/Users/example/secret",
                "active_project": "existing-app",
                "restart_required": False,
            })
        if action == "workspace.repository_catalog":
            return ActionResult(True, "catalog", {
                "active_project": "scene",
                "projects": [{
                    "slug": "scene", "path": "C:/Users/example/secret/project",
                    "apps": ["blender"], "available": True, "allowed_branches": ["main"],
                    "preview_mode": "blender",
                    "repository": {
                        "is_repository": True, "root": "C:/Users/example/secret/project",
                        "path": "C:/Users/example/secret/project", "branch": "main",
                        "remote": "https://github.com/example/scene.git", "has_origin": True,
                        "dirty": False, "changed_entries": 0, "status_available": True,
                    },
                }],
            })
        if action == "agent.project_health":
            return ActionResult(True, "health", {
                "project": {"slug": "scene", "path": "C:/secret", "apps": ["blender"], "available": True},
                "state": "ready",
                "memory": {"ok": True, "db_path": "C:/secret/state.db", "context_dir": "C:/secret/contexts"},
                "git": {"ok": True, "command": ["git", "-C", "C:/secret"], "dirty": False},
                "adapters": {"blender": {"enabled": True, "state": "ready"}},
            })
        if action == "agent.project_briefing":
            return ActionResult(True, "briefing", {
                "project": {"slug": "scene", "path": "C:/secret/project", "apps": ["blender"], "available": True},
                "repository": {"is_repository": True, "root": "C:/secret/project", "path": "C:/secret/project", "branch": "main", "remote": "https://github.com/example/scene.git"},
                "health": {
                    "project": {"slug": "scene", "path": "C:/secret/project", "apps": ["blender"]},
                    "memory": {"ok": True, "db_path": "C:/secret/state.db", "context_dir": "C:/secret/contexts"},
                    "git": {"ok": True, "command": ["git", "status"], "dirty": False},
                    "adapters": {},
                },
                "preview": {"url": "http://127.0.0.1:5173", "runtime": {"state": "running", "running": True, "pid": 42, "command": ["npm"]}, "latest_image": {"path": "C:/secret/preview.png", "relative_path": "preview.png"}},
                "continuity": {"context_path": "C:/secret/contexts/scene.md", "project_state": {"summary": "Ready", "next_action": "Ship"}},
                "workspace": {
                    "top_level": [{"path": "src", "kind": "directory"}],
                    "context_files": ["README.md"],
                    "source_recall": {
                        "query": "renderer",
                        "match_count": 1,
                        "matches": [{"path": "src/render.py", "line": 12, "text": "renderer = ready"}],
                    },
                },
                "capabilities": {"apps": ["blender"]},
                "attention": [],
            })
        if action == "continuity.get":
            return ActionResult(True, "continuity", {
                "project": "scene",
                "state": {"summary": "Ready", "next_action": "Ship", "git": {"branch": "main"}},
            })
        if action == "continuity.update":
            return ActionResult(True, "updated", {
                "project": "scene",
                "state": {"summary": payload.get("summary"), "next_action": payload.get("next_action", "")},
            })
        if action == "project.preview_status":
            return ActionResult(True, "preview", {
                "project": "scene", "mode": "web", "url": "http://127.0.0.1:5173",
                "runtime": {
                    "state": "running", "running": True, "url_ready": True,
                    "ownership_valid": True, "pid": 123, "token": "secret",
                    "command": ["npm", "run", "dev"], "log": "C:/secret/preview.log",
                },
                "latest_image": {
                    "source": "managed", "path": "C:/secret/preview.png",
                    "relative_path": "preview.png", "size_bytes": 42,
                    "artifact_preview_payload": {"artifact_name": "preview.png"},
                },
            })
        if action == "project.inventory":
            return ActionResult(
                True,
                "inventory",
                {
                    "project": "scene",
                    "project_root": "C:/Users/example/secret/project",
                    "entries": [{"path": "docs/README.md", "kind": "file"}],
                },
            )
        if action in {"git.status", "git.diff"}:
            return ActionResult(
                True,
                "git",
                {
                    "stdout": "",
                    "stderr": "",
                    "returncode": 0,
                    "command": ["git", "-C", "C:/Users/example/secret/project", "status"],
                },
            )
        if action == "artifacts.list":
            return ActionResult(
                True,
                "artifacts",
                {
                    "project": "scene",
                    "items": [
                        {
                            "source": "managed",
                            "name": "preview.png",
                            "relative_path": "preview.png",
                            "size_bytes": 42,
                        }
                    ],
                    "truncated": False,
                    "max_items": 100,
                },
            )
        if action == "browser.screenshot":
            return ActionResult(True, "browser screenshot", {
                "session_id": "11111111-1111-4111-8111-111111111111",
                "artifact_name": "browser.png",
                "image_path": "C:/Users/example/AppData/Local/OrdaX/artifacts/scene/browser.png",
                "width": 1440,
                "height": 900,
                "size_bytes": 42,
            })
        if action == "computer.screenshot":
            return ActionResult(True, "desktop screenshot", {
                "project": "scene",
                "mode": "desktop",
                "artifact_name": "computer.png",
                "image_path": "C:/Users/example/AppData/Local/OrdaX/artifacts/scene/computer.png",
                "width": 1920,
                "height": 1080,
                "size_bytes": 84,
            })
        if action == "computer.processes":
            return ActionResult(True, "system processes", {
                "processes": [{
                    "pid": 4242,
                    "parent_pid": 1,
                    "name": "python.exe",
                    "executable": "C:/Python/python.exe",
                    "command_line": "python app.py --token TOP-SECRET",
                }],
                "total_matches": 1,
                "truncated": False,
            })
        if action == "artifact.preview":
            return ActionResult(
                True,
                "preview",
                {
                    "artifact_name": "preview.png",
                    "path": "C:/Users/example/AppData/Local/OrdaX/artifacts/scene/preview.png",
                    "mime_type": "image/png",
                    "base64": "AA==",
                },
            )
        return ActionResult(True, "ok", {"payload": payload})


class AuditSink:
    def __init__(self, *, fail: bool = False):
        self.fail = fail
        self.events: list[ProductAuditEvent] = []

    def record(self, event: ProductAuditEvent) -> None:
        if self.fail:
            raise RuntimeError("audit unavailable")
        self.events.append(event)


class ProductGatewayTests(unittest.TestCase):
    def setUp(self) -> None:
        self.executor = FakeExecutor()
        self.audit = AuditSink()
        self.gateway = ProductActionGateway(
            self.executor,
            self.audit,
            clock=lambda: 2_000_000_000,
        )
        self.context = ProductRequestContext(
            request_id="req-1",
            subject_id="user:123",
            device_id="device-1",
            space_id="space-1",
        )

    def grant(
        self,
        *actions: str,
        projects: tuple[str, ...] = ("scene",),
        subject_id: str = "user:123",
        device_id: str | None = "device-1",
        space_id: str | None = "space-1",
        expires_at_unix: int | None = 2_000_000_100,
    ) -> ProductGrant:
        return ProductGrant(
            grant_id="grant-1",
            subject_id=subject_id,
            actions=frozenset(actions),
            projects=frozenset(projects),
            device_id=device_id,
            space_id=space_id,
            expires_at_unix=expires_at_unix,
        )

    def test_catalog_contains_explicit_capability_surface(self) -> None:
        names = {entry["name"] for entry in product_action_catalog()}
        self.assertEqual(names, set(PRODUCT_ACTIONS))
        self.assertIn("workspace.repository_catalog", names)
        self.assertIn("workspace.project_create", names)
        self.assertIn("workspace.bind_project", names)
        self.assertIn("project.text_read", names)
        self.assertIn("handoff.get", names)
        self.assertIn("handoff.create", names)
        self.assertIn("workspace.file_stat", names)
        self.assertIn("workspace.directory_list", names)
        self.assertIn("workspace.text_read", names)
        self.assertIn("workspace.text_write", names)
        self.assertIn("workspace.path_remove", names)
        self.assertIn("git.command", names)
        self.assertIn("terminal.exec", names)
        self.assertIn("browser.screenshot", names)
        self.assertIn("browser.start", names)
        self.assertIn("computer.screenshot", names)
        self.assertIn("computer.click", names)
        self.assertIn("computer.access_status", names)
        self.assertIn("computer.text_read", names)
        self.assertIn("computer.text_write", names)
        self.assertIn("computer.path_remove", names)
        self.assertIn("project.search_text", names)
        self.assertIn("project.text_read_batch", names)
        self.assertIn("project.preview_status", names)
        self.assertIn("agent.project_health", names)
        self.assertIn("agent.project_briefing", names)
        self.assertIn("continuity.get", names)
        self.assertIn("continuity.update", names)
        self.assertIn("git.diff", names)
        self.assertIn("artifacts.list", names)
        self.assertIn("project.text_write", names)
        self.assertIn("project.text_patch", names)
        self.assertIn("blender.live_status", names)
        self.assertIn("blender.live_object_transform", names)
        self.assertIn("blender.live_save", names)
        self.assertNotIn("git.sync", names)
        self.assertNotIn("artifact.read_chunk", names)
        self.assertFalse(any(name.startswith("unity.") for name in names))

    def test_all_device_scoped_actions_are_projectless_in_gateway_contract(self) -> None:
        self.assertTrue(DEVICE_SCOPED_ACTIONS)
        self.assertTrue(DEVICE_SCOPED_ACTIONS <= set(PRODUCT_ACTIONS))
        for action in sorted(DEVICE_SCOPED_ACTIONS):
            spec = PRODUCT_ACTIONS[action]
            with self.subTest(action=action):
                self.assertFalse(spec.project_required)
                self.assertNotIn("project", spec.allowed_fields)

    def test_project_create_is_global_explicitly_granted_and_redacts_local_paths(self) -> None:
        result = self.gateway.execute(
            "workspace.project_create",
            {"slug": "new-app", "apps": [], "git_init": True},
            context=self.context,
            grant=self.grant("workspace.project_create", projects=()),
        )

        self.assertTrue(result.ok)
        self.assertEqual(result.data["slug"], "new-app")
        self.assertNotIn("project_path", result.data)
        self.assertNotIn("workspace_root", result.data)
        self.assertEqual(
            self.executor.calls,
            [("workspace.project_create", {"slug": "new-app", "apps": [], "git_init": True})],
        )

        denied = self.gateway.execute(
            "workspace.project_create",
            {"slug": "new-app-2"},
            context=self.context,
            grant=self.grant("projects.list", projects=()),
        )
        self.assertFalse(denied.ok)
        self.assertEqual(denied.data["error_code"], "grant_required")

    def test_project_import_is_global_explicitly_granted_and_redacts_local_paths(self) -> None:
        result = self.gateway.execute(
            "workspace.bind_project",
            {"slug": "existing-app", "relative_path": "existing-app", "apps": []},
            context=self.context,
            grant=self.grant("workspace.bind_project", projects=()),
        )

        self.assertTrue(result.ok)
        self.assertEqual(result.data["slug"], "existing-app")
        self.assertNotIn("project_path", result.data)
        self.assertNotIn("workspace_root", result.data)
        self.assertEqual(
            self.executor.calls,
            [("workspace.bind_project", {"slug": "existing-app", "relative_path": "existing-app", "apps": []})],
        )

    def test_browser_is_project_scoped_while_computer_control_is_device_scoped(self) -> None:
        browser = self.gateway.execute(
            "browser.screenshot",
            {
                "project": "scene",
                "session_id": "11111111-1111-4111-8111-111111111111",
                "width": 1440,
                "height": 900,
            },
            context=self.context,
            grant=self.grant("browser.screenshot"),
        )
        self.assertTrue(browser.ok)
        self.assertNotIn("image_path", browser.data)
        self.assertEqual(browser.data["artifact_name"], "browser.png")

        desktop = self.gateway.execute(
            "computer.screenshot",
            {"mode": "desktop"},
            context=self.context,
            grant=self.grant("computer.screenshot", projects=()),
        )
        self.assertTrue(desktop.ok)
        self.assertNotIn("image_path", desktop.data)
        self.assertEqual(desktop.data["artifact_name"], "computer.png")

        denied = self.gateway.execute(
            "computer.click",
            {"x": 10, "y": 20},
            context=self.context,
            grant=self.grant("computer.screenshot", projects=()),
        )
        self.assertFalse(denied.ok)
        self.assertEqual(denied.data["error_code"], "grant_required")

    def test_computer_process_list_redacts_command_line_and_requires_device_grant(self) -> None:
        result = self.gateway.execute(
            "computer.processes",
            {"query": "python", "max_items": 20},
            context=self.context,
            grant=self.grant("computer.processes", projects=()),
        )
        self.assertTrue(result.ok)
        self.assertEqual("python.exe", result.data["processes"][0]["name"])
        self.assertNotIn("command_line", result.data["processes"][0])

        denied = self.gateway.execute(
            "computer.terminate_process",
            {"pid": 4242, "expected_name": "python.exe"},
            context=self.context,
            grant=self.grant("computer.processes", projects=()),
        )
        self.assertFalse(denied.ok)
        self.assertEqual("grant_required", denied.data["error_code"])

    def test_computer_filesystem_actions_are_device_scoped_and_grant_typed(self) -> None:
        read = self.gateway.execute(
            "computer.text_read",
            {"path": "C:/Users/example/notes.txt"},
            context=self.context,
            grant=self.grant("computer.text_read", projects=()),
        )
        self.assertTrue(read.ok)

        denied = self.gateway.execute(
            "computer.text_write",
            {"path": "C:/Users/example/notes.txt", "content": "x"},
            context=self.context,
            grant=self.grant("computer.text_read", projects=()),
        )
        self.assertFalse(denied.ok)
        self.assertEqual(denied.data["error_code"], "grant_required")

    def test_handoff_actions_are_project_scoped_and_capability_typed(self) -> None:
        loaded = self.gateway.execute(
            "handoff.get",
            {"project": "scene", "handoff_id": "hof_0123456789abcdef0123456789abcdef"},
            context=self.context,
            grant=self.grant("handoff.get"),
        )
        self.assertTrue(loaded.ok)

        created = self.gateway.execute(
            "handoff.create",
            {
                "project": "scene",
                "summary": "Continue from here",
                "next_action": "Run tests",
                "completed": ["foundation"],
                "blockers": [],
                "changed_paths": ["src/app.py"],
                "ttl_hours": 24,
            },
            context=self.context,
            grant=self.grant("handoff.create"),
        )
        self.assertTrue(created.ok)

        denied = self.gateway.execute(
            "handoff.get",
            {"project": "scene", "handoff_id": "hof_0123456789abcdef0123456789abcdef"},
            context=self.context,
            grant=self.grant("projects.list"),
        )
        self.assertFalse(denied.ok)
        self.assertEqual(denied.data["error_code"], "grant_required")

    def test_action_requires_explicit_action_grant_and_denial_is_audited(self) -> None:
        result = self.gateway.execute(
            "project.text_read",
            {"project": "scene", "path": "docs/README.md"},
            context=self.context,
            grant=self.grant(),
        )

        self.assertFalse(result.ok)
        self.assertEqual(result.data["error_code"], "grant_required")
        self.assertEqual(self.executor.calls, [])
        self.assertEqual(len(self.audit.events), 1)
        self.assertEqual(self.audit.events[0].decision, "deny")
        self.assertEqual(self.audit.events[0].reason, "grant_required")

    def test_project_scoped_action_requires_project_grant(self) -> None:
        result = self.gateway.execute(
            "git.status",
            {"project": "scene"},
            context=self.context,
            grant=self.grant("git.status", projects=()),
        )

        self.assertFalse(result.ok)
        self.assertEqual(result.data["error_code"], "project_grant_required")
        self.assertEqual(self.executor.calls, [])

    def test_subject_device_space_and_expiry_are_bound_to_verified_context(self) -> None:
        cases = [
            (self.grant("git.status", subject_id="user:999"), "grant_subject_mismatch"),
            (self.grant("git.status", device_id="device-2"), "grant_device_mismatch"),
            (self.grant("git.status", space_id="space-2"), "grant_space_mismatch"),
            (self.grant("git.status", expires_at_unix=2_000_000_000), "grant_expired"),
        ]
        for grant, error_code in cases:
            with self.subTest(error_code=error_code):
                result = self.gateway.execute(
                    "git.status",
                    {"project": "scene"},
                    context=self.context,
                    grant=grant,
                )
                self.assertFalse(result.ok)
                self.assertEqual(result.data["error_code"], error_code)
        self.assertEqual(self.executor.calls, [])

    def test_invalid_request_or_grant_provenance_fails_closed(self) -> None:
        bad_context = ProductRequestContext(
            request_id="bad request with spaces",
            subject_id="user:123",
            device_id="device-1",
            space_id="space-1",
        )
        result = self.gateway.execute(
            "git.status",
            {"project": "scene"},
            context=bad_context,
            grant=self.grant("git.status"),
        )
        self.assertFalse(result.ok)
        self.assertEqual(result.data["error_code"], "invalid_request_context")

        malformed_grants = [
            ProductGrant(
                grant_id="",
                subject_id="user:123",
                actions=frozenset({"git.status"}),
                projects=frozenset({"scene"}),
            ),
            ProductGrant(
                grant_id="grant-1",
                subject_id="user:123",
                actions=["git.status"],  # type: ignore[arg-type]
                projects=frozenset({"scene"}),
            ),
            ProductGrant(
                grant_id="grant-1",
                subject_id="user:123",
                actions=frozenset({"git.status"}),
                projects=frozenset({"scene"}),
                expires_at_unix="tomorrow",  # type: ignore[arg-type]
            ),
        ]
        for bad_grant in malformed_grants:
            result = self.gateway.execute(
                "git.status",
                {"project": "scene"},
                context=self.context,
                grant=bad_grant,
            )
            self.assertFalse(result.ok)
            self.assertEqual(result.data["error_code"], "invalid_grant_provenance")
        self.assertEqual(self.executor.calls, [])

    def test_unsupported_payload_field_is_rejected_before_execution(self) -> None:
        result = self.gateway.execute(
            "git.status",
            {"project": "scene", "command": "whoami"},
            context=self.context,
            grant=self.grant("git.status"),
        )

        self.assertFalse(result.ok)
        self.assertEqual(result.data["error_code"], "invalid_product_action_payload")
        self.assertEqual(self.executor.calls, [])

    def test_mutating_or_bulk_actions_are_not_exposed_even_if_granted(self) -> None:
        actions = (
            "git.sync",
            "artifact.read_chunk",
            "blender.live_inspect",
            "blender.run_python",
        )
        grant = self.grant(*actions)
        for action in actions:
            result = self.gateway.execute(
                action,
                {"project": "scene"},
                context=self.context,
                grant=grant,
            )
            self.assertFalse(result.ok)
            self.assertEqual(result.data["error_code"], "action_not_exposed")
        self.assertEqual(self.executor.calls, [])

    def test_audit_must_persist_authorization_before_local_execution(self) -> None:
        audit = AuditSink(fail=True)
        gateway = ProductActionGateway(
            self.executor,
            audit,
            clock=lambda: 2_000_000_000,
        )

        result = gateway.execute(
            "git.status",
            {"project": "scene"},
            context=self.context,
            grant=self.grant("git.status"),
        )

        self.assertFalse(result.ok)
        self.assertEqual(result.data["error_code"], "audit_unavailable")
        self.assertEqual(self.executor.calls, [])

    def test_success_records_authorization_and_result_without_payload_contents(self) -> None:
        payload = {"project": "scene", "path": "docs/README.md"}
        result = self.gateway.execute(
            "project.text_read",
            payload,
            context=self.context,
            grant=self.grant("project.text_read"),
        )

        self.assertTrue(result.ok)
        self.assertEqual(self.executor.calls, [("project.text_read", payload)])
        self.assertEqual(len(self.audit.events), 2)
        before, after = self.audit.events
        self.assertEqual((before.phase, before.decision), ("decision", "allow"))
        self.assertEqual((after.phase, after.result_ok), ("result", True))
        self.assertEqual(before.payload_fields, ("path", "project"))
        self.assertNotIn("README", repr(before))
        self.assertEqual(before.grant_id, "grant-1")
        self.assertEqual(before.subject_id, "user:123")

    def test_global_project_catalog_redacts_and_filters_to_grant_projects(self) -> None:
        result = self.gateway.execute(
            "projects.list",
            {},
            context=self.context,
            grant=self.grant("projects.list", projects=("scene",)),
        )

        self.assertTrue(result.ok)
        self.assertEqual([item["slug"] for item in result.data["projects"]], ["scene"])
        project = result.data["projects"][0]
        self.assertNotIn("path", project)
        self.assertNotIn("unity", project)
        self.assertNotIn("blender", project)

        empty = self.gateway.execute(
            "projects.list",
            {},
            context=self.context,
            grant=self.grant("projects.list", projects=()),
        )
        self.assertTrue(empty.ok)
        self.assertEqual(empty.data["projects"], [])
        self.assertNotIn("default_project", empty.data)

    def test_repository_catalog_redacts_and_filters_to_grant_projects(self) -> None:
        result = self.gateway.execute(
            "workspace.repository_catalog",
            {},
            context=self.context,
            grant=self.grant("workspace.repository_catalog", projects=("scene",)),
        )

        self.assertTrue(result.ok)
        self.assertEqual([item["slug"] for item in result.data["projects"]], ["scene"])
        project = result.data["projects"][0]
        self.assertEqual(project["preview_mode"], "blender")
        self.assertNotIn("path", project)
        self.assertNotIn("root", project["repository"])
        self.assertNotIn("path", project["repository"])
        self.assertEqual(project["repository"]["branch"], "main")

        empty = self.gateway.execute(
            "workspace.repository_catalog",
            {},
            context=self.context,
            grant=self.grant("workspace.repository_catalog", projects=()),
        )
        self.assertTrue(empty.ok)
        self.assertEqual(empty.data["projects"], [])
        self.assertNotIn("active_project", empty.data)

    def test_project_briefing_exposes_durable_state_without_local_paths(self) -> None:
        result = self.gateway.execute(
            "agent.project_briefing",
            {"project": "scene", "query": "renderer", "recall_limit": 12},
            context=self.context,
            grant=self.grant("agent.project_briefing"),
        )
        self.assertTrue(result.ok)
        self.assertEqual(
            self.executor.calls[-1],
            ("agent.project_briefing", {"project": "scene", "query": "renderer", "recall_limit": 12}),
        )
        self.assertEqual(result.data["continuity"]["project_state"]["summary"], "Ready")
        self.assertNotIn("path", result.data["project"])
        self.assertNotIn("root", result.data["repository"])
        self.assertNotIn("path", result.data["repository"])
        self.assertNotIn("context_path", result.data["continuity"])
        self.assertNotIn("url", result.data["preview"])
        self.assertNotIn("pid", result.data["preview"]["runtime"])
        self.assertNotIn("path", result.data["preview"]["latest_image"])
        self.assertEqual(result.data["workspace"]["top_level"][0]["path"], "src")
        self.assertEqual(result.data["workspace"]["source_recall"]["matches"][0]["path"], "src/render.py")
        self.assertEqual(result.data["workspace"]["source_recall"]["matches"][0]["line"], 12)

    def test_continuity_actions_require_project_grant(self) -> None:
        read = self.gateway.execute(
            "continuity.get",
            {"project": "scene"},
            context=self.context,
            grant=self.grant("continuity.get"),
        )
        self.assertTrue(read.ok)
        self.assertEqual(read.data["state"]["summary"], "Ready")

        write = self.gateway.execute(
            "continuity.update",
            {"project": "scene", "summary": "Ready", "next_action": "Ship"},
            context=self.context,
            grant=self.grant("continuity.update"),
        )
        self.assertTrue(write.ok)

        denied = self.gateway.execute(
            "continuity.get",
            {"project": "scene"},
            context=self.context,
            grant=self.grant("continuity.get", projects=()),
        )
        self.assertFalse(denied.ok)
        self.assertEqual(denied.data["error_code"], "project_grant_required")

    def test_project_health_and_preview_redact_local_runtime_details(self) -> None:
        health = self.gateway.execute(
            "agent.project_health", {"project": "scene"},
            context=self.context, grant=self.grant("agent.project_health"),
        )
        self.assertTrue(health.ok)
        self.assertNotIn("path", health.data["project"])
        self.assertNotIn("db_path", health.data["memory"])
        self.assertNotIn("context_dir", health.data["memory"])
        self.assertNotIn("command", health.data["git"])

        preview = self.gateway.execute(
            "project.preview_status", {"project": "scene"},
            context=self.context, grant=self.grant("project.preview_status"),
        )
        self.assertTrue(preview.ok)
        self.assertNotIn("url", preview.data)
        self.assertNotIn("pid", preview.data["runtime"])
        self.assertNotIn("token", preview.data["runtime"])
        self.assertNotIn("command", preview.data["runtime"])
        self.assertNotIn("log", preview.data["runtime"])
        self.assertNotIn("path", preview.data["latest_image"])
        self.assertEqual(preview.data["latest_image"]["relative_path"], "preview.png")

    def test_inventory_redacts_absolute_project_root(self) -> None:
        result = self.gateway.execute(
            "project.inventory",
            {"project": "scene"},
            context=self.context,
            grant=self.grant("project.inventory"),
        )

        self.assertTrue(result.ok)
        self.assertNotIn("project_root", result.data)
        self.assertEqual(result.data["entries"][0]["path"], "docs/README.md")

    def test_git_results_redact_absolute_command(self) -> None:
        result = self.gateway.execute(
            "git.status",
            {"project": "scene"},
            context=self.context,
            grant=self.grant("git.status"),
        )

        self.assertTrue(result.ok)
        self.assertNotIn("command", result.data)

    def test_artifacts_list_is_project_scoped_read_only_metadata(self) -> None:
        result = self.gateway.execute(
            "artifacts.list",
            {"project": "scene", "max_items": 25},
            context=self.context,
            grant=self.grant("artifacts.list"),
        )

        self.assertTrue(result.ok)
        self.assertEqual(result.data["items"][0]["relative_path"], "preview.png")
        self.assertNotIn("path", result.data["items"][0])
        self.assertEqual(
            self.executor.calls[-1],
            ("artifacts.list", {"project": "scene", "max_items": 25}),
        )

    def test_artifact_preview_redacts_local_absolute_path(self) -> None:
        result = self.gateway.execute(
            "artifact.preview",
            {"project": "scene", "artifact_name": "preview.png"},
            context=self.context,
            grant=self.grant("artifact.preview"),
        )

        self.assertTrue(result.ok)
        self.assertEqual(result.data["artifact_name"], "preview.png")
        self.assertNotIn("path", result.data)


if __name__ == "__main__":
    unittest.main()
