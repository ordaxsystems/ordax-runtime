param(
    [string]$TaskName = "OrdaX Dev Agent",
    [string]$RepoRoot = ""
)

$ErrorActionPreference = "Continue"

if (-not $RepoRoot) {
    $RepoRoot = Join-Path $env:LOCALAPPDATA "OrdaX\DevAgent\src"
}

$stateDir = Join-Path $env:LOCALAPPDATA "OrdaX\DevAgent"
$python = Join-Path $RepoRoot ".venv\Scripts\python.exe"
$pythonw = Join-Path $RepoRoot ".venv\Scripts\pythonw.exe"
$smokePath = Join-Path $stateDir "pythonw-smoke.txt"

function Section([string]$Name) {
    Write-Host ""
    Write-Host ("=== " + $Name + " ===")
}

Section "TASK"
$task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($task) {
    $task | Select-Object TaskName,State
    $task.Actions | Format-List Execute,Arguments,WorkingDirectory
    Get-ScheduledTaskInfo -TaskName $TaskName | Format-List LastRunTime,LastTaskResult,NextRunTime
} else {
    Write-Host "Scheduled task not found: $TaskName"
}

Section "PORT 8765"
netstat -ano | Select-String ":8765"

Section "PYTHON PROCESSES"
Get-Process python,pythonw -ErrorAction SilentlyContinue | Sort-Object StartTime | Select-Object Id,ProcessName,StartTime,CPU,Path

Section "TASK ENTRY LOG"
$taskEntryLog = Join-Path $stateDir "task-entry.log"
if (Test-Path $taskEntryLog) { Get-Content $taskEntryLog -Tail 80 } else { Write-Host "task-entry.log does not exist" }

Section "AGENT STARTUP LOG"
$startupLog = Join-Path $stateDir "agent-startup.log"
if (Test-Path $startupLog) { Get-Content $startupLog -Tail 80 } else { Write-Host "agent-startup.log does not exist" }

Section "TASK SCHEDULER EVENTS"
try {
    $events = Get-WinEvent -FilterHashtable @{
        LogName = "Microsoft-Windows-TaskScheduler/Operational"
        StartTime = (Get-Date).AddMinutes(-30)
    } -ErrorAction Stop | Where-Object { $_.Message -like ("*" + $TaskName + "*") } | Select-Object -First 40 TimeCreated,Id,LevelDisplayName,Message
    if ($events) { $events | Format-List } else { Write-Host "No matching Task Scheduler events in the last 30 minutes." }
} catch {
    Write-Host ("Task Scheduler event log unavailable: " + $_.Exception.Message)
}

Section "PYTHONW ISOLATED SMOKE"
Remove-Item -Force $smokePath -ErrorAction SilentlyContinue
if (Test-Path $pythonw) {
    $escapedSmoke = $smokePath.Replace("'", "''")
    $code = "from pathlib import Path; Path(r'$escapedSmoke').write_text('PYTHONW_OK', encoding='utf-8')"
    try {
        $p = Start-Process -FilePath $pythonw -ArgumentList @("-c", $code) -WorkingDirectory $RepoRoot -PassThru
        if (-not $p.WaitForExit(10000)) {
            Write-Host ("pythonw smoke timed out; pid=" + $p.Id)
        } else {
            Write-Host ("pythonw exit_code=" + $p.ExitCode)
        }
    } catch {
        Write-Host ("pythonw smoke launch failed: " + $_.Exception.Message)
    }
    if (Test-Path $smokePath) {
        Write-Host ("pythonw smoke result: " + (Get-Content $smokePath -Raw))
    } else {
        Write-Host "pythonw smoke file was not created"
    }
} else {
    Write-Host ("pythonw missing: " + $pythonw)
}

Section "PYTHON ISOLATED SMOKE"
if (Test-Path $python) {
    try {
        & $python -c "import sys; print('PYTHON_OK', sys.executable, sys.version)"
        Write-Host ("python exit_code=" + $LASTEXITCODE)
    } catch {
        Write-Host ("python smoke failed: " + $_.Exception.Message)
    }
} else {
    Write-Host ("python missing: " + $python)
}
