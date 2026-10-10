# Single Windows E2E installer smoke for both PR candidates and final signed release bytes.
# The caller must verify release signature/digest BEFORE running this script on a public release.
# Reusing one smoke implementation prevents unsigned and signed installation tests drifting.
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$ArtifactDirectory
)

if (-not (Test-Path -LiteralPath $ArtifactDirectory -PathType Container)) {
    throw "ORDAX Studio installer artifact directory is missing"
}

$ErrorActionPreference = "Stop"

function Wait-OrdaxReady {
  param(
    [string]$Label,
    [int]$TimeoutSeconds = 60
  )
  $deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
  $lastCandidate = $null
  while ([DateTime]::UtcNow -lt $deadline) {
    try {
      $candidate = Invoke-RestMethod -Uri "http://127.0.0.1:8765/status" -TimeoutSec 2
      $lastCandidate = $candidate
      $state = if ($candidate.runtime) { [string]$candidate.runtime.state } else { "" }
      $actionCount = @($candidate.actions).Count
      if ($candidate.runtime -and $state -ne "initializing" -and $actionCount -gt 0) {
        if ($state -notin @("local-ready", "pairing", "ready")) {
          throw "ORDAX Runtime reached unexpected state: $state"
        }
        Write-Host ($Label + "_STATE=" + $state)
        Write-Host ($Label + "_ACTIONS=" + $actionCount)
        return $candidate
      }
    } catch {
    }
    Start-Sleep -Milliseconds 750
  }
  if ($lastCandidate) { $lastCandidate | ConvertTo-Json -Depth 8 | Write-Host }
  throw "Installed ORDAX Runtime did not reach action-ready state for $Label"
}

function Assert-StudioAlias {
  param([string]$StudioExe, [string]$LegacyExe)
  foreach ($path in @($StudioExe, $LegacyExe)) {
    if (-not (Test-Path $path)) { throw "Studio launcher is missing: $path" }
  }
  $studioHash = (Get-FileHash $StudioExe -Algorithm SHA256).Hash
  $legacyHash = (Get-FileHash $LegacyExe -Algorithm SHA256).Hash
  if ($studioHash -ne $legacyHash) {
    throw "ORDAX Dev compatibility alias differs from ORDAX Studio.exe"
  }
  Write-Host "ORDAX_LEGACY_ALIAS_IDENTICAL"
}

$installers = @(Get-ChildItem -LiteralPath $ArtifactDirectory -File -Filter "ORDAX-Studio-Setup-*.exe")
if ($installers.Count -ne 1) { throw "Exactly one ORDAX Studio installer is required for installation smoke" }
$installer = $installers[0]
$installRoot = Join-Path $env:RUNNER_TEMP "ordax-studio-install"
$installLog = Join-Path $env:RUNNER_TEMP "ordax-studio-install.log"
$upgradeLog = Join-Path $env:RUNNER_TEMP "ordax-studio-upgrade.log"
$repairLog = Join-Path $env:RUNNER_TEMP "ordax-studio-repair.log"
Remove-Item $installRoot -Recurse -Force -ErrorAction SilentlyContinue

# Simulate both historical product roots left by older packaging.
# The installer may retire them only when strong ORDAX product markers match.
$alternateInstallRoot = Join-Path $env:LOCALAPPDATA "Programs\ORDAX Studio"
$legacyDevInstallRoot = Join-Path $env:LOCALAPPDATA "Programs\ORDAX Dev"
foreach ($historicalRoot in @($alternateInstallRoot, $legacyDevInstallRoot)) {
  Remove-Item $historicalRoot -Recurse -Force -ErrorAction SilentlyContinue
  New-Item -ItemType Directory -Force -Path $historicalRoot | Out-Null
  Set-Content -Path (Join-Path $historicalRoot "product-manifest.json") -Encoding utf8 -Value @'
{
  "schema": "ordax.windows-product/1",
  "product": "ORDAX Studio",
  "version": "0.4.1"
}
'@
  New-Item -ItemType File -Force -Path (Join-Path $historicalRoot "ORDAX Studio.exe") | Out-Null
  New-Item -ItemType File -Force -Path (Join-Path $historicalRoot "ORDAX Runtime.exe") | Out-Null
}

