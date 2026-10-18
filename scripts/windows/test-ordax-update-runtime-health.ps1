# Isolated proof of the production updater's runtime-listener identity check.
# Parses ONLY the existing function, mocks OS observations and runs no installer.
$ErrorActionPreference = 'Stop'
$updater = Join-Path $PSScriptRoot 'apply-ordax-production-update.ps1'
$tokens = $null
$parseErrors = $null
$ast = [Management.Automation.Language.Parser]::ParseFile($updater, [ref]$tokens, [ref]$parseErrors)
if (@($parseErrors).Count -gt 0) { throw 'Production updater has invalid PowerShell syntax' }
$definition = $ast.Find({
    param($node)
    $node -is [Management.Automation.Language.FunctionDefinitionAst] -and
    $node.Name -eq 'Test-InstalledRuntimeListener'
}, $true)
if (-not $definition) { throw 'Updater runtime identity function is missing' }
Invoke-Expression $definition.Extent.Text

$installRootPath = Join-Path $env:TEMP 'ORDAX-HEALTH-ID-TEST'
$expectedPython = [IO.Path]::GetFullPath((Join-Path $installRootPath 'runtime\python.exe'))
$script:fakeListeners = @([pscustomobject]@{
    LocalAddress = '127.0.0.1'
    LocalPort = 8765
    OwningProcess = 1234
})
$script:fakeProcess = [pscustomobject]@{ ExecutablePath = $expectedPython }

function Get-NetTCPConnection {
    param([string]$LocalAddress, [int]$LocalPort, [string]$State, [string]$ErrorAction)
    if ($LocalAddress -ne '127.0.0.1' -or $LocalPort -ne 8765 -or $State -ne 'Listen') {
        throw 'Health probe requested a noncanonical socket'
    }
    return $script:fakeListeners
}

function Get-CimInstance {
    param([string]$ClassName, [string]$Filter, [string]$ErrorAction)
    if ($ClassName -ne 'Win32_Process' -or $Filter -ne 'ProcessId = 1234') {
        throw 'Health probe did not bind the socket owner PID'
    }
    return $script:fakeProcess
}

function Assert-HealthIdentity {
    param($Status, [bool]$Accept)
    $actual = [bool](Test-InstalledRuntimeListener -Status $Status)
    if ($actual -ne $Accept) {
        throw "Runtime identity result=$actual expected=$Accept"
    }
}

$status = [pscustomobject]@{ runtime = [pscustomobject]@{ pid = 1234; state = 'ready' } }
Assert-HealthIdentity -Status $status -Accept $true
Assert-HealthIdentity -Status ([pscustomobject]@{runtime=[pscustomobject]@{pid=4321}}) -Accept $false

$script:fakeProcess = [pscustomobject]@{ExecutablePath = (Join-Path $env:TEMP 'foreign\python.exe')}
Assert-HealthIdentity -Status $status -Accept $false
$script:fakeProcess = [pscustomobject]@{ExecutablePath = $expectedPython}

$script:fakeListeners = @([pscustomobject]@{LocalAddress='0.0.0.0';LocalPort=8765;OwningProcess=1234})
Assert-HealthIdentity -Status $status -Accept $false

$script:fakeListeners = @()
Assert-HealthIdentity -Status $status -Accept $false

$script:fakeListeners = @(
    [pscustomobject]@{LocalAddress='127.0.0.1';LocalPort=8765;OwningProcess=1234},
    [pscustomobject]@{LocalAddress='127.0.0.1';LocalPort=8765;OwningProcess=1234}
)
Assert-HealthIdentity -Status $status -Accept $false

Write-Host 'ORDAX_UPDATE_RUNTIME_LISTENER_IDENTITY=PASS'
