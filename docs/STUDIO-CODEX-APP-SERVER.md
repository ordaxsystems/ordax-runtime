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

Há testes E2E **do transporte contra processo fake local**, sem API key, sem assinatura ChatGPT, sem login e sem conceder autoridade.

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
| Cliente JSONL stdio e handshake | Implementado; testes com fake local |
| Correlacionar RPC e receber eventos | Implementado; testes com fake local |
| Rejeitar solicitação do servidor | Implementado; fail-closed |
| Token/OAuth e direito de uso comercial | Não integrado; owner externo |
| Sandbox/grants integrados | Não integrado; **não liberar ações** |
| Adaptador tipado Runtime → Studio | Não integrado |
| Chat nativo enviando ao Codex | Não integrado |
| E2E com Codex real no Windows | Não executado |

Essa PR é uma fundação isolada; **não** anuncia o Studio como integrado ao Codex nem muda a readiness do MVP.
