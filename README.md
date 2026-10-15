# ORDAX Runtime

Canonical Windows/external-host ORDAX Runtime, device agent and specialized host adapters.

## Dois modos oficiais de IA — decisão 2026-10-15

O Runtime participa de dois fluxos distintos e eles não devem ser confundidos:

1. **ChatGPT direto do usuário** — a conversa acontece na conta ChatGPT do próprio usuário e o ChatGPT chama o MCP OrdaX para acessar o dispositivo autorizado. Não existe OrdaX Central de inferência no caminho da conversa. Windows entrega Studio + Runtime; no OrdaX OS o Runtime é nativo. O contrato lógico de ferramentas, grants e segurança deve ser o mesmo nos dois sistemas.
2. **OrdaX Central** — apps/PCs enviam mensagens à Central, que multiplexa solicitações, chama o Model Router/provedor autorizado e devolve cada resposta à origem correta por `request_id`/sessão/dispositivo. Esse fluxo é separado do MCP direto.

A referência arquitetural canônica fica em `ordaxsystems/ordax-platform/docs/ORDAX-AI-CONNECTION-MODES.md`. O endpoint MCP atual `https://mcp.ordax.com.br/mcp` é o caminho de validação do modo direto e não deve ser descrito como “Central chamando o GPT”. O control-plane pode autenticar, registrar dispositivos, resolver grants e auditar sem se tornar relay de inferência.

Túnel e URL não são conceitos opostos: um MCP direto por dispositivo pode futuramente usar uma URL HTTPS estável publicada por um túnel/conexão segura iniciado pelo Runtime. Já clientes do modo Central normalmente usam conexões outbound HTTPS/WebSocket; se a própria Central estiver em uma máquina privada, um único túnel nela pode publicar o serviço.

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
