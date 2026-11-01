# Single, pinned Windows embedded-Python source for BOTH canonical Windows builders.
# The SHA-256 is sourced from the Python.org Windows release manifest and is
# checked for cached and newly downloaded bytes before any archive extraction.
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$CacheRoot,
    [Parameter(Mandatory = $true)][string]$PythonVersion
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
if ($PythonVersion -cnotmatch '^\d+\.\d+\.\d+$') {
    throw 'ORDAX_EMBEDDED_PYTHON_VERSION_INVALID'
}
$root = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..\..')).Path
$lockPath = Join-Path $root 'packaging\windows\embedded-python.lock.json'
$lock = Get-Content -LiteralPath $lockPath -Raw | ConvertFrom-Json
$name = "python-$PythonVersion-embeddable-amd64.zip"
$expectedUrl = "https://www.python.org/ftp/python/$PythonVersion/$name"
$expectedManifest = "https://www.python.org/ftp/python/$PythonVersion/windows-$PythonVersion.json"
if ($lock.schema -cne 'ordax.windows-embedded-python-lock/1' -or
    $lock.version -cne $PythonVersion -or
    $lock.architecture -cne 'amd64' -or
    $lock.filename -cne $name -or
    $lock.url -cne $expectedUrl -or
    $lock.source_manifest -cne $expectedManifest -or
    [string]$lock.sha256 -cnotmatch '^[0-9a-f]{64}$') {
    throw 'ORDAX_EMBEDDED_PYTHON_SOURCE_LOCK_INVALID'
}
$expectedDigest = [string]$lock.sha256
New-Item -ItemType Directory -Force -Path $CacheRoot | Out-Null
$archive = Join-Path $CacheRoot $name

function Assert-ArchiveHash([string]$Path) {
    $actual = (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($actual -cne $expectedDigest) {
        throw 'ORDAX_EMBEDDED_PYTHON_SHA256_MISMATCH'
    }
}

if (Test-Path -LiteralPath $archive) {
    if (-not (Test-Path -LiteralPath $archive -PathType Leaf)) {
        throw 'ORDAX_EMBEDDED_PYTHON_CACHE_INVALID'
    }
    Assert-ArchiveHash $archive
} else {
    $temporary = Join-Path $CacheRoot ('.ordax-python-' + [guid]::NewGuid().ToString('N') + '.tmp')
    try {
        Invoke-WebRequest -Uri $expectedUrl -OutFile $temporary -MaximumRedirection 0
        Assert-ArchiveHash $temporary
        Move-Item -LiteralPath $temporary -Destination $archive -ErrorAction Stop
    } finally {
        if (Test-Path -LiteralPath $temporary) {
            Remove-Item -LiteralPath $temporary -Force
        }
    }
}
# Deliberately emit only the verified path; callers use it as a data value.
Write-Output $archive
