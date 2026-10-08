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

    def test_studio_is_the_only_user_facing_runtime_entrypoint(self) -> None:
        launcher = (ROOT / "packaging" / "windows" / "ordax_launcher.c").read_text(encoding="utf-8")
        installer = (ROOT / "packaging" / "windows" / "ordax-studio.iss").read_text(encoding="utf-8")
        self.assertIn("ensure_runtime_running", launcher)
        self.assertIn("runtime_is_running", launcher)
        self.assertIn('L"%ls\\\\ORDAX Runtime.exe"', launcher)
        self.assertIn('Description: "Abrir ORDAX Studio"', installer)
        self.assertNotIn('Description: "Iniciar ORDAX Runtime"', installer)
        self.assertIn('ValueName: "ORDAX Runtime"', installer)

    def test_installer_preserves_app_id_while_migrating_branding(self) -> None:
        installer = (ROOT / "packaging" / "windows" / "ordax-studio.iss").read_text(encoding="utf-8")
        self.assertIn("AppId={{0D31F22D-8451-4CF4-9E34-F0D4D857F55F}", installer)
        self.assertIn("DefaultDirName={localappdata}\\Programs\\ORDAX", installer)
        self.assertIn("UsePreviousAppDir=no", installer)
        self.assertIn("OutputBaseFilename=ORDAX-Studio-Setup-{#AppVersion}-x64", installer)
        self.assertIn('Name: "{group}\\ORDAX Studio"', installer)
        self.assertIn('Name: "{userdesktop}\\ORDAX Studio"', installer)
        self.assertIn('Name: "{group}\\ORDAX Dev.lnk"', installer)
        self.assertIn('Name: "{userdesktop}\\ORDAX Dev.lnk"', installer)

    def test_launchers_fail_closed_when_job_supervision_cannot_be_applied(self) -> None:
        launcher = (ROOT / "packaging" / "windows" / "ordax_launcher.c").read_text(encoding="utf-8")
        self.assertGreaterEqual(launcher.count("!AssignProcessToJobObject(job, process.hProcess)"), 2)
        self.assertIn("TerminateProcess(process.hProcess, error)", launcher)
        self.assertNotIn("if (job != NULL) {\n        AssignProcessToJobObject(job, process.hProcess);", launcher)

    def test_installer_removes_stale_runtime_metadata_before_upgrade_copy(self) -> None:
        installer = (ROOT / "packaging" / "windows" / "ordax-studio.iss").read_text(encoding="utf-8")
        self.assertIn(
            'Name: "{app}\\runtime\\Lib\\site-packages\\ordax_runtime-*.dist-info"',
            installer,
        )
        self.assertEqual(installer.count("ordax_runtime-*.dist-info"), 1)
        self.assertNotIn('Name: "{app}\\runtime\\Lib\\site-packages\\*.dist-info"', installer)

    def test_installer_retires_leaked_workbench_tree_before_runtime_files(self) -> None:
        installer = (ROOT / "packaging" / "windows" / "ordax-studio.iss").read_text(encoding="utf-8")
        self.assertIn('#define WorkbenchExeName "ORDAX Workbench.exe"', installer)
        self.assertIn("StopOrdaxProcess('Local\\ORDAXWorkbenchShutdown', '{#WorkbenchExeName}')", installer)
        workbench_stop = installer.index("StopOrdaxProcess('Local\\ORDAXWorkbenchShutdown', '{#WorkbenchExeName}')")
        runtime_stop = installer.index("StopOrdaxProcess('Local\\ORDAXRuntimeShutdown', '{#RuntimeExeName}')")
        self.assertLess(workbench_stop, runtime_stop)

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

    def test_legacy_mcp_blender_identity_is_retired_from_product_surface(self) -> None:
        pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        server = (ROOT / "ordax_dev_agent" / "mcp_server.py").read_text(encoding="utf-8")
        studio_server = (ROOT / "ordax_studio" / "mcp_server.py").read_text(encoding="utf-8")
        installer = (ROOT / "packaging" / "windows" / "ordax-studio.iss").read_text(encoding="utf-8")
        setup = (ROOT / "scripts" / "windows" / "ordax-device-agent-setup.ps1").read_text(encoding="utf-8")

        self.assertNotIn('mcp-blender = "ordax_studio.mcp_server:main"', pyproject)
        self.assertNotIn('mcp-blender-unity = "mcp_blender_unity.server:main"', pyproject)
        self.assertNotIn('"repository_alias": "mcp-blender"', server)
        self.assertNotIn("historical connector alias: mcp-blender", studio_server)
        self.assertIn("mcp_blender_unity-*.dist-info", installer)
        self.assertIn("mcp-blender.exe", installer)
        self.assertIn("mcp-blender-unity.exe", installer)

        # The only accepted historical repository references are migration input:
        # an existing managed checkout can be cut over to ordax-runtime safely.
        self.assertIn("$legacyRemotes", setup)
        self.assertIn("washingtonmsdj/mcp-blender.git", setup)
        self.assertIn("LEGACY_MIGRATION", setup)
        self.assertIn("washingtonmsdj/ordax-runtime.git", setup)


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
        self.assertIn("ORDAX_STUDIO_APP_SOURCE is required", build)
        self.assertNotIn('$env:GITHUB_REF_TYPE -eq "tag"', build)
        self.assertNotIn('"github-tag"', build)
        self.assertNotIn('"historical-pyproject"', build)
        self.assertIn("ORDAX Studio version mismatch", build)
        self.assertIn("ORDAX Studio version is not valid semantic version syntax", build)
        self.assertIn("version_provenance", build)
        self.assertIn("canonical_version_asserted", build)
        self.assertIn('Write-Output "ORDAX_STUDIO_VERSION=$Version"', build)
        self.assertIn('Write-Output "ORDAX_STUDIO_VERSION_SOURCE=$versionSource"', build)

    def test_windows_installer_refuses_stale_runtime_studio_fallback(self) -> None:
        build = (ROOT / "scripts" / "windows" / "build-ordax-studio-product.ps1").read_text(encoding="utf-8")
        lock = (ROOT / "studio-source.lock.json").read_text(encoding="utf-8")
        workflow = (ROOT / ".github" / "workflows" / "windows-product-build.yml").read_text(encoding="utf-8")
        import json

        source = json.loads(lock)
        self.assertEqual(source["repository"], "ordaxsystems/ordax-apps")
        self.assertEqual(source["path"], "apps/studio")
        self.assertEqual(source["authority"], "none")
        self.assertIn("ORDAX_STUDIO_APP_SOURCE is required", build)
        self.assertIn("git -C $studioAppSource rev-parse --show-toplevel", build)
        self.assertIn("git -C $checkoutRoot rev-parse HEAD", build)
        self.assertIn('git -C $checkoutRoot status --porcelain -- $sourceLock.path', build)
        self.assertIn('sourceLock.commit', build)
        self.assertIn("Studio source checkout must match the exact pinned Git commit", build)
        self.assertIn("Studio source checkout contains local changes or untracked files", build)
        self.assertIn("Remove-Item -LiteralPath $targetAssets -Recurse -Force", build)
        self.assertIn('Copy-Item (Join-Path $studioAppSource "assets\\*") $targetAssets', build)
        self.assertNotIn("historical-pyproject", build)
        self.assertIn("repository: ordaxsystems/ordax-apps", workflow)
        self.assertIn("ORDAX_STUDIO_APP_SOURCE:", workflow)

    def test_windows_package_materializes_studio_outside_the_runtime_checkout(self) -> None:
        build = (ROOT / "scripts" / "windows" / "build-ordax-studio-product.ps1").read_text(encoding="utf-8")
        self.assertIn('$packagedStudio = Join-Path $sitePackages "ordax_studio"', build)
        self.assertIn('$targetAssets = Join-Path $packagedStudio "assets"', build)
        self.assertIn('Join-Path $packagedStudio "studio_product.html"', build)
        self.assertIn('Join-Path $packagedStudio "app_intelligence_registry.json"', build)
        self.assertIn('Join-Path $packagedStudio "host_contract.js"', build)
        self.assertIn("Get-FileHash -Algorithm SHA256 -LiteralPath $sourceAsset.FullName", build)
        self.assertIn("Packaged Studio asset inventory differs from canonical source", build)
        self.assertIn("ORDAX_STUDIO_PACKAGE_SOURCE_VERIFIED=", build)
        self.assertNotIn('$targetStudio = Join-Path $repoRoot "ordax_studio"', build)
        self.assertNotIn('Copy-Item (Join-Path $studioAppSource "assets\\*") (Join-Path $repoRoot', build)
        self.assertNotIn('Join-Path $repoRoot "ordax_studio\\assets"', build)

    def test_windows_ci_rejects_any_mutation_of_runtime_studio_sources(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "windows-product-build.yml").read_text(encoding="utf-8")
        self.assertIn("Verify build did not rewrite Runtime Studio sources", workflow)
        self.assertIn("git status --porcelain -- ordax_studio", workflow)
        self.assertIn("ORDAX_STUDIO_RUNTIME_SOURCE_UNCHANGED", workflow)

    def test_portable_studio_source_is_owned_only_by_ordax_apps(self) -> None:
        build = (ROOT / "scripts" / "windows" / "build-ordax-studio-product.ps1").read_text(encoding="utf-8")
        package = ROOT / "ordax_studio"
        self.assertFalse((package / "studio_product.html").exists())
        self.assertFalse((package / "studio.html").exists())
        self.assertFalse((package / "assets").exists())
        self.assertIn('Copy-Item (Join-Path $studioAppSource "assets\\*") $targetAssets', build)
        self.assertIn('Join-Path $packagedStudio "studio_product.html"', build)
        self.assertIn("ORDAX_STUDIO_PACKAGE_SOURCE_VERIFIED", build)

    def test_windows_start_script_uses_only_installed_product_not_runtime_source(self) -> None:
        script = (ROOT / "scripts" / "windows" / "ordax-studio-start.ps1").read_text(encoding="utf-8")
        project = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        self.assertIn("HKCU:\\Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\{0D31F22D-8451-4CF4-9E34-F0D4D857F55F}_is1", script)
        self.assertIn("$product.InstallLocation", script)
        self.assertIn("Join-Path $installLocation \"ORDAX Studio.exe\"", script)
        self.assertNotIn("Programs\\ORDAX\\ORDAX Studio.exe", script)
        self.assertIn("Start-Process -FilePath $studioExe", script)
        self.assertIn("Install the signed product release", script)
        self.assertNotIn("ordax_studio.web_desktop", script)
        self.assertNotIn("$env:PYTHONPATH", script)
        self.assertNotIn('ordax-studio-web =', project)

    def test_native_workbench_is_provider_neutral_and_uses_webview2(self) -> None:
        project = (ROOT / "native" / "ordax-workbench" / "Ordax.Workbench.csproj").read_text(encoding="utf-8")
        xaml = (ROOT / "native" / "ordax-workbench" / "MainWindow.xaml").read_text(encoding="utf-8")
        code = (ROOT / "native" / "ordax-workbench" / "MainWindow.xaml.cs").read_text(encoding="utf-8")
        bridge = (ROOT / "ordax_studio" / "workbench_bridge.py").read_text(encoding="utf-8")
        host_bridge = (ROOT / "ordax_studio" / "host_bridge.js").read_text(encoding="utf-8")

        self.assertIn("Microsoft.Web.WebView2", project)
        self.assertIn("WebView2CompositionControl", xaml)
        self.assertIn('x:Name="ProviderView"', xaml)
        self.assertIn('x:Name="ProviderSurfaceHost"', xaml)
        self.assertNotIn('Header="Web IA"', xaml)
        self.assertIn('x:Name="PreviewView"', xaml)
        self.assertIn('x:Name="WorkbenchBrowserView"', xaml)
        self.assertIn('Header="Execuções"', xaml)
        self.assertIn("StudioView.CoreWebView2.WebMessageReceived", code)
        self.assertIn('"ordax-assistant-surface"', code)
        self.assertIn("ApplyAssistantSurfaceAsync", code)
        self.assertIn("StudioView.ActualWidth / viewportWidth", code)
        self.assertNotIn("ProviderView.CoreWebView2.WebMessageReceived", code)
        self.assertIn("presentAssistantSurface", host_bridge)
        self.assertIn("_ALLOWED_METHODS", bridge)
        self.assertIn('"ai_sessions_status"', bridge)
        self.assertIn("window.chrome?.webview", host_bridge)
        self.assertIn("window.pywebview?.api", host_bridge)
        self.assertIn("Object.defineProperty(window,'ordaxStudioHost'", host_bridge)

    def test_runtime_supervisor_is_client_neutral(self) -> None:
        launcher = (ROOT / "packaging" / "windows" / "ordax_launcher.c").read_text(encoding="utf-8").lower()
        self.assertNotIn("codex", launcher)
        self.assertNotIn("chatgpt", launcher)
        self.assertNotIn("claude", launcher)
        self.assertIn("ordaxruntime", launcher)

    def test_windows_ci_proves_branding_migration_and_running_runtime_upgrade(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "windows-product-build.yml").read_text(encoding="utf-8")
        smoke = (ROOT / "scripts" / "windows" / "test-ordax-studio-install-smoke.ps1").read_text(encoding="utf-8")
        self.assertIn("ordax-studio-windows-x64", workflow)
        self.assertIn("Upgrade over running ORDAX Runtime and legacy ORDAX Dev launcher", smoke)
        self.assertIn('Wait-OrdaxReady -Label "ORDAX_UPGRADE_RUNTIME"', smoke)
        self.assertIn("running runtime did not exit during upgrade", smoke)
        self.assertIn("legacy ORDAX Dev process survived Studio upgrade", smoke)
        for marker in (
            "LEGACY_ORDAX_DEV_PROCESS_RETIRED",
            "ORDAX_LEGACY_ALIAS_IDENTICAL", "LEGACY_ORDAX_TASK_REMOVED",
            "LEGACY_ORDAX_STARTUP_REMOVED", "ORDAX_WORKBENCH_READY",
            "STUDIO_STARTED_RUNTIME", "ORDAX_RUNTIME_OUTLIVED_STUDIO",
        ):
            self.assertIn(marker, smoke)
        self.assertIn('Wait-OrdaxReady -Label "ORDAX_STUDIO_RUNTIME"', smoke)
        self.assertIn("workbench\\ORDAX Workbench.exe", smoke)
        self.assertIn("[string]$ArtifactDirectory", smoke)
        self.assertIn("Exactly one ORDAX Studio installer", smoke)
        for source in ("ordax_core", "ordax_dev_agent", "ordax_device_agent", "ordax_studio"):
            self.assertIn(f'- "{source}/**"', workflow)

    def test_signed_release_reuses_exact_same_smoke_and_checks_digest_after(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "windows-product-build.yml").read_text(encoding="utf-8")
        release = workflow.split("  publish-release:", 1)[1]
        candidate = workflow.split("  install-smoke:", 1)[1].split("  publish-release:", 1)[0]
        script = r".\scripts\windows\test-ordax-studio-install-smoke.ps1"
        self.assertIn("Checkout pinned Runtime smoke implementation", candidate)
        self.assertIn(script, candidate)
        self.assertIn(script, release)
        self.assertEqual(2, workflow.count(f"run: {script}"))
        self.assertIn("environment: windows-production-release", release)
        self.assertIn("Install and upgrade from final signed release bytes", release)
        self.assertIn("Reverify final installer SHA-256 after signed installation smoke", release)
        self.assertLess(release.index("Verify release provenance and artifact digest"),
                        release.index("Verify production Authenticode signature"))
        self.assertLess(release.index("Verify production Authenticode signature"),
                        release.index("Install and upgrade from final signed release bytes"))
        self.assertLess(release.index("Install and upgrade from final signed release bytes"),
                        release.index("Reverify final installer SHA-256 after signed installation smoke"))
        self.assertLess(release.index("Reverify final installer SHA-256 after signed installation smoke"),
                        release.index("Publish GitHub Release assets"))
        self.assertEqual(2, release.count("assert-release-provenance.ps1"))
        self.assertIn('- "scripts/windows/test-ordax-studio-install-smoke.ps1"', workflow)

    def test_public_release_requires_trusted_authenticode(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "windows-product-build.yml").read_text(encoding="utf-8")
        gate = (ROOT / "scripts" / "windows" / "assert-release-authenticode.ps1").read_text(encoding="utf-8")
        self.assertIn("Verify production Authenticode signature", workflow)
        self.assertIn("assert-release-authenticode.ps1", workflow)
        self.assertIn("Get-AuthenticodeSignature", gate)
        self.assertIn("SignatureStatus]::Valid", gate)
        self.assertIn("TimeStamperCertificate", gate)
        self.assertIn("self-signed certificate", gate)
        self.assertIn("TrustedSignerThumbprints", gate)
        self.assertIn("not an authorized ORDAX publisher", gate)
        self.assertIn("environment: windows-production-release", workflow)
        self.assertIn("vars.ORDAX_RELEASE_SIGNER_THUMBPRINTS", workflow)
        self.assertIn("-TrustedSignerThumbprints $env:ORDAX_RELEASE_SIGNER_THUMBPRINTS", workflow)
        self.assertIn("tests.test_release_signer_identity", workflow)
        self.assertIn("Checkout tagged Runtime source and canonical Studio lock", workflow)
        self.assertIn("Verify release provenance and artifact digest", workflow)
        self.assertIn("assert-release-provenance.ps1", workflow)
        self.assertLess(workflow.index("Verify release provenance and artifact digest"),
                        workflow.index("Verify production Authenticode signature"))
        self.assertLess(workflow.index("Verify production Authenticode signature"),
                        workflow.index("Publish GitHub Release assets"))

    def test_release_tag_must_be_in_canonical_main_history_before_publisher_runs(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "windows-product-build.yml").read_text(encoding="utf-8")
        script = (ROOT / "scripts" / "windows" / "assert-release-main-ancestry.ps1").read_text(encoding="utf-8")
        publish = workflow.split("  publish-release:", 1)[1]
        self.assertIn("fetch-depth: 0", publish)
        self.assertIn("assert-release-main-ancestry.ps1", publish)
        self.assertIn('-ExpectedCommit "$env:GITHUB_SHA"', publish)
        self.assertLess(
            publish.index("Verify tagged Runtime commit belongs to canonical main"),
            publish.index("Download validated Windows installer"),
        )
        self.assertLess(
            publish.index("Verify tagged Runtime commit belongs to canonical main"),
            publish.index("Verify production Authenticode signature"),
        )
        self.assertIn("refs/remotes/origin/main^{commit}", script)
        self.assertIn("refs/tags/$Tag^{commit}", script)
        self.assertIn("merge-base --is-ancestor", script)
        self.assertIn("not an ancestor of canonical origin/main", script)
        self.assertIn('tests.test_release_main_ancestry', workflow)
        self.assertIn('- "scripts/windows/assert-release-main-ancestry.ps1"', workflow)
        self.assertIn('- "tests/test_release_main_ancestry.py"', workflow)

    def test_release_publish_handles_missing_release_without_powershell_error_stream_failure(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "windows-product-build.yml").read_text(encoding="utf-8")
        self.assertIn('$ErrorActionPreference = "Stop"', workflow)
        self.assertIn('cmd /c "gh release view $tag --repo $env:GITHUB_REPOSITORY >nul 2>nul"', workflow)
        self.assertIn("$releaseExists = $LASTEXITCODE -eq 0", workflow)
        self.assertNotIn('gh release view $tag --repo $env:GITHUB_REPOSITORY *> $null', workflow)
        self.assertNotIn("gh release upload $tag", workflow)
        self.assertNotIn("--clobber", workflow)
        self.assertIn("Release tag already published. Refusing to replace immutable installer assets.", workflow)
        self.assertIn("gh release create $tag", workflow)


    def test_windows_build_consumes_pinned_ordax_apps_studio_source(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "windows-product-build.yml").read_text(encoding="utf-8")
        build = (ROOT / "scripts" / "windows" / "build-ordax-studio-product.ps1").read_text(encoding="utf-8")
        source_lock = (ROOT / "studio-source.lock.json").read_text(encoding="utf-8")
        self.assertIn('"repository": "ordaxsystems/ordax-apps"', source_lock)
        import json
        lock = json.loads(source_lock)
        self.assertRegex(lock["commit"], r"^[0-9a-f]{40}$")
        self.assertIn('"path": "apps/studio"', source_lock)
        self.assertIn('"version": "0.5.8"', source_lock)
        self.assertIn("repository: ordaxsystems/ordax-apps", workflow)
        self.assertIn("ORDAX_STUDIO_SOURCE_COMMIT", workflow)
        self.assertIn("ref: ${{ env.ORDAX_STUDIO_SOURCE_COMMIT }}", workflow)
        self.assertIn("ORDAX_STUDIO_APP_SOURCE", workflow)
        self.assertIn("studio-source.lock.json", build)
        self.assertIn("ORDAX_STUDIO_PORTABLE_SOURCE_COMMIT", build)
        self.assertIn('"ordax-apps-lock"', build)
        self.assertIn("host_bridge.js", build)
        self.assertIn("host_contract.js", build)
        self.assertIn("ai\\\\manifest.json", build)
        self.assertIn("ordax.app-intelligence-registry/1", build)
        self.assertIn("ordax.application-action-manifest/1", build)
        self.assertIn("actions\\\\manifest.json", build)
        self.assertIn('execution -ne "proposal-only"', build)
        self.assertIn("app_intelligence_registry.json", build)
        self.assertIn("[System.IO.File]::WriteAllText(", build)
        self.assertIn("[System.Text.UTF8Encoding]::new($false)", build)
        self.assertNotIn('Set-Content (Join-Path $targetStudio "app_intelligence_registry.json") -Encoding UTF8', build)
        self.assertIn("ORDAX_APP_INTELLIGENCE_REGISTRY_OK", build)
        self.assertIn('"*.json"', (ROOT / "pyproject.toml").read_text(encoding="utf-8"))


    def test_installer_retires_only_recognized_historical_install_roots(self) -> None:
        installer = (ROOT / "packaging" / "windows" / "ordax-studio.iss").read_text(encoding="utf-8")
        self.assertIn("function IsRecognizedOrdaxInstallRoot", installer)
        self.assertIn("function IsSafePartialOrdaxResidual", installer)
        self.assertIn("function RetireHistoricalInstallRoot", installer)
        self.assertIn("function RetireHistoricalInstallRoots", installer)
        self.assertIn("runtime\\python.exe", installer)
        self.assertIn("runtime\\Lib\\site-packages\\ordax_studio", installer)
        self.assertIn("unins000.dat", installer)
        self.assertIn("product-manifest.json", installer)
        self.assertIn("ordax.windows-product/1", installer)
        self.assertIn("{localappdata}\\Programs\\ORDAX Studio", installer)
        self.assertIn("{localappdata}\\Programs\\ORDAX Dev", installer)
        self.assertIn("DelTree(HistoricalRoot, True, True, True)", installer)
        self.assertIn("LocalAppData\\OrdaX", installer)
        self.assertNotIn("DelTree(ExpandConstant('{localappdata}\\OrdaX')", installer)


if __name__ == "__main__":
    unittest.main()
