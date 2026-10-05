import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from ordax_studio.cli import main, registry, select_project


class OrdaxStudioCliTests(unittest.TestCase):
    def test_status_and_resume_use_persistent_runtime(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / "project"
            project.mkdir()
            (root / "agent-settings.json").write_text(json.dumps({
                "default_project": "demo",
                "projects": {"demo": {"path": str(project), "apps": ["blender"]}},
            }), encoding="utf-8")
            env = {
                "ORDAX_AGENT_STATE_DIR": str(root),
                "ORDAX_MEMORY_DB": str(root / "memory.db"),
            }

            with patch.dict(os.environ, env, clear=False):
                output = io.StringIO()
                with redirect_stdout(output):
                    self.assertEqual(main(["status"]), 0)
                status = json.loads(output.getvalue())
                self.assertEqual(status["product"], "ORDAX Studio")
                self.assertEqual(status["default_project"], "demo")
                self.assertIn("session", status["action_groups"])

                output = io.StringIO()
                with redirect_stdout(output):
                    self.assertEqual(main(["resume", "demo"]), 0)
                resumed = json.loads(output.getvalue())
                self.assertTrue(resumed["ok"])
                self.assertEqual(resumed["data"]["project"]["slug"], "demo")
                self.assertEqual(resumed["data"]["capabilities"]["apps"], ["blender"])
                self.assertTrue(Path(resumed["data"]["boot_context_path"]).is_file())

    def test_selection_skips_missing_persisted_project(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            available = root / "available"
            available.mkdir()
            missing = root / "missing"
            (root / "agent-settings.json").write_text(json.dumps({
                "default_project": "demo",
                "projects": {
                    "gone": {"path": str(missing), "apps": ["blender"]},
                    "demo": {"path": str(available), "apps": ["blender"]},
                },
            }), encoding="utf-8")
            env = {
                "ORDAX_AGENT_STATE_DIR": str(root),
                "ORDAX_MEMORY_DB": str(root / "memory.db"),
            }
            with patch.dict(os.environ, env, clear=False):
                agent = registry()
                agent._memory_store_instance().set_active_project("gone", missing)
                self.assertEqual(select_project(agent), "demo")


if __name__ == "__main__":
    unittest.main()
