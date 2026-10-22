# OrdaX Project Workspace v1 — Runtime boundary (M1 first slice)

**Status (branch `feat/runtime-workspace-resource-authority-20261022`):** Runtime metadata core, native Windows picker, Owner/Product scope resolver, and client methods for the Platform resource registration, verification and revocation routes. These remain source-only: Platform PR #274 is open, its SQL is still a proposal, and neither the authenticated interactive host nor the Apps port is wired. No production capability is enabled. Related: [Runtime issue #184](https://github.com/ordaxsystems/ordax-runtime/issues/184), [Platform PR #274](https://github.com/ordaxsystems/ordax-platform/pull/274), [Apps PR #268](https://github.com/ordaxsystems/ordax-apps/pull/268).

## Trust boundary

Runtime owns directory resolution and project-scoped observations; Platform owns Product authorization and consent. No method creates permissions, accepts a model path as authority, or turns computer Full Access into a Lab project grant.

ordax_dev_agent.project_workspace_port.ProjectWorkspacePort takes:
- device_id: identity of the local Runtime device.
- grants(actor, deviceId, resourceId): trusted **live** resolver supplied by Platform/Runtime bridge; returns a typed VerifiedProjectResource for the exact actor, client, workspace session, Lab project, device and resource, or None to deny.
- local_policy(root): canonical Runtime computer-access validator; may deny even with a Product grant.

Neither callback has a permissive default. User JSON never manufactures grant evidence; the Runtime transport must authenticate the resolver. A resourceId is a non-bearer opaque reference, not a credential.

The grant includes expiry, a root identity (filesystem device/inode) to detect move/swap and exactly read permission. Reparse points are rejected. Grant and policy are checked before verification/snapshot and again after a snapshot, so a revoked grant cannot yield a stale successful summary.

## ABI (matches the Apps alpha.11 consumer seam)

verify_binding(actor, deviceId, resourceId): authorized, projectId, actorId, clientId, workspaceSessionId, deviceId and resourceId.

snapshot(actor, deviceId, resourceId): authorized, deviceId, resourceId, state=connected, revision, observedAt, fileCount, changedCount and bounded Git metadata (isRepository, optional branch and head). No folder names, paths, content, Git remotes, secrets, command output or per-file diffs. Failure throws instead of falling back to broad filesystem access.

Revision is a fingerprint of file metadata and Git state, **not** a cryptographic content integrity attestation. The snapshot skips symlinks, junctions, reparse points, .git and dependency/build caches. It caps traversal at 20,000 files and 24 levels; Git calls are read-only, with no shell, interactive prompt, optional locks or fsmonitor. Inherited `GIT_*` environment variables are stripped before Git execution, so an ambient `GIT_DIR`, alternate index, worktree or injected config cannot redirect the observation.

**Git trust rule:** only a regular, locally contained `.git` directory is eligible. Linked-worktree `.git` pointer files and symlink/junction/reparse-point `.git` directories are rejected with `workspace_git_boundary_unverified` until the trusted Runtime owner can attest their external Git metadata against an explicit resource grant. This restriction is intentional; it must not be bypassed in Apps.

## Host-only selection seam (source-only, pending host/App composition)

`ordax_dev_agent.workspace_resource_selection.WorkspaceResourceSelection` builds on the **existing** `resolve_project_for_repository` allow-list and `ProjectWorkspacePort`; it is not another filesystem executor or an independent grant ledger.

