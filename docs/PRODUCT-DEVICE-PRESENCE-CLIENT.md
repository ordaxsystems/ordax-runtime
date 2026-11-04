# Transporte can?nico de dispositivo ? Runtime

**SSOT:** `ordaxsystems/ordax-platform` / `ordax-mcp` / PostgreSQL.

O Runtime usa exclusivamente `POST /v3/device/transport/*` na origem `product_mcp_url`. Identidade, presen?a, fila de a??es, leases e resultados terminais s?o autenticados pela credencial device-scoped e persistidos pela autoridade PostgreSQL da Platform. N?o existe dual-write, fallback D1, bearer global de operador ou WebSocket operacional paralelo.

## Contrato

- `heartbeat`: publica presen?a e digest de capacidades;
- `claim`: obt?m no m?ximo uma a??o j? autorizada e cercada por lease;
- `start`: confirma o mesmo contexto de execu??o antes da a??o local;
- `lease`: renova o lease da execu??o corrente;
- `progress`: publica progresso limitado;
- `report` / `recover-report`: entregam resultado terminal can?nico, com SHA-256 e outbox local para recupera??o idempotente.

O Runtime nunca escolhe device, Space, projeto ou grant a partir de argumentos do modelo. A Platform ? a autoridade dessas rela??es.

## Seguran?a e cutover

O transporte anterior `CloudflareControlPlane` (`/v3/device/ws`) e o pairing local foram removidos do Runtime. O enrollment ? feito pelo fluxo Product OAuth + PKCE e `/v3/device/setup`. Configura??es antigas com `control_plane_url` s?o aceitas somente como entrada de migra??o one-shot quando `product_mcp_url` ainda n?o existe; o campo antigo n?o ? autoridade nem volta a ser persistido.

Artifacts e audit remoto permanecem fail-closed at? suas autoridades can?nicas PostgreSQL + R2 estarem implementadas. O Runtime n?o restaura o caminho D1 para preencher essa lacuna.

## Prova

A su?te `tests.test_product_device_control_plane` cobre heartbeat, claim/start cercado, relat?rio terminal can?nico, rejei??o de credencial e aus?ncia de fallback WebSocket/D1/operator.

## Presença durante ociosidade (01/11/2026)

O `product_wake_enabled` permanece desativado por padrão até a validação
do protocolo de wake. Em 04/11/2026, um teste físico no Windows com e sem
credencial de dispositivo terminou em timeout no handshake WSS; isso não
comprova a implantação nem a indisponibilidade geral do servidor e não
justifica ligar o wake ou ampliar o intervalo de claims no dispositivo. A fila de ações é consultada no máximo no prazo
`next_claim_due`, cuja espera conservadora aumenta para 30–60 segundos;
com wake conectado **e entrega de um sinal `wake` verificada na mesma conexão**, a reconciliação de jobs é de até 300 segundos. Conexão silenciosa, sem prova de entrega, preserva o intervalo limitado de 30–60 segundos.

**Heartbeat não é claim.** O mesmo loop canônico agora espera sempre o
menor prazo entre `next_claim_due` e `next_heartbeat`, **independente
do wake**. Ao acordar para um heartbeat de 20 segundos, preserva
`next_claim_due` e não consulta o banco desnecessariamente por uma nova
ação. Uma notificação wake continua apenas antecipando um claim,
sempre sob a autoridade Product; não concede execução ou permissões.

O teste `tests.test_product_idle_poll_budget` cobre wake desligado por
padrão, heartbeat de 20s durante claims a cada 60s/300s, prioridades dos
prazos, tratamento de deadline vencido e limites de consultas PostgreSQL.

## Prontidão local portátil e de baixo custo (Windows / OS Native)

O status completo `GET /status` pode inspecionar aplicativos e componentes
opcionais; isso **não** deve bloquear um Supervisor Native nem uma atualização
Windows. `ordax_dev_agent/status_server.py` oferece `GET /readiness` no
**mesmo** servidor loopback e no mesmo processo canônico, sem novos agentes,
filas, threads de polling ou autoridade. `ordax_dev_agent/main.py` calcula a
resposta em memória via `product_dispatch_readiness`, sem varredura de
arquivos, chamadas remotas ou `agent_status`. O port é o mesmo `status_port`
do Runtime. Na entrada CLI Native o OS Supervisor define `--status-port`
no intervalo 1..65535; não assumir a porta 8765 do Windows no Native.

`local_dispatch_ready=true` exige estado `ready/busy`, transporte
`connected`, heartbeat ainda válido, registry local e identidade confirmada.
É **somente prontidão local**: `client_authorization` permanece
`not_verified_by_local_readiness`. Não atesta grant de cliente, seleção de
sessão, `capability_digest`, capacidade publicada no gateway nem autorização
para despachar. Se o controle remoto falhar, não inventar sucesso nem repetir
POST de ação; consultar recibo por `request_id` e preservar a execução única.

Este caminho não substitui `GET /health` (liveness O(1) do watchdog),
`GET /status` (diagnóstico completo opcional), nem o Product PostgreSQL
(única autoridade de claims e execução). A regra é a mesma no Windows e no
OrdaX OS Native; no Native, o Supervisor do OS continua sendo o owner de
restart, e o Windows continua com sua tarefa única. Não publicar essa porta
fora de loopback nem transformar o endpoint numa ferramenta MCP pública.

## Heartbeat Product versus sessao central (04/11/2026)

A confirmacao de `POST /v3/device/transport/heartbeat` e a prova de
conectividade do dispositivo. A verificacao adicional de
`/v3/product/device/central-session` nao e requisito sincrono para
`claim`, nem pode prender um heartbeat `busy` ou o relatorio terminal:
a resposta desse endpoint opcional pode levar ate 8 segundos por
requisicao. O mesmo heartbeat ja existente agora **agenda** uma
verificacao na propria instancia de `ProductCentralSessionClient`,
em no maximo uma thread daemon de curta duracao por vez, sem timer,
daemon externo, fila de comandos, identidade ou token duplicado.
A cadencia minima anterior de consultas e o conteudo da autoridade
permanecem iguais, inclusive a revalidacao da identidade Native.
A verificacao nao chama `claim` e nao substitui grants Device/Space.

Em shutdown, a instancia impede novos polls e nao confirma challenges
antigos depois de iniciar o fechamento. A resposta publica permanece
uma observacao assincrona historica
(`central_session.last_confirmation_observed`), nao um grant atual
nem uma autorizacao de despacho. Testes:
`tests/test_product_central_session.py` e
`tests/test_product_device_control_plane.py`.
