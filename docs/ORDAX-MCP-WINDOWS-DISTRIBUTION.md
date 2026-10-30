# OrdaX MCP — distribuicao Windows independente

## Produto e owner

**OrdaX MCP** e o nome apresentado ao usuario para conectar um computador
Windows a clientes autorizados (ChatGPT e outros). **ordax-runtime** permanece
o repositorio e executor unico. A Platform e a autoridade de Conta, OAuth,
dispositivos, MCP, grants e revogacao; o Studio pertence ao repositorio
`ordax-apps` e sera **um consumidor opcional** do Runtime, nunca sua fonte
de instalacao nem sua interface obrigatoria.

O candidato independente e
`OrdaX-MCP-Setup-<versao-do-ordax-runtime>-x64.exe`. Ele usa o **mesmo**
`packaging/windows/ordax_launcher.c`, com
`DORDAX_RUNTIME_LAUNCHER=1`, e inclui o Python privado, os executores
de capacidades e o CLI de autorizacao Owner. Nao empacota Electron,
`presentation` ou source `ordax-apps`. O processo visivel ao sistema
continua `ORDAX Runtime.exe`; o produto apresentado e `OrdaX MCP`.

## Estado real — candidato de validacao, nao instalador publico

`scripts/windows/build-ordax-mcp.ps1` consome exclusivamente o Runtime
desta revisao e `control-plane-source.lock.json` existente. O endpoint
e validado pelo resolver canonico. O workflow
`.github/workflows/windows-mcp-candidate.yml` prova build x64,
pacote privado, instalacao, CLI, Runtime e desinstalacao, em runner Windows
descartavel. Publica apenas artefato de curta retencao da CI, sem release,
assinatura falsa ou deploy de servico. **Este candidato nao esta autorizado
para distribuicao publica** ate existir assinador Authenticode/Publisher
aprovado e validacao E2E completa de onboarding/consentimento/dispositivo.

A interface grafica minima propria para onboarding, conexao e politicas
**ainda e pendencia**. O CLI ja existente nao e um substituto silencioso de
UX final. O aplicativo do ChatGPT e um conector que acessa
`https://mcp.ordax.com.br/mcp`; **nao** e um segundo Runtime e nao
precisa de `npx` ou Node.js no Windows. A conexao remota requer
autenticacao Owner, vinculacao do dispositivo e grants explicitos.

## Coexistencia e migracao com instalacoes Studio existentes

O instalador Studio historico usa a raiz
`%LOCALAPPDATA%\\Programs\\ORDAX` e mantem um Runtime proprio, com
AppId Inno preexistente. O candidato independente usa **outra** raiz e
AppId; sua instalacao e bloqueada se detectar aquele Studio ou Runtime,
em vez de iniciar duas instancias concorrentes, sobrescrever arquivos,
destruir identidade ou compartilhar raiz de desinstalacao. O Windows
mutex `Local\\ORDAXRuntime` continua sendo o unico owner local.

Para coexistencia definitiva, deve-se migrar o instalador do Studio para
**descobrir e consumir** o OrdaX MCP instalado, e provar upgrade
in-place, rollback, estado e desinstalacao com uma so identidade executora
e nenhum conflito de processo. Essa migracao e responsabilidade coordenada
entre os owners `ordax-runtime` (host/instalacao) e `ordax-apps`
(UI Studio), sem fazer do Studio dependencia do MCP.

## Escopo

Nao alterar `ordax-os` USB Dev PR #2243, `ordax-flow` ou `ordax-gpt`
por motivo deste empacotamento; nao inferir que um dispositivo offline
foi atualizado por uma build de CI.
