"""UTF-8 is the explicit text boundary for terminal/process output."""
from __future__ import annotations

import sys
import unittest

from ordax_dev_agent.process_runner import run_command


class ProcessRunnerEncodingTests(unittest.TestCase):
    def test_explicit_utf8_bytes_round_trip_without_locale_mojibake(self) -> None:
        text = "Café ✓ 日本語 ção"
        script = (
            "import sys; "
            "text='Café ✓ 日本語 ção'; "
            "sys.stdout.buffer.write((text+'\\n').encode('utf-8')); "
            "sys.stderr.buffer.write(('ERR '+text+'\\n').encode('utf-8'))"
        )
        result = run_command([sys.executable, "-c", script], timeout=10)
        self.assertTrue(result.ok, result.summary)
        self.assertEqual(result.data["stdout"], text + "\n")
        self.assertEqual(result.data["stderr"], "ERR " + text + "\n")
        self.assertEqual(result.data["output_encoding"], "utf-8")
        self.assertNotIn("decode_errors", result.data)

    def test_external_terminal_never_inherits_private_host_grants(self) -> None:
        import json
        import os
        from unittest.mock import patch
        from ordax_dev_agent.process_environment import external_process_environment

        inherited = {
            "ORDAX_DESKTOP_SURFACE_BRIDGE_TOKEN": "private-canary",
            "ORDAX_RUNTIME_STATUS_NONCE": "private-nonce",
            "ORDAX_RUNTIME_HOST_PROFILE": "ordax-desktop",
            "ORDAX_MISSION_ID": "inherited",
            "_PYI_APPLICATION_HOME_DIR": "frozen-private",
            "PYINSTALLER_RESET_ENVIRONMENT": "1",
            "ELECTRON_RUN_AS_NODE": "1",
            "ORDAX_TEST_PUBLIC_ENV": "not-inherited",
            "PUBLIC_TASK_VALUE": "kept",
        }
        with patch.dict(os.environ, inherited, clear=False):
            projected = external_process_environment({"ORDAX_MISSION_ID": "task-explicit"})
            self.assertNotIn("ORDAX_DESKTOP_SURFACE_BRIDGE_TOKEN", projected)
            self.assertNotIn("ORDAX_RUNTIME_STATUS_NONCE", projected)
            self.assertNotIn("ORDAX_RUNTIME_HOST_PROFILE", projected)
            self.assertNotIn("_PYI_APPLICATION_HOME_DIR", projected)
            self.assertNotIn("PYINSTALLER_RESET_ENVIRONMENT", projected)
            self.assertNotIn("ELECTRON_RUN_AS_NODE", projected)
            self.assertEqual(external_process_environment({"ELECTRON_RUN_AS_NODE": "1"})["ELECTRON_RUN_AS_NODE"], "1")
            self.assertEqual(projected["ORDAX_MISSION_ID"], "task-explicit")
            self.assertEqual(projected["PUBLIC_TASK_VALUE"], "kept")
            script = (
                "import json,os;print(json.dumps({"
                "'private':os.getenv('ORDAX_DESKTOP_SURFACE_BRIDGE_TOKEN'),"
                "'nonce':os.getenv('ORDAX_RUNTIME_STATUS_NONCE'),"
                "'host':os.getenv('ORDAX_RUNTIME_HOST_PROFILE'),"
                "'mission':os.getenv('ORDAX_MISSION_ID'),"
                "'public':os.getenv('PUBLIC_TASK_VALUE')}))"
            )
            result = run_command(
                [sys.executable, "-c", script], timeout=10,
                env={"ORDAX_MISSION_ID": "task-explicit"},
            )
        self.assertTrue(result.ok, result.summary)
        observed = json.loads(result.data["stdout"])
        self.assertEqual(observed, {
            "private": None, "nonce": None, "host": None,
            "mission": "task-explicit", "public": "kept",
        })

    def test_non_utf8_output_fails_closed_instead_of_guessing_encoding(self) -> None:
        script = "import sys; sys.stdout.buffer.write(bytes([0xff, 0xfe]))"
        result = run_command([sys.executable, "-c", script], timeout=10)
        self.assertFalse(result.ok)
        self.assertEqual(result.summary, "command output is not valid UTF-8")
        self.assertEqual(result.data["error_code"], "process_output_encoding_invalid")
        self.assertEqual(result.data["output_encoding"], "utf-8")
        self.assertEqual(result.data["decode_errors"][0]["stream"], "stdout")


if __name__ == "__main__":
    unittest.main()
