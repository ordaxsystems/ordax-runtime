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

## Current state — 2026-10-16 (historical record)

- In-place installer upgrade: implemented and tested in CI and on the authorized Windows device.
- Upgrade quiesce: implemented.
- Provenance/checksum gates: implemented.
- Authenticode verification policy: implemented.
- Installed-product discovery: implemented as read-only `status` in `ordax_dev_agent/product_update.py`.
- Production updater transaction: implemented on `feat/windows-production-updater` with canonical download, private staging, target + rollback SHA-256 proof, packaged signer trust root, external bootstrap, bounded health verification and automatic rollback/recovery journaling.
- Production signing service and approved release publication: **not operational yet**. The packaged signer allow-list remains empty, so verification/application stays fail-closed and no `v*` production release should be published yet.

The remaining external gate is provisioning the production code-signing identity/service and inserting its approved Authenticode certificate thumbprint into the packaged trust root before the first stable production release.

## Transaction handoff and rollback integrity

The existing `scripts/windows/apply-ordax-production-update.ps1` is the **only** Windows update executor. Its external bootstrap must rebind every argument (source installation version, canonical target and rollback filenames, versions, staging paths, SHA-256 and transaction identifier) to the durable `transaction.json` before either installer is run. Only the journal's `handoff-launching` phase under `%LOCALAPPDATA%\\OrdaX\\updates` may initiate application; terminal transactions are not replayable, and refused replays cannot overwrite committed, rolled-back or recovery-required receipts.

Before a target install or rollback executable is actually launched, the same production Authenticode verifier and SHA-256 check run again against the corresponding staged file. An installer that changes after initial verification is **never executed**. A failed last-moment verification **before target execution** blocks the update without unnecessarily reinstalling the old version. On a target execution or health failure, rollback is still reported as successful **only** after the previous version is reinstalled and becomes healthy; failure to verify or restore is recorded as `recovery-required`. The executable names and trust roots remain unchanged, and this adds no unsigned test mode or second updater.

Windows packaging CI includes isolated negative bootstrap tests that deliberately forge journal authority, swap rollback hashes, change installed version, alter target paths and replay a terminal transaction. They must stop before calling the verifier or any installer. Passing those tests **does not** substitute for a true on-device recovery test, nor does it authorize applying unsigned Owner/Dev artifacts.

## Post-install Runtime identity verification

The canonical Windows bootstrap must not accept a generic HTTP response as proof of update or recovery health. The Runtime status at `127.0.0.1:8765/status` includes the private Python process PID. Before either a target transaction becomes `committed` or a rollback becomes `rolled-back`, the bootstrap requires the status PID to equal the actual owning PID of the **single** loopback TCP listener on port 8765 and its Windows process executable path to match `{installRoot}\\runtime\\python.exe`. It also checks the installed product version, runtime-ready state and nonempty action inventory.

An unrelated application, stale foreign listener or misleading JSON response therefore cannot approve an update. The same health verification function is used after target and rollback installations, with no alternate endpoint or independent health subsystem. The Windows packaging gate executes the production identity function against positive and adversarial OS observations; it must reject a mismatched PID, foreign executable path, wildcard/nonloopback address, absent listener and multiple listeners. This does not certify the deployment of the new code on the Owner PC until its Runtime is upgraded.

## Owner development preflight — 2026-10-18

The Studio **0.15.9** source is merged in `ordaxsystems/ordax-apps` via PRs #263 and #264; Runtime PR #125 was merged as `e7a3c27252f2a9e044f32c9c7351585e03276855`, pinning the Apps commit `a297b691d5a91ea0370d80f32533931596dfe5f6`. The strict five required checks completed green on the synchronized PR head, including the Windows in-place `install-smoke` in CI. These proofs validate a test runner and are **not** evidence that the user's installed Runtime has been updated.

The authorized development device `DESKTOP-COHT67R` still reports installed Studio **0.15.6** (Runtime manifest version `0.4.6`), whose installed `studio_source_commit` is `ab1ceda55abd226bc5c12b60f47c052276e19e5d`. The old CI artifact from run `38918111156` has the same canonical Studio source commit, version 0.15.6, and a build time consistent with the installed product. The target artifact is from run `39107644356`, version 0.15.9.

