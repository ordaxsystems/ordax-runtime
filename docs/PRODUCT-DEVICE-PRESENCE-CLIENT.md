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
do protocolo de wake. A fila de ações é consultada no máximo no prazo
`next_claim_due`, cuja espera conservadora aumenta para 30–60 segundos;
com wake conectado, a reconciliação de jobs é de até 300 segundos.

**Heartbeat não é claim.** O mesmo loop canônico agora espera sempre o
menor prazo entre `next_claim_due` e `next_heartbeat`, **independente
do wake**. Ao acordar para um heartbeat de 20 segundos, preserva
`next_claim_due` e não consulta o banco desnecessariamente por uma nova
ação. Uma notificação wake continua apenas antecipando um claim,
sempre sob a autoridade Product; não concede execução ou permissões.

O teste `tests.test_product_idle_poll_budget` cobre wake desligado por
padrão, heartbeat de 20s durante claims a cada 60s/300s, prioridades dos
prazos, tratamento de deadline vencido e limites de consultas PostgreSQL.
