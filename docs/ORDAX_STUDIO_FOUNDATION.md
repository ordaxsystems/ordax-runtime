# ORDAX Studio — Fundação

## Objetivo

O ORDAX Studio é a evolução do antigo `mcp-blender`/OrdaX Device Agent para um ambiente de desenvolvimento agentic persistente.
Ele não substitui Blender, Unity, Git ou o Device Agent: unifica essas capacidades em um mesmo núcleo de projeto, memória e execução.

A primeira regra da evolução é preservar compatibilidade. O aplicativo `C:\Users\TONECOS\Apps\OrdaxLocalAI` continua intacto e utilizável durante a migração.

## Decisão de arquitetura

O repositório histórico `mcp-blender` continua sendo a linha de desenvolvimento do agente e pode manter esse nome no Git para preservar integrações existentes. O nome do **produto**, do runtime principal e do MCP canônico, porém, é **ORDAX Studio**. Blender e Unity passam a ser capabilities do ORDAX, não produtos separados.

O alias `mcp-blender` permanece válido para clientes antigos, mas resolve para o MCP completo do Studio. A bridge `mcp-blender-unity` fica restrita ao papel de compatibilidade/diagnóstico de baixo nível. O contrato detalhado está em [ORDAX_STUDIO_MCP_ARCHITECTURE.md](ORDAX_STUDIO_MCP_ARCHITECTURE.md).

```text
ChatGPT / cliente MCP
        |
        v
ORDAX Studio / Device Agent
        |
        +-- ORDAX Core
        |    +-- memória persistente
        |    +-- contexto por projeto
        |    +-- tarefas
        |    +-- checkpoints
        |
        +-- Workspace / Git / arquivos
        +-- Blender Live
        +-- Unity
        +-- adapters futuros
```

## Compatibilidade com ORDAX Local AI

`ordax_core.memory.resolve_memory_db()` procura o banco nesta ordem:

1. `ORDAX_MEMORY_DB`, quando definido explicitamente;
2. banco legado `~/Apps/OrdaxLocalAI/data/state.db`, quando já existe;
3. `%LOCALAPPDATA%/OrdaX/Studio/state.db` para instalações novas.

Isso permite continuar usando as memórias já criadas sem mover ou apagar dados. Contextos novos são gravados em `contexts/<projeto>.md`, ao lado do banco selecionado.

O esquema inicial é compatível com as tabelas já existentes: `projects`, `memories`, `tasks`, `checkpoints` e `events`.

## Capabilities de memória

O `ActionRegistry` agora inclui:

- `memory.status`
- `memory.context`
- `memory.remember`
- `memory.task_add`
- `memory.task_toggle`
- `memory.checkpoint`

O MCP local também possui atalhos de alto nível:

- `session_context`
- `memory_remember`
- `session_checkpoint`

A intenção é que um cliente MCP carregue `session_context` antes de continuar trabalho relevante em um projeto e registre um checkpoint ao concluir uma etapa significativa.

## Fases de produto

### Fase 1 — núcleo funcional

Consolidar memória, projetos, Git, terminal/arquivos e MCP num único runtime. Preservar o ORDAX Local AI como fallback e fonte de dados compatível.

### Fase 2 — Studio desktop

Evoluir a interface para workspace completo: explorador de arquivos, editor/diff, terminal, tarefas, histórico de decisões, capability status e controles de Blender/Unity.

### Fase 3 — produção 3D e engines

Integrar Blender Live, Unity e adapters de assets à experiência do Studio, mantendo contratos tipados, checkpoints e evidência visual.

### Estado atual da integração Blender no Studio

Projetos Blender na shell WebView2 carregam `blender.live_modeling_schema` no
bootstrap do workspace. A visão **MCP / Capacidades** renderiza diretamente as
operações tipadas publicadas pelo `ActionRegistry`, incluindo ARRAY, Geometry
Nodes surface scatter, o workflow Boolean não destrutivo `preview → commit →
cancel`, `mesh_cleanup` e o workflow `degenerate_repair_preview → commit/cancel` e
`merge_by_distance_preview → commit/cancel` com seleção explícita de vértices e
`boundary_hole_fill_preview â†’ commit/cancel` para um Ãºnico loop fechado de borda selecionado explicitamente.
O quality gate também publica repair hints de topologia: fixes realmente seguros
podem apontar para uma action revision-guarded, enquanto zero-length edges e faces
degeneradas apontam apenas para o preview reversível. O Studio não mantém uma
segunda lista de capacidades: status, ações e workflow vêm do mesmo contrato
consumido pelos clientes MCP e pelo companion.

