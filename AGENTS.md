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
