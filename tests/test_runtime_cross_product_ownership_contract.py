"""Validate Runtime documentation references current canonical owners.

Read-only source assertions, not deployment/E2E proofs.
"""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]

class RuntimeCrossProductOwnershipTest(unittest.TestCase):
    def test_one_owner_for_each_product_component(self):
        doc = (ROOT / "docs/ORDAX_STUDIO_MCP_ARCHITECTURE.md").read_text(encoding="utf-8")
        for repo in ("ordaxsystems/ordax-platform", "ordaxsystems/ordax-runtime",
                     "ordaxsystems/ordax-apps", "ordaxsystems/ordax-os"):
            with self.subTest(repo=repo):
                self.assertIn(repo, doc)
        self.assertNotIn("ordaxsystems/ordax-control-plane` (futuro", doc)
        self.assertNotIn("washingtonmsdj/prototipo-ordax-os", doc)

    def test_distinct_space_project_and_local_workspace(self):
        doc = (ROOT / "docs/ORDAX_STUDIO_MCP_ARCHITECTURE.md").read_text(encoding="utf-8")
        for marker in ("ordax_spaces.space_id", "ordax_projects.project_id",
                       "computer_processes", "process_list", "terminal_exec",
                       "Workspace", "ordax_profile"):
            with self.subTest(marker=marker):
                self.assertIn(marker, doc)

if __name__ == "__main__":
    unittest.main()
