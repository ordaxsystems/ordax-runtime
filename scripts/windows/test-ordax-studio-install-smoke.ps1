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

function Wait-OrdaxInstallerCompletion {
  param(
    [System.Diagnostics.Process]$Process,
    [string]$LogPath,
    [string]$Label
  )
  # Start-Process -Wait also waits for children. Since the installer now starts
  # a persistent background Runtime, that incorrectly waits for the Runtime
  # to exit. Wait for the installer PID, then for Inno's durable log footer.
  if (-not $Process.WaitForExit(600000)) {
    throw "$Label installer process exceeded 10 minutes"
  }
  if ($Process.ExitCode -ne 0) {
    throw "$Label installer exited with code $($Process.ExitCode)"
  }
  $deadline = [DateTime]::UtcNow.AddMinutes(6)
  do {
    if (Test-Path -LiteralPath $LogPath -PathType Leaf) {
      $tail = @(Get-Content -LiteralPath $LogPath -Tail 32 -ErrorAction SilentlyContinue)
      $completed = [bool](@($tail | Where-Object { $_ -match 'Installation process succeeded\.' }).Count)
      $closed = [bool](@($tail | Where-Object { $_ -match 'Log closed\.' }).Count)
      if ($completed -and $closed) {
        Write-Host ($Label + "_INSTALL_LOG_CONFIRMED")
        return
      }
      if (@($tail | Where-Object { $_ -match 'Installation process failed\.' }).Count) {
        throw "$Label installer logged a failure"
      }
    }
    Start-Sleep -Milliseconds 750
  } while ([DateTime]::UtcNow -lt $deadline)
  throw "$Label did not complete and close its installation log"
}

