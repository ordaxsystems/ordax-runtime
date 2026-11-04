"""Independent Windows OrdaX MCP distribution contracts (no Studio product authority)."""
import re
import subprocess
import tempfile
import tomllib
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
        self.assertIn('ordax_launcher.c', builder)
        self.assertIn("DORDAX_RUNTIME_LAUNCHER=1", builder)
        self.assertIn('Name: "{group}\\\\Conectar dispositivo"', installer)
        self.assertIn('Filename: "{app}\\\\runtime\\\\pythonw.exe"', installer)
        self.assertIn('Parameters: "-m ordax_dev_agent.owner_device_connect_gui"', installer)
        self.assertIn('Name: "{group}\\\\OrdaX MCP"', installer)
        self.assertIn(
            'Parameters: "-m ordax_dev_agent.owner_device_connect_gui --verificar-rede"',
            installer,
        )
        self.assertIn('Name: "{group}\\\\Selecionar dispositivo para sessão"', installer)
        self.assertIn(
            'Parameters: "-m ordax_dev_agent.owner_device_connect_gui --selecionar-sessao"',
            installer,
        )
        self.assertIn("Canonical Owner session device selection UI missing", builder)
        self.assertIn("Private Windows GUI host missing", builder)
        self.assertIn("ordax_dev_agent.owner_device_connect_gui", builder)
        self.assertNotIn("irm ", installer)
        self.assertNotIn("npx ", installer)
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

    def test_clean_windows_install_has_manual_reactivation_shortcuts_without_second_agent(self):
        installer = INNO.read_text(encoding="utf-8")
        launcher = (ROOT / "packaging/windows/ordax_launcher.c").read_text(encoding="utf-8")
        self.assertEqual(
            installer.count('Name: "{group}\\\\Iniciar ou reativar OrdaX MCP"'), 1
        )
        self.assertIn('Name: "{userdesktop}\\\\OrdaX MCP"', installer)
        self.assertIn(
            'Name: "{userdesktop}\\\\OrdaX MCP"; Filename: "{app}\\\\runtime\\\\pythonw.exe"; '
            'Parameters: "-m ordax_dev_agent.owner_device_connect_gui --verificar-rede"',
            installer,
        )
        self.assertNotIn('Name: "{group}\\\\Verificar conexão"', installer)
        self.assertIn('Name: "desktopicon"', installer)
        self.assertIn("Tasks: desktopicon", installer)
        self.assertIn('Filename: "{app}\\\\{#RuntimeExe}"', installer)
        self.assertIn('ValueName: "OrdaX MCP"', installer)
        self.assertIn('Local\\\\ORDAXRuntime', launcher)
        self.assertIn('ERROR_ALREADY_EXISTS', launcher)
        self.assertIn('run_python_child(', launcher)
        self.assertIn('backoff_ms', launcher)
        self.assertIn('ORDAX_RUNTIME_LAUNCHER ? L"OrdaX MCP" : L"ORDAX Studio"', launcher)
        self.assertIn("O runtime privado do OrdaX MCP", launcher)
        # Existing launcher and Runtime own restart/mutex; no extra watchdog.
        self.assertNotIn("Register-ScheduledTask", installer)
        self.assertNotIn("ordax-agent-watchdog", installer)

    def test_local_candidate_requires_clean_sources_and_supports_user_inno(self):
        builder = BUILDER.read_text(encoding="utf-8")
        self.assertIn(
            "git -C $root status --porcelain --untracked-files=all -- . ':(exclude).ordax/platform'", builder,
        )
        self.assertIn(
            'if ($LASTEXITCODE -ne 0 -or $dirty.Count -ne 0)', builder,
        )
        self.assertIn("Runtime source must be clean for candidate packaging", builder)
        self.assertIn('Join-Path $root "pyproject.toml"', builder)
        self.assertNotIn("open('pyproject.toml','rb')", builder)
        self.assertIn('Programs\\Inno Setup 6\\ISCC.exe', builder)
        self.assertIn('Test-Path -LiteralPath $_ -PathType Leaf', builder)
        self.assertIn('MSVC and Windows SDK developer environment required', builder)
        self.assertLess(builder.index('foreach ($nativeTool'), builder.index('Remove-Item $stage'))
        self.assertNotIn(
            '$env:GITHUB_ACTIONS -eq "true" -and $dirty.Count', builder,
        )

    def test_locked_nested_platform_checkout_is_only_source_status_exclusion(self):
        # Reproduce the CI layout without downloading or rebuilding the Platform.
        # Every other untracked file and every tracked mutation must still fail.
        with tempfile.TemporaryDirectory(prefix="ordax-mcp-git-") as temporary:
            repo = Path(temporary) / "runtime"
            repo.mkdir()
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
            tracked = repo / "tracked.py"
            tracked.write_text("original\n", encoding="utf-8")
            subprocess.run(["git", "add", "tracked.py"], cwd=repo, check=True)
            subprocess.run(
                ["git", "-c", "user.name=OrdaX Test",
                 "-c", "user.email=test@ordax.invalid", "commit", "-qm", "init"],
                cwd=repo, check=True,
            )
            platform = repo / ".ordax" / "platform"
            platform.mkdir(parents=True)
            subprocess.run(["git", "init", "-q"], cwd=platform, check=True)
            (platform / "reference.txt").write_text("pinned checkout\n", encoding="utf-8")

            command = [
                "git", "status", "--porcelain", "--untracked-files=all",
                "--", ".", ":(exclude).ordax/platform",
            ]

            def dirty() -> str:
                return subprocess.check_output(command, cwd=repo, text=True)

            self.assertEqual(dirty(), "", "only the pinned checkout is excluded")
            extra = repo / "unexpected-source.py"
            extra.write_text("must block\n", encoding="utf-8")
            self.assertIn("unexpected-source.py", dirty())
            extra.unlink()
            tracked.write_text("modified\n", encoding="utf-8")
            self.assertIn("tracked.py", dirty())

    def test_no_duplicate_agent_or_clobber_of_existing_studio_installation(self):
        installer = INNO.read_text(encoding="utf-8")
        self.assertIn("InitializeSetup()", installer)
        self.assertIn("Programs\\ORDAX\\ORDAX Runtime.exe", installer)
        self.assertIn("Programs\\ORDAX\\ORDAX Studio.exe", installer)
        self.assertIn("ordax-upgrade-quiesce.ps1", installer)
        self.assertIn("PrepareToInstall(var NeedsRestart: Boolean)", installer)
        self.assertIn("InitializeUninstall()", installer)
        # Inno MsgBox is not silenced by /SUPPRESSMSGBOXES. An error must
        # block uninstall without hanging a headless runner or customer host.
        self.assertIn("SuppressibleMsgBox(", installer)
        self.assertNotIn("    MsgBox(", installer)
        self.assertIn("ORDAX_MCP_QUIESCE_FAILED", installer)
        self.assertIn('ValueName: "OrdaX MCP"', installer)
        self.assertNotIn('ValueName: "ORDAX Runtime"', installer)
        self.assertIn('Filename: "{app}\\{#RuntimeExe}"; Flags: nowait runhidden', installer)
        self.assertNotIn("OrdaX Dev Agent.lnk", installer)
        self.assertNotIn("Register-ScheduledTask", installer)
        self.assertNotIn("DeleteFile", installer)

    def test_runtime_quiesce_only_tolerates_confirmed_exited_process_races(self):
        script = (
            ROOT / "scripts" / "windows" / "ordax-upgrade-quiesce.ps1"
        ).read_text(encoding="utf-8")
        self.assertIn("$current.CreationDate -ne $process.CreationDate", script)
        self.assertIn("ORDAX_UPGRADE_IDENTITY_CHANGED", script)
        self.assertIn("ORDAX_UPGRADE_PROCESS_PATH_UNVERIFIED", script)
        self.assertIn(
            "Stop-Process -InputObject $handle -Force -ErrorAction Stop",
            script,
        )
        self.assertIn(
            'throw "ORDAX packaged processes are still running',
            script,
        )
        # All three transient gaps (Get-Process, executable path, Stop-
        # Process) must re-query exact PID and continue only when absent.
        self.assertEqual(
            script.count(
                'if (-not (Get-CimInstance Win32_Process '
                '-Filter "ProcessId = $pidValue" -ErrorAction Stop))'
            ),
            3,
        )
        self.assertNotIn("Stop-Process -Name", script)
        self.assertNotIn("taskkill", script)
        self.assertNotIn("SilentlyContinue", script)

    def test_normal_wizard_offers_explicit_connect_but_silent_install_never_opens_oauth(self):
        installer = INNO.read_text(encoding="utf-8")
        run_section = installer.split("[Run]", 1)[1].split("[Code]", 1)[0]
        entries = [
            line for line in run_section.splitlines()
            if line.strip().startswith('Filename: ')
        ]
        self.assertEqual(len(entries), 2, "One Runtime + one optional Owner GUI only")
        self.assertIn('Filename: "{app}\\{#RuntimeExe}"', entries[0])
        self.assertIn('Flags: nowait runhidden', entries[0])
        connect = entries[1]
        self.assertIn('Filename: "{app}\\runtime\\pythonw.exe"', connect)
        self.assertIn('Parameters: "-m ordax_dev_agent.owner_device_connect_gui"', connect)
        self.assertIn('Description: "Conectar este computador ao OrdaX MCP"', connect)
        for flag in ("postinstall", "nowait", "skipifsilent"):
            self.assertRegex(connect, r"\b" + flag + r"\b")
        self.assertNotIn("runhidden", connect)
        self.assertNotIn("shellexec", connect)
        self.assertNotIn("Unchecked", connect)
        # No standalone OAuth token injection, grant creation or duplicate
        # agent is performed directly by the Inno installer itself.
        self.assertNotIn("product_access_token", run_section)
        self.assertNotIn("owner_grants_cli", run_section)

    def test_candidate_is_proven_from_exact_main_without_rerunning_unrelated_changes(self):
        workflow = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("paths: &mcp_candidate_paths", workflow)
        self.assertIn("  push:\n    branches:\n      - main\n    paths: *mcp_candidate_paths", workflow)
        self.assertEqual(workflow.count("paths: &mcp_candidate_paths"), 1)
        self.assertEqual(workflow.count("paths: *mcp_candidate_paths"), 1)
        self.assertIn("  workflow_dispatch:", workflow)
        self.assertIn("group: ordax-mcp-candidate-${{ github.ref }}", workflow)
        self.assertIn("cancel-in-progress: false", workflow)
        self.assertIn("permissions:\n  contents: read", workflow)
        # The MCP builder installs the entire package with pip, so the
        # GitHub trigger must track ALL package roots from the packaging
        # authority, not an incomplete list of selected Python entrypoints.
        package_config = tomllib.loads(
            (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        )
        packages = package_config["tool"]["setuptools"]["packages"]["find"]["include"]
        self.assertTrue(packages and all(p.endswith("*") for p in packages))
        expected_roots = {f'      - "{p}/**"' for p in packages}
        trigger_block = workflow.split(
            "paths: &mcp_candidate_paths", 1
        )[1].split("  # Only package-relevant changes", 1)[0]
        watched_roots = {
            line for line in trigger_block.splitlines()
            if line.strip().startswith("- ") and line.strip().endswith('/**"')
        }
        self.assertEqual(watched_roots, expected_roots)
        for changed_input in (
            "ordax_dev_agent/remote_protocol.py",
            "ordax_dev_agent/product_device_control_plane.py",
            "ordax_dev_agent/terminal_outbox.py",
            "ordax_dev_agent/models.py",
        ):
            root = changed_input.split("/", 1)[0]
            self.assertIn(f'      - "{root}*/**"', trigger_block)
        self.assertIn(
            '      - "scripts/windows/resolve-control-plane-provenance.py"',
            trigger_block,
        )
        self.assertNotIn('      - "docs/**"', trigger_block)
        self.assertNotIn('      - "tests/test_product_ingress_failover.py"', trigger_block)
        self.assertIn("runtime_source_commit -cne $env:GITHUB_SHA", workflow)
        self.assertIn("signed_public_release -ne $false", workflow)
        self.assertIn("retention-days: 3", workflow)
        self.assertNotIn("gh release create", workflow)

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
