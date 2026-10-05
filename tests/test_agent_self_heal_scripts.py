import unittest
from pathlib import Path


class AgentSelfHealScriptTests(unittest.TestCase):
    def test_launcher_and_recovery_use_shared_update_policy(self) -> None:
        root = Path(__file__).resolve().parents[1]
        launcher = (root / "scripts" / "windows" / "ordax-agent-start.cmd").read_text(
            encoding="utf-8"
        )
        recovery = (
            root / ".github" / "workflows" / "ordax-agent-recovery.yml"
        ).read_text(encoding="utf-8")

        self.assertIn(
            "ordax_dev_agent.update_policy --check-clean",
            launcher,
        )
        self.assertIn(
            '$policyScript = Join-Path $env:GITHUB_WORKSPACE '
            '"ordax_dev_agent\\update_policy.py"',
            recovery,
        )
        self.assertIn('"--check-clean"', recovery)
        self.assertIn('"--compare-install-contract"', recovery)
        self.assertNotIn(
            "$venvPython -m ordax_dev_agent.update_policy",
            recovery,
        )
        for text in (launcher, recovery):
            self.assertNotIn("diff-files --quiet", text)
            self.assertNotIn("diff-index --cached", text)

    def test_recovery_updates_only_idle_fast_forwardable_agent(self) -> None:
        root = Path(__file__).resolve().parents[1]
        recovery = (
            root / ".github" / "workflows" / "ordax-agent-recovery.yml"
        ).read_text(encoding="utf-8")

        self.assertIn(
            "merge-base --is-ancestor $beforeHead $remoteHead",
            recovery,
        )
        self.assertIn(
            '$activeState -eq "busy" -and -not $explicitRecovery',
            recovery,
        )
        self.assertIn(
            "update deferred without interruption",
            recovery,
        )
        self.assertIn(
            "performing safe maintenance update",
            recovery,
        )
        self.assertIn(
            "if ($installContractChanged)",
            recovery,
        )
        self.assertIn(
            "& $venvPython -m pip install -e $repo",
            recovery,
        )

    def test_launcher_control_flow_is_structurally_intact(self) -> None:
        root = Path(__file__).resolve().parents[1]
        launcher = (
            root / "scripts" / "windows" / "ordax-agent-start.cmd"
        ).read_text(encoding="utf-8")

        self.assertEqual(1, launcher.splitlines().count(":run"))
        self.assertEqual(1, launcher.splitlines().count(":safe_update"))
        self.assertEqual(1, launcher.splitlines().count(":increase_backoff"))

        expected_run_block = (
            ':run\n'
            'if "%SKIP_SAFE_UPDATE%"=="0" (\n'
            '  call :safe_update\n'
            ') else (\n'
            '  set "SKIP_SAFE_UPDATE=0"\n'
            ')\n'
        )
        self.assertIn(expected_run_block, launcher)

        run_index = launcher.index(":run")
        compile_index = launcher.index(
            '"%PYTHON%" -m compileall',
            run_index,
        )
        safe_label_index = launcher.index("\n:safe_update\n")
        self.assertLess(run_index, compile_index)
        self.assertLess(compile_index, safe_label_index)

        self.assertIn(
            'ordax_dev_agent.update_policy --check-clean "%ROOT%"',
            launcher,
        )
        self.assertNotIn("diff-files --quiet", launcher)
        self.assertNotIn("diff-index --cached", launcher)

    def test_emergency_recovery_updates_remote_tracking_ref_before_merge(self) -> None:
        root = Path(__file__).resolve().parents[1]
        recovery = (
            root / "scripts" / "windows" / "ordax-emergency-recover.ps1"
        ).read_text(encoding="utf-8")

        self.assertIn('refs/heads/${Branch}:$remoteRef', recovery)
        self.assertIn('refs/remotes/origin/$Branch', recovery)
        self.assertIn('fetch origin $fetchRefspec', recovery)
        self.assertIn('merge --ff-only $remoteRef', recovery)
        self.assertNotIn('fetch origin $Branch', recovery)
        self.assertIn('"ordax_device_agent"', recovery)
        self.assertIn('Stop-ScheduledTask -TaskName $taskName', recovery)



    def test_watchdog_requests_restart_without_cim_dependency(self) -> None:
        root = Path(__file__).resolve().parents[1]
        watchdog = (
            root / "scripts" / "windows" / "ordax-agent-watchdog.ps1"
        ).read_text(encoding="utf-8")

        self.assertIn("function Request-AgentRestartIfNeeded", watchdog)
        self.assertIn("function Stop-AgentForRecovery", watchdog)
        self.assertIn("function Recover-Agent", watchdog)
        self.assertIn("Stop-Process -Id $AgentPid -Force", watchdog)
        self.assertIn("Invoke-RestMethod -Uri $healthUrl", watchdog)
        self.assertIn(
            "Start-ScheduledTask -TaskName $RestartTaskName",
            watchdog,
        )
        self.assertIn('$task.State -ne "Running"', watchdog)
        self.assertIn('$task.State -eq "Disabled"', watchdog)
        self.assertNotIn("Get-CimInstance", watchdog)
        self.assertNotIn("taskkill.exe", watchdog)

        health_recovery_index = watchdog.index(
            '[void](Recover-Agent "local health endpoint failed'
        )
        health_exit_index = watchdog.index("exit 20", health_recovery_index)
        self.assertLess(health_recovery_index, health_exit_index)

        busy_recovery_index = watchdog.index(
            '[void](Recover-Agent ("job $busyJobId remained busy'
        )
        busy_exit_index = watchdog.index("exit 21", busy_recovery_index)
        self.assertLess(busy_recovery_index, busy_exit_index)

        exit_index = watchdog.index("EXIT parent process ended")
        restart_index = watchdog.index(
            "Request-AgentRestartIfNeeded",
            exit_index,
        )
        self.assertGreater(restart_index, exit_index)

    def test_external_bootstrap_restarts_unexpected_clean_agent_exit(self) -> None:
        root = Path(__file__).resolve().parents[1]
        bootstrap = (
            root / "scripts" / "windows" / "ordax-agent-bootstrap.ps1"
        ).read_text(encoding="utf-8")

        self.assertIn('if ($code -eq 0)', bootstrap)
        self.assertIn('AGENT_RESTART clean-exit code=0', bootstrap)
        self.assertIn('continue', bootstrap)
        self.assertNotIn('if ($code -eq 0) {\n        exit 0', bootstrap)

    def test_external_bootstrap_starts_health_before_safe_update(self) -> None:
        root = Path(__file__).resolve().parents[1]
        bootstrap = (
            root / "scripts" / "windows" / "ordax-agent-bootstrap.ps1"
        ).read_text(encoding="utf-8")

        loop_index = bootstrap.index("while ($true)")
        start_index = bootstrap.index(
            '& $python -m ordax_dev_agent.main',
            loop_index,
        )
        update_index = bootstrap.index(
            '[void](Invoke-SafeUpdate)',
            start_index,
        )
        self.assertLess(start_index, update_index)
        self.assertIn(
            "never in front of initial health",
            bootstrap,
        )


    def test_external_bootstrap_recovers_cloudflare_identity_without_runner(self) -> None:
        root = Path(__file__).resolve().parents[1]
        bootstrap = (
            root / "scripts" / "windows" / "ordax-agent-bootstrap.ps1"
        ).read_text(encoding="utf-8")

        self.assertIn("function Ensure-ConfiguredDeviceIdentity", bootstrap)
        self.assertIn("ordax_dev_agent.device_setup", bootstrap)
        self.assertIn("--control-plane-url", bootstrap)
        self.assertIn("device-token.cloudflare-v3.txt", bootstrap)
        self.assertIn("cloudflare-v3", bootstrap)
        self.assertIn('"device_id"', bootstrap)
        self.assertNotIn("development-v2", bootstrap)
        self.assertNotIn("supabase", bootstrap.lower())
        self.assertNotIn("device-token.development-v2.txt", bootstrap)

        loop_index = bootstrap.index("while ($true)")
        identify_index = bootstrap.index(
            "[void](Ensure-ConfiguredDeviceIdentity)",
            loop_index,
        )
        launch_index = bootstrap.index(
            "& $python -m ordax_dev_agent.main",
            identify_index,
        )
        self.assertLess(identify_index, launch_index)


    def test_all_managed_main_fetches_write_remote_tracking_ref(self) -> None:
        root = Path(__file__).resolve().parents[1]
        launcher = (
            root / "scripts" / "windows" / "ordax-agent-start.cmd"
        ).read_text(encoding="utf-8")
        recovery = (
            root / ".github" / "workflows" / "ordax-agent-recovery.yml"
        ).read_text(encoding="utf-8")
        agent_actions = (
            root / "ordax_dev_agent" / "agent_actions.py"
        ).read_text(encoding="utf-8")

        self.assertIn(
            'refs/heads/%BRANCH%:refs/remotes/origin/%BRANCH%',
            launcher,
        )
        self.assertIn(
            'refs/heads/main:refs/remotes/origin/main',
            recovery,
        )
        self.assertIn(
            'refs/heads/$branch:refs/remotes/origin/$branch',
            recovery,
        )
        self.assertIn(
            'f"refs/heads/{branch}:refs/remotes/origin/{branch}"',
            agent_actions,
        )

        self.assertNotIn(
            'fetch --quiet origin "%BRANCH%"',
            launcher,
        )
        self.assertNotIn(
            'git fetch --quiet origin main',
            recovery,
        )
        self.assertNotIn(
            '[*git, "fetch", "--quiet", "origin", branch]',
            agent_actions,
        )

    def test_external_bootstrap_is_independent_from_managed_checkout_launcher(self) -> None:
        root = Path(__file__).resolve().parents[1]
        bootstrap = (
            root / "scripts" / "windows" / "ordax-agent-bootstrap.ps1"
        ).read_text(encoding="utf-8")
        installer = (
            root / "scripts" / "windows" / "ordax-agent-bootstrap-install.ps1"
        ).read_text(encoding="utf-8")
        agent_installer = (
            root / "scripts" / "windows" / "ordax-agent-install.ps1"
        ).read_text(encoding="utf-8")

        self.assertIn(
            '$stateDir = Join-Path $env:LOCALAPPDATA "OrdaX\\DevAgent"',
            bootstrap,
        )
        self.assertIn(
            '$bootstrapDir = Join-Path $stateDir "bootstrap"',
            bootstrap,
        )
        self.assertIn(
            'refs/heads/${Branch}:$remoteRef',
            bootstrap,
        )
        self.assertIn(
            'function Read-GitValue([string[]]$CommandArgs)',
            bootstrap,
        )
        self.assertIn(
            'Read-GitValue -CommandArgs @("rev-parse", "HEAD")',
            bootstrap,
        )
        self.assertIn(
            'Read-GitValue -CommandArgs @("rev-parse", "--abbrev-ref", "HEAD")',
            bootstrap,
        )
        self.assertIn(
            'Read-GitValue -CommandArgs @("rev-parse", $remoteRef)',
            bootstrap,
        )
        self.assertIn(
            'LAUNCH_READY repo=$repoRootResolved',
            bootstrap,
        )
        self.assertIn(
            'merge-base --is-ancestor $beforeHead $remoteHead',
            bootstrap,
        )
        self.assertIn(
            'Restore-ManagedCheckout',
            bootstrap,
        )
        self.assertIn(
            'function Sync-ExternalBootstrapFromRepo',
            bootstrap,
        )
        self.assertIn(
            '[void](Sync-ExternalBootstrapFromRepo)',
            bootstrap,
        )
        self.assertIn(
            '[void][ScriptBlock]::Create($bootstrapText)',
            bootstrap,
        )
        self.assertIn(
            'ast.parse(open(sys.argv[1]',
            bootstrap,
        )
        self.assertNotIn(
            '-m py_compile $policySource',
            bootstrap,
        )
        self.assertIn(
            '--compare-install-contract',
            bootstrap,
        )
        self.assertIn(
            'ordax_dev_agent.main',
            bootstrap,
        )
        self.assertNotIn(
            'if (-not $updated',
            bootstrap,
        )

        self.assertIn(
            '$bootstrapDir = Join-Path $stateDir "bootstrap"',
            installer,
        )
        self.assertIn(
            '$bootstrapPath = Join-Path $bootstrapDir '
            '"ordax-agent-bootstrap.ps1"',
            installer,
        )
        self.assertIn(
            '$policyPath = Join-Path $bootstrapDir "update_policy.py"',
            installer,
        )
        self.assertIn(
            'Set-ScheduledTask -TaskName $TaskName -Action $action',
            installer,
        )

        self.assertIn(
            '$bootstrapInstaller = Join-Path $repoRoot '
            '"scripts\\windows\\ordax-agent-bootstrap-install.ps1"',
            agent_installer,
        )
        self.assertIn(
            '$bootstrapPath = Join-Path $stateDir '
            '"bootstrap\\ordax-agent-bootstrap.ps1"',
            agent_installer,
        )
        self.assertIn(
            'Execute = $powershellPath',
            agent_installer,
        )
        self.assertIn(
            'Argument = $bootstrapArguments',
            agent_installer,
        )
        self.assertIn(
            '-File `"$bootstrapPath`" -RepoRoot `"$repoRoot`"',
            agent_installer,
        )
        self.assertNotIn(
            "Argument = '-m ordax_dev_agent.task_entry'",
            agent_installer,
        )
        self.assertIn(
            'WorkingDirectory = $repoRoot',
            agent_installer,
        )
        self.assertNotIn(
            'Execute = $env:ComSpec',
            agent_installer,
        )
        self.assertIn(
            'Get-Command powershell.exe -ErrorAction Stop',
            agent_installer,
        )

        self.assertIn(
            '$powershellPath = (Get-Command powershell.exe -ErrorAction Stop).Source',
            installer,
        )
        self.assertIn(
            '-Argument $bootstrapArguments',
            installer,
        )
        self.assertIn(
            '-File `"$bootstrapPath`" -RepoRoot `"$repoRootResolved`"',
            installer,
        )
        self.assertIn(
            "-WorkingDirectory $repoRootResolved",
            installer,
        )
        self.assertNotIn(
            "-Argument '-m ordax_dev_agent.task_entry'",
            installer,
        )

    def test_agent_task_has_logon_and_periodic_self_recovery_triggers(self) -> None:
        root = Path(__file__).resolve().parents[1]
        installer = (
            root / "scripts" / "windows" / "ordax-agent-install.ps1"
        ).read_text(encoding="utf-8")
        bootstrap_installer = (
            root / "scripts" / "windows" / "ordax-agent-bootstrap-install.ps1"
        ).read_text(encoding="utf-8")

        for text in (installer, bootstrap_installer):
            self.assertIn("New-ScheduledTaskTrigger -AtLogOn", text)
            self.assertIn("-RepetitionInterval (New-TimeSpan -Minutes 1)", text)
            self.assertIn("-RepetitionDuration (New-TimeSpan -Days 3650)", text)
            self.assertIn("@($logonTrigger, $maintenanceTrigger)", text)

        self.assertIn("-Trigger $triggers", installer)
        self.assertIn(
            "Set-ScheduledTask -TaskName $TaskName -Action $action -Trigger $triggers",
            bootstrap_installer,
        )
        self.assertIn('MultipleInstances = "IgnoreNew"', installer)

    def test_task_entry_logs_before_importing_full_agent(self) -> None:
        root = Path(__file__).resolve().parents[1]
        entry = (
            root / "ordax_dev_agent" / "task_entry.py"
        ).read_text(encoding="utf-8")
        main = (
            root / "ordax_dev_agent" / "main.py"
        ).read_text(encoding="utf-8")

        self.assertIn("ENTRY_START", entry)
        self.assertIn("IMPORT_MAIN_START", entry)
        self.assertIn("IMPORT_MAIN_OK", entry)
        self.assertIn("task-entry.log", entry)
        self.assertIn("agent-startup.log", main)

        server_index = main.index("start_status_server(status_payload)")
        registry_import_index = main.index("from .actions import ActionRegistry")
        self.assertLess(server_index, registry_import_index)
        self.assertIn("STATUS_SERVER_READY port=8765", main)

    def test_recovery_retargets_task_to_external_bootstrap(self) -> None:
        root = Path(__file__).resolve().parents[1]
        recovery = (
            root / ".github" / "workflows" / "ordax-agent-recovery.yml"
        ).read_text(encoding="utf-8")

        self.assertIn(
            'ordax-agent-bootstrap-install.ps1',
            recovery,
        )
        self.assertIn(
            '-RetargetTask',
            recovery,
        )
        retarget_index = recovery.index(
            'ordax-agent-bootstrap-install.ps1'
        )
        start_index = recovery.index(
            'Start-ScheduledTask -TaskName $taskName',
            retarget_index,
        )
        self.assertLess(retarget_index, start_index)


    def test_unity_refresh_recovery_has_no_cim_dependency(self) -> None:
        root = Path(__file__).resolve().parents[1]
        refresh = (
            root / "scripts" / "windows" / "unity-editor-refresh.ps1"
        ).read_text(encoding="utf-8")

        self.assertIn("Get-Process Unity", refresh)
        self.assertIn("MainWindowTitle", refresh)
        self.assertNotIn("Get-CimInstance", refresh)

    def test_resilience_status_avoids_win32_cim_and_reports_triggers(self) -> None:
        root = Path(__file__).resolve().parents[1]
        status = (
            root / "scripts" / "windows" / "ordax-resilience-status.ps1"
        ).read_text(encoding="utf-8")

        self.assertNotIn("Get-CimInstance", status)
        self.assertIn('Get-Service -Name "actions.runner.*"', status)
        self.assertIn("triggers = @(", status)
        self.assertIn("repetition_interval", status)
        self.assertIn("repetition_duration", status)

    def test_resilience_status_reports_external_bootstrap(self) -> None:
        root = Path(__file__).resolve().parents[1]
        status = (
            root / "scripts" / "windows" / "ordax-resilience-status.ps1"
        ).read_text(encoding="utf-8")

        self.assertIn("external_bootstrap", status)
        self.assertIn("script_exists", status)
        self.assertIn("policy_exists", status)
        self.assertIn("scheduled_task", status)
        self.assertIn("actions = @(", status)


if __name__ == "__main__":
    unittest.main()
