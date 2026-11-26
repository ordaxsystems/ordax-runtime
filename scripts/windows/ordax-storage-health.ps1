# Read-only Windows storage health. Run in an already-authorized host context.
# Never requests elevation, changes configuration, repairs volumes, or reboots.
[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

$principal = [Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
$administrator = $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
$report = [ordered]@{
    schema = 'ordax.windows.storage-health/1'
    observed_at = [DateTime]::UtcNow.ToString('o')
    administrator = $administrator
    physical_disks = @()
    reliability = @()
    smart_prediction = @()
    storage_events = @()
    errors = @()
}
function Add-CollectionError([string]$Section, [string]$Code) {
    $report.errors += [ordered]@{ section = $Section; code = $Code }
}
try {
    $disks = @(Get-PhysicalDisk -ErrorAction Stop)
    $report.physical_disks = @($disks | Select-Object DeviceId,FriendlyName,MediaType,HealthStatus,OperationalStatus,Size)
} catch {
    $disks = @()
    Add-CollectionError 'physical_disks' 'query_failed'
}
if ($administrator) {
    foreach ($disk in $disks) {
        try {
            $report.reliability += @($disk | Get-StorageReliabilityCounter -ErrorAction Stop |
                Select-Object DeviceId,Temperature,PowerOnHours,ReadErrorsTotal,ReadErrorsUncorrected,WriteErrorsTotal,WriteErrorsUncorrected)
        } catch {
            Add-CollectionError 'reliability' ('query_failed_disk_' + $disk.DeviceId)
        }
    }
    try {
        $report.smart_prediction = @(Get-CimInstance -Namespace root/wmi -ClassName MSStorageDriver_FailurePredictStatus -ErrorAction Stop |
            Select-Object InstanceName,PredictFailure,Reason)
    } catch {
        Add-CollectionError 'smart_prediction' 'query_failed'
    }
} else {
    Add-CollectionError 'reliability' 'administrator_required'
    Add-CollectionError 'smart_prediction' 'administrator_required'
}
try {
    $report.storage_events = @(Get-WinEvent -FilterHashtable @{
        LogName = 'System'
        StartTime = (Get-Date).AddHours(-24)
        Id = 7,11,41,51,55,129,153,157,161,1001,1074,6008
    } -MaxEvents 100 -ErrorAction Stop |
        Where-Object { $_.ProviderName -match 'disk|stor|ntfs|volmgr|Kernel-Power|WER-SystemErrorReporting|User32|EventLog' } |
        Select-Object @{Name='time';Expression={$_.TimeCreated.ToUniversalTime().ToString('o')}},Id,ProviderName)
} catch {
    if ($_.FullyQualifiedErrorId -notlike 'NoMatchingEventsFound*') {
        Add-CollectionError 'storage_events' 'query_failed'
    }
}
$report['state'] = if ($report.errors.Count -eq 0) { 'collected' } else { 'partial' }
$report | ConvertTo-Json -Depth 6 -Compress
