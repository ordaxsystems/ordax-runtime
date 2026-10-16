# ORDAX Product Update System

## Objective

The installed Windows product must update in place without source-tree copies, parallel runtimes, screen automation or manual binary replacement.

## Channels

- **Dev/Owner:** Git-first delivery from canonical repositories. A dirty checkout, changed install contract or failed targeted tests blocks promotion.
- **Production:** immutable Windows release only. Production never updates from Git or a portable candidate.

## Production flow

1. Read the canonical Inno Setup registration and installed version.
2. Discover the latest stable release from the canonical `ordaxsystems/ordax-runtime` release endpoint.
3. Require exact `vMAJOR.MINOR.PATCH`, `ORDAX-Studio-Setup-<version>-x64.exe` and `SHA256SUMS.txt`.
4. Download target and rollback release assets into a private per-user staging transaction. Do not execute while verification is incomplete.
5. Verify SHA-256, Authenticode validity, timestamp and an approved ORDAX signer identity for **both** target and rollback installers.
6. Hand off to a staged Windows PowerShell bootstrap outside the installed Runtime tree so the current Runtime can be quiesced safely.
7. Quiesce only packaged ORDAX processes using the existing cooperative shutdown and scoped process verifier.
8. Run the canonical Inno Setup installer. Preserve device identity, projects, journals, profiles and credentials.
9. Start Runtime and verify installed manifest/version plus bounded local action-ready health.
10. If target installation or health cannot be proven, execute the already staged and verified previous production installer. Record `rolled-back` only after the previous version is healthy; otherwise record `recovery-required` instead of claiming success.

## Security boundaries

- `ordax_dev_agent/product_update.py` remains read-only discovery with `authority:none`. It never downloads or executes an installer.
- `ordax_dev_agent/product_update_apply.py` is the separate local privileged transaction layer. It is not exposed as a remote MCP action.
- Release asset URLs must be the exact canonical GitHub Release paths for `ordaxsystems/ordax-runtime`. Redirects are accepted only to GitHub release-asset storage over HTTPS.
- `SHA256SUMS.txt` is parsed strictly; ambiguous, duplicate, malformed or path-bearing entries are rejected.
- Target and rollback installers are hashed again immediately before Authenticode verification and again by the external bootstrap before execution.
- Authenticode policy is not duplicated. The updater calls the canonical `scripts/windows/assert-release-authenticode.ps1` verifier.
- Authorized signer thumbprints come only from the packaged `ordax_dev_agent/assets/release_trust.json` trust root. Release metadata, environment variables and downloaded files cannot add a trusted publisher.
- The trust root is intentionally empty until the production signing identity is provisioned. An empty trust root blocks application fail-closed.
- The first public production release must already contain the approved signer thumbprint; an unsigned release cannot bootstrap trust for a later update.

## Transaction states

A transaction is journaled atomically under `%LOCALAPPDATA%\OrdaX\updates` and progresses through bounded states:

`sha256-verified -> authenticode-verified -> handoff-launching -> bootstrap-verifying -> bootstrap-verified -> installing-target -> committed`

Failure after target installation enters:

`rolling-back -> rolled-back`

If rollback itself cannot be proven healthy, the terminal state is:

`recovery-required`

Pre-execution policy or integrity failures become `blocked`; a failed handoff becomes `handoff-failed`. None of those states are reported as update success.

## Current state — 2026-10-16

- In-place installer upgrade: implemented and tested in CI and on the authorized Windows device.
- Upgrade quiesce: implemented.
- Provenance/checksum gates: implemented.
- Authenticode verification policy: implemented.
- Installed-product discovery: implemented as read-only `status` in `ordax_dev_agent/product_update.py`.
- Production updater transaction: implemented on `feat/windows-production-updater` with canonical download, private staging, target + rollback SHA-256 proof, packaged signer trust root, external bootstrap, bounded health verification and automatic rollback/recovery journaling.
- Production signing service and approved release publication: **not operational yet**. The packaged signer allow-list remains empty, so verification/application stays fail-closed and no `v*` production release should be published yet.

The remaining external gate is provisioning the production code-signing identity/service and inserting its approved Authenticode certificate thumbprint into the packaged trust root before the first stable production release.
