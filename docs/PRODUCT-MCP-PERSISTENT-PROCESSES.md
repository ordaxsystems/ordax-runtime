# Product MCP — processos longos e visibilidade entre conversas

Este guia documenta as ferramentas **existentes** do Runtime: `computer_process_start/status/logs/list/stop` e, para projetos com grant, `process_start/status/logs/list/stop`. Não é um segundo orquestrador nem um mecanismo que mantém ChatGPT Web respondendo.

## Protocolo

1. Use `computer_process_start` (ou `process_start` com projeto autorizado) para um comando **finito**, idempotente quando possível, que registre progresso no stdout. O processo supervisionado segue independente da duração da chamada MCP, enquanto o Runtime/dispositivo suportar a execução.
2. Opcionalmente inclua em `env` a string `ORDAX_TASK_TITLE` (até 160 caracteres, sem quebra de linha). Ela é metadata descritiva, persistida no registro do **mesmo** processo; não identifica nem autentica a conversa GPT e não concede permissões.
3. Guarde o `process_id` original. Consulte `computer_process_status` e `computer_process_logs` para saber execução/progresso reais, inclusive em uma nova conversa com o mesmo Device autorizado. Não reinicie o comando se o resultado estiver incerto; confira status e efeitos.
4. Para descobrir trabalhos de outras conversas, consulte `computer_process_list` no mesmo Device. A resposta compacta tem no máximo 24 entradas, **prioriza processos realmente ativos** e informa `total_registered`/`truncated`. As entradas antigas não são apagadas. Use o ID para detalhes/logs; jamais interprete uma lista truncada como inventário completo dos históricos.
5. Caso haja bloqueio, falha ou necessidade de aprovação, registre o motivo nos logs/estado do trabalho e retome somente após verificar os efeitos externos. Uma tarefa encerrada não é reiniciada automaticamente pela existência do registro.

## Limites comprováveis

- O mecanismo registra e acompanha **processos lançados pelas ferramentas**; não enxerga o raciocínio privado de um chat ou sessões GPT sem chamadas MCP.
- Um processo ainda ativo **não prova** que a conversa GPT está ativa. O MCP não pode forçar a interface GPT Web a continuar respondendo; outra conversa pode consultar o mesmo processo por meio do Device autorizado.
- O estado de processo sobrevive à troca de conversa e pode ser lido após reinicialização do Runtime; a execução do processo em si não é garantida após reinicialização do computador, desligamento ou revogação de grants.
- Respeitar grants, escopo de Device/projeto, idempotência, revisões e recibos reais da Platform e do Runtime. Não criar banco de Task concorrente, daemon redundante ou poller permanente no GPT.

## Teste DEV de 22/11/2026

No Device `DESKTOP-4UFL03B`, a ferramenta real `computer_process_start` gerou o processo `455eb144-5ded-482a-be48-14a00029444f` (smoke de 36 passos de 10 segundos). Status posterior confirmou `running=true` com ownership e logs em sequência. O histórico local possuía 371 recibos; a consulta anterior `computer_process_list` excedia o limite de resposta ao devolver o histórico inteiro. O ajuste nesta revisão torna **a listagem** compacta e prioriza os ativos. Não reivindicar instalação da revisão no Device antes da prova física pós-atualização.


## Missões rastreáveis sem novo orquestrador

O mesmo recibo de processo pode conter metadados **descritivos** via `env` no
`computer_process_start` / `process_start`:

- `ORDAX_TASK_TITLE`: título legível (contrato existente).
- `ORDAX_MISSION_ID`: rótulo estável para correlacionar chats, 1–80 caracteres
  alfanuméricos, ponto, hífen ou `_`; **não é trava nem chave única**.
- `ORDAX_MISSION_PROJECT`: rótulo de projeto com as mesmas regras, opcional e
  somente permitido junto de `ORDAX_MISSION_ID`. **Não equivale a grant**.

Para registrar progresso, o processo escreve linhas completas em stdout:

```text
ORDAX_MISSION_CHECKPOINT|{"step":2,"total":3,"stage":"tests","status":"running","summary":"29 passed","resume_ref":"commit:abc123"}
```

O campo `status` permite `running`, `blocked`, `failed` ou `completed`.
`stage` é obrigatório (1–80 caracteres), `summary` e `resume_ref` são opcionais
(até 160 caracteres, sem controles). `step` e `total` são inteiros de 0..100000,
com `total` positivo e `step <= total`.

