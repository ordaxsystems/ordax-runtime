import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from ordax_studio.desktop import main


class OrdaxStudioDesktopTests(unittest.TestCase):
    def test_smoke_uses_registered_projects_without_gui(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / "project"
            project.mkdir()
            (root / "agent-settings.json").write_text(json.dumps({
                "default_project": "demo",
                "projects": {"demo": {"path": str(project), "apps": ["blender", "unity"]}},
            }), encoding="utf-8")
            env = {
                "ORDAX_AGENT_STATE_DIR": str(root),
                "ORDAX_MEMORY_DB": str(root / "memory.db"),
            }

            with patch.dict(os.environ, env, clear=False):
                output = io.StringIO()
                with redirect_stdout(output):
                    self.assertEqual(main(["--smoke"]), 0)
                payload = json.loads(output.getvalue())
                self.assertEqual(payload["product"], "ORDAX Studio")
                self.assertEqual(payload["default_project"], "demo")
                self.assertEqual(payload["projects"][0]["apps"], ["blender", "unity"])
                self.assertGreater(payload["action_count"], 0)
                self.assertEqual(payload["memory"]["counts"]["sessions"], 0)


if __name__ == "__main__":
    unittest.main()
