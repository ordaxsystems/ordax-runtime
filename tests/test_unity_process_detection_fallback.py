import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ordax_dev_agent.unity_actions import _unity_process_ids_for_project


class UnityProcessDetectionFallbackTests(unittest.TestCase):
    def test_windows_cim_timeout_with_no_unity_process_is_safe(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            project = Path(raw)
            tasklist = subprocess.CompletedProcess(
                args=["tasklist"],
                returncode=0,
                stdout='INFO: No tasks are running which match the specified criteria.\n',
                stderr="",
            )
            with patch("ordax_dev_agent.unity_actions.sys.platform", "win32"), patch(
                "ordax_dev_agent.unity_actions.subprocess.run",
                side_effect=[subprocess.TimeoutExpired(cmd="powershell", timeout=20), tasklist],
            ):
                self.assertEqual([], _unity_process_ids_for_project(project))

    def test_windows_cim_timeout_with_unity_running_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            project = Path(raw)
            tasklist = subprocess.CompletedProcess(
                args=["tasklist"],
                returncode=0,
                stdout='"Unity.exe","18116","Console","1","1,234,567 K"\n',
                stderr="",
            )
            with patch("ordax_dev_agent.unity_actions.sys.platform", "win32"), patch(
                "ordax_dev_agent.unity_actions.subprocess.run",
                side_effect=[subprocess.TimeoutExpired(cmd="powershell", timeout=20), tasklist],
            ):
                with self.assertRaisesRegex(OSError, "refusing unsafe stale-lock cleanup"):
                    _unity_process_ids_for_project(project)

    def test_windows_cim_still_selects_matching_project(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            project = Path(raw).resolve()
            command = str(project).replace("\\", "/")
            cim = subprocess.CompletedProcess(
                args=["powershell"],
                returncode=0,
                stdout='{"ProcessId":321,"CommandLine":"Unity.exe -projectPath ' + command.replace("\\", "\\\\") + '"}',
                stderr="",
            )
            with patch("ordax_dev_agent.unity_actions.sys.platform", "win32"), patch(
                "ordax_dev_agent.unity_actions.subprocess.run",
                return_value=cim,
            ):
                self.assertEqual([321], _unity_process_ids_for_project(project))


if __name__ == "__main__":
    unittest.main()
