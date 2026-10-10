# Read-only, fail-closed validation of the *installed* Studio UI and asset encoding.
# Called by the canonical Windows installer smoke; safe to invoke manually.
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$InstallRoot,
    [Parameter(Mandatory = $true)][int]$PresentationProcessId,
    [ValidateRange(1, 180)][int]$WindowTimeoutSeconds = 60
)

$ErrorActionPreference = "Stop"

# Get-Process.MainWindowHandle can be 0 even when a WPF top-level window exists.
# Check actual HWND ownership/visibility rather than treating a PID as proof of UI.
Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
using System.Text;
public static class OrdaxStudioVisibleWindow
{
    private delegate bool EnumWindowCallback(IntPtr hwnd, IntPtr data);
    [StructLayout(LayoutKind.Sequential)]
    private struct Rect { public int Left, Top, Right, Bottom; }
    [DllImport("user32.dll")] private static extern bool EnumWindows(EnumWindowCallback callback, IntPtr data);
    [DllImport("user32.dll")] private static extern uint GetWindowThreadProcessId(IntPtr hwnd, out uint processId);
    [DllImport("user32.dll")] private static extern bool IsWindowVisible(IntPtr hwnd);
    [DllImport("user32.dll")] private static extern bool IsHungAppWindow(IntPtr hwnd);
    [DllImport("user32.dll", CharSet = CharSet.Unicode)] private static extern int GetWindowText(IntPtr hwnd, StringBuilder text, int count);
    [DllImport("user32.dll")] private static extern bool GetWindowRect(IntPtr hwnd, out Rect rect);

    public static bool HasReadyWindow(int processId)
    {
        bool found = false;
        EnumWindows((hwnd, data) =>
        {
            uint owner;
            GetWindowThreadProcessId(hwnd, out owner);
            if (owner != (uint)processId || !IsWindowVisible(hwnd) || IsHungAppWindow(hwnd))
                return true;
            var title = new StringBuilder(256);
            GetWindowText(hwnd, title, title.Capacity);
            Rect rect;
            if (title.ToString().StartsWith("ORDAX Studio", StringComparison.OrdinalIgnoreCase)
                && GetWindowRect(hwnd, out rect)
                && rect.Right > rect.Left && rect.Bottom > rect.Top)
            {
                found = true;
                return false;
            }
            return true;
        }, IntPtr.Zero);
        return found;
    }
}
'@

$deadline = [DateTime]::UtcNow.AddSeconds($WindowTimeoutSeconds)
$found = $false
do {
    $process = Get-Process -Id $PresentationProcessId -ErrorAction SilentlyContinue
    if ($null -eq $process -or $process.HasExited) {
        throw "ORDAX Studio Electron exited before showing the Conversation window"
    }
    if ([OrdaxStudioVisibleWindow]::HasReadyWindow($PresentationProcessId)) {
        $found = $true
        break
    }
    Start-Sleep -Milliseconds 350
} while ([DateTime]::UtcNow -lt $deadline)

if (-not $found) {
    throw "ORDAX Studio Electron process is running but no responsive, visible Conversation window appeared"
}
Write-Host "ORDAX_STUDIO_VISIBLE_WINDOW_OK"

# Installed executable/bytes must match the single verified Apps presenter.
# Never accept only a process PID or file name as proof of the UI version.
$manifestFile = Join-Path $InstallRoot "product-manifest.json"
if (-not (Test-Path -LiteralPath $manifestFile -PathType Leaf)) { throw "Installed Product manifest missing" }
$product = Get-Content -LiteralPath $manifestFile -Raw -Encoding UTF8 | ConvertFrom-Json
$presentation = Join-Path $InstallRoot "presentation"
$inventoryPath = Join-Path $presentation "build-manifest.json"
$hostPath = Join-Path $presentation "resources\app\package.json"
$conversation = Join-Path $presentation "resources\app\apps\studio\conversation\src\index.html"
$legacyWorkbench = Join-Path $InstallRoot "workbench\ORDAX Workbench.exe"
$legacyHtml = Join-Path $InstallRoot "runtime\Lib\site-packages\ordax_studio\studio_product.html"
if (Test-Path -LiteralPath $legacyWorkbench -PathType Leaf) { throw "Retired WPF presentation survived upgrade" }
if (Test-Path -LiteralPath $legacyHtml -PathType Leaf) { throw "Retired WebView2 Studio page survived upgrade" }
foreach ($file in @($inventoryPath, $hostPath, $conversation)) {
    if (-not (Test-Path -LiteralPath $file -PathType Leaf)) { throw "Installed Studio Conversation file missing: $file" }
}
$inventory = Get-Content -LiteralPath $inventoryPath -Raw -Encoding UTF8 | ConvertFrom-Json
$hostPackage = Get-Content -LiteralPath $hostPath -Raw -Encoding UTF8 | ConvertFrom-Json
if ($product.schema -ne "ordax.windows-product/1" -or
    $product.presentation_host -ne "electron" -or
    $product.entrypoints.studio_ui -ne "presentation\\ORDAX Studio.exe" -or
    $product.entrypoints.runtime -ne "ORDAX Runtime.exe" -or
    $inventory.sourceRepository -ne "ordaxsystems/ordax-apps" -or
    $inventory.entrypoint -ne "apps/studio/conversation/src/index.html" -or
    $inventory.candidate -ne $true -or
    $inventory.version -ne $product.version -or
    $hostPackage.version -ne $product.version -or
    $hostPackage.main -ne "tools/assistant-host/native/main.cjs") {
    throw "Installed Studio UI provenance, owner, version or entrypoint diverged"
}
$expected = @{}
foreach ($source in @($inventory.files)) {
    $relative = [string]$source.path
    if ($relative -notmatch '^[^/\\][^\\]*$' -or
        $relative.Split('/') -contains '..' -or
        $relative.Split('/') -contains '.' -or
        $expected.ContainsKey($relative) -or
        [string]$source.sha256 -cnotmatch '^[a-f0-9]{64}$') {
        throw "Untrusted Studio file inventory"
    }
    $expected[$relative] = [string]$source.sha256
}
if ($expected.Count -lt 100) { throw "Installed Electron UI has unexpectedly few files" }
$checked = 0
foreach ($file in @(Get-ChildItem -LiteralPath $presentation -File -Recurse -Force)) {
    if (($file.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) { throw "Installed UI contains a reparse point" }
    $relative = $file.FullName.Substring($presentation.Length).TrimStart('\', '/').Replace('\', '/')
    if ($relative -eq "build-manifest.json") { continue }
    if (-not $expected.ContainsKey($relative)) { throw "Unexpected installed UI file: $relative" }
    if ((Get-FileHash -Algorithm SHA256 -LiteralPath $file.FullName).Hash.ToLowerInvariant() -cne $expected[$relative]) {
        throw "Installed Electron UI hash mismatch: $relative"
    }
    $checked++
}
if ($checked -ne $expected.Count) { throw "Installed Electron UI file set differs from canonical inventory" }
# Prove that the actual Conversation HTML remains correctly encoded.
$utf8 = New-Object System.Text.UTF8Encoding($false, $true)
$conversationText = $utf8.GetString([IO.File]::ReadAllBytes($conversation))
if (-not $conversationText.Contains("ORDAX Studio") -or
    -not $conversationText.Contains("studio-product") -and
    -not $conversationText.Contains("conversation")) {
    throw "Installed Studio Conversation HTML is incompatible"
}
Write-Host "ORDAX_STUDIO_INSTALLED_ELECTRON_FILES_VERIFIED=$checked"
Write-Host "ORDAX_STUDIO_INSTALLED_CONVERSATION=PASS"
