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