# Simulate the historical pre-0.4 supervisor so the product install proves
# it retires the duplicate runtime without depending on a preconfigured host.
$legacyTaskName = "OrdaX Dev Agent"
$legacyAction = New-ScheduledTaskAction -Execute "powershell.exe" -Argument '-NoProfile -NonInteractive -Command "Start-Sleep -Seconds 300"'
$legacyTrigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(10)
Register-ScheduledTask -TaskName $legacyTaskName -Action $legacyAction -Trigger $legacyTrigger -Force | Out-Null
Start-ScheduledTask -TaskName $legacyTaskName
$startup = [Environment]::GetFolderPath("Startup")
$legacyStartup = Join-Path $startup "OrdaX Dev Agent.lnk"
Set-Content -Path $legacyStartup -Value "legacy" -Encoding ascii

$installProcess = Start-Process -FilePath $installer.FullName -ArgumentList @(
  "/VERYSILENT",
  "/SUPPRESSMSGBOXES",
  "/NORESTART",
  "/DIR=$installRoot",
  "/LOG=$installLog"
) -Wait -PassThru
if ($installProcess.ExitCode -ne 0) {
  if (Test-Path $installLog) { Get-Content $installLog -Tail 200 }
  throw "ORDAX Studio silent install failed: $($installProcess.ExitCode)"
}

if (Get-ScheduledTask -TaskName $legacyTaskName -ErrorAction SilentlyContinue) {
  throw "legacy OrdaX Dev Agent scheduled task survived product install"
}
Write-Host "LEGACY_ORDAX_TASK_REMOVED"
if (Test-Path $legacyStartup) {
  throw "legacy OrdaX Dev Agent startup link survived product install"
}
Write-Host "LEGACY_ORDAX_STARTUP_REMOVED"

if (Test-Path $alternateInstallRoot) {
  throw "recognized alternate ORDAX Studio install root survived product install"
}
if (Test-Path $legacyDevInstallRoot) {
  throw "recognized legacy ORDAX Dev install root survived product install"
}
Write-Host "LEGACY_INSTALL_ROOTS_REMOVED"

$studioExe = Join-Path $installRoot "ORDAX Studio.exe"
$legacyStudioExe = Join-Path $installRoot "ORDAX Dev.exe"
$runtimeExe = Join-Path $installRoot "ORDAX Runtime.exe"
$presentationExe = Join-Path $installRoot "presentation\ORDAX Studio.exe"
$privatePython = Join-Path $installRoot "runtime\python.exe"
foreach ($required in @($studioExe, $legacyStudioExe, $runtimeExe, $presentationExe, $privatePython)) {
  if (-not (Test-Path $required)) {
    if (Test-Path $installLog) { Get-Content $installLog -Tail 200 }
    throw "Installed product file is missing: $required"
  }
}
Assert-StudioAlias -StudioExe $studioExe -LegacyExe $legacyStudioExe

$runKey = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run"
$autoStart = (Get-ItemProperty -Path $runKey -Name "ORDAX Runtime" -ErrorAction Stop)."ORDAX Runtime"
if (-not $autoStart -or $autoStart -notlike "*$runtimeExe*") {
  throw "ORDAX Runtime auto-start does not point to installed runtime: $autoStart"
}

& $privatePython -c "import ordax_studio, ordax_dev_agent, ordax_device_agent; print('ORDAX_INSTALLED_RUNTIME_OK')"
if ($LASTEXITCODE -ne 0) { throw "Installed private runtime import smoke failed" }
& $privatePython -c "import importlib.util; assert importlib.util.find_spec('webview') is None; print('ORDAX_NO_LEGACY_WEBVIEW_IMPORT=PASS')"
if ($LASTEXITCODE -ne 0) { throw "Retired WebView2/pywebview Python host survived Electron cutover" }

