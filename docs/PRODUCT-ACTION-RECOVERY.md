# Product action recovery

Owner: `ordaxsystems/ordax-runtime`. Scope: Runtime client and executable Python
Product MCP; MVP-04/Studio remote continuity. The portable Studio UI remains in
`ordaxsystems/ordax-apps/apps/studio/`. Product identity, grants, account/client
isolation, persistent tasks and audit remain with Platform and the existing
Runtime execution gateway. This increment creates no queue, grant, pairing,
global memory, project mapping or provider-specific execution path.

## Existing transport contract

The single `ProductRemoteClient` implements the current authenticated
`/v3/product/actions` POST and `/v3/product/actions/{request_id}` GET.
Every call receives the current access token; the client does not persist it.
The Platform response remains the authority for scope and task state.

Action IDs must be nonempty path-safe ASCII identifiers of at most 128
characters. Legacy opaque IDs remain valid; UUIDs are not fabricated from slugs.
An action response must have `ok: true`, the **same request_id**, and a published
state: `queued`, `leased`, `running`, `succeeded`, `failed` or `cancelled`.
The client validates this once for both direct status reads and polling.
It does not reinterpret an unknown state as progress or completion.

Responses are streamed and closed on every exit, bounded to **4 MiB decoded
bytes**, including gzip expansion. This accommodates the existing 2 MiB inline
artifact after base64 expansion and metadata. JSON must use application/json,
valid UTF-8 and a JSON object; invalid, deeply nested or nonfinite constant data
is rejected. Error codes are bounded public identifiers; arbitrary backend
messages and transport exception details are not returned to MCP clients.
Redirects are not followed with bearer credentials, including with an injected
HTTP client configured to follow redirects. Wait durations must be finite;
poll sleep/read timeouts use the remaining budget, and a slow stream is checked
against the same monotonic deadline without buffering small chunks indefinitely.

## Uncertain submission and completion

- A malformed ACK, lost transport, HTTP 408 or server failure during POST may
  occur **after** execution was accepted. `acceptance_unknown: true` says to
  verify effects before submitting another action. No automatic POST retry or
  request ID is invented. An explicit, valid grant denial remains a denial.
- After an ID was accepted, status transport errors, invalid responses and wait
  expiry preserve that exact ID. MCP returns `ok: false`, `completion_unknown:
  true`, the public error and an instruction to query `product_action_status`.
  It does not claim the task is failed, cancelled or still running.
- `product_action_status(request_id)` delegates to that same client's GET using
  the current token. It never enqueues, dispatches, grants or replays an action.
  Its executable MCP annotations describe a read-only, idempotent operation;
  annotations grant no permission. Auth/revocation errors preserve the ID but
  expose no task result.
- Successful and terminal failed/cancelled task responses retain the existing
  receipt shape and authorized result. `ProductActionWaitTimeout` remains a
  `TimeoutError`, with the recoverable `request_id` attached. `ProductRemoteError`
  permits Python's normal traceback updates; freezing an Exception broke stream
  cleanup and error propagation.

These are transport/receipt rules, not exactly-once execution guarantees or a
global task journal. Idempotency, authorization at claim/start/report, lease
fencing and durable receipts belong to their existing owners.

Runtime package version is read from `pyproject.toml` in a source checkout and
from `ordax-runtime` distribution metadata when installed. The existing component
inventory reports it as `runtime_package`, separately from the Blender bridge,
device agent and portable Studio. The compatibility descriptor records the same
package version, verified by CI; it is a compatibility snapshot, not a second
release authority. Tests formerly equating Runtime distribution version with
the independent Blender bridge version were corrected to check the actual owner.

## Verification and acceptance

`tests/test_product_action_recovery.py` exercises the registered FastMCP tools
and the real HTTP client over an isolated `httpx.MockTransport`:

- accepted POST → failed GET → status recovery GET, **one POST total**;
- revocation, every published state, mismatched task IDs, missing/unknown states;
- malformed/lost ACK and wait expiry without false terminal state or replay;
- invalid paths before any network operation;
- bounded stream closure, gzip, invalid UTF-8/MIME/JSON and private error detail;
- slow small chunks, finite timing, remaining wait budget and inline artifacts;
- the published status tool schema contains only `request_id`, never credentials
  or execution parameters.

The Runtime CI runs this suite together with the existing Product gateway,
audit, device-scope, local policy, host and managed installation contracts.
Acceptance requires those contracts and package compilation to pass. Fixtures
do not establish an authenticated production account/device or installation.

The dependency floor is the tested MCP Python SDK **1.30.0**. An isolated
1.9.0 load failed during registration of the existing typed wrappers
(`issubclass` received a non-class annotation); exposing an annotations argument
alone did not prove compatibility with this product. Earlier SDKs are no longer
declared compatible, and the Runtime does not monkey-patch their registration
internals. CI separately tests the published tools/recovery flow against 1.30.0
and the resolver-selected supported SDK.

## PostgreSQL cutover remains separate

Platform's [prepared canonical reads](https://github.com/ordaxsystems/ordax-platform/pull/112)
use verified OAuth user/client identity and existing PostgreSQL RPCs. This
increment repairs the **current** Runtime transport; it does not activate those
routes or adapt `product_remote.py`'s legacy execution envelope.

Canonical enqueue → claim/lease → local policy/gateway → report/audit → status,
client-specific grants and the authoritative project UUID/local binding must
migrate together. Do not fabricate the legacy `grant` envelope from a claimed
canonical job, translate a slug into a UUID, mirror tasks into D1 or add another
local orchestrator to force compatibility. Production readiness and rollback
must be established by Platform and Runtime owners before activation.
