"""Independent Windows OrdaX MCP distribution contracts (no Studio product authority)."""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BUILDER = ROOT / "scripts/windows/build-ordax-mcp.ps1"
INNO = ROOT / "packaging/windows/ordax-mcp.iss"
WORKFLOW = ROOT / ".github/workflows/windows-mcp-candidate.yml"


class WindowsMcpDistributionTests(unittest.TestCase):
    def test_mcp_has_independent_package_without_studio_build(self):
        builder = BUILDER.read_text(encoding="utf-8")
        installer = INNO.read_text(encoding="utf-8")
        self.assertIn("OrdaX-MCP-Setup-{#AppVersion}-x64", installer)
        self.assertIn("AppName=OrdaX MCP", installer)
        self.assertIn('AppId={{B5845369-1D88-42D2-BD77-D0F73C5DB5AE}', installer)
        self.assertNotIn("0D31F22D-8451-4CF4-9E34-F0D4D857F55F", installer)
        self.assertIn("DefaultDirName={localappdata}\\Programs\\OrdaX MCP", installer)
        self.assertIn('"ordax_launcher.c"', builder)
        self.assertIn("DORDAX_RUNTIME_LAUNCHER=1", builder)
        self.assertIn("ordax_dev_agent.owner_grants_cli", builder)
        self.assertIn("assert-runtime-package-capabilities.ps1", builder)
        self.assertIn("control-plane-source.lock.json", builder)
        for forbidden in ("studio-source.lock.json", "ORDAX_STUDIO_APP_SOURCE",
                          "verify-studio-presentation-handoff", "ordax-apps",
                          'DORDAX_RUNTIME_LAUNCHER=0'):
            self.assertNotIn(forbidden, builder)
        self.assertIn("studio_required = $false", builder)
        self.assertIn("signed_public_release = $false", builder)
        self.assertIn("Standalone OrdaX MCP production requires a separate signed release gate", builder)
        self.assertIn("Remove-Item $stage -Recurse -Force", builder)
        self.assertNotIn("presentation\\ORDAX Studio.exe", installer)
        self.assertNotIn("ORDAX Studio.exe\"; Flags: nowait", installer)

    def test_no_duplicate_agent_or_clobber_of_existing_studio_installation(self):
        installer = INNO.read_text(encoding="utf-8")
        self.assertIn("InitializeSetup()", installer)
        self.assertIn("Programs\\ORDAX\\ORDAX Runtime.exe", installer)
        self.assertIn("Programs\\ORDAX\\ORDAX Studio.exe", installer)
        self.assertIn("ordax-upgrade-quiesce.ps1", installer)
        self.assertIn("PrepareToInstall(var NeedsRestart: Boolean)", installer)
        self.assertIn("InitializeUninstall()", installer)
        self.assertIn('ValueName: "OrdaX MCP"', installer)
        self.assertNotIn('ValueName: "ORDAX Runtime"', installer)
        self.assertIn('Filename: "{app}\\{#RuntimeExe}"; Flags: nowait runhidden', installer)
        self.assertNotIn("OrdaX Dev Agent.lnk", installer)
        self.assertNotIn("Register-ScheduledTask", installer)
        self.assertNotIn("DeleteFile", installer)

    def test_candidate_build_proof_is_real_windows_and_never_public_release(self):
        workflow = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("  pull_request:", workflow)
        self.assertIn("  workflow_dispatch:", workflow)
        self.assertIn("windows-latest", workflow)
        self.assertIn("choco install innosetup", workflow)
        self.assertIn("build-ordax-mcp.ps1", workflow)
        self.assertIn("OrdaX-MCP-Setup-", workflow)
        self.assertIn("ordax-platform", workflow)
        self.assertIn("ref: ${{ steps.lock.outputs.commit }}", workflow)
        self.assertIn("validation-candidate", workflow)
        self.assertIn("runtime\\python.exe", workflow)
        self.assertIn("unins000.exe", workflow)
        self.assertIn("assert-runtime-package-capabilities.ps1", workflow)
        self.assertNotIn("release create", workflow)
        self.assertNotIn("windows-production-release", workflow)
        self.assertNotIn("ORDAX_STUDIO_APP_SOURCE", workflow)


if __name__ == "__main__":
    unittest.main()
