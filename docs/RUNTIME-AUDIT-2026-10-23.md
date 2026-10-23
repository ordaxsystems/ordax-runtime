# Runtime audit and operational readiness — 2026-10-23

## Scope and provenance

- Canonical repository verified before edits: `C:\Users\TONECOS\Documents\github\ordax-runtime`.
- `origin`: `https://github.com/ordaxsystems/ordax-runtime.git`.
- Initial audited base: `main` at `9154cb977bbad9f007e7734ebfbeeb4997e7db57` (PR #224). This follow-up reconciles the existing PR #175 with `main` at `db43388cf7d6c36cfaee5439c93a357f12b1929b`, after PRs #225 and #159 were integrated.
- Current follow-up is isolated in `C:\Users\TONECOS\Documents\github\ordax-runtime-operational-readiness-20261023` on the existing `fix/owner-recovery-canonical-only-20261021` branch. The `ordax-apps`, `ordax-os`, and `ordax-platform` repositories were not modified.
- PR #159's Windows auto-start and recovery changes are merged. #205/#208 cover workspace lifecycle/issuance; #114/#121/#136 remain distinct pending work. This follow-up does not duplicate those scopes.

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

The existing PR #175 removes the Owner Linux agent's D1/WebSocket execution
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
load. The Linux CI workflow must pass on the rebased source before merge. Its
static artifact is built for Linux/amd64; neither installation under the real
OrdaX OS supervisor nor execution on an OS device is claimed. The OS owner must
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
  is rebased onto the current `main`; its Linux workflow and Runtime contract
  checks are required on the revised head. The local Windows host did not
  complete a Linux Go cross-build, so no local Go build result is claimed.
- GitHub CI and physical Windows/OrdaX OS/MCP integration are separate gates;
  none is represented as passed by these local checks.

A passing source test or CI workflow is not a claim that the Windows installer
or an OrdaX OS device was exercised.

