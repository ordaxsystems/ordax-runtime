# Isolated Windows contract for the canonical updater's Inno completion gate.
# Only a fake Start-Process is used; no ORDAX installer is executed.
[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'

$bootstrap = Join-Path $PSScriptRoot 'apply-ordax-production-update.ps1'
$tokens = $null
$parseErrors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile(
    $bootstrap, [ref]$tokens, [ref]$parseErrors
)
if (@($parseErrors).Count -gt 0) { throw 'Updater bootstrap has invalid PowerShell syntax' }
$functions = @($ast.FindAll({
    param($node)
    $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
    $node.Name -ceq 'Invoke-Installer'
}, $true))
if ($functions.Count -ne 1) { throw 'Canonical installer completion function is missing or duplicated' }
. ([ScriptBlock]::Create($functions[0].Extent.Text))

$transactionRootPath = Join-Path ([IO.Path]::GetTempPath()) ('ordax-installer-contract-' + [guid]::NewGuid().ToString('N'))
$installRootPath = Join-Path $transactionRootPath 'installed'
New-Item -ItemType Directory -Path $transactionRootPath -Force | Out-Null
$script:scenario = ''
$script:startCount = 0
$script:waitChecked = $false
$script:disposeCount = 0

function Start-Process {
    param(
        [string]$FilePath,
        [string[]]$ArgumentList,
        [switch]$PassThru,
        [switch]$Wait
    )
    if ($Wait -or -not $PassThru) {
        throw 'Updater must wait for the installer PID, never persistent descendants'
    }
    if ($FilePath -cne 'fixture-installer') { throw 'Unexpected executable' }
    $script:startCount++
    $logArgument = @($ArgumentList | Where-Object { $_ -like '/LOG=*' })
    if ($logArgument.Count -ne 1) { throw 'Installer must receive one durable log path' }
    $path = $logArgument[0].Substring(5)
    switch ($script:scenario) {
        'success' {
            [IO.File]::WriteAllText($path, "Installation process succeeded.`r`nLog closed.`r`n")
            $code = 0
        }
        'inno-failure' {
            [IO.File]::WriteAllText($path, "Installation process failed.`r`nLog closed.`r`n")
            $code = 0
        }
        'exit-failure' {
            [IO.File]::WriteAllText($path, "Installation process failed.`r`nLog closed.`r`n")
            $code = 5
        }
        'timeout' { $code = 0 }
        default { throw "Unknown isolated test scenario: $($script:scenario)" }
    }
    $fake = [pscustomobject]@{ ExitCode = $code }
    $fake | Add-Member -MemberType ScriptMethod -Name WaitForExit -Value {
        param($timeoutMs)
        if ($timeoutMs -ne 600000) { throw 'Installer PID wait is not bounded to 10 minutes' }
        $script:waitChecked = $true
        return ($script:scenario -ne 'timeout')
    }
    $fake | Add-Member -MemberType ScriptMethod -Name Dispose -Value {
        $script:disposeCount++
    }
    return $fake
}

function Assert-Fails {
    param([string]$ExpectedMessage)
    $failure = $null
    try { Invoke-Installer -Path 'fixture-installer' -LogName 'case.log' }
    catch { $failure = $_.Exception.Message }
    if (-not $failure -or $failure -notlike "*$ExpectedMessage*") {
        throw "Expected installer failure '$ExpectedMessage', got '$failure'"
    }
}

try {
    $script:scenario = 'success'
    Invoke-Installer -Path 'fixture-installer' -LogName 'case.log'
    if (-not $script:waitChecked -or $script:startCount -ne 1 -or $script:disposeCount -ne 1) {
        throw 'Successful installer did not wait/dispose exactly once'
    }
    Remove-Item -LiteralPath (Join-Path $transactionRootPath 'case.log') -Force

    $script:scenario = 'inno-failure'
    Assert-Fails -ExpectedMessage 'reported failure'
    Remove-Item -LiteralPath (Join-Path $transactionRootPath 'case.log') -Force

    $script:scenario = 'exit-failure'
    Assert-Fails -ExpectedMessage 'exited with code 5'
    Remove-Item -LiteralPath (Join-Path $transactionRootPath 'case.log') -Force

    $script:scenario = 'timeout'
    Assert-Fails -ExpectedMessage 'exceeded 10 minutes'

    $startsBeforeReplay = $script:startCount
    [IO.File]::WriteAllText((Join-Path $transactionRootPath 'case.log'), "Installation process succeeded.`r`nLog closed.`r`n")
    $script:scenario = 'success'
    Assert-Fails -ExpectedMessage 'log already exists'
    if ($script:startCount -ne $startsBeforeReplay) {
        throw 'A stale log caused an unintended installer launch'
    }
    if ($script:startCount -ne $script:disposeCount) {
        throw 'Installer process handles were not disposed'
    }
    Write-Output 'ORDAX_INSTALLER_COMPLETION_CONTRACT=PASS'
} finally {
    Remove-Item -LiteralPath $transactionRootPath -Recurse -Force -ErrorAction SilentlyContinue
}
