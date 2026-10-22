# OrdaX Project Workspace v1 — Runtime boundary (M1 first slice)

**Status:** read-only Runtime metadata core + host-only picker seam, **not** a deployed Product grant, native picker UI, or App integration. Related: [Runtime issue #184](https://github.com/ordaxsystems/ordax-runtime/issues/184), [Apps PR #268](https://github.com/ordaxsystems/ordax-apps/pull/268).

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

## Host-only selection seam (source-only, pending Product channel)

`ordax_dev_agent.workspace_resource_selection.WorkspaceResourceSelection` builds on the **existing** `resolve_project_for_repository` allow-list and `ProjectWorkspacePort`; it is not another filesystem executor or an independent grant ledger.

- Constructor requires `choose_directory(project)`, `confirm_owner(project, root)`, and `live_authority(actor, device, resource)` supplied by **trusted local host composition**. They have **no defaults**. Merely setting a callback is not proof of trusted caller identity.
- The native picker must run in the interactive **Owner user session** (not the headless Windows service session). Its selected root must equal the unique already-registered GitHub project root; selecting a parent, arbitrary child, symlink, junction or different path fails closed. The owner must separately confirm it. No model or renderer-selected path may call this Python seam.
- `choose_for_owner` returns only a cryptographically random non-bearer `resourceId`. Path and filesystem identity remain in the Runtime process memory, never in Platform, Lab SQLite or MCP payload. Pending proposals expire after five minutes; all in-process references expire within 24 hours (even if previously bound) and are limited to 64, with expired entries pruned before a new selection.
- `resolve_live` **always** calls the trusted Platform proof callback; it requires exact Lab project, actor, OAuth client, workspace session, device and resource, canonical UUID fields, expiry and read-only scope. It revalidates the registered repository mapping and root identity. A local selection, an opaque ID or full-computer-control permission cannot independently allow `verify_binding` or `snapshot`.
- `as_port(local_policy=...)` composes the canonical `ProjectWorkspacePort`; there is no new snapshot API. `discard_local_selection` revokes only the Runtime reference, **not** the canonical Product grant.
- The reference registry is intentionally **ephemeral**: Runtime restart loses all selections and denies reuse. Durable cross-restart recovery needs its own authenticated rebind gate; never deserialize SQLite rows into a local root.
- This seam is **not wired to any endpoint**, and the callback does not exist in production until Platform #266 issuance, attested Runtime transport, and real Windows Owner native UI. It cannot silently elevate a legacy project or run automatically in a service.

## Not yet implemented

- Native trusted user-session folder picker, authenticated Runtime/Platform bridge, and actual Owner resource-grant issuance/expiry in Platform (the host-only in-process selection seam is source-only).
- Verified mapping from a Lab projectId to canonical Product project/Space.
- Authenticated Runtime transport binding this port to the App, typed errors and revocation.
- Canonical local policy callback wired into a live device agent.
- Packaged Windows App + real granted VESTE fixture E2E; deployment/install/rollback.

Do **not** wire a temporary raw filesystem adapter into Electron. The App alpha.11 intentionally reports capability_unavailable until the actual grant transport exists.

## Test gate

`python -m unittest tests.test_project_workspace_port tests.test_workspace_resource_selection -v` runs dedicated boundary regressions (environment isolation, local Git and external `.git` denial, scope, local policy and mid-scan revocation) on Linux and Windows CI. `python -m unittest tests.test_product_action_scope_parity -v` covers broader Product action scope. Source tests are not proof of live Product grants.

Next: jointly agree trusted picker and scope adapter with Platform. Only then connect Apps alpha.11 to this port, validate a disposable local VESTE fixture read-only, and proceed to M2.