- Constructor requires `choose_directory(project)`, `confirm_owner(project, root)`, `canonical_project_scope(repository, device)`, and `live_authority(actor, device, resource, scope)` supplied by **trusted local host composition**. Optional `register_resource(scope, actor, resource)` and `revoke_resource(scope, actor, resource)` callbacks must be supplied together for the production lifecycle. Merely setting a callback or building a typed dataclass is not proof of trusted caller identity.
- The native picker must run in the interactive **Owner user session** (not the headless Windows service session). Its selected root must equal the unique already-registered GitHub project root; selecting a parent, arbitrary child, symlink, junction or different path fails closed. The owner must separately confirm it. No model or renderer-selected path may call this Python seam.
- **Canonical Owner scope pinning:** before opening any native dialog, Runtime requires `OwnerCanonicalProjectScope` supplied by the authenticated Owner host, based on the existing `ProductRemoteClient.owner_project_scope()` resolver and intended Product client. It pins exact Owner UUID, client UUID, Space UUID, Product project UUID, device UUID and normalized GitHub repository. Rechecks the same scope after Owner confirmation to reject mid-dialog reassignment. The opaque resource references that exact scope; it cannot later be used with an otherwise well-formed proof for another Owner/Space/project/client. This is a *local equality boundary*, not another authority ledger.
- With lifecycle callbacks, `choose_for_owner(repository, actor)` registers the opaque ID using the signed Owner JWT and existing Device credential, checks the returned exact read-only proof, then revalidates consent, canonical project scope and root identity before returning only the cryptographically random non-bearer `resourceId`. Without those callbacks, the ID remains a non-authorizing local selection. Paths and filesystem identity stay in Runtime memory, never in Platform, Lab SQLite or MCP payload. Unconfirmed registrations are revoked best-effort and never retained locally. In-process references expire within 24 hours and are limited to 64.
- `resolve_live` **always** calls the trusted Platform proof callback; it requires exact pinned canonical Owner/Space/Product project, Lab project, actor, OAuth client, workspace session, device and resource, grant UUID, issued-at timestamp, expiry (original lifetime <=24 hours) and read-only scope. It revalidates the registered repository mapping and root identity. A local selection, an opaque ID or full-computer-control permission cannot independently allow `verify_binding` or `snapshot`.
- `as_port(local_policy=...)` composes the canonical `ProjectWorkspacePort`; there is no new snapshot API. `revoke_for_owner` calls Platform before dropping the matching local reference. `discard_local_selection` drops only the Runtime reference and **does not** revoke the canonical Product resource.
- The reference registry is intentionally **ephemeral**: Runtime restart loses all selections and denies reuse. Durable cross-restart recovery needs its own authenticated rebind gate; never deserialize SQLite rows into a local root.
- The seam is **not wired to a live Owner UI or the Orchestrator App**. Runtime's client targets the draft route in Platform PR #274, not a deployed endpoint. No read is enabled until Platform governance, authenticated Owner/Device host composition, the App bridge and a real Windows E2E are complete. It cannot silently elevate a legacy project or run automatically in a service.

## Authenticated Owner/Product scope resolver (source-only)

`ordax_dev_agent.owner_project_scope_resolver.AuthenticatedOwnerProjectScopeResolver` is the **single adapter** for canonical project scope and the optional resource lifecycle callbacks. It reuses the existing Owner session, consent and project-scope reads, and calls typed `ProductRemoteClient` methods for the proposed Platform routes. The Device credential and Owner token are supplied just-in-time and are not retained. It creates no cloud route, grant table, filesystem executor, or persistent token cache.

- The trusted interactive Owner host supplies (1) its private, already authenticated account-token supplier, (2) the signed Product Owner `subject_id`, (3) the enrolled exact device UUID, and (4) the explicitly selected OAuth MCP client UUID. These fields must NEVER originate from model/renderer/MCP payload. The adapter cannot prove caller provenance on its own and is not exported as an action.
- Each call reads `GET /v3/product/session` (signed Owner identity + token expiry), `GET /v3/owner/remote-authority?device_id=...` (owned active device and OAuth client consent, not merely grant-group presence), and `GET /v3/owner/project-scope` (canonical Space/project for the registered GitHub repository). It rechecks the session and consent after project resolution, before returning the exact typed `OwnerCanonicalProjectScope`. The broker repeats the entire resolution after native Owner confirmation and pins all fields for future proofs.
- Only an **exactly once** matching, non-revoked OAuth client in the owner-authority consent list is acceptable. Unknown client, different Owner, expired session, device mismatch, changed project mapping, transport failure, canceled sign-in or missing consent deny. There is no cached success, fallback to generic full-computer-control, fabricated scope or token persisted in the Runtime.
- Read-only source and disposable HTTP mock integration tests verify endpoint sequence, consent revoked during resolution, session invalidated during resolution, malformed project mapping, negative identity, absence of token in the adapter instance, and that selecting a local Git fixture still produces no readable grant.
- **Not yet operational:** the packaged Owner UI has not composed these callbacks with its private authenticated account session. OAuth consent and project scope are necessary but not sufficient for a filesystem grant. Platform PR #274 contains the route/SQL proposal for binding an opaque resource to the existing read grant; issue #266 still gates migration/deploy. No production migration/deploy or real file access is included.

## Product workspace verification projection (source-only)

`ordax_dev_agent.project_workspace_live_proof.project_workspace_live_proof` is the read-only **response decoder** for the canonical Platform SQL proposal `ordax.project-workspace-resource/1`, not a new permission service. It accepts **only a reply already obtained through an independently authenticated Device/Product transport**, the previously pinned `OwnerCanonicalProjectScope`, and the trusted Runtime `WorkspaceActor`. Raw renderer, LLM or MCP JSON must never be given authority by constructing a response.