`computer_process_status` e a listagem compacta retornam o último
`latest_checkpoint` válido de **até 32 KiB finais do mesmo log**; as ferramentas
equivalentes de projeto seguem a mesma regra. Nenhum poller adicional, banco
Task, serviço de automação ou mudança nos grants é criado. A listagem continua
limitada a 24 processos, com prioridade para os ativos. Logs históricos
maiores podem exigir `process_logs` por ID; a ausência de `latest_checkpoint`
não significa que uma etapa não aconteceu.

Os marcadores são classificados `source=unverified_child_stdout`: **não provam**
uma alteração externa, commit, aprovação, execução do GPT ou conclusão real.
Para continuar uma missão, a nova conversa deve validar os efeitos com as
ferramentas do respectivo owner, verificar processo e código de saída e só
então decidir a próxima etapa. Não executar novamente ações de efeito incerto.
Use referências opacas e não armazene segredos nos rótulos, logs ou resumos.

## Continuidade canônica e próximos passos — regra operacional

**Não criar outro banco de Task, agente autônomo, agenda periódica ou memória
de missões paralela.** A memória de projetos (`ordax_core.MemoryStore`), os
handoffs existentes e os recibos do supervisor já são os mecanismos
do Runtime. O Forge tem seu próprio owner/SQLite de Tasks, claims e
observações; somente o **protocolo de verificação** é compartilhado.

### Propostas não são realidade

- `handoff.create` registra **uma proposta que expira em 1–168 horas**, com
  `next_action`, `git` observado, `completed` e `blockers` informados pelo
  cliente. **Não atualiza mais `project_state`** (remoção da promoção automática
  legada). `handoff.get` devolve `next_action_status=proposal_revalidation_required`.
- `orchestrator.session_checkpoint` e `orchestrator.session_rotate` registram
  seus dados de agente, mas **não substituem o estado corrente do projeto**.
  Apresentam `continuity_proposal` com referência ao checkpoint,
  sempre exigindo revalidação; a antiga promoção implícita foi removida.
- `continuity.get` devolve `state.revision` (ou `state=null`, que significa
  revisão `0`). O texto `state.next_action`, quando existir, sempre retorna
  `next_action_status=proposal_revalidation_required`. Nem checkpoint, nem
  nota, nem handoff são prova de conclusão de efeitos externos.
- **Somente após reconciliar efeitos reais** a conversa chama
  `continuity.update` com `expected_revision` igual à revisão que leu.
  Sem esse inteiro o comando é recusado (`expected_revision_required`).
  Se outra conversa ou checkpoint alterou o estado, a gravação
  é recusada (`continuity_revision_conflict`). Ler novamente, verificar
  autoridades e decidir; **nunca repetir a escrita cegamente**.
  O SQLite existente aplica a comparação atomicamente por projeto e mantém
  revisões crescentes. Bancos antigos são migrados **sem apagar dados**.
  A leitura `continuity.get` não deve registrar ou renomear projetos; uma
  atualização recusada por revisão antiga também não pode alterar nome ou
  cadastro do projeto. A criação/renomeação e a gravação do estado acontecem
  na **mesma transação**, somente após a verificação da revisão.
- Revisão CAS impede **perda de atualização entre conversas no mesmo banco**,
  mas não descobre automaticamente alterações em GitHub, outras máquinas
  ou aplicativos. CAS não equivale a grant, lock de execução,
  identidade de conversa verificada ou atestação de commit.

### Fluxo seguro de retomada

1. Descobrir missão/handoff; conferir projeto, dispositivo, `process_status`,
   código de saída, logs e `resume_ref`. Lista truncada não prova ausência:
   recuperar pelo ID conhecido ou referência persistente.
2. Consultar `continuity.get` e guardar `state.revision`. Tratar cada
   `next_action` como **proposta**; identificar origem e idade.
3. Consultar os owners do efeito: Git remoto e local (branch/HEAD/PR/checks),
   manifesto instalado, processos, recibos e grants, conforme a tarefa.
   Se houver execução ativa/incerta, não repetir a ação.
4. Classificar a proposta como `proposed`, `needs_revalidation`, `ready`,
   `in_progress`, `completed_verified`, `blocked_uncertain` ou `superseded`.
   Esses são **rótulos de avaliação**, não outro banco de estados.
5. Só publicar novo resumo/próximos passos via `continuity.update` após
   verificação, usando a revisão observada. Conflito exige reconciliação;
   handoffs antigos e logs permanecem históricos.
6. Revalidar imediatamente **antes** das operações com efeito. Não criar
   polling periódico para inferir mudanças fora do MCP; atualizar sob demanda
   e pelos eventos já disponíveis, quando confiáveis.

### Fronteiras e código legado