# Verify the actual installed Python supports the canonical Electron login pipe
# in isolated (-I) mode, and rejects a synthetic request before network/login.
$accountProbeIn = Join-Path $env:RUNNER_TEMP "ordax-account-probe-in.json"
$accountProbeOut = Join-Path $env:RUNNER_TEMP "ordax-account-probe-out.json"
$accountProbeErr = Join-Path $env:RUNNER_TEMP "ordax-account-probe-err.txt"
try {
  [IO.File]::WriteAllText($accountProbeIn, '{"schema":"ordax.studio-product-account-session/1","operation":"unsupported","email":"invalid","password":"synthetic"}')
  $accountProbe = Start-Process -FilePath $privatePython -WorkingDirectory $installRoot -ArgumentList @('-I', '-u', '-m', 'ordax_studio.electron_account_session') -RedirectStandardInput $accountProbeIn -RedirectStandardOutput $accountProbeOut -RedirectStandardError $accountProbeErr -NoNewWindow -Wait -PassThru
  if ($accountProbe.ExitCode -ne 1) { throw "Installed account IPC did not reject invalid operation" }
  $accountReply = Get-Content -Raw -Encoding UTF8 -LiteralPath $accountProbeOut | ConvertFrom-Json
  if ($accountReply.schema -ne "ordax.studio-product-account-session/1" -or
      $accountReply.ok -ne $false -or $accountReply.error -ne "account_auth_failed" -or
      $accountReply.PSObject.Properties.Name -contains "access_token" -or
      -not [string]::IsNullOrWhiteSpace((Get-Content -Raw -Encoding UTF8 -LiteralPath $accountProbeErr))) {
    throw "Installed Product account IPC violated the fail-closed response contract"
  }
  Write-Host "ORDAX_INSTALLED_ACCOUNT_PIPE_FAIL_CLOSED=PASS"
} finally {
  Remove-Item -LiteralPath $accountProbeIn,$accountProbeOut,$accountProbeErr -Force -ErrorAction SilentlyContinue
}

