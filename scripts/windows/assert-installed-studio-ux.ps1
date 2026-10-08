# Read-only, fail-closed validation of the *installed* Studio UI and asset encoding.
# Called by the canonical Windows installer smoke; safe to invoke manually.
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$InstallRoot,
    [Parameter(Mandatory = $true)][int]$WorkbenchProcessId,
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
    $process = Get-Process -Id $WorkbenchProcessId -ErrorAction SilentlyContinue
    if ($null -eq $process -or $process.HasExited) {
        throw "ORDAX Workbench exited before showing its main window"
    }
    if ([OrdaxStudioVisibleWindow]::HasReadyWindow($WorkbenchProcessId)) {
        $found = $true
        break
    }
    Start-Sleep -Milliseconds 350
} while ([DateTime]::UtcNow -lt $deadline)

if (-not $found) {
    throw "ORDAX Workbench process is running but no responsive, visible Studio main window appeared"
}
Write-Host "ORDAX_STUDIO_VISIBLE_WINDOW_OK"

$package = Join-Path $InstallRoot "runtime\Lib\site-packages\ordax_studio"
$document = Join-Path $package "studio_product.html"
if (-not (Test-Path -LiteralPath $document -PathType Leaf)) {
    throw "Installed Studio HTML document is missing"
}
$utf8 = New-Object System.Text.UTF8Encoding($false, $true)
$assets = @(Get-ChildItem -LiteralPath $package -Recurse -File | Where-Object {
    $_.Extension -in @(".html", ".js", ".css")
})
if ($assets.Count -eq 0) {
    throw "Installed Studio contains no HTML/JS/CSS assets"
}

# Byte patterns decoded twice (UTF-8 read as Latin-1/Windows-1252) are
# unacceptable in UI; do not include non-ASCII source literals in this PS1.
$badContinuations = @(0x00A0, 0x00A1, 0x00A2, 0x00A3, 0x00A7, 0x00A9,
    0x00AA, 0x00AD, 0x00B3, 0x00B5, 0x00BA)
$badSequences = @($badContinuations | ForEach-Object {
    ([string][char]0x00C3) + ([string][char]$_)
})
$badSequences += ([string][char]0x00E2) + ([string][char]0x20AC)
$badSequences += ([string][char]0x00C2) + ([string][char]0x00B7)
$badSequences += ([string][char]0x00C2) + ([string][char]0x00B0)

foreach ($asset in $assets) {
    try {
        $text = $utf8.GetString([IO.File]::ReadAllBytes($asset.FullName))
    } catch [System.Text.DecoderFallbackException] {
        throw "Invalid UTF-8 in installed Studio asset: $($asset.Name)"
    }
    foreach ($bad in $badSequences) {
        if ($text.Contains($bad)) {
            throw "Double-decoded text found in installed Studio asset: $($asset.Name)"
        }
    }
}
Write-Host ("ORDAX_STUDIO_UTF8_ASSETS_OK=" + $assets.Count)
