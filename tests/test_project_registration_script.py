import unittest
from pathlib import Path


class ProjectRegistrationScriptTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(__file__).resolve().parents[1]
        self.script = (
            self.root / "scripts" / "windows" / "ordax-project-register.ps1"
        ).read_text(encoding="utf-8")

    def test_registration_is_local_and_preserves_existing_settings(self) -> None:
        self.assertIn(
            '$settingsPath = Join-Path $stateDir "agent-settings.json"',
            self.script,
        )
        self.assertIn("ConvertFrom-Json", self.script)
        self.assertIn(
            "$settings.projects | Add-Member -NotePropertyName $Slug",
            self.script,
        )
        self.assertNotIn("supabase", self.script.lower())
        self.assertNotIn("Invoke-WebRequest", self.script)
        self.assertNotIn("git clone", self.script.lower())

    def test_project_path_and_apps_are_validated(self) -> None:
        self.assertIn("Resolve-Path -LiteralPath $Path", self.script)
        self.assertIn('[string[]]$Apps = @("unity", "blender")', self.script)
        self.assertIn('[string]$value -split "[,;]"', self.script)
        self.assertIn('$allowedApps = @("unity", "blender")', self.script)
        self.assertIn('Unsupported application', self.script)
        self.assertIn(
            "Unity project needs Assets and ProjectSettings directories",
            self.script,
        )
        self.assertIn(
            "BlenderFile must remain inside the registered project",
            self.script,
        )

    def test_comma_separated_apps_from_powershell_file_invocation_are_supported(self) -> None:
        self.assertIn('$normalizedApps = @(', self.script)
        self.assertIn('$Apps = @($normalizedApps)', self.script)

    def test_restart_is_explicit_and_refuses_busy_agent_by_default(self) -> None:
        self.assertIn("[switch]$RestartAgent", self.script)
        self.assertIn("[switch]$ForceRestart", self.script)
        self.assertIn('$status.runtime.state -eq "busy"', self.script)
        self.assertIn(
            "Project was registered, but the Agent is busy.",
            self.script,
        )
        self.assertIn("Start-ScheduledTask -TaskName $TaskName", self.script)


if __name__ == "__main__":
    unittest.main()
