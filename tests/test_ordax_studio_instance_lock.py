import tempfile
import unittest
from pathlib import Path

from ordax_studio.instance_lock import SingleInstanceLock


class OrdaxStudioInstanceLockTests(unittest.TestCase):
    def test_only_one_shell_instance_can_hold_lock(self):
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "studio.lock"
            first = SingleInstanceLock(path)
            second = SingleInstanceLock(path)
            self.assertTrue(first.acquire())
            self.assertFalse(second.acquire())
            first.release()
            self.assertTrue(second.acquire())
            second.release()


if __name__ == "__main__":
    unittest.main()
