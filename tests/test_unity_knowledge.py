import json
import tempfile
import unittest
from pathlib import Path

from ordax_dev_agent.unity_knowledge import capability_report, project_profile, skill_catalog


class UnityKnowledgeTests(unittest.TestCase):
    def make_project(self, root: Path, *, version="6000.3.0f1", packages=None):
        (root / "ProjectSettings").mkdir(parents=True)
        (root / "Packages").mkdir()
        (root / "Assets" / "Scenes").mkdir(parents=True)
        (root / "ProjectSettings" / "ProjectVersion.txt").write_text(
            f"m_EditorVersion: {version}\n", encoding="utf-8"
        )
        (root / "Packages" / "manifest.json").write_text(
            json.dumps({"dependencies": packages or {}}), encoding="utf-8"
        )
        (root / "Assets" / "Scenes" / "Main.unity").write_text("%YAML", encoding="utf-8")

    def test_project_profile_detects_unity6_and_urp(self):
        with tempfile.TemporaryDirectory() as raw:
            root=Path(raw)
            self.make_project(root, packages={
                "com.unity.render-pipelines.universal":"17.0.4",
                "com.unity.ai.navigation":"2.0.9",
            })
            profile=project_profile(root)
            self.assertTrue(profile["unity_6_or_newer"])
            self.assertEqual("URP", profile["render_pipeline"])
            self.assertEqual(1, profile["scene_count"])

    def test_capability_report_uses_installed_package_signals(self):
        with tempfile.TemporaryDirectory() as raw:
            root=Path(raw)
            self.make_project(root, packages={"com.unity.localization":"1.5.7"})
            report=capability_report(root)
            self.assertIn("localization", report["applicable_skill_ids"])
            self.assertFalse(report["codex_dependency"])
            self.assertFalse(report["unity_plugin_runtime_dependency"])

    def test_catalog_has_source_refs_without_runtime_dependency(self):
        catalog=skill_catalog()
        self.assertFalse(catalog["runtime_dependency"])
        ids={item["id"] for item in catalog["skills"]}
        self.assertIn("physics-3d-collision", ids)
        self.assertIn("ui-uitk", ids)
        self.assertIn("validate-urp-render-graph-renderer-feature", ids)


if __name__ == "__main__":
    unittest.main()
