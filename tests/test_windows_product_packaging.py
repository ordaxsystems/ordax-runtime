from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class WindowsProductPackagingTests(unittest.TestCase):
    def test_native_launchers_are_product_entrypoints(self) -> None:
        launcher = (ROOT / "packaging" / "windows" / "ordax_launcher.c").read_text(encoding="utf-8")
        installer = (ROOT / "packaging" / "windows" / "ordax-studio.iss").read_text(encoding="utf-8")
        self.assertIn('#define AppName "ORDAX Studio"', installer)
        self.assertIn('#define AppExeName "ORDAX Studio.exe"', installer)
        self.assertIn('#define LegacyAppExeName "ORDAX Dev.exe"', installer)
        self.assertIn("ORDAX Runtime.exe", installer)
        self.assertIn('L"%ls\\\\workbench\\\\ORDAX Workbench.exe"', launcher)
        self.assertIn("run_executable_child", launcher)
        self.assertNotIn('L"ordax_studio.product_web_desktop"', launcher)
        self.assertNotIn("ordax_chat_app", launcher)
        self.assertIn("ordax_device_agent.main", launcher)
        self.assertNotIn('L"ORDAX Dev"', launcher)

    def test_installer_preserves_app_id_while_migrating_branding(self) -> None:
        installer = (ROOT / "packaging" / "windows" / "ordax-studio.iss").read_text(encoding="utf-8")
        self.assertIn("AppId={{0D31F22D-8451-4CF4-9E34-F0D4D857F55F}", installer)
        self.assertIn("DefaultDirName={localappdata}\\Programs\\ORDAX Studio", installer)
        self.assertIn("OutputBaseFilename=ORDAX-Studio-Setup-{#AppVersion}-x64", installer)
        self.assertIn('Name: "{group}\\ORDAX Studio"', installer)
        self.assertIn('Name: "{userdesktop}\\ORDAX Studio"', installer)
        self.assertIn('Name: "{group}\\ORDAX Dev.lnk"', installer)
        self.assertIn('Name: "{userdesktop}\\ORDAX Dev.lnk"', installer)

    def test_launchers_expose_cooperative_shutdown_for_updates(self) -> None:
        launcher = (ROOT / "packaging" / "windows" / "ordax_launcher.c").read_text(encoding="utf-8")
        installer = (ROOT / "packaging" / "windows" / "ordax-studio.iss").read_text(encoding="utf-8")
        self.assertIn("ORDAXRuntimeShutdown", launcher)
        self.assertIn("ORDAXStudioShutdown", launcher)
        self.assertIn("CreateEventW", launcher)
        self.assertIn("WaitForMultipleObjects", launcher)
        self.assertIn("TerminateJobObject", launcher)
        self.assertIn("LegacyAppExeName", installer)
        self.assertIn("StopOrdaxProcess('Local\\ORDAXStudioShutdown', '{#LegacyAppExeName}')", installer)

    def test_installer_is_host_only_and_has_no_chat_browser_bundle(self) -> None:
        installer = (ROOT / "packaging" / "windows" / "ordax-studio.iss").read_text(encoding="utf-8").lower()
        build = (ROOT / "scripts" / "windows" / "build-ordax-studio-product.ps1").read_text(encoding="utf-8")
        self.assertNotIn("codex", installer)
        self.assertNotIn("browser_extension", build)
        self.assertNotIn("ordax_chat_app", build)
        self.assertIn("microsoftedgewebview2setup.exe", installer)
        self.assertIn("closeapplications=no", installer)
        self.assertIn("software\\microsoft\\windows\\currentversion\\run", installer)

    def test_installer_retires_legacy_scheduled_runtime_without_deleting_state(self) -> None:
        installer = (ROOT / "packaging" / "windows" / "ordax-studio.iss").read_text(encoding="utf-8")
        self.assertIn("RetireLegacyScheduledTask", installer)
        self.assertIn('/Query /TN "OrdaX Dev Agent"', installer)
        self.assertIn('/End /TN "OrdaX Dev Agent"', installer)
        self.assertIn('/Delete /F /TN "OrdaX Dev Agent"', installer)
        self.assertIn('{userstartup}\\OrdaX Dev Agent.lnk', installer)
        self.assertNotIn('filesandordirs; Name: "{localappdata}\\OrdaX\\DevAgent"', installer)

    def test_product_build_bundles_private_runtime_and_identical_legacy_alias(self) -> None:
        build = (ROOT / "scripts" / "windows" / "build-ordax-studio-product.ps1").read_text(encoding="utf-8")
        self.assertIn("python-$PythonVersion-embed-amd64.zip", build)
        self.assertIn("Lib\\site-packages", build)
        self.assertIn("pip install", build)
        self.assertIn("ORDAX_STUDIO_SETUP_SHA256", build)
        self.assertIn("dotnet publish", build)
        self.assertIn("ORDAX Workbench.exe", build)
        self.assertIn("--self-contained true", build)
        self.assertIn('$studioExe = Join-Path $stageRoot "ORDAX Studio.exe"', build)
        self.assertIn('$legacyStudioExe = Join-Path $stageRoot "ORDAX Dev.exe"', build)
        self.assertIn("Copy-Item -LiteralPath $studioExe -Destination $legacyStudioExe -Force", build)
        self.assertIn("Legacy ORDAX Dev launcher alias is not byte-identical", build)
        self.assertIn('product = "ORDAX Studio"', build)
        self.assertIn('studio = "ORDAX Studio.exe"', build)
        self.assertIn('studio_legacy_alias = "ORDAX Dev.exe"', build)
        self.assertIn('Get-ChildItem $OutputDirectory -Filter "ORDAX-Studio-Setup-*.exe"', build)

    def test_product_build_enforces_canonical_studio_version_provenance(self) -> None:
        build = (ROOT / "scripts" / "windows" / "build-ordax-studio-product.ps1").read_text(encoding="utf-8")
        self.assertIn("ORDAX_STUDIO_CANONICAL_VERSION", build)
        self.assertIn('$env:GITHUB_REF_TYPE -eq "tag"', build)
        self.assertIn('"github-tag"', build)
        self.assertIn('"historical-pyproject"', build)
        self.assertIn("ORDAX Studio version mismatch", build)
        self.assertIn("ORDAX Studio version is not valid semantic version syntax", build)
        self.assertIn("version_provenance", build)
        self.assertIn("canonical_version_asserted", build)
        self.assertIn('Write-Output "ORDAX_STUDIO_VERSION=$Version"', build)
        self.assertIn('Write-Output "ORDAX_STUDIO_VERSION_SOURCE=$versionSource"', build)

    def test_product_shell_is_project_host_not_embedded_chat(self) -> None:
        product = (ROOT / "ordax_studio" / "studio_product.html").read_text(encoding="utf-8")
        script = (ROOT / "ordax_studio" / "assets" / "studio.js").read_text(encoding="utf-8")
        self.assertIn("ORDAX Studio", product)
        self.assertIn("Visão geral", product)
        self.assertIn("MCP", product)
        self.assertNotIn("Agent / Responses", product)
        self.assertNotIn("Browser Companion", product)
        self.assertNotIn("Chat normal", product)
        self.assertNotIn("agentPrompt", script)

    def test_native_workbench_is_provider_neutral_and_uses_webview2(self) -> None:
        project = (ROOT / "native" / "ordax-workbench" / "Ordax.Workbench.csproj").read_text(encoding="utf-8")
        xaml = (ROOT / "native" / "ordax-workbench" / "MainWindow.xaml").read_text(encoding="utf-8")
        code = (ROOT / "native" / "ordax-workbench" / "MainWindow.xaml.cs").read_text(encoding="utf-8")
        bridge = (ROOT / "ordax_studio" / "workbench_bridge.py").read_text(encoding="utf-8")
        host_bridge = (ROOT / "ordax_studio" / "host_bridge.js").read_text(encoding="utf-8")
        studio_js = (ROOT / "ordax_studio" / "assets" / "studio.js").read_text(encoding="utf-8")
        blender_js = (ROOT / "ordax_studio" / "assets" / "blender-connection.js").read_text(encoding="utf-8")
        account_js = (ROOT / "ordax_studio" / "assets" / "product_account.js").read_text(encoding="utf-8")

        self.assertIn("Microsoft.Web.WebView2", project)
        self.assertIn("WebView2CompositionControl", xaml)
        self.assertIn('x:Name="ProviderView"', xaml)
        self.assertIn('x:Name="PreviewView"', xaml)
        self.assertIn('x:Name="WorkbenchBrowserView"', xaml)
        self.assertIn('Header="Execuções"', xaml)
        self.assertIn("StudioView.CoreWebView2.WebMessageReceived", code)
        self.assertNotIn("ProviderView.CoreWebView2.WebMessageReceived", code)
        self.assertIn("_ALLOWED_METHODS", bridge)
        self.assertIn('"ai_sessions_status"', bridge)
        self.assertIn("window.chrome?.webview", host_bridge)
        self.assertIn("window.pywebview?.api", host_bridge)
        self.assertIn("Object.defineProperty(window,'ordaxStudioHost'", host_bridge)
        for portable in (studio_js, blender_js, account_js):
            self.assertNotIn("window.chrome", portable)
            self.assertNotIn("window.pywebview", portable)
            self.assertNotIn("pywebviewready", portable)

    def test_runtime_supervisor_is_client_neutral(self) -> None:
        launcher = (ROOT / "packaging" / "windows" / "ordax_launcher.c").read_text(encoding="utf-8").lower()
        self.assertNotIn("codex", launcher)
        self.assertNotIn("chatgpt", launcher)
        self.assertNotIn("claude", launcher)
        self.assertIn("ordaxruntime", launcher)

    def test_windows_ci_proves_branding_migration_and_running_runtime_upgrade(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "windows-product-build.yml").read_text(encoding="utf-8")
        self.assertIn("ORDAX-Studio-Setup-*.exe", workflow)
        self.assertIn("ordax-studio-windows-x64", workflow)
        self.assertIn("Upgrade over running ORDAX Runtime and legacy ORDAX Dev launcher", workflow)
        self.assertIn('Wait-OrdaxReady -Label "ORDAX_UPGRADE_RUNTIME"', workflow)
        self.assertIn("running runtime did not exit during upgrade", workflow)
        self.assertIn("legacy ORDAX Dev process survived Studio upgrade", workflow)
        self.assertIn("LEGACY_ORDAX_DEV_PROCESS_RETIRED", workflow)
        self.assertIn("ORDAX_LEGACY_ALIAS_IDENTICAL", workflow)
        self.assertIn("LEGACY_ORDAX_TASK_REMOVED", workflow)
        self.assertIn("LEGACY_ORDAX_STARTUP_REMOVED", workflow)
        self.assertIn("ORDAX_WORKBENCH_READY", workflow)
        self.assertIn("workbench\\ORDAX Workbench.exe", workflow)
        self.assertIn('- "ordax_core/**"', workflow)
        self.assertIn('- "ordax_dev_agent/**"', workflow)
        self.assertIn('- "ordax_device_agent/**"', workflow)
        self.assertIn('- "ordax_studio/**"', workflow)


    def test_release_publish_handles_missing_release_without_powershell_error_stream_failure(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "windows-product-build.yml").read_text(encoding="utf-8")
        self.assertIn('$ErrorActionPreference = "Stop"', workflow)
        self.assertIn('cmd /c "gh release view $tag --repo $env:GITHUB_REPOSITORY >nul 2>nul"', workflow)
        self.assertIn("$releaseExists = $LASTEXITCODE -eq 0", workflow)
        self.assertNotIn('gh release view $tag --repo $env:GITHUB_REPOSITORY *> $null', workflow)
        self.assertIn("gh release upload $tag", workflow)
        self.assertIn("gh release create $tag", workflow)


    def test_windows_build_consumes_pinned_ordax_apps_studio_source(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "windows-product-build.yml").read_text(encoding="utf-8")
        build = (ROOT / "scripts" / "windows" / "build-ordax-studio-product.ps1").read_text(encoding="utf-8")
        source_lock = (ROOT / "studio-source.lock.json").read_text(encoding="utf-8")
        self.assertIn('"repository": "washingtonmsdj/ordax-apps"', source_lock)
        self.assertIn('"commit": "fb8044d124eb9222d3ca39f52d7fc68938ddaa4d"', source_lock)
        self.assertIn('"path": "apps/studio"', source_lock)
        self.assertIn('"version": "0.4.2"', source_lock)
        self.assertIn("repository: washingtonmsdj/ordax-apps", workflow)
        self.assertIn("ref: fb8044d124eb9222d3ca39f52d7fc68938ddaa4d", workflow)
        self.assertIn("ORDAX_STUDIO_APP_SOURCE", workflow)
        self.assertIn("studio-source.lock.json", build)
        self.assertIn("ORDAX_STUDIO_PORTABLE_SOURCE_COMMIT", build)
        self.assertIn('"ordax-apps-lock"', build)
        self.assertIn("host_bridge.js", build)
        self.assertIn("host_contract.js", build)


if __name__ == "__main__":
    unittest.main()