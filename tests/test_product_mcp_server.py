from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from ordax_dev_agent import product_mcp_server as server


class FakeClient:
    instances = []

    def __init__(self, base_url):
        self.base_url = base_url
        self.calls = []
        self.__class__.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def session(self, token):
        self.calls.append(("session", token))
        return {"subject_id": "user:1"}

    def targets(self, token, *, space_id=None):
        self.calls.append(("targets", token, space_id))
        return [{"device_id": "dev-1", "grants": []}]

    def submit_action(self, token, **kwargs):
        self.calls.append(("submit", token, kwargs))
        return "req-1"

    def wait_action(self, token, request_id):
        self.calls.append(("wait", token, request_id))
        return {
            "request_id": request_id,
            "status": "succeeded",
            "result": {"ok": True, "data": {"branch": "main"}},
            "error_code": None,
        }


class ProductMcpServerTests(unittest.TestCase):
    def setUp(self):
        FakeClient.instances.clear()
        self.env = patch.dict(
            os.environ,
            {
                "ORDAX_PRODUCT_ACCESS_TOKEN": "jwt-secret",
                "ORDAX_PRODUCT_CONTROL_PLANE_URL": "https://control.example.test",
            },
            clear=False,
        )
        self.client = patch.object(server, "ProductRemoteClient", FakeClient)
        self.env.start()
        self.client.start()

    def tearDown(self):
        self.client.stop()
        self.env.stop()

    def test_session_and_targets_use_environment_token(self):
        self.assertEqual(server.product_session()["subject_id"], "user:1")
        targets = server.product_targets("space-1")
        self.assertEqual(targets[0]["device_id"], "dev-1")
        calls = [call for instance in FakeClient.instances for call in instance.calls]
        self.assertIn(("session", "jwt-secret"), calls)
        self.assertIn(("targets", "jwt-secret", "space-1"), calls)

    def test_explicit_read_only_tool_routes_via_remote_client(self):
        result = server.git_status("dev-1", "demo", "space-1")
        self.assertEqual(result["status"], "succeeded")
        calls = [call for instance in FakeClient.instances for call in instance.calls]
        submit = next(call for call in calls if call[0] == "submit")
        self.assertEqual(submit[1], "jwt-secret")
        self.assertEqual(submit[2]["device_id"], "dev-1")
        self.assertEqual(submit[2]["action"], "git.status")
        self.assertEqual(submit[2]["project"], "demo")
        self.assertEqual(submit[2]["arguments"], {"project": "demo"})
        self.assertIn(("wait", "jwt-secret", "req-1"), calls)

    def test_project_create_routes_as_global_typed_write(self):
        result = server.project_create(
            "dev-1",
            "new-app",
            "New App",
            ["blender"],
            True,
            True,
            True,
            "space-1",
        )
        self.assertEqual(result["status"], "succeeded")
        calls = [call for instance in FakeClient.instances for call in instance.calls]
        submit = next(call for call in calls if call[0] == "submit")
        self.assertEqual(submit[2]["action"], "workspace.project_create")
        self.assertIsNone(submit[2]["project"])
        self.assertEqual(
            submit[2]["arguments"],
            {
                "slug": "new-app",
                "name": "New App",
                "apps": ["blender"],
                "set_default": True,
                "git_init": True,
                "readme": True,
            },
        )

    def test_project_import_routes_as_global_typed_write(self):
        result = server.project_import(
            "dev-1",
            "existing-app",
            "existing-app",
            ["blender"],
            True,
            "automation/blender",
            "scene.blend",
            "space-1",
        )
        self.assertEqual(result["status"], "succeeded")
        calls = [call for instance in FakeClient.instances for call in instance.calls]
        submit = next(call for call in calls if call[0] == "submit")
        self.assertEqual(submit[2]["action"], "workspace.bind_project")
        self.assertIsNone(submit[2]["project"])
        self.assertEqual(
            submit[2]["arguments"],
            {
                "slug": "existing-app",
                "relative_path": "existing-app",
                "apps": ["blender"],
                "set_default": True,
                "blender_scripts_dir": "automation/blender",
                "blend_file": "scene.blend",
            },
        )

    def test_browser_and_desktop_tools_route_with_project_scope(self):
        shot = server.browser_screenshot(
            "dev-1",
            "demo",
            "11111111-1111-4111-8111-111111111111",
            1280,
            720,
            "space-1",
        )
        self.assertEqual(shot["status"], "succeeded")
        clicked = server.computer_click(
            "dev-1", "demo", 100, 200, "left", 1, "space-1"
        )
        self.assertEqual(clicked["status"], "succeeded")

        calls = [call for instance in FakeClient.instances for call in instance.calls if call[0] == "submit"]
        browser = next(call for call in calls if call[2]["action"] == "browser.screenshot")
        self.assertEqual(browser[2]["project"], "demo")
        self.assertEqual(browser[2]["arguments"]["width"], 1280)
        self.assertEqual(browser[2]["arguments"]["height"], 720)
        desktop = next(call for call in calls if call[2]["action"] == "computer.click")
        self.assertEqual(desktop[2]["project"], "demo")
        self.assertEqual(desktop[2]["arguments"]["x"], 100)
        self.assertEqual(desktop[2]["arguments"]["y"], 200)

    def test_computer_filesystem_tools_route_without_project_scope(self):
        read = server.computer_text_read(
            "dev-1", "C:/Users/example/notes.txt", 1, 20, "space-1"
        )
        self.assertEqual(read["status"], "succeeded")
        write = server.computer_text_write(
            "dev-1",
            "C:/Users/example/notes.txt",
            "hello",
            "a" * 64,
            False,
            "space-1",
        )
        self.assertEqual(write["status"], "succeeded")

        calls = [call for instance in FakeClient.instances for call in instance.calls if call[0] == "submit"]
        read_call = next(call for call in calls if call[2]["action"] == "computer.text_read")
        self.assertIsNone(read_call[2]["project"])
        self.assertEqual(read_call[2]["arguments"]["path"], "C:/Users/example/notes.txt")
        write_call = next(call for call in calls if call[2]["action"] == "computer.text_write")
        self.assertIsNone(write_call[2]["project"])
        self.assertEqual(write_call[2]["arguments"]["expected_sha256"], "a" * 64)

    def test_project_briefing_and_continuity_route_with_project_scope(self):
        briefing = server.project_briefing("dev-1", "demo", "space-1")
        self.assertEqual(briefing["status"], "succeeded")
        state = server.continuity_state("dev-1", "demo", "space-1")
        self.assertEqual(state["status"], "succeeded")
        updated = server.continuity_update(
            "dev-1", "demo", "Ready", "Ship", ["foundation"], [], ["src/app.py"], "space-1"
        )
        self.assertEqual(updated["status"], "succeeded")

        calls = [call for instance in FakeClient.instances for call in instance.calls if call[0] == "submit"]
        actions = [call[2]["action"] for call in calls]
        self.assertIn("agent.project_briefing", actions)
        self.assertIn("continuity.get", actions)
        self.assertIn("continuity.update", actions)
        update = next(call for call in calls if call[2]["action"] == "continuity.update")
        self.assertEqual(update[2]["project"], "demo")
        self.assertEqual(update[2]["arguments"]["summary"], "Ready")
        self.assertEqual(update[2]["arguments"]["next_action"], "Ship")

    def test_app_intelligence_routes_without_project_scope(self):
        catalog = server.app_intelligence_catalog("dev-1", "space-1")
        self.assertEqual(catalog["status"], "succeeded")
        detail = server.app_intelligence_detail("dev-1", "studio", "space-1")
        self.assertEqual(detail["status"], "succeeded")

        calls = [
            call for instance in FakeClient.instances for call in instance.calls
            if call[0] == "submit"
        ]
        catalog_call = next(
            call for call in calls if call[2]["action"] == "intelligence.app_catalog"
        )
        self.assertIsNone(catalog_call[2]["project"])
        self.assertEqual(catalog_call[2]["arguments"], {})
        detail_call = next(
            call for call in calls if call[2]["action"] == "intelligence.app_detail"
        )
        self.assertIsNone(detail_call[2]["project"])
        self.assertEqual(detail_call[2]["arguments"], {"app_id": "studio"})

    def test_studio_catalog_routes_without_project_scope(self):
        result = server.repository_catalog("dev-1", "space-1")
        self.assertEqual(result["status"], "succeeded")
        calls = [call for instance in FakeClient.instances for call in instance.calls]
        submit = next(call for call in calls if call[0] == "submit")
        self.assertEqual(submit[2]["action"], "workspace.repository_catalog")
        self.assertIsNone(submit[2]["project"])
        self.assertEqual(submit[2]["arguments"], {})

    def test_persistent_process_tools_route_as_project_scoped_actions(self):
        started = server.process_start(
            "dev-1", "demo", ["python", "server.py"], "services", {"MODE": "test"}, 1.0, "space-1"
        )
        self.assertEqual(started["status"], "succeeded")
        calls = [call for instance in FakeClient.instances for call in instance.calls]
        submit = next(call for call in calls if call[0] == "submit")
        self.assertEqual(submit[2]["action"], "process.start")
        self.assertEqual(submit[2]["project"], "demo")
        self.assertEqual(submit[2]["arguments"], {
            "project": "demo", "argv": ["python", "server.py"], "cwd": "services",
            "wait_seconds": 1.0, "env": {"MODE": "test"},
        })

        FakeClient.instances.clear()
        server.process_write_stdin("dev-1", "demo", "proc-1", "yes", True, "space-1")
        server.process_stop("dev-1", "demo", "proc-1", "space-1")
        actions = [call[2]["action"] for instance in FakeClient.instances for call in instance.calls if call[0] == "submit"]
        self.assertEqual(actions, ["process.write_stdin", "process.stop"])

    def test_system_process_tools_route_as_device_scoped_actions(self):
        listed = server.computer_processes("dev-1", "python", 25, "space-1")
        self.assertEqual(listed["status"], "succeeded")
        terminated = server.computer_terminate_process(
            "dev-1", 4242, "python.exe", False, True, "space-1"
        )
        self.assertEqual(terminated["status"], "succeeded")

        calls = [
            call for instance in FakeClient.instances for call in instance.calls
            if call[0] == "submit"
        ]
        process_call = next(call for call in calls if call[2]["action"] == "computer.processes")
        self.assertIsNone(process_call[2]["project"])
        self.assertEqual(
            {"query": "python", "max_items": 25},
            process_call[2]["arguments"],
        )
        terminate_call = next(
            call for call in calls if call[2]["action"] == "computer.terminate_process"
        )
        self.assertIsNone(terminate_call[2]["project"])
        self.assertEqual(
            {
                "pid": 4242,
                "expected_name": "python.exe",
                "force": False,
                "tree": True,
            },
            terminate_call[2]["arguments"],
        )

    def test_typed_mutations_are_exposed_but_generic_execution_is_not(self):
        for name in (
            "project_create", "project_import", "continuity_update", "browser_start", "browser_click", "computer_click", "computer_text_write", "computer_path_remove", "project_text_write", "project_text_patch", "process_start", "process_write_stdin", "process_stop", "blender_start",
            "blender_transform", "blender_create_primitive",
            "blender_apply_material", "blender_save",
        ):
            self.assertTrue(hasattr(server, name), name)
        for name in ("action_execute", "shell_exec", "blender_run_python"):
            self.assertFalse(hasattr(server, name), name)

    def test_token_is_required_but_never_a_tool_parameter(self):
        for name in (
            "product_session",
            "product_targets",
            "app_intelligence_catalog",
            "app_intelligence_detail",
            "projects_list",
            "project_create",
            "project_import",
            "repository_catalog",
            "project_inventory",
            "project_text_read",
            "project_health",
            "project_briefing",
            "continuity_state",
            "continuity_update",
            "project_search",
            "project_read_batch",
            "project_preview_status",
            "git_status",
            "git_diff",
            "artifacts_list",
            "artifact_preview",
            "browser_status",
            "browser_list",
            "browser_snapshot",
            "browser_screenshot",
            "browser_start",
            "browser_navigate",
            "browser_click",
            "browser_type",
            "browser_stop",
            "process_status",
            "process_list",
            "process_logs",
            "process_start",
            "process_write_stdin",
            "process_stop",
            "computer_windows",
            "computer_active_window",
            "computer_screenshot",
            "computer_focus_window",
            "computer_click",
            "computer_scroll",
            "computer_type",
            "computer_hotkey",
            "computer_access_status",
            "computer_processes",
            "computer_terminate_process",
            "computer_file_stat",
            "computer_directory_list",
            "computer_text_read",
            "computer_search",
            "computer_text_write",
            "computer_text_patch",
            "computer_directory_create",
            "computer_path_move",
            "computer_path_remove",
        ):
            annotations = getattr(server, name).__annotations__
            self.assertNotIn("access_token", annotations)
            self.assertNotIn("token", annotations)

        with patch.dict(os.environ, {"ORDAX_PRODUCT_ACCESS_TOKEN": ""}, clear=False):
            with self.assertRaises(RuntimeError):
                server.product_session()


if __name__ == "__main__":
    unittest.main()
