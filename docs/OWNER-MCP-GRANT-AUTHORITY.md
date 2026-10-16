# Owner MCP grant authority

The Runtime manages Product MCP authorization only through the canonical Product PostgreSQL boundary exposed by `mcp.ordax.com.br`.

Canonical owner endpoints:

- `GET /v3/owner/remote-authority?device_id=<uuid>` — lists the authenticated owner's active Product MCP OAuth clients and current grant groups for exactly one owned device.
- `POST /v3/owner/remote-grant-groups` — replaces one profile-scoped grant group for an exact Product MCP client, device, and optional project scope.
- `DELETE /v3/owner/remote-grant-groups/<grant_group_id>?client_id=<uuid>` — revokes that exact client's grant group.

The Runtime never mints capabilities itself. Profile-to-capability mapping belongs to Platform and PostgreSQL remains the authorization SSOT. Account bearer tokens stay in the owning Studio process and are supplied to `ProductRemoteClient` per call; the client does not persist them.

Retired Product pairing/device-link and specialized grant endpoints are not accepted as fallback. Device ownership comes from Product enrollment and owner authorization targets the real consented MCP client returned by the canonical authority.

For the initial ChatGPT observation rollout, `computer-observe` is the least-privilege profile. Platform currently maps it to read-only `computer.access_status`, `computer.active_window`, `computer.processes`, `computer.screen_info`, and `computer.windows`. It does not grant screenshots, clipboard, filesystem writes, process termination, clicks, typing, or other interactive control.
