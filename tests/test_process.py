import sys
import unittest

from mcp_blender_unity.process import run_process


class ProcessTests(unittest.TestCase):
    def test_success_is_structured(self) -> None:
        result = run_process([sys.executable, "-c", "print('ok')"], timeout_seconds=10)
        self.assertTrue(result["ok"])
        self.assertFalse(result["timed_out"])
        self.assertEqual(result["stdout"].strip(), "ok")

    def test_timeout_is_failure(self) -> None:
        result = run_process(
            [sys.executable, "-c", "import time; time.sleep(2)"], timeout_seconds=1
        )
        self.assertFalse(result["ok"])
        self.assertTrue(result["timed_out"])


if __name__ == "__main__":
    unittest.main()
