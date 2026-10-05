from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ordax_studio.web_desktop import StudioApi


class StudioProjectResumeContractTests(unittest.TestCase):
    def _env(self, root: Path) -> dict[str, str]:
        return {
            "ORDAX_AGENT_STATE_DIR": str(root),
            "ORDAX_MEMORY_DB": str(root / "memory.db"),
            "ORDAX_STUDIO_OPEN_PROJECT": "",
        }

    def test_opening_studio_does_not_invent_an_active_project(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            alpha = root / "alpha"
            beta = root / "beta"
            alpha.mkdir()
            beta.mkdir()
            (root / "agent-settings.json").write_text(json.dumps({
                "default_project": "beta",
                "projects": {
                    "alpha": {"path": str(alpha), "apps": []},
                    "beta": {"path": str(beta), "apps": []},
                },
            }), encoding="utf-8")
            with patch.dict(os.environ, self._env(root), clear=False):
                api = StudioApi()
                self.assertIsNone(api.store.active_project())
                self.assertIsNone(api.session_id)
                catalog = api.projects_catalog()
                self.assertIsNone(catalog.get("active_project"))
                self.assertEqual("beta", api.project)  # operational fallback only

    def test_bootstrap_is_an_explicit_entry_into_project_context(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / "demo"
            project.mkdir()
            (root / "agent-settings.json").write_text(json.dumps({
                "default_project": "demo",
                "projects": {"demo": {"path": str(project), "apps": []}},
            }), encoding="utf-8")
            with patch.dict(os.environ, self._env(root), clear=False):
                api = StudioApi()
                boot = api.bootstrap()
                self.assertEqual("demo", boot["project"]["slug"])
                self.assertIsNotNone(boot["session_id"])
                self.assertEqual("demo", api.store.active_project()["name"])

    def test_persisted_active_project_survives_studio_construction(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            alpha = root / "alpha"
            beta = root / "beta"
            alpha.mkdir()
            beta.mkdir()
            (root / "agent-settings.json").write_text(json.dumps({
                "default_project": "beta",
                "projects": {
                    "alpha": {"path": str(alpha), "apps": []},
                    "beta": {"path": str(beta), "apps": []},
                },
            }), encoding="utf-8")
            with patch.dict(os.environ, self._env(root), clear=False):
                seed = StudioApi()
                seed.store.set_active_project("alpha", alpha)
                api = StudioApi()
                catalog = api.projects_catalog()
                self.assertEqual("alpha", catalog.get("active_project"))
                self.assertEqual("alpha", api.project)
                self.assertIsNone(api.session_id)

    def test_shared_surface_prefers_explicit_startup_then_persisted_active(self) -> None:
        script = (Path(__file__).resolve().parents[1] / "ordax_studio" / "assets" / "studio.js").read_text(encoding="utf-8")
        self.assertIn("data.startup_project||data.active_project||null", script)
        self.assertIn("state.projects.some(project=>project.slug===requested)", script)
        self.assertNotIn("state.projects[0]", script)


if __name__ == "__main__":
    unittest.main()
