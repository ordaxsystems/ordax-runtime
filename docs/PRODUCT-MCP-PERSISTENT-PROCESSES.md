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
