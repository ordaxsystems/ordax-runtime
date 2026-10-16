# Sign one production release artifact with the explicitly authorized ORDAX publisher certificate.
# The private key must already be provisioned on the dedicated signing runner by an approved HSM/KSP/CSP.
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$Path,
    [Parameter(Mandatory = $true)][string]$CertificateThumbprint,
    [Parameter(Mandatory = $true)][string]$TimestampServer,
    [Parameter(Mandatory = $true)][string]$TrustedSignerThumbprints,
    [Parameter(Mandatory = $true)][string]$AllowedKeyProviders
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

if ($CertificateThumbprint -cnotmatch '^[0-9a-fA-F]{40}$') {
    throw "ORDAX release signing certificate thumbprint is missing or invalid"
}
if ($TrustedSignerThumbprints -cnotmatch '^[0-9a-fA-F]{40}(,[0-9a-fA-F]{40}){0,3}$') {
    throw "Trusted ORDAX publisher certificate thumbprints are missing or invalid"
}
if ([string]::IsNullOrWhiteSpace($AllowedKeyProviders)) {
    throw "Approved ORDAX release signing key providers are not configured"
}

$normalizedThumbprint = $CertificateThumbprint.ToUpperInvariant()
$trusted = @($TrustedSignerThumbprints.Split(',') | ForEach-Object { $_.ToUpperInvariant() })
if (-not $trusted.Contains($normalizedThumbprint)) {
    throw "Release signing certificate is not present in the authorized ORDAX publisher trust set"
}

$allowedProviders = @(
    $AllowedKeyProviders.Split(',') |
        ForEach-Object { $_.Trim() } |
        Where-Object { -not [string]::IsNullOrWhiteSpace($_) }
)
if ($allowedProviders.Count -eq 0 -or $allowedProviders.Count -gt 8) {
    throw "Approved ORDAX release signing key provider set is invalid"
}
$providerKeys = @($allowedProviders | ForEach-Object { $_.ToUpperInvariant() })
if (($providerKeys | Select-Object -Unique).Count -ne $providerKeys.Count) {
    throw "Approved ORDAX release signing key provider set contains duplicates"
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
    throw "ORDAX release signing certificate does not expose its HSM-backed private key"
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

$privateKey = [System.Security.Cryptography.X509Certificates.RSACertificateExtensions]::GetRSAPrivateKey($certificate)
if (-not $privateKey) {
    $privateKey = [System.Security.Cryptography.X509Certificates.ECDsaCertificateExtensions]::GetECDsaPrivateKey($certificate)
}
if (-not $privateKey) {
    throw "ORDAX release signing certificate private key type is unsupported"
}

try {
    $providerName = $null
    if ($privateKey -is [System.Security.Cryptography.RSACng]) {
        $providerName = $privateKey.Key.Provider.Provider
        if ($privateKey.Key.ExportPolicy -ne [System.Security.Cryptography.CngExportPolicies]::None) {
            throw "ORDAX release signing private key is exportable or archivable"
        }
    } elseif ($privateKey -is [System.Security.Cryptography.ECDsaCng]) {
        $providerName = $privateKey.Key.Provider.Provider
        if ($privateKey.Key.ExportPolicy -ne [System.Security.Cryptography.CngExportPolicies]::None) {
            throw "ORDAX release signing private key is exportable or archivable"
        }
    } elseif ($privateKey -is [System.Security.Cryptography.RSACryptoServiceProvider]) {
        $providerName = $privateKey.CspKeyContainerInfo.ProviderName
        if ($privateKey.CspKeyContainerInfo.Exportable) {
            throw "ORDAX release signing private key is exportable"
        }
    } else {
        throw "ORDAX release signing private key provider type is unsupported"
    }

    if ([string]::IsNullOrWhiteSpace($providerName) -or
        -not ($providerKeys -ccontains $providerName.ToUpperInvariant())) {
        throw "ORDAX release signing private key is not backed by an approved cryptographic provider"
    }
} finally {
    $privateKey.Dispose()
}

$signtool = Get-Command signtool.exe -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
if (-not $signtool) {
    throw "Windows SDK signtool.exe is not available on the dedicated release signing runner"
}

& $signtool.Source sign /sha1 $normalizedThumbprint /s My /fd SHA256 /tr $timestampUri.AbsoluteUri /td SHA256 $resolvedPath
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
Write-Host ("ORDAX_RELEASE_SIGNING_KEY_PROVIDER=" + $providerName)
Write-Host ("ORDAX_RELEASE_TIMESTAMP_SERVER=" + $timestampUri.AbsoluteUri)
