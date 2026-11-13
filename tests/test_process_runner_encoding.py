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
