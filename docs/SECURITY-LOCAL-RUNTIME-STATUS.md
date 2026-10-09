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
