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

## Perfil de host do OrdaX Desktop (fonte, sem ativacao)

O Desktop Windows do OrdaX OS e um host do **mesmo produto OrdaX**,
nao o agente de controle do Windows. A entrada assinada/pacote canonico do
Runtime aceita um perfil **explicito** por ambiente:

- \`ORDAX_RUNTIME_HOST_PROFILE=ordax-desktop\`;
- \`ORDAX_DESKTOP_USER_DATA\` = userData privado do Electron OrdaX;
- \`ORDAX_AGENT_STATE_DIR\` = \`<userData>/native-state/product-runtime\`.

A inscricao **Owner OAuth** deve ser concluida nessa pasta, com ID
de dispositivo e token distintos dos do agente auxiliar do Windows. Falta
credencial, existe link de pasta, ou IDs/transport divergem? Falha
fechada sem publicar uma falsa disponibilidade MCP.

Essa composicao usa o mesmo \`ordax_dev_agent.main.run\`, nao um segundo
dispatcher, mas o seu \`ActionRegistry\` declara somente
\`agent.status\` na primeira fatia: nao expor Win32, terminal, browser,
workspace ou controle de arquivos Windows por causa do nome "OrdaX OS".
Se a Surface apresentar presença, ela nao pode transformar isso em
\`capability_digest\`, grant ou acao autorizada. Novas capacidades devem
vir dos adaptadores Native do OS, com comprovacao da fronteira de Space.

A invocacao canônica deve ser feita **somente** pelo Supervisor de
\`ordax-os\` a partir de um pacote verificado. Esta fonte não instala
o Runtime no Electron, não cria credenciais automaticamente e não faz
prova de execução MCP no OrdaX Desktop. Enquanto a custódia/trust do
runtime component não estiver pronta, a ativação permanece bloqueada.
O launcher Windows mantém \`Local\\ORDAXRuntime\` como mutex de instância
unica: não assumir que uma identidade Windows auxiliar e uma identidade
Desktop podem executar simultaneamente sem contrato de coexistência
ou handoff atestado. Na execução final, o OS é o dono do lifecycle.

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
