from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from ordax_dev_agent.artifact_actions import ArtifactActions


class _Host(ArtifactActions):
    def __init__(self, root: Path):
        self.project = SimpleNamespace(slug="demo", root=root / "project")
        self.project.root.mkdir(parents=True)
        self.config = SimpleNamespace(state_dir=root / "state")

    def _project(self, _payload):
        return self.project


class ArtifactsListTests(unittest.TestCase):
    def test_lists_only_relative_metadata_from_authorized_roots(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            host = _Host(root)
            managed = host.config.state_dir / "artifacts" / "demo"
            project_artifacts = host.project.root / "Artifacts"
            managed.mkdir(parents=True)
            project_artifacts.mkdir(parents=True)
            (managed / "preview.png").write_bytes(b"abc")
            (project_artifacts / "exports").mkdir()
            (project_artifacts / "exports" / "scene.glb").write_bytes(b"12345")

            result = host.artifacts_list({"project": "demo"})

            self.assertTrue(result.ok)
            self.assertEqual(len(result.data["items"]), 2)
            self.assertEqual(
                {(item["source"], item["relative_path"]) for item in result.data["items"]},
                {("managed", "preview.png"), ("project", "exports/scene.glb")},
            )
            self.assertTrue(all("path" not in item for item in result.data["items"]))

    def test_enforces_item_limit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            host = _Host(root)
            managed = host.config.state_dir / "artifacts" / "demo"
            managed.mkdir(parents=True)
            for index in range(3):
                (managed / f"{index}.txt").write_text(str(index), encoding="utf-8")

            result = host.artifacts_list({"project": "demo", "max_items": 2})

            self.assertTrue(result.ok)
            self.assertEqual(len(result.data["items"]), 2)
            self.assertTrue(result.data["truncated"])

    def test_rejects_invalid_limit(self):
        with tempfile.TemporaryDirectory() as tmp:
            host = _Host(Path(tmp))
            result = host.artifacts_list({"project": "demo", "max_items": 0})
            self.assertFalse(result.ok)


if __name__ == "__main__":
    unittest.main()
