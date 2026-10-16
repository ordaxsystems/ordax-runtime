[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$ArtifactDirectory
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path -LiteralPath $ArtifactDirectory -PathType Container)) {
    throw "ORDAX installer artifact directory is missing"
}

$installers = @(Get-ChildItem -LiteralPath $ArtifactDirectory -File -Filter "ORDAX-Studio-Setup-*.exe")
if ($installers.Count -ne 1) {
    throw "Exactly one ORDAX Studio installer is required for provenance verification"
}

$installer = $installers[0]
$installRoot = Join-Path $env:RUNNER_TEMP "ordax-runtime-provenance-install"
$installLog = Join-Path $env:RUNNER_TEMP "ordax-runtime-provenance-install.log"
Remove-Item -LiteralPath $installRoot -Recurse -Force -ErrorAction SilentlyContinue

try {
    $installProcess = Start-Process -FilePath $installer.FullName -ArgumentList @(
        "/VERYSILENT",
        "/SUPPRESSMSGBOXES",
        "/NORESTART",
        "/DIR=$installRoot",
        "/LOG=$installLog"
    ) -Wait -PassThru

    if ($installProcess.ExitCode -ne 0) {
        if (Test-Path -LiteralPath $installLog) {
            Get-Content -LiteralPath $installLog -Tail 200
        }
        throw "ORDAX provenance verification install failed: $($installProcess.ExitCode)"
    }

    $privatePython = Join-Path $installRoot "runtime\python.exe"
    if (-not (Test-Path -LiteralPath $privatePython -PathType Leaf)) {
        throw "Installed private Python is missing"
    }

    $probe = @'
import ordax_dev_agent
import ordax_dev_agent.product_device_control_plane as control

plane = control.ProductDeviceControlPlane
assert plane.runtime_kind == "desktop-agent", plane.runtime_kind
assert hasattr(plane, "_runtime_provenance")
assert control.DEVICE_AGENT_VERSION == ordax_dev_agent.__version__
assert isinstance(ordax_dev_agent.__version__, str) and ordax_dev_agent.__version__.strip()
print("ORDAX_INSTALLED_RUNTIME_PROVENANCE=PASS")
print("ORDAX_INSTALLED_AGENT_VERSION=" + ordax_dev_agent.__version__)
'@

    & $privatePython -I -c $probe
    if ($LASTEXITCODE -ne 0) {
        throw "Installed Runtime provenance module verification failed"
    }
} finally {
    $uninstaller = Join-Path $installRoot "unins000.exe"
    if (Test-Path -LiteralPath $uninstaller -PathType Leaf) {
        $uninstallProcess = Start-Process -FilePath $uninstaller -ArgumentList @(
            "/VERYSILENT",
            "/SUPPRESSMSGBOXES",
            "/NORESTART"
        ) -Wait -PassThru
        if ($uninstallProcess.ExitCode -ne 0) {
            throw "ORDAX provenance verification uninstall failed: $($uninstallProcess.ExitCode)"
        }
    }
    Remove-Item -LiteralPath $installRoot -Recurse -Force -ErrorAction SilentlyContinue
}
