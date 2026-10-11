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

## Princípio de acesso do proprietário

O acesso remoto é autorizado pela **Conta OrdaX, cliente OAuth exato e dispositivo vinculado**, combinado com a escolha **local** do proprietário. O modo `full_access=true` libera as listas locais de pastas e aplicativos para as ações tipadas já disponibilizadas pelo Runtime, respeitando as permissões da sessão Windows. Não cria acesso a computadores de outros usuários e não concede novas capacidades por texto de prompt.

O Runtime preserva a integridade de seus próprios arquivos de estado e de execução, que devem ser modificados somente pelas superfícies oficiais de configuração e atualização. Não há classificação automática confiável de arquivos pessoais por nome ou extensão: o proprietário deve considerar que Full Access pode alcançar também conteúdos privados armazenados no computador. Autorizar acesso integral tem efeito amplo, inclusive sobre o material sensível legível pelo processo, e **avisos do GPT/Grok não substituem a autorização da Conta OrdaX nem o controle de destino**. Nenhum provedor externo recebe dados automaticamente apenas por estar conectado; as ferramentas acionadas continuam seguindo seu fluxo de autorização, resultado e auditoria.

O proprietário pode revogar os grants Product ou desativar o Computer Control local a qualquer momento. As proteções de identidade, cliente, dispositivo e sessão operam independentemente de quais pastas e aplicativos o proprietário decidiu liberar. O catálogo de capacidades não deve ser artificialmente limitado a um único projeto no modo de controle do computador.
