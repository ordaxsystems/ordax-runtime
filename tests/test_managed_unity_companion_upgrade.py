import tempfile
import unittest
from pathlib import Path

from ordax_dev_agent.actions import ActionRegistry
from ordax_dev_agent.config import AgentConfig


class ManagedUnityCompanionUpgradeTests(unittest.TestCase):
    def make_registry(self, root: Path) -> tuple[ActionRegistry, Path]:
        project = root / "project"
        (project / "Assets" / "OrdaX" / "Editor").mkdir(parents=True)
        config = AgentConfig(
            "test",
            
            
            5,
            root / "state",
            root / "agent",
            root / "hordax",
            root / "bridge",
            projects={"world": {"path": str(project), "apps": ["unity"]}},
            default_project="world",
        )
        return ActionRegistry(config), project

    def test_managed_generic_companion_is_upgraded_atomically(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            registry, project = self.make_registry(root)
            target = project / "Assets" / "OrdaX" / "Editor" / "OrdaXGenericAgent.cs"
            target.write_text(
                '#if UNITY_EDITOR\nnamespace OrdaX.EditorTools { class OrdaXGenericAgent { string protocol = "ordax-generic-v1"; } }\n#endif\n',
                encoding="utf-8",
            )

            result = registry.execute("unity.install_companion", {})

            self.assertTrue(result.ok)
            self.assertTrue(result.data["updated"])
            self.assertFalse(result.data["installed"])
            self.assertIn("ordax-generic-v5", target.read_text(encoding="utf-8"))

    def test_foreign_companion_is_never_overwritten(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            registry, project = self.make_registry(root)
            target = project / "Assets" / "OrdaX" / "Editor" / "OrdaXGenericAgent.cs"
            original = "public static class MyCustomEditorTool {}\n"
            target.write_text(original, encoding="utf-8")

            result = registry.execute("unity.install_companion", {})

            self.assertFalse(result.ok)
            self.assertIn("not recognized as OrdaX-managed", result.summary)
            self.assertEqual(original, target.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
