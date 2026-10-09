# Transporte de presença canônica de dispositivo — Runtime

**Owner de presença, identidade e credencial:** `ordaxsystems/ordax-platform`,
com endpoint `POST /v3/product/device/presence` introduzido na
[Platform PR #114](https://github.com/ordaxsystems/ordax-platform/pull/114).
**Owner de transporte cliente:** `ordaxsystems/ordax-runtime`,
`ordax_dev_agent/product_device_presence.py`. **Owner da interface:**
`ordaxsystems/ordax-apps`. Este módulo não cria uma segunda autoridade.

## Limite funcional e uso

`ProductDevicePresenceClient.report(device_id, device_credential, snapshot)`
envia **uma** observação explicitamente autenticada para o Product PostgreSQL.
A identidade é um UUID normalizado, a credencial é device-scoped e o
snapshot contém somente os quatro campos fechados
(`online`, `runtime_kind`, `agent_version`, `capability_digest`).
`changed:false` é recibo bem-sucedido de coalescência; não promete que
`last_seen_at` foi atualizado. O cliente não armazena tokens; responde
com `ProductPresenceReceipt` sem autoridade de execução.

O módulo verifica HTTPS e origin restrito, status, MIME, limite máximo de
resposta **8 KiB descomprimidos**, UTF-8, JSON sem campos ou chaves duplicados
e correspondência exata do `device_id` retornado. Não segue redirecionamento,
recusa autenticação de conta herdada do transporte antes da rede e aplica
prazo total de confirmação, inclusive a respostas lentas em vários chunks.
O tamanho de `agent_version` segue as 80 unidades UTF-16 do contrato Platform.
Não tenta repetir POST após timeout ou ACK inválido, e não expõe stack/segredo
do servidor. Erros são códigos públicos curtos e nunca sinalizam
`online:true` com base em um timeout, timestamp ou alegação do cliente.

## Cutover deliberadamente bloqueado

O Device Agent em uso consome token `cloudflare-v3` e WebSocket
`/v3/device/ws` em `CloudflareControlPlane.heartbeat`. **Não foi provado**
que esse UUID/credencial legado é o mesmo par reconhecido pelo RPC de
autenticação PostgreSQL. Por isso **o módulo novo não é chamado automaticamente**
e não há dual-write, fallback D1 ou heartbeat concorrente.

Condições para wiring e rollout:

1. Platform demonstra o vínculo do dispositivo com UUID e credencial
   canônicos, incluindo rota de registro, rotação e revogação sem expor
   credencial de outro proprietário;
2. Runtime implementa migração explícita com **um único canal selecionado**
   para presença, claim, report/audit e autorização, preservando outbox
   terminal e recuperação do `request_id` do Runtime #65;
3. Studio somente considera executor disponível mediante grants correntes,
   canal real e prova online — timestamp de presença isolado não basta;
4. Prova E2E real em hardware com negação entre contas, token revogado,
   queda/retorno, estado desconhecido e comando recebido uma vez.

Este módulo é um avanço de transporte verificável, não um dispositivo
conectado, migração de produção ou autenticação já homologada.

## Provas

`python -m unittest tests.test_product_device_presence_client -v`

O teste com `httpx.MockTransport` cobre um POST e quatro campos exatos,
coalescência `changed:false`, negar UUID/token inválido **antes de rede**,
revogação, 307 e falhas 408/429/5xx sem retry, resposta com token privado,
MIME/JSON/UTF-8/bomba gzip fora do limite, campos extras, receipt incorreto
e preservação do owner. A suíte faz parte de `ORDAX Runtime Contracts` no CI
Windows do repositório. Sem credenciais, deploy ou sessão real.

Seis checks de source do Worker foram transferidos para o owner Platform na
[PR 116](https://github.com/ordaxsystems/ordax-platform/pull/116);
os 14 testes do transporte Runtime permanecem aqui e passam a executar na CI.
As verificações migradas seguem o registro canônico de ações de navegador e
a revogação administrativa atual. A antiga afirmação de retenção agendada
foi substituída pela prova do bloqueio de retenção no gate canônico existente.
Nenhum serviço D1 obsoleto foi restaurado e nenhum gate foi removido.
