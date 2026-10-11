# ORDAX Studio — Computer Control policy

Computer Control is a typed capability of ORDAX Runtime. It can provide broad remote-desktop-class computer control, but it remains auditable and bounded by the authority Windows actually grants to the installed Runtime/session.

## Authority model

A remote action is executable only when every required boundary agrees:

1. the authenticated ORDAX account can reach the device;
2. an active Product grant includes the project and typed action;
3. the local Runtime policy allows Computer Control;
4. the action-specific validator accepts the typed payload.

A remote grant never changes local computer policy. Local policy never creates a remote grant.

## Local policy SSOT

The canonical local policy is the `computer_access` section of:

`%LOCALAPPDATA%\OrdaX\DevAgent\agent-settings.json`

Example:

```json
{
  "computer_access": {
    "enabled": true,
    "full_access": false,
    "full_filesystem": false,
    "allowed_roots": ["C:\\Users\\USER\\Documents\\github"],
    "allowed_applications": ["notepad.exe"]
  }
}
```

Existing installations that do not yet contain `full_access` are interpreted as `false`.

## Owner modes

The Windows host exposes two principal owner choices:

### Bounded

`full_access=false` keeps the existing least-privilege controls:

- filesystem actions use `allowed_roots`, unless the owner separately enables `full_filesystem=true`;
- application launch uses `allowed_applications`;
- `allowed_applications` defaults to an empty list, so `computer.launch_app` is denied until the owner allows an executable.

Entries in `allowed_applications` may be absolute `.exe` paths or `.exe` basenames. Basenames are resolved to a concrete executable path before use; policy compares normalized resolved paths.

### Full Access

`full_access=true` is an explicit local owner choice for users who want Remote Desktop Commander-class coverage.

In this mode:

- ORDAX `allowed_roots` restrictions are not applied to supported filesystem actions;
- ORDAX `allowed_applications` restrictions are not applied to supported application launch/control actions;
- the normal typed Computer Control gateway, action validation, remote identity/grants and receipt/audit path remain in force;
- Windows/UAC remains the final platform boundary. Full Access does not bypass elevation, protected resources, another user/session or permissions the Runtime process does not possess.

A remote MCP/AI client cannot enable, disable or widen Full Access. Only a local owner surface or a managed deployment policy can do that.

## Managed deployment overrides

Environment overrides exist for managed deployments:

- `ORDAX_COMPUTER_ACCESS_ENABLED`
- `ORDAX_COMPUTER_FULL_ACCESS`
- `ORDAX_COMPUTER_FULL_FILESYSTEM`
- `ORDAX_COMPUTER_ALLOWED_ROOTS`
- `ORDAX_COMPUTER_ALLOWED_APPLICATIONS`

On Windows, path-list environment values use the platform path separator (`;`). Invalid policy fails closed. Environment-managed fields are exposed as managed in Studio and are not overwritten by local UI saves.

## Owner-local Studio controls

ORDAX Studio exposes **Acesso ao computador** as a local owner surface for the same `computer_access` SSOT. The UI can:

- enable/disable Computer Control;
- explicitly approve/revoke Full Access;
- optionally enable filesystem-wide access while still keeping an app allowlist;
- maintain bounded-mode roots/applications.

Enabling Full Access requires a local confirmation explaining that an authorized remote ORDAX client may broadly read/write files, inspect/control windows, use mouse/keyboard/clipboard, inspect processes and launch supported applications.

The editor is deliberately **not** a Product MCP action. Remote clients can read the effective non-secret policy through `computer.access_status`, but cannot mutate the local policy. Local writes preserve unrelated agent settings, use atomic replacement and require `expected_revision` so stale Studio windows cannot overwrite newer policy.

## App launch

`computer.launch_app` does not use a shell. The Runtime resolves one Windows `.exe` and validates bounded arguments.

- Bounded mode requires the resolved executable in `allowed_applications`.
- Full Access accepts supported `.exe` applications without maintaining an ORDAX per-app allowlist.

This does not convert Computer Control into an unaudited shell. If terminal/shell capabilities are supported, they remain separate typed actions with their own authorization and validation.

## Filesystem

- Bounded mode restricts filesystem actions to `allowed_roots`.
- `full_filesystem=true` removes only the filesystem root restriction while retaining the application allowlist.
- `full_access=true` removes both ORDAX filesystem-root and application allowlist restrictions.

## Auditability

`computer.access_status` exposes the effective non-secret local policy so Studio and authorized MCP clients can explain the active mode.

Remote Computer Control actions continue through Product MCP, Cloudflare authorization, ORDAX Runtime validation and the existing receipt/audit path. Full Access changes the local resource allowlist decision; it does not remove authentication, grants, typed validation or audit.

## Protection for sensitive local credentials

The local Runtime retains a **non-negotiable credential boundary** for typed
`computer.*` filesystem operations, including when the owner explicitly enables
`full_access=true` or `full_filesystem=true`. This is implemented in
`ordax_dev_agent/computer_filesystem_actions.py` and applies to file reads,
stats, edits, moves, deletions, directory traversal and content search.

The initial supported safeguards cover SSH/GPG/cloud/Kubernetes credential
directories, Windows DPAPI/Credential Manager stores, Chromium/Firefox profile
credential stores and explicitly named authentication material such as
`.env`, `.npmrc`, `.pypirc`, private key files and service credential JSON.
Symlink targets are resolved before authorization. Recursive changes to
ancestors of a protected credential store are rejected. Template
`.env.example`/`.env.sample` files remain accessible.

These filesystem safeguards are **not a complete data loss prevention (DLP)
system**: arbitrary files may contain secrets, already-visible screen content
and clipboard text can still expose sensitive data, and a permitted interactive
application may possess capabilities beyond filesystem tools. Future
screen/clipboard egress protection must be implemented at the corresponding
typed-action boundary; it must not be claimed as present here. Windows UAC and
the operating system's privileges remain authoritative.

The remote authorization remains **account + exact OAuth client + device
Product grant**, combined with **local owner Full Access approval**. Account
login does not silently elevate local policy. Consent may be captured once and
persisted for the selected device, instead of requiring a fresh confirmation
for every ordinary task; the owner can revoke the grant or disable local
access. Neither a plugin nor an LLM may change the local Full Access setting.

Regression test: `python -m unittest tests.test_computer_credential_boundary`.
