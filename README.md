# ORDAX Runtime

Canonical Windows/external-host ORDAX Runtime, device agent and specialized host adapters.

## OrdaX Intelligence transversal — decisão 2026-10-10

O [handoff do OS](https://github.com/ordaxsystems/ordax-os/blob/main/INTELLIGENCE-HANDOFF.md) fixa a fronteira: Runtime possui o host do dispositivo, execução/política local e adapters host autorizados; **não** passa a ser owner do Intelligence, de inferência global, do plugin Product MCP ou do Studio. A [PR #63](https://github.com/ordaxsystems/ordax-runtime/pull/63) é um experimento DEV de bridge ChatGPT Web: testes fake de loopback não autorizam ativação nem provam login real. O plugin externo (Platform) e o chat OrdaX (OS/provider) são fluxos distintos. Nenhuma mudança nesta documentação ativa provider, serviço, grant ou conexão.



Migrated from the retired `washingtonmsdj/mcp-blender` repository at commit `ce31808f950e04207f028c5925cca2da17d18553` on 2026-10-05. It is historical provenance only and is not a product authority or runtime dependency.

`ordaxsystems/ordax-runtime` is the canonical Runtime repository. Migration code may recognize the retired `mcp-blender` remote and the previous `washingtonmsdj/ordax-runtime` owner only to repoint existing managed checkouts; neither old URL is a product authority or an architectural dependency. New installs clone the `ordaxsystems` repository directly.

Product remote action recovery reuses `ProductRemoteClient` and the existing
authenticated action-status endpoint. See [Product action recovery](docs/PRODUCT-ACTION-RECOVERY.md)
for request identity, transport limits, uncertain submission/completion and the
separate PostgreSQL cutover dependencies. Source version is owned exclusively by
`pyproject.toml`; source tests do not establish an installed or production release.

Device-only presence uses the existing [Product presence client](docs/PRODUCT-DEVICE-PRESENCE-CLIENT.md)
with a verified canonical UUID/credential, bounded receipts and a total deadline.
It rejects inherited account authentication. The normal legacy heartbeat is not
rewired automatically; production cutover remains a separate coordinated gate.
