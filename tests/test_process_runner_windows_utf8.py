"""Windows child processes must emit the Runtime's canonical UTF-8 terminal contract."""
from __future__ import annotations

import io
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from ordax_dev_agent.process_runner import run_command


class _CompletedProcess:
    pid = 12345
    returncode = 0

    def __init__(self, stdout: bytes = b"ok\n", stderr: bytes = b""):
        self._stdout = stdout
        self._stderr = stderr
        self.stdout = io.BytesIO()
        self.stderr = io.BytesIO()

    def communicate(self, *, timeout):
        return self._stdout, self._stderr


class WindowsTerminalUtf8ContractTests(unittest.TestCase):
    def test_python_receives_utf8_stdio_defaults_on_windows(self):
        child = _CompletedProcess("Café ✓ 日本語 ção\n".encode("utf-8"))
        with (
            patch("ordax_dev_agent.process_runner.sys", SimpleNamespace(platform="win32", frozen=False)),
            patch("ordax_dev_agent.process_runner.subprocess.Popen", return_value=child) as popen,
        ):
            result = run_command(["python", "-c", "print('unicode')"])

        self.assertTrue(result.ok)
        self.assertEqual(result.data["stdout"], "Café ✓ 日本語 ção\n")
        environment = popen.call_args.kwargs["env"]
        self.assertEqual(environment["PYTHONUTF8"], "1")
        self.assertEqual(environment["PYTHONIOENCODING"], "utf-8")
        self.assertFalse(popen.call_args.kwargs["text"])

    def test_explicit_python_encoding_override_is_not_silently_rewritten(self):
        child = _CompletedProcess()
        with (
            patch("ordax_dev_agent.process_runner.sys", SimpleNamespace(platform="win32", frozen=False)),
            patch("ordax_dev_agent.process_runner.subprocess.Popen", return_value=child) as popen,
        ):
            result = run_command(
                ["python", "-c", "print('ok')"],
                env={"PYTHONUTF8": "0", "PYTHONIOENCODING": "cp1252"},
            )

        self.assertTrue(result.ok)
        environment = popen.call_args.kwargs["env"]
        self.assertEqual(environment["PYTHONUTF8"], "0")
        self.assertEqual(environment["PYTHONIOENCODING"], "cp1252")

    def test_legacy_powershell_command_sets_console_output_to_utf8_without_shell(self):
        child = _CompletedProcess("Café ✓ 日本語 ção\r\n".encode("utf-8"))
        original = [r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe", "-NoProfile", "-Command", "Write-Output 'unicode'"]
        with (
            patch("ordax_dev_agent.process_runner.sys", SimpleNamespace(platform="win32", frozen=False)),
            patch("ordax_dev_agent.process_runner.subprocess.Popen", return_value=child) as popen,
        ):
            result = run_command(original)

        self.assertTrue(result.ok)
        launched = popen.call_args.args[0]
        self.assertEqual(launched[:3], original[:3])
        self.assertTrue(
            launched[3].startswith(
                "[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new($false); "
            )
        )
        self.assertTrue(launched[3].endswith(original[3]))
        self.assertFalse(popen.call_args.kwargs["shell"])
        self.assertEqual(result.data["command"], original)

    def test_non_command_powershell_modes_are_not_rewritten(self):
        child = _CompletedProcess()
        original = ["powershell.exe", "-NoProfile", "-File", "script.ps1"]
        with (
            patch("ordax_dev_agent.process_runner.sys", SimpleNamespace(platform="win32", frozen=False)),
            patch("ordax_dev_agent.process_runner.subprocess.Popen", return_value=child) as popen,
        ):
            result = run_command(original)

        self.assertTrue(result.ok)
        self.assertEqual(popen.call_args.args[0], original)


if __name__ == "__main__":
    unittest.main()