$runtime = $null
$studio = Start-Process -FilePath $studioExe -WorkingDirectory $installRoot -PassThru
try {
  Start-Sleep -Seconds 2
  $studio.Refresh()
  if ($studio.HasExited) {
    throw "ORDAX Studio launcher exited before Electron initialization with code $($studio.ExitCode)"
  }
  $presentationProcess = Get-CimInstance Win32_Process -Filter "ParentProcessId = $($studio.Id)" | Where-Object {
    $_.ExecutablePath -and
      [IO.Path]::GetFullPath($_.ExecutablePath) -eq [IO.Path]::GetFullPath($presentationExe)
  } | Select-Object -First 1
  if (-not $presentationProcess) {
    throw "ORDAX Studio supervisor did not launch canonical Electron presentation"
  }
  # A PID alone never proves a functional UI. Verify the real visible HWND,
  # actual entrypoint and independent SHA-256 inventory of installed bytes.
  & (Join-Path $PSScriptRoot "assert-installed-studio-ux.ps1") `
    -InstallRoot $installRoot `
    -PresentationProcessId $presentationProcess.ProcessId `
    -WindowTimeoutSeconds 60
  Write-Host "ORDAX_ELECTRON_CONVERSATION_READY"

  $null = Wait-OrdaxReady -Label "ORDAX_STUDIO_RUNTIME"
  $runtime = Get-Process | Where-Object {
    $_.Path -and [IO.Path]::GetFullPath($_.Path) -eq [IO.Path]::GetFullPath($runtimeExe)
  } | Select-Object -First 1
  if (-not $runtime) {
    throw "ORDAX Studio did not start the packaged ORDAX Runtime"
  }
  Write-Host "STUDIO_STARTED_RUNTIME"
} finally {
  Stop-Process -Id $studio.Id -Force -ErrorAction SilentlyContinue
  Get-Process | Where-Object {
    $_.Path -and [IO.Path]::GetFullPath($_.Path) -eq [IO.Path]::GetFullPath($presentationExe)
  } | Stop-Process -Force -ErrorAction SilentlyContinue
  Start-Sleep -Seconds 1
}

$runtime.Refresh()
if ($runtime.HasExited) {
  throw "ORDAX Runtime exited when ORDAX Studio closed"
}
$null = Wait-OrdaxReady -Label "ORDAX_RUNTIME_AFTER_STUDIO_CLOSE"
Write-Host "ORDAX_RUNTIME_OUTLIVED_STUDIO"

$upgradedRuntime = $null
$legacyProcess = $null
$orphanPrivatePython = $null
try {
  $null = Wait-OrdaxReady -Label "ORDAX_RUNTIME"

  # This private Python orphan loads OpenSSL from the installed ORDAX runtime
  # and survives its launcher. It must be retired before DLL replacement.
  $orphanScript = Join-Path $env:RUNNER_TEMP "ordax-private-runtime-lock.py"
  @(
    "import ssl"
    "import time"
    "time.sleep(300)"
  ) | Set-Content -LiteralPath $orphanScript -Encoding ascii
  $orphanPrivatePython = Start-Process -FilePath $privatePython -ArgumentList @(
    ('"{0}"' -f $orphanScript)
  ) -WorkingDirectory $installRoot -PassThru
  Start-Sleep -Seconds 1
  $orphanPrivatePython.Refresh()
  if ($orphanPrivatePython.HasExited) {
    throw "Synthetic packaged private Python orphan did not start"
  }
  Write-Host "ORDAX_PACKAGED_PRIVATE_PYTHON_ORPHAN_RUNNING"

  # Simulate a pre-shutdown-event historical ORDAX Dev process. It runs
  # outside the installation and has no ORDAX cooperative event, so only
  # the explicit LegacyAppExeName fallback can retire it during upgrade.
  $legacyProcessRoot = Join-Path $env:RUNNER_TEMP "ordax-legacy-process"
  New-Item -ItemType Directory -Force -Path $legacyProcessRoot | Out-Null
  $legacyProcessExe = Join-Path $legacyProcessRoot "ORDAX Dev.exe"
  Copy-Item "$PSHOME\powershell.exe" $legacyProcessExe -Force
  $legacyProcess = Start-Process -FilePath $legacyProcessExe -ArgumentList @(
    "-NoProfile",
    "-NonInteractive",
    "-Command",
    "Start-Sleep -Seconds 300"
  ) -PassThru
  Start-Sleep -Seconds 1
  $legacyProcess.Refresh()
  if ($legacyProcess.HasExited) { throw "Synthetic legacy ORDAX Dev process did not start" }
  Write-Host "LEGACY_ORDAX_DEV_PROCESS_RUNNING"

  # Recreate the historical directory name without ORDAX product markers.
  # A safe migration must leave this unrelated directory untouched.
  New-Item -ItemType Directory -Force -Path $alternateInstallRoot | Out-Null
  $alternateSentinel = Join-Path $alternateInstallRoot "user-owned.txt"
  Set-Content -Path $alternateSentinel -Value "preserve" -Encoding ascii

  Write-Host "Upgrade over running ORDAX Runtime and legacy ORDAX Dev launcher"
  $upgradeProcess = Start-Process -FilePath $installer.FullName -ArgumentList @(
    "/VERYSILENT",
    "/SUPPRESSMSGBOXES",
    "/NORESTART",
    "/DIR=$installRoot",
    "/LOG=$upgradeLog"
  ) -Wait -PassThru
  if ($upgradeProcess.ExitCode -ne 0) {
    if (Test-Path $upgradeLog) { Get-Content $upgradeLog -Tail 200 }
    throw "ORDAX Studio silent upgrade failed: $($upgradeProcess.ExitCode)"
  }

  Start-Sleep -Milliseconds 750
  $runtime.Refresh()
  if (-not $runtime.HasExited) {
    throw "running runtime did not exit during upgrade"
  }
  $legacyProcess.Refresh()
  if (-not $legacyProcess.HasExited) {
    throw "legacy ORDAX Dev process survived Studio upgrade"
  }
  Write-Host "LEGACY_ORDAX_DEV_PROCESS_RETIRED"
  $orphanPrivatePython.Refresh()
  if (-not $orphanPrivatePython.HasExited) {
    throw "packaged private Python orphan survived upgrade"
  }
  Write-Host "ORDAX_PACKAGED_PRIVATE_PYTHON_ORPHAN_RETIRED"

  if (-not (Test-Path $alternateSentinel)) {
    throw "installer removed an unrecognized alternate directory"
  }
  Write-Host "UNRECOGNIZED_ALTERNATE_ROOT_PRESERVED"
  Remove-Item $alternateInstallRoot -Recurse -Force

  # Simulate an interrupted historical-root retirement: product identity
  # files are already gone, but the private runtime and uninstaller data
  # remain. A subsequent install must finish this cleanup idempotently.
  $partialOrdaxMarker = Join-Path $alternateInstallRoot "runtime\Lib\site-packages\ordax_studio"
  New-Item -ItemType Directory -Force -Path $partialOrdaxMarker | Out-Null
  New-Item -ItemType File -Force -Path (Join-Path $alternateInstallRoot "runtime\python.exe") | Out-Null
  New-Item -ItemType File -Force -Path (Join-Path $alternateInstallRoot "unins000.dat") | Out-Null
  New-Item -ItemType Directory -Force -Path (Join-Path $alternateInstallRoot "scripts") | Out-Null

  $repairProcess = Start-Process -FilePath $installer.FullName -ArgumentList @(
    "/VERYSILENT",
    "/SUPPRESSMSGBOXES",
    "/NORESTART",
    "/DIR=$installRoot",
    "/LOG=$repairLog"
  ) -Wait -PassThru
  if ($repairProcess.ExitCode -ne 0) {
    if (Test-Path $repairLog) { Get-Content $repairLog -Tail 200 }
    throw "ORDAX Studio idempotent repair install failed: $($repairProcess.ExitCode)"
  }
  if (Test-Path $alternateInstallRoot) {
    throw "partial alternate ORDAX install root survived idempotent repair"
  }
  Write-Host "PARTIAL_LEGACY_INSTALL_ROOT_REMOVED"

  foreach ($required in @($studioExe, $legacyStudioExe, $runtimeExe, $presentationExe, $privatePython)) {
    if (-not (Test-Path $required)) {
      if (Test-Path $upgradeLog) { Get-Content $upgradeLog -Tail 200 }
      throw "Upgraded product file is missing: $required"
    }
  }
  Assert-StudioAlias -StudioExe $studioExe -LegacyExe $legacyStudioExe

  $upgradedRuntime = Start-Process -FilePath $runtimeExe -WorkingDirectory $installRoot -PassThru
  $null = Wait-OrdaxReady -Label "ORDAX_UPGRADE_RUNTIME"
} finally {
  if ($legacyProcess) {
    Stop-Process -Id $legacyProcess.Id -Force -ErrorAction SilentlyContinue
  }
  if ($orphanPrivatePython) {
    Stop-Process -Id $orphanPrivatePython.Id -Force -ErrorAction SilentlyContinue
  }
  if ($upgradedRuntime) {
    Stop-Process -Id $upgradedRuntime.Id -Force -ErrorAction SilentlyContinue
  }
  Stop-Process -Id $runtime.Id -Force -ErrorAction SilentlyContinue
  Start-Sleep -Seconds 2
}

$uninstaller = Join-Path $installRoot "unins000.exe"
if (-not (Test-Path $uninstaller)) { throw "ORDAX Studio uninstaller is missing" }
$uninstallProcess = Start-Process -FilePath $uninstaller -ArgumentList @(
  "/VERYSILENT",
  "/SUPPRESSMSGBOXES",
  "/NORESTART"
) -Wait -PassThru
if ($uninstallProcess.ExitCode -ne 0) {
  throw "ORDAX Studio silent uninstall failed: $($uninstallProcess.ExitCode)"
}

$leftoverAutoStart = (Get-ItemProperty -Path $runKey -Name "ORDAX Runtime" -ErrorAction SilentlyContinue)."ORDAX Runtime"
if ($leftoverAutoStart) {
  throw "ORDAX Runtime auto-start was not removed by uninstall: $leftoverAutoStart"
}
