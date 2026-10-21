# Runtime → Orchestrator Lab: private read channel

Status: source candidate, not registered or activated in Product MCP.
Owner host transport/client: `ordax-runtime`; owner App service/protocol:
`ordax-apps/prototypes/ordax-orchestrator/product`.

The dedicated channel uses inherited descriptors of an App child that the
managed launcher starts and verifies: fd 3 is host-to-App and fd 4 is App-to-host.
Stdin/stdout/stderr are reserved for diagnostics, never protocol data. It never opens a network listener, reads
App SQLite, searches for a window or converts actions to terminal commands.
On Windows the managed launcher must supply child handles opened for overlapped
I/O (`FILE_FLAG_OVERLAPPED`) as required by Electron/libuv. Ordinary synchronous
`subprocess.PIPE` handles must not be assumed compatible with the packaged App.
`InheritedOrchestratorPipe` requires the launcher-owned `request_pipe` and
`response_pipe` explicitly. It never assumes these are child stdin/stdout,
accepts paths from a model, or implements the managed launcher. A standalone
Python subprocess fixture tests deadlines only; it does not certify Electron
handle creation or Product authorization.
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

**Not yet delivered:** trusted installation registration/provenance binding,
canonical Platform capability/profile migration, remote tool projection, grant
resolver binding to this client and live OAuth/device E2E. A callable Python
client does not make `orchestrator.app_read` a published or available grant.
Do not add a route that accepts serialized VerifiedAuthorization from the model.
Only enable the canonical path after these owners are integrated and tested.

## Managed Windows launcher candidate — 2026-10-21

`orchestrator_app_launcher.py` accepts a typed installation-owner result with
an independently verified inventory of **all** bundle files and expected App
identity. It never reads a serialized receipt or executable path from tool
arguments, persists a registry, or treats App-authored hashes as provenance.
It rejects path aliases, traversal, alternate streams, case duplicates,
missing/extra files, hash mismatches and reparse points. Windows file/directory
handles deny replacement; bundle files deny writes while the session is open.
The installation owner remains responsible for a protected root and publisher
provenance. The source candidate does not manufacture that authority.

For Python-to-Electron on Windows the launcher creates a fresh local named
pipe before starting the exact verified executable. Its protected DACL admits
only the current Windows user; remote clients are rejected. Windows reports
the connecting client's PID, which must equal the still-live Popen child before
any bootstrap secret is written. This is not a TCP/HTTP endpoint or App server.
The App connects as a client via the explicit `--ordax-runtime-bridge-pipe` mode;
there is no fallback from missing descriptors or lookup by window/process name.
The earlier fd 3/4 transport remains an explicit separate inherited-pipe mode.

Connect/write/read use cancellable overlapped I/O with a finite total deadline.
Authorization is resolved before verification, immediately before launch, and
again through the existing client before bootstrap/read/result disclosure.
Node injection/debug/model-key environment variables are removed; the supervisor
is disabled. The exact default profile fingerprint must match the receipt.
Session closure releases transport/file pins without terminating the human App.
An existing human instance is not silently attached, restarted or killed.

Tests include actual Windows pipe I/O, PID rejection, cancellation deadlines,
DACL inspection and file mutation/replacement denial. The disposable CI helper
`scripts/orchestrator/managed_app_e2e.py` constructs **synthetic installation and
authorization fixtures** only under Windows CI; it is not a registry importer.
It checks the actual Python client against the built Electron App on a new
profile. Consult the workflow result before claiming that E2E passed.

Still required before remote availability: installation-registry/provenance
binding, Platform capability/profile and canonical live grant resolver, public
MCP projection, and live account/device E2E. No production action is registered.

Windows API sources: [pipe security](https://learn.microsoft.com/en-us/windows/win32/ipc/named-pipe-security-and-access-rights),
[peer PID](https://learn.microsoft.com/en-us/windows/win32/api/winbase/nf-winbase-getnamedpipeclientprocessid),
[overlapped I/O](https://learn.microsoft.com/en-us/windows/win32/sync/synchronization-and-overlapped-input-and-output).
