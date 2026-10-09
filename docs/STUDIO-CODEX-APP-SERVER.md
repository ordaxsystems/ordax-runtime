# OrdaX Studio — integração do Codex App Server (fundação)

**Owner do transporte de processo/stdio:** ordaxsystems/ordax-runtime.  
**Owner da UI:** ordaxsystems/ordax-apps/apps/studio.  
**Owner de autenticação remota, políticas e grants:** owners já canônicos de identidade/plataforma/Control Plane, não este transporte.

## Decisão

**Integrar**, não fazer fork do Codex nem automatizar o DOM do ChatGPT Web. O Codex oficial (Apache-2.0) fornece o App Server; a UI e contratos do Studio permanecem independentes de provedor. Licenças/transitivos, artefato assinado, SBOM e termos comerciais ainda exigem verificação no release.

Documentação upstream: https://developers.openai.com/codex/app-server  
Fluxo OAuth de assinatura elegível: https://developers.openai.com/siwc/token-sharing-open-source/codex-app-server

## Incremento implementado

O módulo ordax_studio/codex_app_server.py implementa **somente a camada privada de transporte**:

- execução controlada de **um** processo filho explicitamente configurado, sem shell e com ambiente explícito, sem herdar segredos arbitrários;
- handshake initialize → initialized, com identificação OrdaX Studio;
- RPC correlacionado por ID, com timeout, limite de frame e erros redigidos;
- leitura ordenada de eventos de streaming via JSONL e fila limitada;
- pedido espontâneo de permissão pelo servidor é **rejeitado** até existir fluxo de autorização local apropriado;
- desconexão, erros de protocolo e saturação falham fechados; close() encerra apenas o filho próprio.

Há testes do transporte contra processo fake local e um **smoke real, opcional e isolado** com o binário oficial do Codex. `ordax_studio/codex_events.py` normaliza apenas eventos selecionados (deltas de mensagens, início/fim de turnos e metadados de itens), exigindo `threadId` e `turnId` válidos, com limites e sem expor comandos, caminhos ou saídas de ferramentas. Esse mapeamento é privado: nenhum evento vira concessão de authority.

## Bloqueios antes de ligar o botão Enviar do Studio

1. **Segurança:** o Codex pode executar ferramentas próprias. sandbox=read-only por si só **não comprova** que leituras estão limitadas ao projeto nem que o Action Gateway OrdaX validou cada ação. O provider adapter deve usar política/isolamento efetivos, aprovação de owner, auditoria, grants e limites; não mapear o RPC cru para window.ordaxStudioHost nem conceder execução diretamente pela UI.
2. **Conta e compliance:** autenticação ChatGPT/Codex por fluxo oficialmente autorizado; sem cookies, scraping ou automação privada do navegador. A capacidade de usar assinatura ChatGPT comercialmente depende de elegibilidade/autorizações. Credenciais pertencem a auth owner, com armazenamento seguro, rotação e revogação; a UI nunca recebe token.
3. **Port público:** modelar conversas, turnos, eventos, aprovações, anexos, cancelamentos e erros na interface provider-neutral do Runtime, sem criar segundo banco de sessões ou duas listas de chats.
4. **Protocolo/versionamento:** fixar versão compatível do Codex; verificar esquema codex app-server generate-json-schema, handshake, thread/resume, turn/status, streaming, falhas e upgrades; bloquear regressões em CI.
5. **Windows:** testes reais de processo Codex instalado, instalação limpa, conta autorizada, reconexão/restart, retorno de streaming, cancelamento e manutenção do estado por projeto. Os testes desta PR **não** equivalem ao E2E do produto.
6. **Coordenação:** não alterar assistant_surface.js, ide_shell.js, host_bridge.js, WebView2 ou políticas de browser enquanto outros chats trabalham nesses owners.

## Gates objetivos

| Camada | Situação neste incremento |
|---|---|
| Cliente JSONL stdio e handshake | Implementado; validado com fake e Codex CLI oficial 0.162.0 no Windows |
| Correlacionar RPC e receber eventos | Implementado; fake para streaming + smoke real para RPC |
| Rejeitar solicitação do servidor | Implementado; fail-closed |
| Token/OAuth e direito de uso comercial | Não integrado; owner externo |
| Sandbox/grants integrados | Não integrado; **não liberar ações** |
| Adaptador tipado Runtime → Studio | Não integrado |
| Chat nativo enviando ao Codex | Não integrado |
| Handshake/RPC com Codex real no Windows | Executado em 2026-10-08: `initialize`, `config/read` e encerramento, sem conta/modelo | 
| E2E de conversa/turno com modelo real no Windows | Não executado |

## Evidência de testes (Windows, 2026-10-08)

Em checkout separado de `ordax-runtime`, com Python 3.13.14, **13/13 testes passaram**, incluindo 7 testes de transporte fake, 5 de normalização e 1 smoke real. O smoke usou `@openai/codex@0.162.0`, `codex.exe app-server` e `CODEX_HOME` temporário sem reutilizar a sessão ChatGPT. O teste opcional `tests/test_codex_app_server_official_smoke.py` só roda quando `CODEX_APP_SERVER_BINARY` aponta para um executável local; não baixa nada em CI e, sem o binário, é ignorado. Comandos:

```powershell
$env:PYTHONPATH='.'
$env:CODEX_APP_SERVER_BINARY='C:\\caminho\\para\\codex.exe'
py -3 -m unittest discover -s tests -p 'test_codex*.py' -v
```

Esse resultado **não autoriza** o Codex a ler projetos, executar tarefas ou acessar modelos. A integração E2E de produto permanece pendente.

Essa PR é uma fundação isolada; **não** anuncia o Studio como integrado ao Codex nem muda a readiness do MVP.
