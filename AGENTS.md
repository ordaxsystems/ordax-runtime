# AGENTS.md — OrdaX Runtime

Este repositório é o owner de código e contratos do Runtime; não copiar autoridade de outros serviços.

## Continuidade canônica Fabric/Aura/MCP — reconciliada em 17/11/2026

Se a tarefa for conectar apps, Aura ou Product MCP, consultar
[`docs/CURRENT-STATE.md` do OS](https://github.com/ordaxsystems/ordax-os/blob/main/docs/CURRENT-STATE.md)
e o guia de
[`Capability Fabric`](https://github.com/ordaxsystems/ordax-os/blob/main/docs/CAPABILITY-FABRIC.md)
**antes de retomar um plano antigo**. O `README.md` deste repo
é o handoff local de Product Actions/snapshot e do registro
`app_intelligence_registry.json` empacotado (não `CURRENT`).
O Runtime é dono de ferramentas suportadas pelo Device, adapter,
política de execução e recibos; **não** é dono da Store, OAuth/
grants ou inferência da Aura.

**Missão encerrada:** [Platform #291](https://github.com/ordaxsystems/ordax-platform/issues/291)
comprovou MCP DEV funcional no Windows físico e revogou cinco
devices antigos. Não repetir matrícula, limpeza ou grants.
**Handoff reconciliado em 17/11/2026:** a ponte Native Product
[`ordax-os#2400`](https://github.com/ordaxsystems/ordax-os/pull/2400)
foi **integrada em 04/11/2026** (merge `5eb9e2f8d6c95c457c307e872e6f953bd48e2a36`).
Não reabrir essa PR nem duplicar ponte/dispatcher. A leitura autenticada da
Store `CURRENT`, prontidão do Runtime no Device, credencial Product e grants
mantêm gates próprios: validar estado atual nos contratos e recibos dos owners
antes de alterar o código. Um processo local ou `capabilities_digest` não
substitui OAuth/consentimento/grant da Platform. A regra transversal de
composição/adoção permanece em [`ordax-os/docs/DEVELOPMENT-WORKFLOW.md`](https://github.com/ordaxsystems/ordax-os/blob/main/docs/DEVELOPMENT-WORKFLOW.md#integração-multi-repositório--composição-do-produto-2026-11-13)
e ADR-031; o Runtime não deve manter pins transitivos nem reconstruir source
alheio para sincronizar revisões.
O catálogo Product global (93 IDs no source verificado) não prova
tools habilitadas no dispositivo (snapshot limitado a 64).
Distinguir metadados estáticos, instalação atual e permissão
vigente; promover sucesso só com executor e recibo reais.

## Execução DEV — Evolução Rápida

Seguir a [política transversal canônica](https://github.com/ordaxsystems/ordax-os/blob/main/docs/DEVELOPMENT-WORKFLOW.md#regra-operacional-transversal--evolucao-rapida-dev-2026-11-04): modificar diretamente o owner necessário, integrar a correção, e validar proporcionalmente ao impacto, sem auditorias repetidas ou workflows extras. Respeitar os contratos e proteção de `main`; identidade, grants, trust, autorização, segurança e integridade são gates não dispensáveis.

## Retomada multichat e próximos passos — fonte canônica

Antes de editar um projeto com base em memória anterior, consultar
`docs/PRODUCT-MCP-PERSISTENT-PROCESSES.md`, `continuity.get` e os recibos
`process.status/list/logs`; conferir efeitos nas autoridades externas.
`next_action` em handoff, checkpoint ou estado é proposta não atestada.
`continuity.update` exige `expected_revision` da leitura corrente; conflito
impede substituição silenciosa e requer nova verificação. Handoffs e
checkpoints do Agent Orchestrator não promovem automaticamente sugestões
para o estado do projeto. Não criar outro Task Store, scheduler, polling
agressivo nem integração direta com SQLite do Forge.
Para Forge, a SSOT de Tasks/claims/observações é o próprio Forge App/SQLite
sob coordenação do Forge MCP; OrdaX MCP é ferramenta auxiliar de DEV.

## Banco da continuidade — migração sem perda

A SSOT do Runtime para memória e orquestração permanece o mesmo SQLite
resolvido por `ordax_core.memory.resolve_memory_db`. Instalações novas usam
`OrdaX/Runtime/memory/state.db`; antes de trocar uma instalação já existente,
consultar `docs/ORDAX_STUDIO_FOUNDATION.md` e usar o migrador explícito
`python -m ordax_core.memory_migration` (preflight) e `--apply` somente
com consumidores quiescentes e destino vazio. **Não remover nem renomear**
`OrdaX/Studio/state.db` antes da prova de cutover da instalação física.
O caminho antigo não pode ser atualizado em paralelo ao novo. Nunca
considerar o registro no Git como migração de dados físicos concluída.
