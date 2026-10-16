from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "ordax-agent-recovery.yml"


class AgentRecoveryGovernanceContractTests(unittest.TestCase):
    def test_self_hosted_recovery_requires_hosted_canonical_main_governance(self) -> None:
        text = WORKFLOW.read_text(encoding="utf-8")
        governance = text.split("  governance:", 1)[1].split("  recover-if-offline:", 1)[0]
        recovery = text.split("  recover-if-offline:", 1)[1]

        self.assertIn("needs: current-head", governance)
        self.assertIn("runs-on: ubuntu-latest", governance)
        self.assertIn("permissions:\n      contents: read", governance)
        self.assertIn("persist-credentials: false", governance)
        self.assertIn("assert-github-main-governance.ps1", governance)
        self.assertIn('-ExpectedMainCommit "${{ github.sha }}"', governance)
        self.assertNotIn("runs-on: [self-hosted", governance)
        self.assertNotIn("contents: write", governance)

        self.assertIn("needs: [current-head, governance]", recovery)
        self.assertIn("needs.current-head.outputs.current == 'true'", recovery)
        self.assertIn("needs.governance.result == 'success'", recovery)
        self.assertIn("runs-on: [self-hosted, Windows, X64, ordax-recovery]", recovery)
        self.assertNotIn("assert-github-main-governance.ps1", recovery)

    def test_recovery_workflow_does_not_gain_write_or_secret_credentials(self) -> None:
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("permissions:\n  contents: read", text)
        self.assertNotIn("contents: write", text)
        self.assertNotIn("${{ secrets.", text)
        self.assertNotIn("persist-credentials: true", text)


if __name__ == "__main__":
    unittest.main()
