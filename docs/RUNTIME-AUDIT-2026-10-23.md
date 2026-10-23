# Runtime audit and operational readiness — 2026-10-23

## Scope and provenance

- Canonical repository verified before edits: `C:\Users\TONECOS\Documents\github\ordax-runtime`.
- `origin`: `https://github.com/ordaxsystems/ordax-runtime.git`.
- Initial audited base: `main` at `9154cb977bbad9f007e7734ebfbeeb4997e7db57` (PR #224). This follow-up reconciles the existing PR #175 with `main` at `db43388cf7d6c36cfaee5439c93a357f12b1929b`, after PRs #225 and #159 were integrated.
- The #175 follow-up was prepared in an isolated Runtime checkout. The `ordax-apps`, `ordax-os`, and `ordax-platform` repositories were not modified; status after subsequent merges is recorded below.
- At the time of the original audit, PR #159 was merged while #205/#208, #114/#121/#136 were open. Their current status is recorded below.

## Repository and PR state after follow-up — 2026-10-23

At PR #226 merge, Runtime `main` advanced from `ea8c863736ef4482c216235a994aa4b01e0e8322` to `95b48c91283c605cd8950e238d0d7542e4e479fc`.

- PR #175 merged at `b2049e72811ebb81d3e56f5341dd51e77c954206`. Its Linux recovery CI, Runtime Contracts, and Windows Product Build passed; no physical OrdaX OS execution is claimed.
- PR #114 merged at `c26af984970a5db8bae37444273c3fd2505d58c3`. It exposes the Product origin validated during Electron account authorization. Windows Product Build, Install Smoke, Packaging Contracts, Product MCP Minimum SDK and Runtime Contracts passed on its head. The pin remains canonical Apps Studio 0.15.9; no Apps source was changed.
- PR #136 merged at `ea8c863736ef4482c216235a994aa4b01e0e8322`. It updates canonical repository ownership and scope terminology, with a documentation regression test. The two focused tests passed locally; required Runtime, Product MCP, Native Composition, Packaging, Windows Product Build and Install Smoke checks passed in CI.
- PR #121 was closed as duplicate product-level documentation; its canonical source belongs to Apps.
- Runtime #205 remains a draft aligned with Platform #274, which is still draft and SQL proposal-only. Runtime #208 was closed without merge because its separate staged endpoints are absent from #274; its branch remains preserved for reference. No migration, deployment, grant issuance, authenticated Owner host composition or Apps capability is enabled.
- CI results above validate source/build/install-smoke workflows only. They do not prove a production MCP action, physical Windows recovery, or execution under an OrdaX OS supervisor.

## Follow-up after PR #226 — review snapshot

PR #226 merged the audit follow-up at `95b48c91283c605cd8950e238d0d7542e4e479fc`. At the start of this review, that was the remote `main` SHA. The status paragraph above now records the merge commit rather than its pre-merge parent.

### Runtime PR #205

- PR #205 is the only open Runtime PR. It is still draft at head `01f8bcde2d38266fc3f7cfdad5da8f2532e6fcc6`, based on `f6b4c38f1dcb356714a32c54b9a02fc17661751b`; GitHub reports `mergeable=false`.
- Static review of its changed resolver, HTTP client, and selection broker found no route to workspace reading without interactive Owner confirmation and a fresh exact-scope read-only proof. This is a source review only; the host composition and Apps port are absent.
- Existing checks on that old head passed Runtime Contracts, Packaging Contracts, Native Runtime Composition, Product MCP Minimum SDK, Windows Product Build, and Install Smoke. Signing, signed-release smoke, release governance, and Owner/Dev attestation were skipped. These checks do not validate a rebased branch or live Platform/Windows integration.
- Platform #274 remains draft and its SQL is proposal-only. Do not enable the resource lifecycle or merge #205 until the external contract, authenticated Owner/Device host composition, Apps integration, and required post-reconciliation checks are complete.

### Open issue triage at this snapshot

| Issue | Current evidence and next owner gate |
| --- | --- |
| #184 | The typed workspace-resource lifecycle remains pending. Runtime #205 is source-only; Platform #274 and the Apps host integration are not enabled. |
| #149 | Latest Owner/Dev evidence keeps `rollbackReady=false` and `applyAllowed=false`; the installed 0.15.9 bytes have no proven byte-exact rollback. No machine update was performed. |
| #127 | The recorded real-device result is a fail-closed `product_grant_not_resolved` denial, not a positive interaction E2E. Runtime `full_access` does not imply a remote grant; the grant/profile and target-discovery contract requires its canonical authority owner. |
| #49 | Production Authenticode signing is not available in the latest recorded gate; no approved signing service/runner is in place. Keep production publication closed until signing custody and final-byte verification exist. |
| #61 | The current Windows build script still embeds `https://mcp.ordax.com.br`. The issue requires a versioned Control Plane release reference; do not substitute a URL without that owner's canonical provenance contract. |
| #67 | The issue's source evidence is from 2026-10-09, while the Runtime lock now pins Apps Studio 0.15.9. Refresh the Apps source/composition evidence before changing the lock or claiming the Conversation surface is integrated. |
| #7 | Bounded action-result compatibility still depends on the OS-owned Studio Runtime v3 contract; no duplicate result service belongs in Runtime. |
| #1 | The bootstrap/migration issue remains open. Revalidate current consumers and legacy references before closing it or deleting legacy code; no deletion is justified by the issue title alone. |

No changes were made to `ordax-apps`, `ordax-os`, or `ordax-platform`. The issue list is a Runtime handoff snapshot, not evidence that cross-repository or physical gates passed.

## Findings

### Corrected here: operational status could be stale during a long action

The Runtime's normal dispatch loop already marks transport errors as
`reconnecting`. The separate keepalive thread used while an action is running
renewed the lease and emitted the 20-second device heartbeat, but a failed
heartbeat only wrote to stderr. The local `/status` snapshot could therefore
continue to say `connected` while the remote transport was failing. The
keepalive now records the transport error and a subsequent successful
heartbeat records recovery, without replacing the active `busy` job state.

The `/status` runtime object now reports `action_registry_ready`,
`product_dispatch_ready`, and `product_readiness_code`. Product dispatch
readiness requires a loaded ActionRegistry, configured remote control, paired
device identity, `ready` or `busy` lifecycle state, connected transport, and a
Product heartbeat no older than 60 seconds. Status consumers must continue to
treat `/health` as liveness only. The Studio status adapter exposes the new
readiness fields additively while retaining legacy `device_agent.ok` semantics.

This is diagnostic state only. It creates no grant and does not prove the
authorization, execution, completion, or audit receipt for any particular
action.

### Existing MCP path and its evidence boundary

The Product MCP adapter (`product_mcp_server.py`) delegates to the shared
typed handlers. A call submits once, waits using the returned request ID, and
returns a receipt; uncertain completion directs the caller to
`product_action_status` rather than replaying the action. The Runtime dispatcher
uses the existing Product transport and ActionRegistry. The Platform remains
the owner of remote authentication, grants, queueing, and audit; local Runtime
policy remains a separate execution check.

The code and contract tests establish source-level flow and recovery behavior,
not a live MCP session. No production MCP request or authorized computer action
was issued during this audit. Remote endpoint `/health` success is not evidence
that a particular device Runtime is paired, dispatch-ready, granted, or able
to complete an action.

### Windows and resource use

The Windows Product Runtime dispatch loop has one idle-claim path, with an
adaptive delay that settles between 30 and 60 seconds; it keeps the existing
20-second heartbeat. The optional wake listener is disabled by default and,
when enabled, bounds reconciliation claims to five minutes. The per-action
keepalive is scoped to the active action. Existing idle-budget tests cover the
query ceiling. No resident process or new polling loop was added to the Windows
path. The separate Linux recovery cadence is recorded below.

Windows silent-install restart and recovery changes merged through PR #159 at
`db43388cf7d6c36cfaee5439c93a357f12b1929b`; the Windows Product Build includes
install/upgrade smoke coverage. This audit did not execute an installer,
upgrade, rollback, or physical recovery on a user device. Process memory/CPU
snapshots and a loopback port probe are observations of this host only, not a
repeatable product smoke test.

### OrdaX OS Native boundary

`docs/NATIVE-BUILDROOT-COMPATIBILITY.md` records a real gap: the Runtime lock
targets CPython 3.12 while the current independent Base uses CPython 3.14;
several package versions differ and the MCP SDK has no Buildroot package. CI's
Buildroot artifact probe (PR #224) is a useful artifact/source check, but does
not prove that the complete agent dependency closure is packaged, installed,
or executed on an OrdaX OS device. The OS-owned private identity provider,
local Connector policy, single presence owner, and signed slot lifecycle remain
the existing contracts; this PR changes none of them.

### Owner Linux recovery transport

Merged PR #175 removed the Owner Linux agent's D1/WebSocket execution
path and its Gorilla WebSocket dependency. It uses only the existing
PostgreSQL-backed `/v3/device/transport/*` endpoints and rejects the obsolete
`ORDAX_OWNER_TRANSPORT` selector. Its fixed capability set remains
`agent.owner.snapshot`, `agent.owner.git_status`, `agent.owner.pull`,
`agent.owner.runtime_stop`, and `agent.owner.runtime_start`; it does not add a
shell, grant, queue, authority, or OS service. Payload digest checks, execution
context fencing, the local effect journal, and the terminal report outbox stay
in place.

An idle cycle performs a heartbeat and a claim. The follow-up starts at 12
seconds and grows to a 30-second cap after repeated empty claims, resetting
after a claimed job. At the cap that is about 5,760 transport requests per idle
device per day; this is a calculated budget, not observed Platform database
load. Linux CI passed on the revised PR #175 head before merge. Its static artifact
is built for Linux/amd64; neither installation under the real OrdaX OS
supervisor nor execution on an OS device is claimed. The OS owner must
still confirm the actual target architecture, credential-provider handoff,
single presence ownership, packaging, and lifecycle before pinning a release.

## Cross-repository handoffs

- **ordax-apps:** consume `device_agent.product_dispatch_ready` and
  `device_agent.product_readiness_code` (or the canonical `/status` fields)
  separately from local `/health` and `remote_mcp.ok`. An unknown transport
  state must remain unknown; endpoint presence alone must not render the local
  Runtime as operational. Any polling-frequency or UI changes stay in Apps.
- **ordax-os:** before device enablement, resolve the recorded CPython/dependency
  closure against the exact OS Buildroot artifact and demonstrate install,
  supervised lifecycle, heartbeat ownership, and execution of the signed
  Runtime on the OS target. No OS source was modified here.
- **ordax-platform:** keep OAuth/session/grant resolution, request queue, and
  audit receipt canonical. Runtime readiness is device telemetry and cannot
  replace those checks.

## Validation record

- Local source tests passed: `tests.test_runtime_operational_status`,
  `tests.test_product_idle_poll_budget`, and
  `tests.test_status_server_loopback_security` (15 tests total).
- Python AST parsing passed for the modified Runtime modules and tests;
  `git diff --check` passed.
- `tests.test_ordax_studio_web` was not run locally because the bundled Python
  environment does not include the Runtime's `httpx`/MCP dependencies. Its
  regression is included for the repository's full Runtime Contracts workflow.
- PR #159's merged revision passed its Windows install/upgrade CI smoke. PR #175
  merged after Linux and Runtime Contract checks passed on its revised head. The
  local Windows host did not complete a Linux Go cross-build, so no local Go
  build result is claimed.
- GitHub CI and physical Windows/OrdaX OS/MCP integration are separate gates;
  none is represented as passed by these local checks.

A passing source test or CI workflow is not a claim that the Windows installer
or an OrdaX OS device was exercised.

