param(
    [Parameter(Mandatory = $true)]
    [string]$Path
)

$ErrorActionPreference = "Stop"

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

Write-Host "ORDAX_AUTHENTICODE_VALID"
Write-Host ("ORDAX_SIGNER_SUBJECT=" + $signature.SignerCertificate.Subject)
Write-Host ("ORDAX_SIGNER_THUMBPRINT=" + $signature.SignerCertificate.Thumbprint)
Write-Host ("ORDAX_TIMESTAMP_SUBJECT=" + $signature.TimeStamperCertificate.Subject)