### Fase 4 — autonomia controlada

Adicionar planejamento persistente, retomada automática de tarefas, políticas de permissão, auditoria e execução multi-etapa com gates explícitos para operações sensíveis.

### Fase 5 — integração no protótipo Ordax OS

A integração com `prototipo-ordax-os` fica deliberadamente adiada até o Studio cumprir um gate funcional mínimo. O desenvolvimento do Studio não deve depender do protótipo do sistema operacional.

Gate mínimo para iniciar a integração no OS:

- memória persistente validada entre processos/sessões;
- projeto ativo e contexto recuperável por MCP;
- operações de arquivos/Git estáveis;
- Blender e Unity operáveis como capabilities, sem acoplamento à UI;
- interface desktop funcional;
- testes de regressão do agente verdes;
- caminho de atualização e rollback definido.

## Continuidade durável do projeto

Handoffs continuam existindo para transferência pontual entre conversas e permanecem
temporários por desenho. Eles não são mais a única fonte do ponto de retomada.

O banco persistente possui agora `project_state`, um registro único e não expirável por
projeto. Esse estado guarda resumo operacional, próxima ação, concluídos, bloqueios,
caminhos alterados, estado Git, origem e timestamp.

- `continuity.update` grava explicitamente esse estado;
- `continuity.get` recupera o estado sem exigir um ID de handoff;
- cada `memory.checkpoint` atualiza o resumo e Git preservando detalhes ricos já existentes;
- cada `handoff.create` atualiza o estado durável na mesma transação;
- `memory.context`, `session.resume`, `BOOT_CONTEXT.md` e `agent.project_briefing`
  passam a carregar `project_state`;
- o Product MCP publica `project_briefing`, `continuity_state` e
  `continuity_update` com grants explícitos e sem expor caminhos locais.

Com isso, uma conversa nova pode descobrir o projeto e chamar `project_briefing` sem
conhecer um token de handoff anterior. Busca semântica/RAG de longo prazo continua sendo
uma camada posterior; o estado durável resolve primeiro a fonte de verdade operacional.

### Recall contextual no briefing

`agent.project_briefing` aceita opcionalmente `query` e `recall_limit` (1-50).
Quando uma consulta é fornecida, o runtime faz recuperação lexical limitada e project-scoped
sobre memórias, tarefas, checkpoints e `project_state`, retornando apenas snippets ranqueados.
Essa camada é deliberadamente lexical nesta fase: não declara similaridade semântica sem um
backend de embeddings validado. O contrato foi desenhado para que uma implementação vetorial
futura possa substituir ou complementar o ranking sem alterar a API consumida pelo ChatGPT.

Quando `query` tem até 200 caracteres, o mesmo briefing também reutiliza
`project.search_text` para localizar ocorrências em código e documentação aprovados pelo
gateway. Essa busca de fonte é limitada a 20 resultados e 1.500 arquivos por briefing e
retorna apenas caminho relativo, linha e trecho. Consultas maiores continuam recebendo recall
de memória, mas a busca em fonte é explicitamente marcada como ignorada em vez de ser truncada
silenciosamente.

## Sessao resumivel

A fundacao agora trata a continuidade como contrato explicito de runtime. `session.resume`:

- seleciona um projeto registrado;
- marca esse projeto como ativo no banco persistente;
- cria uma sessao com timestamp e referencia ao ultimo checkpoint;
- recupera memorias, tarefas e checkpoints do projeto;
- captura branch, HEAD e status Git atuais;
- publica as capabilities configuradas e os grupos de acoes disponiveis;
- atualiza `BOOT_CONTEXT.md` para compatibilidade com o ORDAX Local AI existente.

O MCP local expoe `session_resume` e `session_finish`. Quando nenhum projeto e informado, a retomada tenta primeiro o projeto ativo persistido, depois `default_project` e por fim o primeiro projeto registrado.

Essa camada nao depende da futura interface desktop: Blender, Unity, Git e workspace continuam capabilities do mesmo runtime e podem ser usados por qualquer cliente MCP autorizado.

## Runtime de produto

O pacote `ordax_studio` adiciona o primeiro entrypoint de produto sem duplicar o Device Agent:

