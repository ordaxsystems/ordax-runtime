from pathlib import Path
import unittest


class OwnerGrantObserveProfileContractTests(unittest.TestCase):
    def test_runtime_docs_keep_observe_profile_read_only(self):
        text = Path("docs/OWNER-MCP-GRANT-AUTHORITY.md").read_text(encoding="utf-8")
        for required in (
            "computer.access_status",
            "computer.active_window",
            "computer.processes",
            "computer.screen_info",
            "computer.windows",
        ):
            self.assertIn(required, text)
        for forbidden in (
            "computer.click`",
            "computer.type`",
            "computer.terminate_process`",
        ):
            self.assertNotIn(forbidden, text)


if __name__ == "__main__":
    unittest.main()
