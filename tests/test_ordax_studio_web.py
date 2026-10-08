import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from ordax_dev_agent.models import ActionResult
from ordax_studio.web_desktop import StudioApi


class OrdaxStudioWebTests(unittest.TestCase):
    def test_web_shell_api_reuses_persistent_runtime(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / "project"
            project.mkdir()
            (project / "index.html").write_text("<h1>demo</h1>", encoding="utf-8")
            (project / "src").mkdir()
            (project / "src" / "demo.py").write_text("value = 1\n", encoding="utf-8")
            (root / "agent-settings.json").write_text(json.dumps({
                "default_project": "demo",
                "projects": {"demo": {"path": str(project), "apps": []}},
            }), encoding="utf-8")
            env = {
                "ORDAX_AGENT_STATE_DIR": str(root),
                "ORDAX_MEMORY_DB": str(root / "memory.db"),
            }
            with patch.dict(os.environ, env, clear=False):
                api = StudioApi()
                boot = api.bootstrap()
                self.assertEqual("demo", boot["project"]["slug"])
                self.assertIsNotNone(boot["session_id"])
                self.assertEqual("web", boot["preview"]["data"]["mode"])

                inventory = api.inventory()
                self.assertTrue(inventory["ok"])
                self.assertIn("src/demo.py", [item["path"] for item in inventory["data"]["entries"]])

                opened = api.read_file("src/demo.py")
                self.assertTrue(opened["ok"])
                saved = api.save_file("src/demo.py", "value = 2\n", opened["data"]["sha256"])
                self.assertTrue(saved["ok"])
                self.assertEqual("value = 2\n", (project / "src" / "demo.py").read_text(encoding="utf-8"))
                briefing = api.briefing()
                self.assertTrue(briefing["ok"])
                self.assertIn("continuity", briefing["data"])
                search = api.search("value")
                self.assertTrue(search["ok"])
                self.assertIn("src/demo.py", [item["path"] for item in search["data"]["matches"]])
                executions = api.execution_status()
                self.assertTrue(executions["ok"])
                self.assertEqual("demo", executions["data"]["project"])
                self.assertEqual([], executions["data"]["processes"])
                self.assertEqual([], executions["data"]["browsers"])
                self.assertIn("preview", executions["data"])

                task = api.task_add("Refinar a experiência do preview")
                self.assertTrue(task["ok"])
                updated = api.briefing()
                self.assertEqual(
                    "Refinar a experiência do preview",
                    updated["data"]["continuity"]["open_tasks"][-1]["title"],
                )

    def test_blender_bootstrap_exposes_typed_modeling_capabilities(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / "project"
            project.mkdir()
            (root / "agent-settings.json").write_text(json.dumps({
                "default_project": "demo",
                "projects": {"demo": {"path": str(project), "apps": ["blender"], "blender": {}}},
            }), encoding="utf-8")
            env = {
                "ORDAX_AGENT_STATE_DIR": str(root),
                "ORDAX_MEMORY_DB": str(root / "memory.db"),
            }
            with patch.dict(os.environ, env, clear=False):
                boot = StudioApi().bootstrap()

        modeling = boot["modeling"]
        self.assertTrue(modeling["ok"])
        tools = modeling["data"]["tools"]
        self.assertEqual("available", tools["surface_scatter"]["status"])
        self.assertEqual("available", tools["boolean_cut_preview"]["status"])
        self.assertEqual("available", tools["mesh_cleanup"]["status"])
        self.assertEqual("blender.live_mesh_cleanup", tools["mesh_cleanup"]["action"])
        self.assertIn("expected_base_geometry_sha256_matches", tools["mesh_cleanup"]["runtime_requirements"])
        self.assertIn("no_modifiers", tools["mesh_cleanup"]["runtime_requirements"])
        self.assertEqual("available", tools["degenerate_repair_preview"]["status"])
        self.assertEqual(
            "blender.live_degenerate_repair_preview",
            tools["degenerate_repair_preview"]["action"],
        )
        self.assertEqual(
            {
                "commit": "blender.live_degenerate_repair_commit",
                "cancel": "blender.live_degenerate_repair_cancel",
            },
            tools["degenerate_repair_preview"]["workflow_actions"],
        )
        self.assertEqual("available", tools["merge_by_distance_preview"]["status"])
        self.assertEqual(
            {
                "commit": "blender.live_merge_by_distance_commit",
                "cancel": "blender.live_merge_by_distance_cancel",
            },
            tools["merge_by_distance_preview"]["workflow_actions"],
        )
        self.assertEqual("available", tools["boundary_hole_fill_preview"]["status"])
        self.assertEqual(
            {
                "commit": "blender.live_boundary_hole_fill_commit",
                "cancel": "blender.live_boundary_hole_fill_cancel",
            },
            tools["boundary_hole_fill_preview"]["workflow_actions"],
        )
        self.assertEqual(
            {"commit": "blender.live_boolean_cut_commit", "cancel": "blender.live_boolean_cut_cancel"},
            tools["boolean_cut_preview"]["workflow_actions"],
        )

    def test_blender_prepare_adopts_existing_window_without_starting_blender(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / "project"
            project.mkdir()
            (root / "agent-settings.json").write_text(json.dumps({
                "default_project": "demo",
                "projects": {"demo": {"path": str(project), "apps": ["blender"], "blender": {}}},
            }), encoding="utf-8")
            env = {
                "ORDAX_AGENT_STATE_DIR": str(root),
                "ORDAX_MEMORY_DB": str(root / "memory.db"),
            }
            with patch.dict(os.environ, env, clear=False):
                api = StudioApi()
                with patch.object(api.agent, "execute") as execute:
                    execute.side_effect = [
                        ActionResult(False, "not connected", {}),
                        ActionResult(True, "Existing Blender window adopted by ORDAX Studio", {
                            "pid": 4242,
                            "presence": {"pid": 4242, "file": str(project / "scene.blend")},
                        }),
                    ]
                    result = api.blender_prepare()
            self.assertTrue(result["ok"])
            self.assertEqual("adopted", result["data"]["state"])
            self.assertEqual(4242, result["data"]["pid"])
            self.assertEqual(
                ["blender.live_status", "blender.adopt"],
                [call.args[0] for call in execute.call_args_list],
            )

    def test_blender_prepare_auto_adopts_single_clean_blank_window(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / "project"
            project.mkdir()
            (root / "agent-settings.json").write_text(json.dumps({
                "default_project": "demo",
                "projects": {"demo": {"path": str(project), "apps": ["blender"], "blender": {}}},
            }), encoding="utf-8")
            env = {"ORDAX_AGENT_STATE_DIR": str(root), "ORDAX_MEMORY_DB": str(root / "memory.db")}
            with patch.dict(os.environ, env, clear=False):
                api = StudioApi()
                with patch.object(api.agent, "execute") as execute:
                    execute.side_effect = [
                        ActionResult(False, "not connected", {}),
                        ActionResult(False, "no match", {"no_match": True}),
                        ActionResult(True, "instances", {
                            "ready_pids": [5151],
                            "restart_required_pids": [],
                            "unmanaged_blender_pids": [],
                            "instances": [{
                                "pid": 5151,
                                "file": "",
                                "is_dirty": False,
                                "attached_project": None,
                            }],
                        }),
                        ActionResult(True, "adopted blank", {
                            "pid": 5151,
                            "presence": {"pid": 5151, "file": ""},
                        }),
                    ]
                    result = api.blender_prepare()
            self.assertTrue(result["ok"])
            self.assertEqual("adopted_blank", result["data"]["state"])
            self.assertEqual(5151, result["data"]["pid"])
            last_payload = execute.call_args_list[-1].args[1]
            self.assertTrue(last_payload["allow_blank"])
            self.assertEqual(5151, last_payload["pid"])

    def test_blender_prepare_reports_stale_matching_addon_without_waiting_for_instances(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / "project"
            project.mkdir()
            (root / "agent-settings.json").write_text(json.dumps({
                "default_project": "demo",
                "projects": {"demo": {"path": str(project), "apps": ["blender"], "blender": {}}},
            }), encoding="utf-8")
            env = {"ORDAX_AGENT_STATE_DIR": str(root), "ORDAX_MEMORY_DB": str(root / "memory.db")}
            with patch.dict(os.environ, env, clear=False):
                api = StudioApi()
                with patch.object(api.agent, "execute") as execute:
                    execute.side_effect = [
                        ActionResult(False, "not connected", {}),
                        ActionResult(False, "stale addon", {
                            "restart_required": True,
                            "pid": 6161,
                            "install_action": "blender.adoption_install",
                        }),
                    ]
                    result = api.blender_prepare()
            self.assertTrue(result["ok"])
            self.assertEqual("restart_required", result["data"]["state"])
            self.assertEqual([6161], result["data"]["blender_pids"])
            self.assertEqual(2, execute.call_count)

    def test_blender_prepare_reports_running_unmanaged_window(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / "project"
            project.mkdir()
            (root / "agent-settings.json").write_text(json.dumps({
                "default_project": "demo",
                "projects": {"demo": {"path": str(project), "apps": ["blender"], "blender": {}}},
            }), encoding="utf-8")
            env = {"ORDAX_AGENT_STATE_DIR": str(root), "ORDAX_MEMORY_DB": str(root / "memory.db")}
            with patch.dict(os.environ, env, clear=False):
                api = StudioApi()
                with patch.object(api.agent, "execute") as execute:
                    execute.side_effect = [
                        ActionResult(False, "not connected", {}),
                        ActionResult(False, "no match", {"no_match": True}),
                        ActionResult(True, "instances", {"unmanaged_blender_pids": [7777]}),
                    ]
                    result = api.blender_prepare()
            self.assertTrue(result["ok"])
            self.assertEqual("restart_required", result["data"]["state"])
            self.assertEqual([7777], result["data"]["blender_pids"])
            self.assertEqual(
                ["blender.live_status", "blender.adopt", "blender.instances"],
                [call.args[0] for call in execute.call_args_list],
            )

    def test_package_without_dev_script_is_not_misclassified_as_web_preview(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / "project"
            project.mkdir()
            (project / "package.json").write_text(
                json.dumps({"scripts": {"test": "echo ok"}}), encoding="utf-8"
            )
            (root / "agent-settings.json").write_text(json.dumps({
                "default_project": "demo",
                "projects": {"demo": {"path": str(project), "apps": []}},
            }), encoding="utf-8")
            env = {
                "ORDAX_AGENT_STATE_DIR": str(root),
                "ORDAX_MEMORY_DB": str(root / "memory.db"),
            }
            with patch.dict(os.environ, env, clear=False):
                api = StudioApi()
                boot = api.bootstrap()
            self.assertEqual("artifact", boot["preview"]["data"]["mode"])

    def test_project_catalog_exposes_git_repository_identity(self):
        if not shutil.which("git"):
            self.skipTest("git is required")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / "repo"
            project.mkdir()
            subprocess.run(["git", "init", "-b", "main", str(project)], check=True, capture_output=True)
            subprocess.run(["git", "-C", str(project), "remote", "add", "origin", "https://github.com/example/demo.git"], check=True)
            (root / "agent-settings.json").write_text(json.dumps({
                "default_project": "demo",
                "projects": {
                    "demo": {"path": str(project), "apps": []},
                    "demo-alias": {"path": str(project), "apps": []},
                },
            }), encoding="utf-8")
            env = {"ORDAX_AGENT_STATE_DIR": str(root), "ORDAX_MEMORY_DB": str(root / "memory.db")}
            with patch.dict(os.environ, env, clear=False):
                catalog = StudioApi().projects_catalog()
            self.assertEqual(1, len(catalog["projects"]))
            repo = catalog["projects"][0]["repository"]
            self.assertTrue(repo["is_repository"])
            self.assertEqual("main", repo["branch"])
            self.assertEqual("https://github.com/example/demo.git", repo["remote"])


    def test_product_status_combines_agent_remote_mcp_and_blender(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / "project"
            project.mkdir()
            (root / "agent-settings.json").write_text(json.dumps({
                "default_project": "demo",
                "device_id": "device-1",
                "control_plane_url": "https://example.test",
                "projects": {"demo": {"path": str(project), "apps": ["blender"], "blender": {}}},
            }), encoding="utf-8")
            env = {"ORDAX_AGENT_STATE_DIR": str(root), "ORDAX_MEMORY_DB": str(root / "memory.db")}
            with patch.dict(os.environ, env, clear=False):
                api = StudioApi()
                with patch.object(api.agent, "execute") as execute, patch("ordax_studio.web_desktop.httpx.get") as get:
                    execute.side_effect = [
                        ActionResult(True, "resilience", {"resilience": {
                            "scheduled_task": {"exists": True, "state": "Running"},
                            "local_health": {"ok": True},
                        }}),
                        ActionResult(True, "health", {"adapters": {"blender": {
                            "enabled": True, "state": "ready", "summary": "ready",
                            "blender_version": "5.2.2", "file": str(project / "scene.blend"),
                        }}}),
                    ]
                    local_response = Mock()
                    local_response.is_success = True
                    local_response.status_code = 200
                    local_response.headers = {"content-type": "application/json"}
                    local_response.json.return_value = {
                        "runtime": {
                            "state": "ready",
                            "paired": True,
                            "transport_state": "connected",
                            "last_transport_error": None,
                            "last_transport_recovered_at": None,
                        }
                    }
                    remote_response = Mock()
                    remote_response.is_success = True
                    remote_response.status_code = 200
                    remote_response.headers = {"content-type": "application/json"}
                    remote_response.json.return_value = {
                        "ok": True, "product_auth_configured": True,
                        "capabilities": ["product_typed_actions_v2"],
                    }
                    get.side_effect = [local_response, remote_response]
                    result = api.product_status()
            self.assertTrue(result["ok"])
            self.assertTrue(result["data"]["device_agent"]["ok"])
            self.assertEqual("ready", result["data"]["device_agent"]["runtime_state"])
            self.assertTrue(result["data"]["device_agent"]["paired"])
            self.assertEqual("connected", result["data"]["device_agent"]["transport_state"])
            self.assertTrue(result["data"]["remote_mcp"]["ok"])
            self.assertTrue(result["data"]["remote_mcp"]["oauth"])
            self.assertTrue(result["data"]["remote_mcp"]["typed_actions_v2"])
            self.assertTrue(result["data"]["computer_control"]["input"])
            self.assertTrue(result["data"]["blender_live"]["ok"])
            self.assertEqual(2, get.call_count)
            get.assert_any_call("http://127.0.0.1:8765/status", timeout=1.5, follow_redirects=False)
            get.assert_any_call("https://example.test/health", timeout=4.0, follow_redirects=False)


    def test_host_runtime_has_no_second_portable_studio_source(self):
        root = Path(__file__).resolve().parents[1] / "ordax_studio"
        self.assertFalse((root / "studio.html").exists())
        self.assertFalse((root / "studio_product.html").exists())
        self.assertFalse((root / "assets").exists())



if __name__ == "__main__":
    unittest.main()