```powershell
ordax-studio status
ordax-studio resume [projeto]
ordax-studio context [projeto]
ordax-studio checkpoint <projeto> "resumo"
ordax-studio finish <session_id>
ordax-studio mcp
```

`ordax-studio-mcp` permanece disponivel como alias direto para o MCP local. O CLI usa o mesmo `AgentConfig`, `ActionRegistry`, projetos cadastrados e banco persistente do Studio.

## Primeira shell desktop

`ordax-studio-desktop` abre a primeira interface desktop do produto. Ela usa o mesmo runtime do Device Agent e nao cria uma segunda implementacao de Blender/Unity/Git.

A shell inicial oferece projetos registrados, contexto persistente, memorias, tarefas, capabilities e terminal no diretorio do projeto. O botao **Retomar sessao** cria uma sessao persistente ligada ao ultimo checkpoint e ao estado Git atual; uma nova retomada encerra logicamente a sessao ativa anterior sem apagar seu historico, mantendo apenas uma sessao global aberta no Studio.

Para validacao sem abrir janela:

```powershell
ordax-studio-desktop --smoke
```

O aplicativo legado `ORDAX Local AI` permanece preservado como fallback durante esta fase.

## Workspace e editor seguro

A shell desktop inclui uma aba **Workspace** alimentada pelas actions existentes `project.inventory`, `project.text_read` e `project.text_write`.

O explorador ignora caches, builds e controle do repositorio. O editor aceita apenas caminhos/extensoes aprovados pelo gateway do projeto e usa o SHA-256 retornado na leitura como precondicao de salvamento; se outro processo alterar o arquivo antes do save, o Studio recusa a sobrescrita stale em vez de destruir a mudanca externa.

Essa camada e deliberadamente construida sobre o ActionRegistry, e nao sobre acesso irrestrito ao filesystem, para que a mesma politica possa ser reutilizada no futuro pelo app desktop, MCP e Ordax OS.

## Capabilities no Studio

A aba **Capabilities** deixa de ser apenas diagnostico e passa a usar o mesmo ActionRegistry do MCP. A primeira versao oferece Git status e controles de status/inicializacao para Blender e Unity, respeitando as capabilities declaradas por projeto.

Essas operacoes rodam fora da thread da interface para que inicializacao ou recuperacao de uma engine nao congele o Studio. Projetos sem a capability solicitada sao recusados pela propria UI antes da execucao.

## Retomada automatica

Ao abrir a shell desktop, o Studio seleciona o projeto ativo persistido quando ele ainda esta registrado; caso contrario usa `default_project` ou o primeiro projeto local disponivel. Em seguida executa uma retomada silenciosa de sessao.

Isso faz com que fechar e reabrir o aplicativo nao zere o trabalho: a nova sessao reaproveita memorias, tarefas e ultimo checkpoint, captura o Git atual e encerra logicamente qualquer sessao global anterior ainda aberta.

## Health unificado do projeto

O Studio expõe `agent.project_health` no ActionRegistry e `project_health` no MCP. A leitura é observacional: não inicia, recupera nem modifica Blender ou Unity.

O diagnóstico agrega memória persistente, estado Git e adapters habilitados. Cada adapter informa estados como `ready`, `update_required`, `stale`, `offline` ou `disabled`; o projeto passa a `attention` quando uma integração habilitada requer intervenção.

CLI, desktop e MCP usam a mesma regra `select_available_project`: projeto explícito válido, projeto ativo persistido, default disponível e, por fim, primeiro projeto registrado cuja pasta exista. Isso evita que uma pasta removida impeça a retomada de uma sessão futura.

## Painel inicial

A shell desktop passa a abrir com um **Painel** de continuidade antes do editor. Ele resume projeto ativo, sessão, memória/tarefas, Git, Blender, Unity e o último checkpoint.

A saúde de Git e adapters é carregada fora da thread da interface usando `agent.project_health`; portanto uma inspeção lenta não deve congelar a janela. O painel é observacional e não inicia nem modifica engines automaticamente.

## Preview visual contínuo

O Workspace agora possui um painel lateral de preview, inspirado no fluxo do Lovable.
A mesma superfície atende projetos web, Blender e Unity sem duplicar o runtime.

Blender e Unity mostram o artifact visual mais recente e permitem captura sob demanda.
Projetos web com `preview.url`, `preview.entry` ou `index.html` podem gerar screenshot via Chrome/Edge.
O painel acompanha novos artifacts automaticamente sem reiniciar a sessão.

