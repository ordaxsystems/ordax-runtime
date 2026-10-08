# ORDAX Studio, Runtime e MCP — ownership canônico

## Decisão de arquitetura

**Uma fonte portátil do Studio, um executor local de capacidades, um MCP
remoto do produto.** Os transports MCP locais são portas do mesmo
`ActionRegistry`, não um segundo Runtime, um segundo sistema de grants ou
outro aplicativo Studio.

| Responsabilidade | Owner canônico | Contrato |
| --- | --- | --- |
| Interface portátil, projetos, assistente e assets do Studio | `ordaxsystems/ordax-apps/apps/studio` | Host Bridge / App SDK |
| Device Agent, Computer Control, Blender/Unity, Git, action executor | `ordaxsystems/ordax-runtime` | Ações tipadas + política local |
| MCP remoto, OAuth, grants remotos, auditoria e filas Product | `ordaxsystems/ordax-control-plane` (futuro `ordax-platform`) | Product MCP / PostgreSQL |
| Execução e ports do host no OrdaX OS | `washingtonmsdj/prototipo-ordax-os` | App SDK publicado |
| Integração com ChatGPT/Grok | Conector externo do Control Plane | Adapter de provider sem authority |

## Pontos MCP no Runtime (não confundir com executores)

- `ordax_dev_agent/mcp_server.py`: transporte **stdio local** canônico de
  desenvolvimento para o `ActionRegistry` do mesmo Runtime.
- `ordax_device_agent/mcp_server.py`: alias de compatibilidade que reexporta o
  primeiro; não possui outro registro de execução.
- `ordax_studio/mcp_server.py`: adaptador de ferramentas específicas ao
  workspace Studio sobre o mesmo objeto `mcp`/registro local. Não é o Product
  MCP remoto nem uma autoridade paralela.
- `ordax_dev_agent/product_mcp_server.py`: adaptador MCP cliente/local para
  chamadas Product remotas, que reutiliza os handlers tipados. Não concede
  permissões nem substitui o endpoint autenticado `/mcp` do Control Plane.

Nenhum desses transports pode manter cópia própria de Identity, grants, Memory,
ou execução de `computer.*`/Blender. Nenhum é requisito para o Studio funcionar
offline em capacidades locais. Produto remoto exige autorização no servidor **e**
política do dispositivo; canal de desenvolvimento não concede grants de produto.

## Source único e empacotamento Windows

O instalador Windows **não pode usar os assets do Runtime como fallback**.
O build exige `ORDAX_STUDIO_APP_SOURCE` como checkout Git limpo, cujo `HEAD`
coincida exatamente com `studio-source.lock.json#commit`, usando o path
`apps/studio` e o owner `ordaxsystems/ordax-apps`. A versão vem exclusivamente
de `apps/studio/app.json` naquele commit. O build substitui integralmente
`ordax_studio/assets` pelos assets fixados e injeta somente o host bridge
Windows no HTML portátil.

A cópia de oito assets atualmente versionada em `ordax_studio/assets` é
**resíduo de desenvolvimento/compatibilidade, não fonte de distribuição**.
Quatro arquivos históricos já divergiram do Studio portátil atual:
`studio.js`, `computer_access.js`, `computer_access.css` e
`product_account.js`. A divergência desses arquivos jamais pode ser
silenciosamente empacotada como produto Windows.

### Condições para remover fisicamente os snapshots remanescentes

1. Migrar testes que ainda leem `ordax_studio/assets` no checkout da fonte
   Runtime para fixtures ou package com commit fixado de `ordax-apps`.
2. Fazer as superfícies locais Python/Workbench consumirem sempre o pacote
   materializado, também fora do instalador.
3. Provar launch local offline, build Windows limpo, upgrade com dados
   preservados e equivalência de contratos com OrdaX OS.
4. Remover os assets e HTML portáteis históricos do Git do Runtime em PR
   separada, sem remover `host_bridge.js` e os adapters host.
5. Provar que não existe fallback de import, build ou launch para os
   snapshots retirados.

Até esses gates passarem, os arquivos residuais permanecem identificados e
não são removidos de modo a quebrar usuários Windows existentes. Isso é uma
transição fechada, não permissão para desenvolver features duplicadas neles.

## Stop conditions

- Build sem checkout do Studio ou com checkout modificado/divergente: falha.
- App Studio com servidor MCP, autoridade de permissões ou Runtime próprio:
  falha.
- MCP remoto executando ações locais diretamente ou aceitando grant local do
  provider como autorização: falha.
- Publicar binário Windows a partir de `pyproject.toml`, tag ou JS legado:
  falha.
- Deletar adaptadores locais/legados antes de provar que não têm consumidores:
  falha.

A fonte de produto é `ordax-apps`; `ordax-runtime` é host/executor;
`ordax-control-plane` é transporte/autoridade remota. A separação é
contrato de segurança, não multiplicação de produtos.
