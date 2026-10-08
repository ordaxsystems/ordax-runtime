# Verify public Authenticode trust and the specifically authorized ORDAX publisher.
# Thumbprints are certificate identities, not artifact integrity hashes.
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$Path,
    [Parameter(Mandatory = $true)][string]$TrustedSignerThumbprints
)

$ErrorActionPreference = "Stop"

# Explicit, bounded certificate rotation list. Empty/malformed configuration fails closed.
if ($TrustedSignerThumbprints -cnotmatch '^[0-9a-fA-F]{40}(,[0-9a-fA-F]{40}){0,3}$') {
    throw "Trusted ORDAX publisher certificate thumbprints are missing or invalid"
}
$trusted = @($TrustedSignerThumbprints.Split(',') | ForEach-Object { $_.ToUpperInvariant() })
if (@($trusted | Select-Object -Unique).Count -ne $trusted.Count) {
    throw "Trusted ORDAX publisher certificate thumbprints contain duplicates"
}
if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
    throw "Release installer file is missing"
}

$resolved = (Resolve-Path -LiteralPath $Path).Path
$signature = Get-AuthenticodeSignature -FilePath $resolved

if ($signature.Status -ne [System.Management.Automation.SignatureStatus]::Valid) {
    throw "Release artifact is not Authenticode-valid: $resolved (status=$($signature.Status); message=$($signature.StatusMessage))"
}
if (-not $signature.SignerCertificate) {
    throw "Release artifact has no signer certificate: $resolved"
}
if (-not $signature.TimeStamperCertificate) {
    throw "Release artifact has no trusted timestamp: $resolved"
}
if ($signature.SignerCertificate.Subject -eq $signature.SignerCertificate.Issuer) {
    throw "Release artifact uses a self-signed certificate, which is not accepted for public ORDAX distribution."
}

$actualThumbprint = [string]$signature.SignerCertificate.Thumbprint
if ($actualThumbprint -cnotmatch '^[a-fA-F0-9]{40}$') {
    throw "Release artifact signer certificate has an invalid thumbprint"
}
if (-not $trusted.Contains($actualThumbprint.ToUpperInvariant())) {
    throw "Release artifact signer is not an authorized ORDAX publisher"
}

Write-Host "ORDAX_AUTHENTICODE_VALID"
Write-Host ("ORDAX_SIGNER_SUBJECT=" + $signature.SignerCertificate.Subject)
Write-Host ("ORDAX_SIGNER_THUMBPRINT=" + $actualThumbprint.ToUpperInvariant())
Write-Host ("ORDAX_TIMESTAMP_SUBJECT=" + $signature.TimeStamperCertificate.Subject)
