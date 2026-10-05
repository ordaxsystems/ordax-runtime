# Shared Unity discovery and installation diagnostics for Windows PowerShell 5.1+.

function Get-RequiredUnityVersion {
    param([string]$Root)

    if (-not $Root) { return $null }

    $versionFile = Join-Path $Root "ProjectSettings\ProjectVersion.txt"
    if (-not (Test-Path -LiteralPath $versionFile)) { return $null }

    $match = Select-String -LiteralPath $versionFile -Pattern '^m_EditorVersion:\s*(\S+)\s*$' |
        Select-Object -First 1
    if ($match) { return $match.Matches[0].Groups[1].Value }
    return $null
}

function Get-UnityEditorRoots {
    $roots = @()

    if ($env:UNITY_EDITOR_ROOTS) {
        $roots += $env:UNITY_EDITOR_ROOTS -split [IO.Path]::PathSeparator
    }

    $roots += "C:\Program Files\Unity\Hub\Editor"
    if ($env:LOCALAPPDATA) { $roots += (Join-Path $env:LOCALAPPDATA "Unity\Hub\Editor") }
    if ($env:USERPROFILE) { $roots += (Join-Path $env:USERPROFILE "Unity\Hub\Editor") }

    $seen = @{}
    foreach ($root in $roots) {
        if ([string]::IsNullOrWhiteSpace($root)) { continue }
        $key = $root.ToLowerInvariant()
        if (-not $seen.ContainsKey($key)) {
            $seen[$key] = $true
            $root
        }
    }
}

function Test-UnityVersionCandidateName {
    param(
        [string]$DirectoryName,
        [string]$RequiredVersion
    )

    if (-not $RequiredVersion) { return $true }
    $pattern = '^' + [regex]::Escape($RequiredVersion) + '(?:[-_].+)?$'
    return $DirectoryName -match $pattern
}

function Get-UnityCandidatePaths {
    param([string]$RequiredVersion)

    $paths = @()
    foreach ($editorRoot in (Get-UnityEditorRoots)) {
        if (-not (Test-Path -LiteralPath $editorRoot -PathType Container)) { continue }

        $directories = Get-ChildItem -LiteralPath $editorRoot -Directory -ErrorAction SilentlyContinue
        foreach ($directory in $directories) {
            if (-not (Test-UnityVersionCandidateName -DirectoryName $directory.Name -RequiredVersion $RequiredVersion)) {
                continue
            }
            $paths += (Join-Path $directory.FullName "Editor\Unity.exe")
        }
    }
    return $paths
}

function Get-UnityApiCompatibilityLevel {
    param([string]$Root)

    if (-not $Root) { return $null }
    $settingsFile = Join-Path $Root "ProjectSettings\ProjectSettings.asset"
    if (-not (Test-Path -LiteralPath $settingsFile)) { return $null }

    $match = Select-String -LiteralPath $settingsFile -Pattern '^\s*apiCompatibilityLevel:\s*(\d+)\s*$' |
        Select-Object -First 1
    if ($match) { return [int]$match.Matches[0].Groups[1].Value }
    return $null
}

function Get-UnityInstallationDiagnostics {
    param(
        [string]$Unity,
        [string]$ProjectRoot
    )

    if (-not $Unity) {
        return [PSCustomObject]@{
            EditorAvailable = $false
            ApiCompatibilityLevel = Get-UnityApiCompatibilityLevel -Root $ProjectRoot
            RequiredUnityVersion = Get-RequiredUnityVersion -Root $ProjectRoot
            InstalledUnityVersion = $null
            VersionCompatible = $false
            ReferenceAssembliesRequired = $null
            ReferenceAssembliesAvailable = $null
            ReferenceAssembliesPath = $null
            PackageManagerAvailable = $false
            PackageManagerPath = $null
            MissingComponents = @("Unity.exe")
            InstallationHealthy = $false
        }
    }

    $editorDir = Split-Path -Parent $Unity
    $versionDir = Split-Path -Parent $editorDir
    $installedVersion = Split-Path -Leaf $versionDir
    $dataDir = Join-Path $editorDir "Data"
    $apiLevel = Get-UnityApiCompatibilityLevel -Root $ProjectRoot
    $requiredVersion = Get-RequiredUnityVersion -Root $ProjectRoot
    $references = Join-Path $dataDir "UnityReferenceAssemblies\unity-4.8-api\Facades"
    $upm = Join-Path $dataDir "Resources\PackageManager\Server\UnityPackageManager.exe"
    $required = if ($null -ne $apiLevel) { $apiLevel -eq 6 } else { $null }
    $referencesAvailable = if ($required -eq $false) { $null } else { Test-Path -LiteralPath $references }
    $missing = @()

    if (-not (Test-Path -LiteralPath $Unity)) { $missing += $Unity }
    if (-not (Test-Path -LiteralPath $dataDir)) { $missing += $dataDir }
    $versionCompatible = Test-UnityVersionCandidateName -DirectoryName $installedVersion -RequiredVersion $requiredVersion
    if ($requiredVersion -and -not $versionCompatible) {
        $missing += "Unity editor version $installedVersion does not match required $requiredVersion"
    }
    if ($required -and -not $referencesAvailable) { $missing += $references }
    if (-not (Test-Path -LiteralPath $upm)) { $missing += $upm }

    [PSCustomObject]@{
        EditorAvailable = Test-Path -LiteralPath $Unity
        ApiCompatibilityLevel = $apiLevel
        RequiredUnityVersion = $requiredVersion
        InstalledUnityVersion = $installedVersion
        VersionCompatible = $versionCompatible
        ReferenceAssembliesRequired = $required
        ReferenceAssembliesAvailable = $referencesAvailable
        ReferenceAssembliesPath = $references
        PackageManagerAvailable = Test-Path -LiteralPath $upm
        PackageManagerPath = $upm
        MissingComponents = $missing
        InstallationHealthy = ($missing.Count -eq 0)
    }
}

