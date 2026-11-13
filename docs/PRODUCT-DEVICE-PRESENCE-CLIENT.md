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

O `product_wake_enabled` é o caminho canônico de baixa latência no Runtime,
mas continua estritamente **advisory**: PostgreSQL permanece a única autoridade
de fila, lease e resultado. Uma conexão WSS silenciosa não é prova de entrega.
Enquanto nenhum sinal `wake` real foi entregue na conexão atual, a fila é
reconciliada por SQL com fallback limitado a **18 segundos**.

Depois de uma entrega de wake comprovada, o Runtime reduz a frequência de
polling, mas a reconciliação de sinal perdido é de no máximo **60 segundos**.
Esse limite é intencionalmente menor que o TTL canônico de **120 segundos** das
ações Product MCP. Nunca aumentar a reconciliação para além do TTL: nesse caso
um wake perdido faria a ação expirar antes de o próprio fallback poder recuperá-la.

**Heartbeat não é claim.** O loop espera sempre o menor prazo entre
`next_claim_due` e `next_heartbeat`, independentemente do wake. O heartbeat
de presença ocorre a cada **30 segundos** e não consulta a fila só por manter
presença. Uma notificação wake apenas antecipa um claim; não carrega comando,
grant, challenge ou permissão.

O teste `tests.test_product_idle_poll_budget` e a verificação do pacote Windows
travam os três contratos atuais: fallback sem wake ≤18 s, reconciliação após
wake =60 s e heartbeat =30 s, além de exigir que a reconciliação permaneça abaixo
do TTL de ação Product.

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

## Descoberta local de capacidades sem varredura (04/11/2026)

O endpoint `GET /capabilities` no **mesmo** servidor loopback,
protegido pelo mesmo controle de Host, usa diretamente os nomes
registrados em `ActionRegistry.names` (já filtrados pelas restrições
do host e pelos adaptadores reais). Continua aplicando o contrato
`device_capability_snapshot`, que só publica capacidades previamente
expostas em `PRODUCT_ACTIONS`: não concede grants nem anuncia
handlers gráficos ausentes no Native. Durante inicialização, com
registry ainda indisponível, devolve a lista vazia/degraded.

Antes, a mesma rota chamava `agent_status`, com sondagens de apps
e arquivos sem relação com a descoberta. Medição física no Windows
antes da alteração: `/readiness` = 211 ms e `/capabilities` expirou
após 12,6 s. O diagnóstico detalhado permanece em `GET /status`;
clientes que só precisam de ferramentas não dependem dele. Sem novo
serviço, thread, cache, lista duplicada ou consulta ao banco.

## Limite real do catalogo Product (04/11/2026)

A `main` declara 93 IDs no `PRODUCT_ACTIONS`; o antigo teto fixo de 64
causava excecao no `device_capability_snapshot` e fechamento da conexao
`GET /capabilities` mesmo apos remover a varredura lenta. O teto local
permanece **limitado**, agora em 128, com teste cobrindo todos os IDs.
O endpoint continua publicando **somente a intersecao** dos IDs Product
aprovados com `ActionRegistry.names` realmente registrados no host.
Nenhuma entrada e grant, consentimento, autorizacao remota ou disponibilidade
grafica Native; crescimento acima do limite exige revisao explicita.
