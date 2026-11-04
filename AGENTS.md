# AGENTS.md — OrdaX Runtime

Este repositório é o owner de código e contratos do Runtime; não copiar autoridade de outros serviços.

## Continuidade canônica Fabric/Aura/MCP — 04/11/2026

Se a tarefa for conectar apps, Aura ou Product MCP, consultar
[`ordax-os/INTELLIGENCE-HANDOFF.md`](https://github.com/ordaxsystems/ordax-os/blob/main/INTELLIGENCE-HANDOFF.md)
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
**Trabalho diferente ainda pendente:** Native Desktop
[`ordax-os#2400`](https://github.com/ordaxsystems/ordax-os/pull/2400)
aberta e com conflito na checagem, e leitura autenticada da Store
`CURRENT` de outro owner; não montar uma segunda ponte ou inventário.
O catálogo Product global (93 IDs no source verificado) não prova
tools habilitadas no dispositivo (snapshot limitado a 64).
Distinguir metadados estáticos, instalação atual e permissão
vigente; promover sucesso só com executor e recibo reais.

## Execução DEV — Evolução Rápida

Seguir a [política transversal canônica](https://github.com/ordaxsystems/ordax-os/blob/main/docs/DEVELOPMENT-WORKFLOW.md#regra-operacional-transversal--evolucao-rapida-dev-2026-11-04): modificar diretamente o owner necessário, integrar a correção, e validar proporcionalmente ao impacto, sem auditorias repetidas ou workflows extras. Respeitar os contratos e proteção de `main`; identidade, grants, trust, autorização, segurança e integridade são gates não dispensáveis.
