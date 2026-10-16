# Sign one production release artifact with the explicitly authorized ORDAX publisher certificate.
# The private key must already be present in CurrentUser\My on the release runner.
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$Path,
    [Parameter(Mandatory = $true)][string]$CertificateThumbprint,
    [Parameter(Mandatory = $true)][string]$TimestampServer,
    [Parameter(Mandatory = $true)][string]$TrustedSignerThumbprints
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

if ($CertificateThumbprint -cnotmatch '^[0-9a-fA-F]{40}$') {
    throw "ORDAX release signing certificate thumbprint is missing or invalid"
}
if ($TrustedSignerThumbprints -cnotmatch '^[0-9a-fA-F]{40}(,[0-9a-fA-F]{40}){0,3}$') {
    throw "Trusted ORDAX publisher certificate thumbprints are missing or invalid"
}

$normalizedThumbprint = $CertificateThumbprint.ToUpperInvariant()
$trusted = @($TrustedSignerThumbprints.Split(',') | ForEach-Object { $_.ToUpperInvariant() })
if (-not $trusted.Contains($normalizedThumbprint)) {
    throw "Release signing certificate is not present in the authorized ORDAX publisher trust set"
}

$timestampUri = $null
if (-not [Uri]::TryCreate($TimestampServer, [UriKind]::Absolute, [ref]$timestampUri) -or
    $timestampUri.Scheme -ne [Uri]::UriSchemeHttps) {
    throw "Release timestamp server must be an absolute HTTPS URL"
}

if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
    throw "Release installer file is missing"
}
$resolvedPath = (Resolve-Path -LiteralPath $Path).Path

$certificatePath = "Cert:\CurrentUser\My\$normalizedThumbprint"
$certificate = Get-Item -LiteralPath $certificatePath -ErrorAction SilentlyContinue
if (-not $certificate) {
    throw "Authorized ORDAX release signing certificate is not installed in CurrentUser\\My"
}
if (-not $certificate.HasPrivateKey) {
    throw "ORDAX release signing certificate does not expose its private key"
}
if ($certificate.Subject -eq $certificate.Issuer) {
    throw "Self-signed certificates are not accepted for public ORDAX release signing"
}
$now = Get-Date
if ($now -lt $certificate.NotBefore -or $now -ge $certificate.NotAfter) {
    throw "ORDAX release signing certificate is outside its validity window"
}

$codeSigningOid = '1.3.6.1.5.5.7.3.3'
$ekuOids = @(
    $certificate.Extensions |
        Where-Object { $_.Oid.Value -eq '2.5.29.37' } |
        ForEach-Object { $_.EnhancedKeyUsages } |
        ForEach-Object { $_.Value }
)
if (-not $ekuOids.Contains($codeSigningOid)) {
    throw "ORDAX release signing certificate is not valid for code signing"
}

$kitsRoot = Join-Path ${env:ProgramFiles(x86)} 'Windows Kits\10\bin'
$signtool = Get-ChildItem -Path $kitsRoot -Filter signtool.exe -Recurse -File -ErrorAction SilentlyContinue |
    Where-Object { $_.FullName -match '\\x64\\signtool\.exe$' } |
    Sort-Object FullName -Descending |
    Select-Object -First 1
if (-not $signtool) {
    throw "Windows SDK signtool.exe was not found on the release runner"
}

& $signtool.FullName sign /sha1 $normalizedThumbprint /s My /fd SHA256 /tr $timestampUri.AbsoluteUri /td SHA256 $resolvedPath
if ($LASTEXITCODE -ne 0) {
    throw "Authenticode signing failed"
}

$verificationScript = Join-Path $PSScriptRoot 'assert-release-authenticode.ps1'
& $verificationScript -Path $resolvedPath -TrustedSignerThumbprints $TrustedSignerThumbprints
if ($LASTEXITCODE -ne 0) {
    throw "Signed release artifact failed ORDAX Authenticode verification"
}

Write-Host "ORDAX_RELEASE_SIGNING_VALID"
Write-Host ("ORDAX_RELEASE_SIGNING_CERT_THUMBPRINT=" + $normalizedThumbprint)
Write-Host ("ORDAX_RELEASE_TIMESTAMP_SERVER=" + $timestampUri.AbsoluteUri)