- Requires the exact known outer/grant fields. Rejects unknown fields (including `path`, credentials), wrong schema, inactive/revoked resources, wrong issuance provenance, write capabilities, noncanonical UUIDs, any mismatch of Owner/Space/Product project/Lab project/client/actor/session/device/resource and malformed timestamps.
- Requires original issuance and expiry, both timezone-aware, with original grant lifetime at most 24h and positive remaining duration. The output is the **existing** `LiveWorkspaceAuthority` dataclass used by `WorkspaceResourceSelection.resolve_live`, not a duplicate grant ledger.
- Tests exercise wrong identity in each dimension, foreign grant, mid-revocation response, extra/missing fields, legacy picker, token/path injection and expiry boundaries.
- **No network is performed** by the decoder. Platform PR #274 proposes the Owner registration/revocation route and reuses the existing Device proof transport from PR #273; the SQL is still unapplied and the route is not deployed. Runtime's typed client now targets those contracts. The next gate must compose the authenticated Owner and Device providers and preserve fresh proof checks on every `verify_binding` and postscan. A forged raw response must never be treated as attested.
- No App capability is enabled by this file. An Owner-authenticated UI selection, transactional grant issuance/revocation, migration governance, signed Device transport and real VESTE Windows E2E remain mandatory.

## Windows native Owner-session UI adapter (source-only)

`ordax_dev_agent.windows_owner_workspace_picker.WindowsOwnerWorkspacePicker` implements the **two existing callbacks** of `WorkspaceResourceSelection` directly via Windows APIs; there is no separate filesystem/policy implementation.

- **Choose:** COM `IFileOpenDialog` with `FOS_PICKFOLDERS|FOS_FORCEFILESYSTEM|FOS_PATHMUSTEXIST`, initialized at the Runtime-registered project root. The native OS returns the chosen directory **inside** the same process; Runtime broker still enforces exact root identity and reparse/junction restrictions.
- **Confirm:** a separate native `MessageBoxW` explaining the project and full selected path, with default answer **No**. Clicking `Sim` confirms **only** the local selection; it does not emit a Product grant, send a path or enable reading.
- **Session gate:** Windows only, main GUI thread, process session != 0 and visible interactive window station. Any headless service, scheduler, incompatible COM apartment, unavailable dialog, cancellation, or confirmation error must deny; no fallback picker, shell command, PowerShell, localhost listener or temporary persistence.
- **COM:** STA apartment scoped to the dialog; interfaces, path memory, and COM apartment released on success, cancellation or error. No Win32 permissions are changed.
- **Ownership constraint:** these callbacks can be composed ONLY by an already authenticated interactive Owner host. The session/desktop check is not identity attestation; the **host composition and Platform Product authority must remain separate**. The adapter is deliberately **not wired** into Studio HTTP, generic computer actions, MCP, the headless Runtime service, or the Orchestrator Lab renderer.

Tests exercise both callbacks, denial in noninteractive environments, native COM cancel/error cleanup and default-deny confirmation with **mock dialogs** on Windows and Linux CI. They do not pretend to prove a real interactive Owner UI or Platform grant E2E.

## Not yet implemented

- Trusted **interactive Owner host composition** of the Windows native picker and the new Owner/Device callbacks; the UI adapter exists but is not yet invoked by a packaged Owner GUI.
- Authenticated Runtime transport binding this port to the App. The current packaged App still has no port and reports `capability_unavailable`.
- Canonical local policy callback wired into a live device agent.
- Packaged Windows App + real granted VESTE fixture E2E; deployment/install/rollback.

Do **not** wire a temporary raw filesystem adapter into Electron. The App alpha.11 intentionally reports capability_unavailable until the actual grant transport exists.

## Test gate

`python -m unittest tests.test_project_workspace_port tests.test_workspace_resource_selection tests.test_windows_owner_workspace_picker tests.test_owner_project_scope_resolver tests.test_project_workspace_live_proof -v` runs dedicated boundary regressions (environment isolation, local Git and external `.git` denial, scope, local policy and mid-scan revocation) on Linux and Windows CI. `python -m unittest tests.test_product_action_scope_parity -v` covers broader Product action scope. Source tests are not proof of live Product grants.

Next: merge/govern the Platform route proposal without applying its migration until issue #266 allows it; compose the existing Owner session and Device credential in the interactive Runtime host; then connect Apps to this existing port and validate a disposable local VESTE fixture read-only before M2.