`project.preview_status` e `project.preview_capture` são actions tipadas do mesmo gateway.
O MCP `project_preview_image` devolve os pixels reais da imagem ao modelo via `ImageContent`.
Essa camada prepara o futuro preview web interativo com WebView2 usando o mesmo contrato no Desktop, MCP e Ordax OS.

O runtime web também possui `project.preview_start` e `project.preview_stop`.
Para projetos estáticos o Studio usa um servidor HTTP local; para projetos com `package.json`, detecta Vite/Next ou um script `dev` suportado e inicia o processo sem shell remoto genérico.
A UI expõe **Executar**, **Parar**, **Capturar**, **Atualizar** e atualização automática do preview.

## Shell interativa WebView2

Além da shell Tk de fallback, `ordax-studio-web` oferece uma interface WebView2 inspirada em IDEs agentic modernas e no fluxo do Lovable.
Ela usa o mesmo `ActionRegistry`, banco de memória e regras de segurança do Studio.

O workspace segue um contrato estável de três superfícies:

1. **sidebar esquerda — Projetos:** lista apenas workspaces/repositórios, com o projeto ativo destacado; arquivos não competem com projetos nessa navegação;
2. **centro — Agente:** continuidade, tarefas e futuro transporte de chat ocupam a superfície principal; arquivos, busca, Git, memória e MCP são vistas contextuais do projeto;
3. **direita — Preview:** superfície visual de primeira classe, independente do arquivo selecionado, com refresh, captura, logs e modo maximizado.

Ao clicar em um projeto `web`, o Studio reutiliza o runtime local quando ele já existe ou executa `project.preview_start` automaticamente quando está parado. O `iframe` recebe a URL local do runtime assim que ela fica disponível.
Projetos Blender/Unity não iniciam engines pesadas apenas por navegação: o painel restaura a captura visual mais recente e permite uma nova captura sob demanda.
Projetos sem runtime visual permanecem em modo `artifact`.

A detecção web é conservadora: configuração explícita de preview, `index.html` ou `package.json` com script `dev`. Um `package.json` sem runtime de desenvolvimento não transforma um repositório de código em projeto visual.
O Studio persiste a vista central por projeto e invalida respostas assíncronas antigas durante trocas rápidas de workspace para impedir que o preview do projeto anterior substitua o atual.

Salvar continua exigindo o SHA-256 da leitura original, portanto a nova UI não contorna o controle de escrita stale.

## Modelo de projeto: repositório primeiro

A unidade principal do ORDAX Studio é um repositório Git, não um arquivo nem uma pasta arbitrária.

### Provisionamento de projeto

O Studio possui uma raiz local explícita de workspaces. Instalações novas podem defini-la por
`ORDAX_WORKSPACE_ROOT` ou `workspace_root` em `agent-settings.json`; instalações antigas
continuam compatíveis usando o diretório pai do HORDAX como fallback.

A action tipada `workspace.project_create` cria projetos somente dentro dessa raiz. Ela:

- valida um slug limitado antes de tocar o disco;
- cria `.ordax/project.json`, `.gitignore` e, por padrão, `README.md`;
- prepara `automation/blender` quando Blender é habilitado;
- inicializa Git em `main` por padrão, sem criar commit ou exigir identidade Git;
- registra o projeto atomicamente em `agent-settings.json`;
- adiciona o projeto ao runtime atual e o torna o projeto ativo da memória;
- remove a pasta recém-criada se alguma etapa anterior ao registro falhar.

O MCP local publica `project_create`. O Product MCP/Cloudflare publica a mesma capacidade
somente através do grant explícito `workspace.project_create`; o cliente remoto não escolhe
um caminho absoluto e o resultado público remove os caminhos locais do dispositivo.

Criar um repositório remoto no GitHub é uma etapa separada: o provisionamento local não recebe,
armazena nem reutiliza credenciais de provedor Git de forma implícita.

A home consulta `workspace.repository_catalog`, deduplica worktrees/aliases pelo remoto Git e oculta entradas legadas que não sejam repositórios.
Projetos web ou de software podem ser registrados com `apps: []`; Blender e Unity passam a ser capabilities opcionais do repositório.

Arquivos só aparecem depois de `Abrir projeto`. O preview continua pertencendo ao projeto/repositório ativo e não ao arquivo selecionado.
O mesmo catálogo é exposto pelo MCP através de `repository_catalog`, preparando a integração futura com o Ordax OS.