Both Inno installers and their `SHA256SUMS.txt` were downloaded **only to a private per-user staging directory**, not to Downloads or the installed Runtime tree. Streaming SHA-256 comparison against the respective CI manifests succeeded:

| Role | CI run | Installer | SHA-256 |
| --- | --- | --- | --- |
| Rollback candidate | `38918111156` | `ORDAX-Studio-Setup-0.15.6-x64.exe` | `8cab1ac935d70f4cce622df5217597d048495a245d071dc76f2522fe55397c85` |
| Upgrade candidate | `39107644356` | `ORDAX-Studio-Setup-0.15.9-x64.exe` | `b28d542ba65baaf4ed5d9b46862aaa58484e88605aca157521a9fb43a5731897` |

This is **artifact provenance and integrity evidence**, not a signature or an end-to-end rollback proof. Source-commit equality does not alone prove the recovered installer is byte-identical to the one originally installed. No installer has been executed. No local account, device identity, personal files or Runtime service has been changed during staging.

**Promotion remains blocked:** production lacks an approved signing service and populated signer trust root, so the production updater must stay fail-closed. Do **not** pass these unsigned CI candidates through production update or quietly bypass Authenticode. To enable an Owner-only development upgrade, first implement and test an explicit authorized Dev/Owner transaction path that **reuses the canonical installer/quiesce/health/rollback primitives** rather than duplicating an updater; require immutable CI run IDs, pinned source revisions, exact staged checksums, previous-installation identity, restoration proof and durable state journaling. Until that passes, retain installed 0.15.6.

Separately, the connected OrdaX MCP reports the local computer policy `full_access=true` and `full_filesystem=true`, while the remote `full-computer-control` OAuth grant has not been confirmed. Studio 0.15.9 adds the active Central Owner authorization UI, but it must first reach the installed product. Test the first MCP operation in an entirely fresh conversation after Owner consent; warm-session operations do not establish cold-start reliability.

## Owner/Dev CI provenance — protected-main gate

The canonical `.github/workflows/windows-product-build.yml` is also triggered on a protected `main` push. When, and **only when**, the `build-installer` and `install-smoke` jobs both succeed on that push, the `attest-owner-dev-candidate` job downloads the exact artifact from its workflow run, checks a one-installer / one-`SHA256SUMS.txt` inventory, verifies the installer digest, and creates the standard **GitHub Artifact Attestation** (Sigstore-backed SLSA build provenance) using a pinned upstream action and ephemeral GitHub OIDC. The job has no release signing keys and no permission to publish releases. After creating the attestation, **the same job independently invokes `gh attestation verify`** against the exact candidate bytes, the canonical GitHub workflow and `GITHUB_SHA`, failing closed on any mismatch.

A trusted Owner/Dev preflight must reject PR-built, branch-only or ad hoc unsigned installers that lack that attestation. After downloading the candidate from the original main run, verify the actual executable bytes with the GitHub CLI using the expected **immutable canonical main commit**:

```powershell
gh attestation verify "ORDAX-Studio-Setup-<version>-x64.exe" --repo ordaxsystems/ordax-runtime --source-digest "<exact-protected-main-commit>" --signer-workflow "ordaxsystems/ordax-runtime/.github/workflows/windows-product-build.yml"
```

The GitHub CLI treats `--signer-repo` and `--signer-workflow` as mutually exclusive issuer selectors; use **only** the more specific workflow selector shown above, together with `--repo` and `--source-digest`.

Replace the placeholders only after verifying the selected run identity and exact SHA-256 of the downloaded file. Attestation absence, mismatch, unsupported identity or a non-main commit must fail closed. The **existing** 0.15.6/0.15.9 preflight artifacts were generated before this gate existed; SHA-256 validation alone is **not** a GitHub attestation, and this workflow does not retroactively certify them.

This is a provenance prerequisite, **not** an installer or a second updater. Production continues to require a publicly trusted Authenticode timestamped signature from an approved signing service, canonical immutable releases and the shared verification/transaction/rollback mechanism. Do not pass unsigned CI artifacts into `product_update_apply.py` or `apply-ordax-production-update.ps1`; no unsigned fallback or per-tool override is authorized. A future Owner/Dev apply route may use only a separately approved, independently tested trust policy that reuses the same transaction and recovery primitives and proves both the target and rollback.

