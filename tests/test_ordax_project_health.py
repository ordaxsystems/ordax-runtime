import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ordax_dev_agent.actions import ActionRegistry
from ordax_dev_agent.config import AgentConfig
from ordax_dev_agent.models import ActionResult


class OrdaxProjectHealthTests(unittest.TestCase):
    def test_health_reports_outdated_blender_companion_without_mutation(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            project = root / "project"
            project.mkdir()
            (project / ".git").mkdir()
            config = AgentConfig(
                agent_name="test", poll_seconds=1, state_dir=root / "state",
                agent_repo_path=root / "agent", hordax_path=root / "hordax",
                bridge_path=root / "bridge",
                projects={"demo": {"path": str(project), "apps": ["blender"]}},
                default_project="demo",
            )
            env = {"ORDAX_MEMORY_DB": str(root / "memory.db")}
            with patch.dict(os.environ, env, clear=False):
                registry = ActionRegistry(config)
                blender = ActionResult(False, "Visible Blender live session is not running", {
                    "presence_fresh": False,
                    "protocol_version": 9,
                    "protocol_compatible": True,
                    "companion_current": False,
                    "presence": {
                        "blender_version": "5.2.2",
                        "file": str(project / "scene.blend"),
                    },
                })
                git = ActionResult(True, "command completed", {"stdout": " M scene.blend\n"})
                with patch.object(registry, "blender_live_status", return_value=blender), \
                     patch.object(registry, "git_quick_status", return_value=git):
                    result = registry.execute("agent.project_health", {"project": "demo"})

            self.assertTrue(result.ok)
            self.assertEqual("attention", result.data["state"])
            self.assertEqual("update_required", result.data["adapters"]["blender"]["state"])
            self.assertEqual("5.2.2", result.data["adapters"]["blender"]["blender_version"])
            self.assertTrue(result.data["git"]["dirty"])
            self.assertEqual(1, result.data["git"]["changed_entries"])
            self.assertEqual("disabled", result.data["adapters"]["unity"]["state"])

    def test_continuity_update_rejects_malformed_lists_before_overwrite(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            project = root / "project"
            project.mkdir()
            config = AgentConfig(
                agent_name="test", poll_seconds=1, state_dir=root / "state",
                agent_repo_path=root / "agent", hordax_path=root / "hordax",
                bridge_path=root / "bridge",
                projects={"demo": {"path": str(project), "apps": []}},
                default_project="demo",
            )
            env = {"ORDAX_MEMORY_DB": str(root / "memory.db")}
            with patch.dict(os.environ, env, clear=False):
                registry = ActionRegistry(config)
                first = registry.execute(
                    "continuity.update",
                    {"project": "demo", "summary": "Stable", "completed": ["foundation"]},
                )
                bad = registry.execute(
                    "continuity.update",
                    {"project": "demo", "summary": "Bad", "completed": "not-a-list"},
                )
                state = registry.execute("continuity.get", {"project": "demo"})

            self.assertTrue(first.ok)
            self.assertFalse(bad.ok)
            self.assertEqual("completed", bad.data["field"])
            self.assertEqual("Stable", state.data["state"]["summary"])
            self.assertEqual(["foundation"], state.data["state"]["completed"])

    def test_project_briefing_combines_continuity_workspace_and_capabilities(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            project = root / "project"
            project.mkdir()
            (project / "README.md").write_text("# Demo\n\nContinuity architecture notes.\n", encoding="utf-8")
            config = AgentConfig(
                agent_name="test", poll_seconds=1, state_dir=root / "state",
                agent_repo_path=root / "agent", hordax_path=root / "hordax",
                bridge_path=root / "bridge",
                projects={"demo": {"path": str(project), "apps": []}},
                default_project="demo",
            )
            env = {"ORDAX_MEMORY_DB": str(root / "memory.db")}
            with patch.dict(os.environ, env, clear=False):
                registry = ActionRegistry(config)
                registry.execute("memory.remember", {"project": "demo", "content": "Keep continuity"})
                registry.execute("memory.task_add", {"project": "demo", "title": "Ship preview"})
                registry.execute("memory.checkpoint", {"project": "demo", "summary": "Initial checkpoint"})
                result = registry.execute(
                    "agent.project_briefing",
                    {"project": "demo", "query": "continuity", "recall_limit": 10},
                )

            self.assertTrue(result.ok, result.summary)
            self.assertEqual("demo", result.data["project"]["slug"])
            self.assertIn("README.md", result.data["workspace"]["context_files"])
            self.assertEqual("Ship preview", result.data["continuity"]["open_tasks"][-1]["title"])
            self.assertEqual("Initial checkpoint", result.data["continuity"]["latest_checkpoint"]["summary"])
            self.assertEqual("Initial checkpoint", result.data["continuity"]["project_state"]["summary"])
            self.assertEqual("checkpoint", result.data["continuity"]["project_state"]["source"])
            self.assertEqual("continuity", result.data["continuity"]["recall_query"])
            self.assertTrue(result.data["continuity"]["recall"])
            self.assertIn("Keep continuity", result.data["continuity"]["recall"][0]["snippet"])
            source_recall = result.data["workspace"]["source_recall"]
            self.assertEqual("continuity", source_recall["query"])
            self.assertGreaterEqual(source_recall["match_count"], 1)
            self.assertEqual("README.md", source_recall["matches"][0]["path"])
            self.assertIn("Continuity architecture", source_recall["matches"][0]["text"])
            self.assertIn("project", result.data["capabilities"]["action_groups"])


if __name__ == "__main__":
    unittest.main()
