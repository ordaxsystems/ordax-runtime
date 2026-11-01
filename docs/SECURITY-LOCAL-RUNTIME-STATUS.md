# Fronteira HTTP local do Runtime — segurança de leitura

**Autoridade de execução:** `ordax_dev_agent.product_gateway`, grants verificadas
pela Plataforma e transporte Product. O servidor
`ordax_dev_agent.status_server` em `127.0.0.1:8765` é um
**diagnóstico sem autenticação**, não uma ponte de comandos.

## Modelo de ameaça

O servidor publica `/health`, `/capabilities`, `/status` e uma página
de observação `/`. O status pode conter metadados de projetos, caminhos
locais e estado do agente. **Escutar apenas em loopback não impede DNS
rebinding**: uma página remota pode controlar o cabeçalho HTTP `Host`
de requisições resolvidas para `127.0.0.1`. Os caminhos de observação não
devem ser expostos a esse navegador.

O servidor valida, antes de consultar `status_provider` em qualquer rota:

- O bind deve ser **exatamente `127.0.0.1`**; `0.0.0.0`, IP de LAN, IPv6
  não suportado e `localhost` como *bind* são rejeitados.
- O cliente de conexão deve ser `127.0.0.1`.
- A requisição deve apresentar **exatamente um** `Host`, e apenas a
  autoridade literal `127.0.0.1:<porta-escutada>` ou
  `localhost:<porta-escutada>`; porta ausente/errada, domínio alternativo,
  nomes DNS, userinfo, IP abreviado e `Host` duplicado são recusados.
- A validação usa a porta **real** do servidor, inclusive nos testes que
  obtêm porta dinâmica, e responde `403` sem corpo, sem cache e sem
  executar o provider em caso de recusa.

A mudança compartilha **um único gate** para todas as rotas; não deixa
`/health` mais permissivo que `/status`. Não publica CORS e não concede
ações. `GET` conserva contrato e cabeçalhos CSP/no-store/deny-frame
existentes para ferramentas e clientes locais legítimos.

## Fronteira do Studio

O Studio Electron `ordax-apps` usa somente
`GET http://127.0.0.1:8765/health` para o indicador **serviço local
observado**. Esta resposta não confirma a identidade do processo da porta,
conta, autorizador, credenciais ou grants e **não** permite invocação de
arquivos, terminal, controle do computador ou instalação. A autorização
Product permanece exclusiva da plataforma e do Runtime.

O Runtime preserva essa semântica de liveness e publica no objeto `runtime`
de `/status` os campos aditivos `action_registry_ready`,
`product_dispatch_ready` e `product_readiness_code`. Prontidão de despacho
Product exige ActionRegistry carregado, controle remoto configurado, identidade
pareada e heartbeat Product bem-sucedido nos últimos 60 segundos. Durante uma
ação longa, falha de heartbeat passa o transporte para `reconnecting` sem
apagar o estado `busy`; uma recuperação volta a `connected`. Os códigos
incluem `action_registry_unavailable`, `remote_control_not_configured`,
`pairing_required`, `transport_reconnecting`, `heartbeat_not_observed`,
`heartbeat_stale`, `credential_recovery_required` e `local_access_disabled`.

Esses campos não provam que um MCP remoto está online, que uma ação específica
tem grant, nem que um comando foi executado ou auditado. A interface deve
mostrar separadamente presença local, saúde do endpoint MCP e prontidão do
dispatcher Runtime. O agregador `StudioApi.product_status` conserva o campo
legado `device_agent.ok` para compatibilidade e acrescenta
`device_agent.product_dispatch_ready` e `device_agent.product_readiness_code`.

**Não** transformar `/status` ou `/health` em token, proxy de comandos,
API de execução ou canal temporário de migração. O cutover do Studio 0.14.1
para o instalador oficial exige ponte com autenticação do processo/usuário,
identidade canônica, grants por escopo, auditoria, revogação, confirmação
de efeitos e recuperação idempotente, seguida de E2E da instalação final
(issue Runtime #67).

## Prova no Windows

`python -m unittest tests.test_status_server_loopback_security -v`

O teste usa um servidor real em porta efêmera e cliente HTTP do Python
com `Host` forjado/duplicado. Verifica `/`, `/health`,
`/capabilities`, `/status`, métodos não autorizados, as respostas sem
vazamento, zero invocação do `status_provider` para requests recusados e
bind externo rejeitado **antes** de abrir socket. O teste roda na workflow
Windows `Studio Canonical Presentation Handoff` junto com a prova
canônica do pacote Electron. A alteração não publica release nem muda
o installer bloqueado no source lock antigo.

## Confirmação de presença remota (01/11/2026)

No transporte Product canônico, `pair_if_needed()` **não** autentica uma
sessão remota: o token foi matriculado anteriormente via Owner OAuth. Carregar
identidade, iniciar o dispatcher e publicar `/health` não atribuem
`paired=true`, `state=ready` ou `transport_state=connected`.

O primeiro `POST /v3/device/transport/heartbeat` confirmado é o gate que
define `paired=true`, `state=ready` e `last_heartbeat_at`. Erro HTTP,
rede ou credencial não publica esse estado. A confirmação de presença não
autoriza ações, não atesta sessão Central nem comprova execução MCP E2E.

A correção reutiliza o único Product transport/dispatcher existente; nenhum
serviço, perfil de permissões, protocolo ou fallback foi criado.

## Recuperação de erro de transporte (01/11/2026)

O retorno do temporizador de retry **não** altera `state` para
`ready`. Após erro de entrega ou de claim do Control Plane o Runtime
permanece `control-plane-error` e `transport_state=reconnecting`
até receber um **novo heartbeat Product confirmado**. Isso evita
prontidão positiva por passagem de tempo ou simples carregamento de token.
O heartbeat bem-sucedido restaura `ready/connected` e a fase ociosa
apenas quando apropriado; não sobrescreve `busy`, `stopping`,
`local-disabled` nem `credential-recovery-required`. O recibo de
heartbeat não substitui autorização Owner ou E2E MCP de ação.
