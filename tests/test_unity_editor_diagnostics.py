import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ordax_dev_agent.actions import ActionRegistry
from ordax_dev_agent.config import AgentConfig
from ordax_dev_agent.unity_actions import _unity_editor_log_candidates


class UnityEditorDiagnosticsTests(unittest.TestCase):
    def test_project_editor_log_is_first_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            project = Path(raw) / "project"
            project.mkdir()
            candidates = _unity_editor_log_candidates(project)
            self.assertEqual(
                (project / "Logs" / "Editor.log").resolve(),
                candidates[0],
            )

    def test_windows_diagnostics_uses_lock_probe_without_process_enumeration(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            project = root / "project"
            (project / "Logs").mkdir(parents=True)
            (project / "Logs" / "Editor.log").write_text(
                "WINDOWS_LOCK_PROBE_TEST\n",
                encoding="utf-8",
            )
            config = AgentConfig(
                "test",
                
                
                5,
                root / "state",
                root / "agent",
                root / "hordax",
                root / "bridge",
                projects={"unity": {"path": str(project), "apps": ["unity"]}},
                default_project="unity",
            )
            registry = ActionRegistry(config)
            with patch(
                "ordax_dev_agent.unity_actions.sys.platform",
                "win32",
            ), patch(
                "ordax_dev_agent.unity_actions._windows_unity_lock_probe",
                return_value={"state": "active", "path": str(project / "Temp" / "UnityLockfile")},
            ), patch(
                "ordax_dev_agent.unity_actions._unity_process_ids_for_project"
            ) as process_ids:
                result = registry.execute(
                    "unity.editor_diagnostics",
                    {"max_lines": 40},
                )

            self.assertTrue(result.ok)
            process_ids.assert_not_called()
            self.assertIsNone(result.data["unity_process_ids"])
            self.assertEqual("active", result.data["project_lock_probe"]["state"])
            self.assertIn("WINDOWS_LOCK_PROBE_TEST", result.data["editor_log_tail"])

    def test_diagnostics_prefers_project_local_editor_log(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            project = root / "project"
            (project / "Logs").mkdir(parents=True)
            (project / "Logs" / "Editor.log").write_text(
                "PROJECT_LOCAL_EDITOR_LOG_MARKER\n",
                encoding="utf-8",
            )
            config = AgentConfig(
                "test",
                
                
                5,
                root / "state",
                root / "agent",
                root / "hordax",
                root / "bridge",
                projects={"unity": {"path": str(project), "apps": ["unity"]}},
                default_project="unity",
            )
            registry = ActionRegistry(config)
            with patch(
                "ordax_dev_agent.unity_actions._unity_process_ids_for_project",
                return_value=[],
            ):
                result = registry.execute(
                    "unity.editor_diagnostics",
                    {"max_lines": 40},
                )

            self.assertTrue(result.ok)
            self.assertEqual("project", result.data["editor_log_source"])
            self.assertEqual(
                str((project / "Logs" / "Editor.log").resolve()),
                result.data["editor_log_path"],
            )
            self.assertIn(
                "PROJECT_LOCAL_EDITOR_LOG_MARKER",
                result.data["editor_log_tail"],
            )


if __name__ == "__main__":
    unittest.main()