function Get-UnityResolution {
    param(
        [string]$ProjectRoot,
        [string]$RequiredVersion = ""
    )

    if (-not $RequiredVersion) {
        $RequiredVersion = Get-RequiredUnityVersion -Root $ProjectRoot
    }

    # An explicit override is authoritative. Keep it selected so its health
    # is reported instead of silently replacing it with another installation.
    if (-not [string]::IsNullOrWhiteSpace($env:UNITY_EXE)) {
        $explicit = $env:UNITY_EXE
        $diagnostics = Get-UnityInstallationDiagnostics -Unity $explicit -ProjectRoot $ProjectRoot
        $candidate = [PSCustomObject]@{
            Path = $explicit
            DirectoryName = $diagnostics.InstalledUnityVersion
            Healthy = $diagnostics.InstallationHealthy
            MissingComponents = @($diagnostics.MissingComponents)
            Diagnostics = $diagnostics
        }
        $errorText = $null
        if (-not $diagnostics.InstallationHealthy) {
            $errorText = "UNITY_EXE points to an invalid Unity installation: " + ($diagnostics.MissingComponents -join "; ")
        }
        return [PSCustomObject]@{
            SelectedPath = $explicit
            Source = "explicit"
            RequiredVersion = $RequiredVersion
            Candidates = @($candidate)
            SelectedDiagnostics = $diagnostics
            InstallationHealthy = $diagnostics.InstallationHealthy
            ExplicitInvalid = (-not $diagnostics.InstallationHealthy)
            ExplicitError = $errorText
        }
    }

    $records = @()
    foreach ($candidatePath in @(Get-UnityCandidatePaths -RequiredVersion $RequiredVersion)) {
        $diagnostics = Get-UnityInstallationDiagnostics -Unity $candidatePath -ProjectRoot $ProjectRoot
        $records += [PSCustomObject]@{
            Path = $candidatePath
            DirectoryName = $diagnostics.InstalledUnityVersion
            Healthy = $diagnostics.InstallationHealthy
            MissingComponents = @($diagnostics.MissingComponents)
            Diagnostics = $diagnostics
        }
    }

    $records = @($records | Sort-Object `
        @{Expression = { if ($_.Healthy) { 0 } else { 1 } }}, `
        @{Expression = { if ($RequiredVersion -and $_.DirectoryName -ieq $RequiredVersion) { 0 } else { 1 } }}, `
        @{Expression = { $_.DirectoryName.ToLowerInvariant() }}, `
        @{Expression = { $_.Path.ToLowerInvariant() }})

    $selected = $records | Select-Object -First 1
    $selectedDiagnostics = if ($selected) {
        $selected.Diagnostics
    }
    else {
        Get-UnityInstallationDiagnostics -Unity $null -ProjectRoot $ProjectRoot
    }

    return [PSCustomObject]@{
        SelectedPath = if ($selected) { $selected.Path } else { $null }
        Source = "automatic"
        RequiredVersion = $RequiredVersion
        Candidates = $records
        SelectedDiagnostics = $selectedDiagnostics
        InstallationHealthy = $selectedDiagnostics.InstallationHealthy
        ExplicitInvalid = $false
        ExplicitError = $null
    }
}

function Find-Unity {
    param([string]$Root)
    return (Get-UnityResolution -ProjectRoot $Root).SelectedPath
}