A promoção automática de `handoff.create` e do antigo Agent Orchestrator
para o `project_state` foi **removida**. Mantêm-se recibos, memória e
tabelas atuais; não se apagou nenhum dado histórico. O caminho de banco
legado ainda requer **auditoria física e migração sem perda de dados** antes
de eventual substituição. Não remover dados reais só para cumprir uma meta
abstrata de limpeza.

### Evidência de implantação — 22/11/2026 (não é estado futuro presumido)

- **Código:** Runtime PR #458 integrada em `b9740bc0`. A `ordax-runtime/main`
  avançou depois para `e88882d7` (Aura/Desktop), descendente do contrato CAS.
- **Windows físico:** `DESKTOP-4UFL03B` atualizado pelo lifecycle canônico
  para `e88882d7777278399a8d88a7564d7e8b73e5cc81`, pacote SHA-256
  `048A56C53C0A42B2791326E120B92D5E585481AF728AFBE4F8AAB348A5602EF0`.
  Journal `46c7ae18d4ad4200bc948d3b2f430995` confirmou `phase=committed`;
  manifesto físico conferido; readiness local verdadeiro e Product conectado.
- **Prova física de comportamento:** Python do *Runtime instalado*, com
  SQLite temporário isolado, confirmou `expected_revision=1` recusado após
  revisão `2` com `continuity_revision_conflict`, sem sobrescrever o estado;
  `handoff.create` preservou a proposta antiga sem alterar o estado corrente.
  Isso **não** modificou memória de projeto real.
- **Worker remoto:** Platform PR #440 integrada em
  `fbb7fa50dc834c4db94ca606b59f94f859ef0adc`. Workflow canônico
  de produção `45079651689` concluiu `validate-candidate` e
  `deploy-production` com sucesso. Cloudflare registrou deployment
  `60db1267-278f-43e1-ac5b-5b73c533de18` às `2026-11-22T22:48:37Z`,
  100% da versão nova do Worker `ordax-mcp`.
- **Limite ainda não comprovado:** a rota Product MCP desta conversa
  respondeu `product_grant_not_resolved` para `repository_catalog`. Além
  disso, a definição da ferramenta `continuity_update` que esta conversa
  recebeu ainda não expunha `expected_revision`. Não foi executada escrita
  E2E autenticada pelo Worker após o deploy. É necessário **grant válido**
  e metadados de ferramentas atualizados no cliente antes do teste E2E.
  Não contornar autorização, inferir sucesso nem chamar `continuity_update`
  sem `expected_revision`.
- **Bancos antigos:** `%LOCALAPPDATA%/OrdaX/Studio/state.db` ainda existia
  fisicamente na auditoria. Sem prova de migração e ausência de consumidores,
  **não apagar**. A migração de esquema CAS é aditiva e testada, mas não
  equivale a migrar/descartar definitivamente esse arquivo.

**Ao retomar em outra conversa:** tratar este bloco como prova histórica
datada, nunca como status live. Reconsultar `main`, manifesto, Runtime,
Worker/CI, grants e schema da ferramenta antes de continuar; se a
execução/gravação estiver incerta, recuperar recibos em vez de repetir.

### Migração controlada do diretório de memória

O Runtime usa `OrdaX/Runtime/memory/state.db` como destino de instalações
novas. `OrdaX/Studio/state.db` não deve ser apagado automaticamente:
pode conter projetos/eventos e processos de versões antigas podem usá-lo.
O migrador `python -m ordax_core.memory_migration` faz preflight sem
alterações; `--apply` transporta um backup consistente do SQLite e seus
contextos somente se o destino não existir, preservando a origem.
Confrontar contagens com as do banco de origem e validar o manifesto físico.
**Commit Git não equivale a cutover concluído.**

## Roadmap operacional canônico — 23/11/2026

**SSOT:** este documento e `ordax-os/docs/DEVELOPMENT-WORKFLOW.md`. Owner do supervisor, missões e instalação Windows: `ordax-runtime`; owner do catálogo Product/OAuth/grants/Worker: `ordax-platform`. Não criar segundo Task Store, daemon, updater, banco ou política de DEV. Nenhum marco é "concluído" só por merge, bundle offline ou heartbeat.

