from pathlib import Path
import unittest


class OwnerGrantCutoverContractTests(unittest.TestCase):
    def test_cutover_document_requires_real_oauth_client_and_fail_closed(self):
        text = Path("docs/OWNER-MCP-GRANT-AUTHORITY-CUTOVER.md").read_text(encoding="utf-8")
        self.assertIn("real consented Product MCP client", text)
        self.assertIn("fail closed", text)
        self.assertIn("no production source references retired", text)


if __name__ == "__main__":
    unittest.main()
