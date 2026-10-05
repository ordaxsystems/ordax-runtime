param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern("^[a-z0-9][a-z0-9_-]{0,63}$")]
    [string]$Slug,

    [Parameter(Mandatory = $true)]
    [string]$Path,

    [string[]]$Apps = @("unity", "blender"),

    [string[]]$AllowedBranches = @(),
    [string]$BlenderScriptsDir = "automation/blender",
    [string]$BlenderFile,
    [string]$UnityCompanionSource,
    [switch]$SetDefault,
    [switch]$RestartAgent,
    [switch]$ForceRestart,
    [string]$TaskName = "OrdaX Dev Agent"
)

$ErrorActionPreference = "Stop"

$normalizedApps = @(
    foreach ($value in @($Apps)) {
        if ($null -eq $value) { continue }
        foreach ($piece in ([string]$value -split "[,;]")) {
            $name = $piece.Trim().ToLowerInvariant()
            if ($name) { $name }
        }
    }
) | Select-Object -Unique

if (-not $normalizedApps -or $normalizedApps.Count -eq 0) {
    throw "At least one application must be enabled."
}

$allowedApps = @("unity", "blender")
$invalidApp = $normalizedApps | Where-Object { $_ -notin $allowedApps } | Select-Object -First 1
if ($invalidApp) {
    throw "Unsupported application '$invalidApp'. Allowed values: unity, blender."
}

$Apps = @($normalizedApps)

$invalidBranch = $AllowedBranches | Where-Object {
    [string]::IsNullOrWhiteSpace($_) -or $_.StartsWith("-")
} | Select-Object -First 1
if ($invalidBranch) {
    throw "AllowedBranches contains an invalid branch name: $invalidBranch"
}

if (-not (Test-Path -LiteralPath $Path -PathType Container)) {
    throw "Project directory not found: $Path"
}
$projectRoot = (Resolve-Path -LiteralPath $Path).Path

if ($Apps -contains "unity") {
    $assetsPath = Join-Path $projectRoot "Assets"
    $projectSettingsPath = Join-Path $projectRoot "ProjectSettings"
    if (-not (Test-Path -LiteralPath $assetsPath -PathType Container) -or
        -not (Test-Path -LiteralPath $projectSettingsPath -PathType Container)) {
        throw "Unity project needs Assets and ProjectSettings directories: $projectRoot"
    }
}

if ($BlenderFile) {
    $blendCandidate = if ([System.IO.Path]::IsPathRooted($BlenderFile)) {
        [System.IO.Path]::GetFullPath($BlenderFile)
    } else {
        [System.IO.Path]::GetFullPath((Join-Path $projectRoot $BlenderFile))
    }
    $rootWithSeparator = $projectRoot.TrimEnd("\", "/") + [System.IO.Path]::DirectorySeparatorChar
    if (-not $blendCandidate.StartsWith($rootWithSeparator, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "BlenderFile must remain inside the registered project."
    }
    if (-not (Test-Path -LiteralPath $blendCandidate -PathType Leaf)) {
        throw "BlenderFile does not exist: $blendCandidate"
    }
    $BlenderFile = $blendCandidate.Substring($rootWithSeparator.Length).Replace("\", "/")
}

$stateDir = Join-Path $env:LOCALAPPDATA "OrdaX\DevAgent"
$settingsPath = Join-Path $stateDir "agent-settings.json"
if (-not (Test-Path -LiteralPath $settingsPath -PathType Leaf)) {
    throw "OrdaX Dev Agent settings not found: $settingsPath"
}

try {
    $settings = Get-Content -LiteralPath $settingsPath -Raw -Encoding UTF8 | ConvertFrom-Json
} catch {
    throw "Cannot parse OrdaX Dev Agent settings: $($_.Exception.Message)"
}
if ($null -eq $settings) {
    throw "OrdaX Dev Agent settings are empty."
}

if ($null -eq $settings.projects) {
    $settings | Add-Member -NotePropertyName projects -NotePropertyValue ([pscustomobject]@{}) -Force
}

$projectEntry = [ordered]@{
    path = $projectRoot
    apps = @($Apps | Select-Object -Unique)
    allowed_branches = @($AllowedBranches | Select-Object -Unique)
}

if ($Apps -contains "unity") {
    $unity = [ordered]@{}
    if ($UnityCompanionSource) {
        $unity.companion_source = $UnityCompanionSource.Replace("\", "/")
    }
    $projectEntry.unity = [pscustomobject]$unity
}

if ($Apps -contains "blender") {
    $blender = [ordered]@{
        scripts_dir = $BlenderScriptsDir.Replace("\", "/")
    }
    if ($BlenderFile) {
        $blender.blend_file = $BlenderFile
    }
    $projectEntry.blender = [pscustomobject]$blender
}

$settings.projects | Add-Member -NotePropertyName $Slug -NotePropertyValue ([pscustomobject]$projectEntry) -Force

if ($SetDefault) {
    if ($settings.PSObject.Properties.Name -contains "default_project") {
        $settings.default_project = $Slug
    } else {
        $settings | Add-Member -NotePropertyName default_project -NotePropertyValue $Slug
    }
}

$json = $settings | ConvertTo-Json -Depth 20
$tempPath = Join-Path $stateDir ("agent-settings." + [Guid]::NewGuid().ToString("N") + ".tmp")
try {
    [System.IO.File]::WriteAllText($tempPath, $json, (New-Object System.Text.UTF8Encoding($false)))
    $validated = Get-Content -LiteralPath $tempPath -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($null -eq $validated.projects.$Slug -or $validated.projects.$Slug.path -ne $projectRoot) {
        throw "Generated settings failed validation before replacement."
    }
    Move-Item -LiteralPath $tempPath -Destination $settingsPath -Force
} finally {
    if (Test-Path -LiteralPath $tempPath) {
        Remove-Item -LiteralPath $tempPath -Force -ErrorAction SilentlyContinue
    }
}

$restarted = $false
if ($RestartAgent) {
    $busy = $false
    try {
        $status = Invoke-RestMethod -Uri "http://127.0.0.1:8765/status" -TimeoutSec 3
        $busy = $status.runtime -and $status.runtime.state -eq "busy"
    } catch {
        # The local status endpoint may be unavailable during recovery. The
        # Scheduled Task remains the authoritative local restart mechanism.
    }

    if ($busy -and -not $ForceRestart) {
        throw "Project was registered, but the Agent is busy. Re-run with -RestartAgent when it is idle."
    }

    Import-Module ScheduledTasks -ErrorAction Stop
    $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if (-not $task) {
        throw "Project was registered, but Scheduled Task was not found: $TaskName"
    }
    if ($task.State -eq "Disabled") {
        throw "Project was registered, but Scheduled Task is disabled: $TaskName"
    }
    if ($task.State -eq "Running") {
        Stop-ScheduledTask -TaskName $TaskName -ErrorAction Stop
        Start-Sleep -Seconds 1
    }
    Start-ScheduledTask -TaskName $TaskName -ErrorAction Stop
    $restarted = $true
}

[pscustomobject]@{
    ok = $true
    slug = $Slug
    path = $projectRoot
    apps = @($Apps | Select-Object -Unique)
    settings_path = $settingsPath
    default_project = if ($SetDefault) { $Slug } else { $settings.default_project }
    agent_restarted = $restarted
} | ConvertTo-Json -Depth 8 -Compress