| Gate | Resultado exigido | Estado nesta revisão |
| --- | --- | --- |
| R0 — reconciliar source | `ordax-runtime/main` igual ao checkout DEV, sem descartar trabalho alheio | Em 23/11, Runtime local/remoto e7ea6b9; Platform local/remoto 4f57306, 16 arquivos SQL de outro owner preservados sem alteração de conteúdo. Novos commits exigem reconciliação |
| R1 — descobrir missões antigas | `computer.process_list` e `process.list` aceitam filtros exatos `mission_id`, `mission_project`, `offset`, retornam páginas de até 24, priorizam ativos, ocultam argv/token | Runtime PR #465 integrada em e7ea6b9, pacote Windows instalado/committed; E2E pelo catálogo de ferramenta do cliente ainda pendente |
| R2 — contrato remoto | Publicar os mesmos parâmetros e campos `total_matching`, `offset`, `next_offset` pelo schema Product/Worker; preservar isolamento e autorização | Platform PR #445 integrada em 4f57306; workflow oficial `validate-candidate`/`deploy-production` aprovado, Worker Cloudflare deployment d7a9187d (05:18 UTC, 100%). Catálogo desta conexão GPT ainda não expõe filtros: não declarar teste autenticado E2E |
| R3 — continuidade entre chats | Owner concede `repository-catalog-read` no Device e `project-continuity` no projeto, catálogo exibe `continuity_update.expected_revision`, duas conversas leem/atualizam CAS sem sobrescrever ou repetir efeitos | Bloqueado por grants Owner/catalogação do cliente; proibição de bypass |
| R4 — resiliência e autonomia | Provar agentes recuperados com o mesmo launcher, processos/recibos preservados, sem ações reencaminhadas após resultado incerto; fechar somente mediante E2E real da issue #376 | Recuperação física da issue #376 ainda pendente. Incidente de órfão supervisionado após upgrade em 23/11 exige preflight fail-closed e prova independente |
| R5 — sync físico | `main`/checkout limpos, pacote atestado, lifecycle único, journal `phase=committed`, manifesto de commit instalado, readiness e despacho Product comprovados | Pacote e7ea6b9 instalado em 23/11: transação 419b9faf... `committed`, manifesto, readiness local e Product `true`. Processo Forge host sobreviveu, mas seu manager foi encerrado; não repetir upgrade sem preflight de supervisores |
| R6 — eliminação de versões antigas | Migrador de memória comprova contagem/checksums e ausência de writers; instalar versão canônica, depois remover apenas caches/pacotes sem referências nem processos | Dados antigos preservados até prova de cutover; sem apagar banco ou binário em uso |
| R7 — paridade | Mesmo protocolo de missões/retomada no Windows, OrdaX Desktop e USB/Linux, com provas físicas independentes | Pendente de teste no alvo Linux/USB |

**Sequência obrigatória:** concluir e testar R1 no Runtime; alinhar contrato R2 no Platform; resolver R3 sem alterar grants silenciosamente; sincronizar R5 com preflight de processos vivos; validar R4/R7; executar R6 somente com evidência de descarte seguro. Integração DEV usa testes causais; nunca reconstruir USB/kernel por mudança exclusiva de catálogo ou documentação.

**Regras de retomada:** em novo chat, consultar `ordax_targets`, `computer_process_list` (incluindo páginas/filtros quando o Product os expuser), `continuity.get` com revisão e autoridades Git/manifesto; tratar `next_action` e checkpoints como propostas. Sem processo repetido, sem acesso ao SQLite do Forge e sem interpretar `git pull` como instalação do executável.

### Incidente físico e correção de lifecycle — 23/11/2026

- A atualização canônica do Windows a `e7ea6b9` concluiu `committed`, com `local_dispatch_ready=true`, `transport_state=connected`, `product_dispatch_ready=true`. O Node Forge do Achegue-se PID `15612`, iniciado pelo processo supervisionado `c48a882c-d77d-4eb2-9368-3c6de4c92102`, **permaneceu vivo e servindo HTTP 200** em `127.0.0.1:60559`. O manager PID `7096` desapareceu e o recibo ficou `stopped`. **Não reiniciar essa missão nem lançar um Forge duplicado.** Uma porta viva não prova supervisão recuperável.
- A rota canônica `ordax-upgrade-quiesce.ps1` enumera Python dentro da instalação e pode interromper um manager persistente durante a substituição. A correção proposta adiciona preflight read-only no mesmo script, chamado antes do staging, novamente antes de encerrar a tarefa do launcher, e antes de qualquer kill: manager vivo/atestado bloqueia upgrade sem tocar no filho. Não adiciona segundo processo, banco nem serviço.
- Prova DEV isolada: simula um manager Python real com recibo temporário, verifica `ORDAX_UPGRADE_BLOCKED_SUPERVISED_PROCESS` e que o processo continua vivo; valida ausência de manager. A correção só será considerada ativa após PR, checks, instalação física atestada e teste de não regressão.
- Próximas etapas: impedir órfãos futuros, introduzir reconciliação segura de processo já órfão sem fingir ownership; E2E de filtros por nova conexão com catálogo atualizado; resolver grants Owner canônicos e issue #376. Não apagar recibos antigos nem forçar adoção do PID do host sem prova de identidade e ciclo de vida.
