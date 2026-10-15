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
4. Download into a private staging directory. Do not execute while verification is incomplete.
5. Verify SHA-256, Authenticode validity, timestamp and an approved ORDAX signer identity.
6. Quiesce only packaged ORDAX processes using the existing cooperative shutdown and scoped process verifier.
7. Run the canonical Inno Setup installer. Preserve device identity, projects, journals, profiles and credentials.
8. Start Runtime, verify installed manifest/version and bounded local health.
9. Start Studio only when appropriate and verify its installed presentation inventory.
10. If the installer or post-update health cannot be proven, enter recovery/rollback instead of claiming success.

## Current state — 2026-10-15

- In-place installer upgrade: implemented and tested in CI.
- Upgrade quiesce: implemented.
- Provenance/checksum gates: implemented.
- Authenticode verification policy: implemented.
- Production signing service and approved release publication: not operational yet; release stays fail-closed.
- Automatic installed-product discovery: started in `ordax_dev_agent/product_update.py` as read-only `status`.
- Automatic download/apply/rollback: not implemented yet.

The read-only discovery layer has `authority:none` and must never download or execute an installer. Application remains a separate privileged updater step after every production verification gate passes.
