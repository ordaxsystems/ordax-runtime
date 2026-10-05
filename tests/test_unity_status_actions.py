import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from ordax_dev_agent.actions import ActionRegistry
from ordax_dev_agent.config import AgentConfig
from ordax_dev_agent.unity_status_actions import UnityStatusActions


class UnityStatusActionsTests(unittest.TestCase):
    def make_config(self, root: Path) -> AgentConfig:
        project = root / "project"
        project.mkdir(parents=True, exist_ok=True)
        return AgentConfig(
            agent_name="test-agent",
            poll_seconds=1.0,
            state_dir=root / "state",
            agent_repo_path=root / "agent",
            hordax_path=project,
            bridge_path=root / "bridge",
            projects={
                "game": {
                    "path": str(project),
                    "apps": ["unity"],
                    "allowed_branches": [],
                }
            },
            default_project="game",
        )

    def test_registry_mro_routes_status_to_read_only_mixin(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            registry = ActionRegistry(self.make_config(Path(raw)))
            self.assertEqual(
                UnityStatusActions.unity_editor_status,
                registry.unity_editor_status.__func__,
            )

    def test_status_never_nudges_or_probes_mutating_helpers(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            registry = ActionRegistry(self.make_config(Path(raw)))
            editor = Mock()
            editor.status.return_value = {
                "project_appears_open": True,
                "presence_fresh": False,
            }
            with patch.object(registry, "_editor", return_value=editor):
                result = registry.execute("unity.editor_status", {"project": "game"})
            self.assertFalse(result.ok)
            self.assertEqual("Unity Editor companion not ready", result.summary)
            editor.status.assert_called_once_with()
            editor.nudge_companion.assert_not_called()
            editor.presence_is_fresh.assert_not_called()
            editor.project_appears_open.assert_not_called()

    def test_status_reports_ready_from_presence_only(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            registry = ActionRegistry(self.make_config(Path(raw)))
            editor = Mock()
            editor.status.return_value = {
                "project_appears_open": True,
                "presence_fresh": True,
                "presence": {"compiling": False},
            }
            with patch.object(registry, "_editor", return_value=editor):
                result = registry.execute("unity.editor_status", {"project": "game"})
            self.assertTrue(result.ok)
            self.assertEqual("Unity Editor companion ready", result.summary)

    def test_status_rejects_recovery_parameters(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            registry = ActionRegistry(self.make_config(Path(raw)))
            result = registry.execute(
                "unity.editor_status",
                {"project": "game", "wait_seconds": 30},
            )
            self.assertFalse(result.ok)
            self.assertIn("unsupported fields", result.summary)


if __name__ == "__main__":
    unittest.main()
