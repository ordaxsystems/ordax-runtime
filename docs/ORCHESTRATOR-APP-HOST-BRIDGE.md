# Runtime → Orchestrator Lab: private read channel

Status: source candidate, not registered or activated in Product MCP.
Owner host transport/client: `ordax-runtime`; owner App service/protocol:
`ordax-apps/prototypes/ordax-orchestrator/product`.

The dedicated channel uses inherited stdin/stdout of an App child that the
managed launcher starts and verifies. It never opens a network listener, reads
App SQLite, searches for a window or converts actions to terminal commands.
On Windows the managed launcher must supply child handles opened for overlapped
I/O (`FILE_FLAG_OVERLAPPED`) as required by Electron/libuv. Ordinary synchronous
`subprocess.PIPE` handles must not be assumed compatible with the packaged App.
The pipe adapter accepts already-created handles; it does not implement that launcher.
No product grant is minted by an App role, bootstrap message or ephemeral key.

## Contract and authority

`ordax.orchestrator-host/1` binds one subject, OAuth client, device and grant to
an exact expected App source commit, content digest and profile fingerprint.
The host must verify installed executable AND packaged ASAR bytes against a
trusted installation receipt **before** launch. The App's self-reported SHA-256
proof is an additional consistency check, not a trusted publisher signature.

`VerifiedOrchestratorReadAuthorization` must come from the canonical Product
authorization boundary. Its sole capability is `orchestrator.app_read`, an
explicit **device + App** read scope. It is not a project grant or permission
to operate files/other apps. It must not be inferred from a computer grant,
local project slug, persona, manifest or client arguments.

The host re-resolves authorization before bootstrap, before every read and
before releasing each result. Subject/client/device/grant changes, expiry,
revocation, identity mismatch, malformed result or uncertain transport poison
the session. No request or bootstrap is retried. The host owns child lifecycle;
this client neither kills the human App nor opens a fallback channel.

The ephemeral HMAC key travels once over the launcher's private inherited pipe.
It is not an account credential; it is not saved to disk, environment, argv,
logs, SQLite or the renderer. Lease maximum is five minutes, signed reads expire
within 30 seconds, IDs are unique and App rejects replay. Framing is bounded.
This first channel is **read-only**, even if a signed request asks to send.

## Shared public contract

`ordax_dev_agent/assets/orchestrator_app_contract.json` is the exact exported
projection of the App's `orchestrator-contract.mjs`, pinned by
`contracts/orchestrator-app.lock.json`. Runtime derives read operations from
this metadata instead of owning a second tool catalog or service. The App
continues to validate every operation with its own canonical schema.

## Validation and remaining integration

Tests cover exact identity, Unicode signatures, scope, expiry, changed caller,
revocation after a reply, missing authority, no replay, mismatched response,
secret erasure and a real inherited subprocess-pipe timeout. They are included
in Linux native and Windows Runtime contract jobs. No paid API or GPT send.

**Not yet delivered:** managed App launcher/installation registration,
canonical Platform capability/profile migration, remote tool projection, grant
resolver binding to this client and live OAuth/device E2E. A callable Python
client does not make `orchestrator.app_read` a published or available grant.
Do not add a route that accepts serialized VerifiedAuthorization from the model.
Only enable the canonical path after these owners are integrated and tested.