function Assert-SingleAutoStartedRuntime {
  param(
    [string]$ExecutablePath,
    [string]$Label
  )
  # An HTTP reply alone could come from a stale localhost listener. Require
  # exactly one installed process at this path and an action-ready Runtime.
  $deadline = [DateTime]::UtcNow.AddSeconds(60)
  do {
    $matches = @(Get-Process -ErrorAction SilentlyContinue | Where-Object {
      $_.Path -and [IO.Path]::GetFullPath($_.Path) -ieq [IO.Path]::GetFullPath($ExecutablePath)
    })
    if ($matches.Count -gt 1) {
      throw "$Label launched duplicate packaged ORDAX Runtime processes"
    }
    if ($matches.Count -eq 1) {
      $null = Wait-OrdaxReady -Label $Label
      Write-Host ($Label + "_AUTO_STARTED=PASS")
      return $matches[0]
    }
    Start-Sleep -Milliseconds 750
  } while ([DateTime]::UtcNow -lt $deadline)
  throw "$Label did not start the packaged ORDAX Runtime automatically"
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
) -PassThru
  Wait-OrdaxInstallerCompletion -Process $installProcess -LogPath $installLog -Label 'installProcess'
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
$ownerHelp = @(& $privatePython -I -m ordax_dev_agent.owner_grants_cli --help)
if ($LASTEXITCODE -ne 0 -or -not (($ownerHelp -join " ") -match '\bdiagnose\b')) {
  throw "Installed private Runtime lacks canonical Owner MCP readiness CLI"
}
Write-Host "ORDAX_INSTALLED_OWNER_MCP_DIAGNOSE=PASS"
& (Join-Path $PSScriptRoot "assert-runtime-package-capabilities.ps1") `
  -PrivatePython $privatePython `
  -RuntimeRoot (Join-Path $installRoot "runtime") `
  -Phase "installed"
& $privatePython -c "import importlib.util; assert importlib.util.find_spec('webview') is None; print('ORDAX_NO_LEGACY_WEBVIEW_IMPORT=PASS')"
if ($LASTEXITCODE -ne 0) { throw "Retired WebView2/pywebview Python host survived Electron cutover" }

# Verify the actual installed Python supports the canonical Electron login pipe
# in isolated (-I) mode, and rejects a synthetic request before network/login.
$accountProbeIn = Join-Path $env:RUNNER_TEMP "ordax-account-probe-in.json"
$accountProbe = $null
try {
  [IO.File]::WriteAllText($accountProbeIn, '{"schema":"ordax.studio-product-account-session/1","operation":"unsupported","email":"invalid","password":"synthetic"}')
  # Start-Process -PassThru may return an unowned PID-based process wrapper
  # if the child exits immediately; its ExitCode is then unavailable. Own the
  # process handle directly and enforce an exact bounded wait instead.
  $probeStart = New-Object System.Diagnostics.ProcessStartInfo
  $probeStart.FileName = $privatePython
  $probeStart.WorkingDirectory = $installRoot
  $probeStart.Arguments = "-I -u -m ordax_studio.electron_account_session"
  $probeStart.UseShellExecute = $false
  $probeStart.CreateNoWindow = $true
  $probeStart.RedirectStandardInput = $true
  $probeStart.RedirectStandardOutput = $true
  $probeStart.RedirectStandardError = $true
  $accountProbe = New-Object System.Diagnostics.Process
  $accountProbe.StartInfo = $probeStart
  if (-not $accountProbe.Start()) { throw "Installed account IPC probe failed to start" }
  $accountProbe.StandardInput.Write([IO.File]::ReadAllText($accountProbeIn))
  $accountProbe.StandardInput.Close()
  if (-not $accountProbe.WaitForExit(15000)) {
    $accountProbe.Kill()
    $null = $accountProbe.WaitForExit(5000)
    throw "Installed account IPC probe exceeded 15 seconds"
  }
  # Invalid requests yield only a single small bounded JSON line.
  $accountOut = $accountProbe.StandardOutput.ReadToEnd()
  $accountErr = $accountProbe.StandardError.ReadToEnd()
  if ($accountProbe.ExitCode -ne 1) {
    throw "Installed account IPC did not reject invalid operation (exit $($accountProbe.ExitCode))"
  }
  $accountReply = $accountOut | ConvertFrom-Json
  if ($accountReply.schema -ne "ordax.studio-product-account-session/1" -or
      $accountReply.ok -ne $false -or $accountReply.error -ne "account_auth_failed" -or
      $accountReply.PSObject.Properties.Name -contains "access_token" -or
      -not [string]::IsNullOrWhiteSpace($accountErr)) {
    throw "Installed Product account IPC violated the fail-closed response contract"
  }
  Write-Host "ORDAX_INSTALLED_ACCOUNT_PIPE_FAIL_CLOSED=PASS"
} finally {
  if ($accountProbe) { $accountProbe.Dispose() }
  Remove-Item -LiteralPath $accountProbeIn -Force -ErrorAction SilentlyContinue
}

# Silent install must start the Runtime itself; do not let Studio mask a
# missing post-install launch by starting it on the first UI open.
$runtime = Assert-SingleAutoStartedRuntime -ExecutablePath $runtimeExe -Label "ORDAX_SILENT_INSTALL_RUNTIME"
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
  # One official launcher for both presentations; no separate Central runtime.
  $centralShortcut = Join-Path ([Environment]::GetFolderPath("Programs")) "ORDAX\ORDAX Central.lnk"
  if (-not (Test-Path -LiteralPath $centralShortcut -PathType Leaf)) {
    throw "Installed Central Start Menu shortcut is missing"
  }
  $shortcut = (New-Object -ComObject WScript.Shell).CreateShortcut($centralShortcut)
  if ([IO.Path]::GetFullPath($shortcut.TargetPath) -ne [IO.Path]::GetFullPath($studioExe) -or
      $shortcut.Arguments -ne "--ordax-central") {
    throw "Central shortcut bypasses official Studio supervisor or lacks mode parameter"
  }
  Write-Host "ORDAX_INSTALLED_CENTRAL_SHORTCUT=PASS"
  # This one-shot navigation may exit before Start-Process -PassThru
  # retains an owned handle; use Process.Start to obtain a real ExitCode.
  $navigationStart = New-Object System.Diagnostics.ProcessStartInfo
  $navigationStart.FileName = $studioExe
  $navigationStart.WorkingDirectory = $installRoot
  $navigationStart.Arguments = "--ordax-central"
  $navigationStart.UseShellExecute = $false
  $centralNavigation = New-Object System.Diagnostics.Process
  $centralNavigation.StartInfo = $navigationStart
  try {
    if (-not $centralNavigation.Start()) { throw "Central navigation did not start" }
    if (-not $centralNavigation.WaitForExit(30000)) {
      $centralNavigation.Kill()
      throw "Central navigation did not finish within 30 seconds"
    }
    if ($centralNavigation.ExitCode -ne 0) {
      throw "Running Studio did not accept Central navigation request"
    }
  } finally {
    $centralNavigation.Dispose()
  }
  $studio.Refresh()
  if ($studio.HasExited) { throw "Central navigation terminated the existing Studio supervisor" }
  Write-Host "ORDAX_CENTRAL_EXISTING_SUPERVISOR=PASS"


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

# Opening Central from a cold Studio startup must pass the flag to the same
# Electron owner; the already running Runtime remains independent.
$centralStudio = Start-Process -FilePath $studioExe -WorkingDirectory $installRoot -ArgumentList "--ordax-central" -PassThru
try {
  # A cold Electron startup can take longer than two seconds on a new Windows
  # profile. Observe the actual child and argument until the bounded deadline;
  # neither a bare launcher PID nor a foreign Electron instance is sufficient.
  $centralPresentation = $null
  $sawExpectedChild = $false
  $sawCentralArgument = $false
  $centralDeadline = [DateTime]::UtcNow.AddSeconds(45)
  do {
    $centralStudio.Refresh()
    if ($centralStudio.HasExited) {
      throw "Central-only Studio launcher exited with code $($centralStudio.ExitCode)"
    }
    $candidate = Get-CimInstance Win32_Process -Filter "ParentProcessId = $($centralStudio.Id)" | Where-Object {
      $_.ExecutablePath -and [IO.Path]::GetFullPath($_.ExecutablePath) -eq [IO.Path]::GetFullPath($presentationExe)
    } | Select-Object -First 1
    if ($candidate) {
      $sawExpectedChild = $true
      $sawCentralArgument = [bool]($candidate.CommandLine -match '(?<!\S)--ordax-central(?!\S)')
      if ($sawCentralArgument) {
        $centralPresentation = $candidate
        break
      }
    }
    Start-Sleep -Milliseconds 300
  } while ([DateTime]::UtcNow -lt $centralDeadline)
  if (-not $centralPresentation) {
    Write-Host "ORDAX_CENTRAL_EXPECTED_CHILD_SEEN=$sawExpectedChild"
    Write-Host "ORDAX_CENTRAL_ARGUMENT_SEEN=$sawCentralArgument"
    throw "Central startup did not reach the canonical Electron presentation with the required argument"
  }
  & (Join-Path $PSScriptRoot "assert-installed-studio-ux.ps1") -InstallRoot $installRoot -PresentationProcessId $centralPresentation.ProcessId -WindowTimeoutSeconds 60
  Write-Host "ORDAX_CENTRAL_COLD_START=PASS"
} finally {
  Stop-Process -Id $centralStudio.Id -Force -ErrorAction SilentlyContinue
  Get-Process | Where-Object { $_.Path -and [IO.Path]::GetFullPath($_.Path) -eq [IO.Path]::GetFullPath($presentationExe) } |
    Stop-Process -Force -ErrorAction SilentlyContinue
  Start-Sleep -Seconds 1
}


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
  ) -PassThru
  Wait-OrdaxInstallerCompletion -Process $upgradeProcess -LogPath $upgradeLog -Label 'upgradeProcess'
  if ($upgradeProcess.ExitCode -ne 0) {
    if (Test-Path $upgradeLog) { Get-Content $upgradeLog -Tail 200 }
    throw "ORDAX Studio silent upgrade failed: $($upgradeProcess.ExitCode)"
  }

  Start-Sleep -Milliseconds 750
  $runtime.Refresh()
  if (-not $runtime.HasExited) {
    throw "running runtime did not exit during upgrade"
  }
  $upgradeRuntime = Assert-SingleAutoStartedRuntime -ExecutablePath $runtimeExe -Label "ORDAX_SILENT_UPGRADE_RUNTIME"
  if ($upgradeRuntime.Id -eq $runtime.Id) {
    throw "Silent upgrade left the previous ORDAX Runtime in place"
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

  & (Join-Path $PSScriptRoot "assert-runtime-package-capabilities.ps1") `
    -PrivatePython $privatePython `
    -RuntimeRoot (Join-Path $installRoot "runtime") `
    -Phase "upgraded"

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
  ) -PassThru
  Wait-OrdaxInstallerCompletion -Process $repairProcess -LogPath $repairLog -Label 'repairProcess'
  if ($repairProcess.ExitCode -ne 0) {
    if (Test-Path $repairLog) { Get-Content $repairLog -Tail 200 }
    throw "ORDAX Studio idempotent repair install failed: $($repairProcess.ExitCode)"
  }
  if (Test-Path $alternateInstallRoot) {
    throw "partial alternate ORDAX install root survived idempotent repair"
  }
  Write-Host "PARTIAL_LEGACY_INSTALL_ROOT_REMOVED"

  & (Join-Path $PSScriptRoot "assert-runtime-package-capabilities.ps1") `
    -PrivatePython $privatePython `
    -RuntimeRoot (Join-Path $installRoot "runtime") `
    -Phase "repaired"

  foreach ($required in @($studioExe, $legacyStudioExe, $runtimeExe, $presentationExe, $privatePython)) {
    if (-not (Test-Path $required)) {
      if (Test-Path $upgradeLog) { Get-Content $upgradeLog -Tail 200 }
      throw "Upgraded product file is missing: $required"
    }
  }
  Assert-StudioAlias -StudioExe $studioExe -LegacyExe $legacyStudioExe

  # The repair is also a silent upgrade. The installer must restart the
  # Runtime by itself; a manual Start-Process here would conceal regressions.
  $upgradedRuntime = Assert-SingleAutoStartedRuntime -ExecutablePath $runtimeExe -Label "ORDAX_SILENT_REPAIR_RUNTIME"
  $upgradeRuntime.Refresh()
  if (-not $upgradeRuntime.HasExited -or $upgradedRuntime.Id -eq $upgradeRuntime.Id) {
    throw "Silent repair did not replace the ORDAX Runtime started by the previous upgrade"
  }
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
# Own the short-lived uninstaller handle as well; a PID-based
# Start-Process wrapper can lose ExitCode if it finishes before attach.
$uninstallStart = New-Object System.Diagnostics.ProcessStartInfo
$uninstallStart.FileName = $uninstaller
$uninstallStart.Arguments = "/VERYSILENT /SUPPRESSMSGBOXES /NORESTART"
$uninstallStart.UseShellExecute = $false
$uninstallStart.CreateNoWindow = $true
$uninstallProcess = New-Object System.Diagnostics.Process
$uninstallProcess.StartInfo = $uninstallStart
try {
  if (-not $uninstallProcess.Start()) { throw "ORDAX Studio uninstaller did not start" }
  if (-not $uninstallProcess.WaitForExit(300000)) {
    $uninstallProcess.Kill()
    throw "ORDAX Studio uninstaller exceeded 5 minutes"
  }
  if ($uninstallProcess.ExitCode -ne 0) {
    throw "ORDAX Studio silent uninstall failed: $($uninstallProcess.ExitCode)"
  }
} finally {
  $uninstallProcess.Dispose()
}

$leftoverAutoStart = (Get-ItemProperty -Path $runKey -Name "ORDAX Runtime" -ErrorAction SilentlyContinue)."ORDAX Runtime"
if ($leftoverAutoStart) {
  throw "ORDAX Runtime auto-start was not removed by uninstall: $leftoverAutoStart"
}
