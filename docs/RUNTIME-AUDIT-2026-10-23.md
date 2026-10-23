# Runtime audit and operational readiness — 2026-10-23

## Scope and provenance

- Canonical repository verified before edits: `C:\Users\TONECOS\Documents\github\ordax-runtime`.
- `origin`: `https://github.com/ordaxsystems/ordax-runtime.git`.
- Audited base: `main` at `9154cb977bbad9f007e7734ebfbeeb4997e7db57` (PR #224).
- Work is isolated on `codex/runtime-operational-readiness-20261023`; the pre-existing checkout, other Runtime worktrees, and `ordax-apps` were not changed.
- Open PRs were checked before implementation. #159 already covers Windows silent-install restart and recovery; #205/#208 cover workspace resource lifecycle/issuance. #114/#121/#136/#175 are outside this status correction. This change does not duplicate those scopes.

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

The dispatch loop has one idle-claim path, with an adaptive delay that settles
between 30 and 60 seconds; it keeps the existing 20-second heartbeat. The
optional wake listener is disabled by default and, when enabled, bounds
reconciliation claims to five minutes. The per-action keepalive is scoped to
the active action. Existing idle-budget tests cover the query ceiling. No new
resident process, polling loop, or dependency was introduced.

Windows silent-install restart is already under PR #159 and is intentionally
not duplicated. This audit did not execute an installer, upgrade, rollback, or
physical recovery on Windows. Process memory/CPU snapshots and a loopback port
probe are observations of this host only, not a repeatable product smoke test.

### OrdaX OS Native boundary

`docs/NATIVE-BUILDROOT-COMPATIBILITY.md` records a real gap: the Runtime lock
targets CPython 3.12 while the current independent Base uses CPython 3.14;
several package versions differ and the MCP SDK has no Buildroot package. CI's
Buildroot artifact probe (PR #224) is a useful artifact/source check, but does
not prove that the complete agent dependency closure is packaged, installed,
or executed on an OrdaX OS device. The OS-owned private identity provider,
local Connector policy, single presence owner, and signed slot lifecycle remain
the existing contracts; this PR changes none of them.

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
- GitHub CI and physical Windows/OrdaX OS/MCP integration are separate gates;
  none is represented as passed by these local checks.

A passing source test or CI workflow is not a claim that the Windows installer
or an OrdaX OS device was exercised.

