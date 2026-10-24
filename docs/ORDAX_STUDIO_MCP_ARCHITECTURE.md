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
| MCP remoto, OAuth, grants remotos, auditoria e filas Product | `ordaxsystems/ordax-platform` | Product MCP / PostgreSQL |
| Execução e ports do host no OrdaX OS | `ordaxsystems/ordax-os` | App SDK publicado |
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

O cliente Product usa `DEFAULT_PRODUCT_MCP_URL` de `ordax_dev_agent.config` como
origem padrão (`https://mcp.ordax.com.br`). O exemplo
`config/product-mcp.example.json` deixa `ORDAX_PRODUCT_CONTROL_PLANE_URL` ausente
para herdar esse único default. Um override explícito continua disponível para
ambientes controlados; ele escolhe o destino de transporte e não concede
permissões. A ingress `workers.dev` restrita descrita em
`PRODUCT-MCP-TRUSTED-INGRESS-RECOVERY.md` é exclusiva dos caminhos de transporte
do Device Runtime.

O host Product MCP conserva uma única pool de conexões HTTPX, criada sob demanda,
com limites explícitos de conexões e encerramento no shutdown do processo. Cada
invocação de ferramenta mantém seu próprio cliente HTTP de curta duração e seu
próprio estado de cookies; apenas o transporte/pool é compartilhado. O token
Product continua sendo enviado no cabeçalho de cada requisição e não fica salvo
no cliente HTTP.

Nenhum desses transports pode manter cópia própria de Identity, grants, Memory,
ou execução de `computer.*`/Blender. Nenhum é requisito para o Studio funcionar
offline em capacidades locais. Produto remoto exige autorização no servidor **e**
política do dispositivo; canal de desenvolvimento não concede grants de produto.

## Source único e empacotamento Windows

O instalador Windows **não pode usar os assets do Runtime como fallback**.
O build exige `ORDAX_STUDIO_APP_SOURCE` como checkout Git limpo, cujo `HEAD`
coincida exatamente com `studio-source.lock.json#commit`, usando o path
`apps/studio` e o owner `ordaxsystems/ordax-apps`. A versão vem exclusivamente
de `apps/studio/app.json` naquele commit. O build **não escreve** em `ordax_studio/` do checkout Git: após
instalar o host Python, materializa o Studio exclusivamente em
`build/windows-product/stage/runtime/Lib/site-packages/ordax_studio/`.
Substitui integralmente os assets empacotados por arquivos do commit pinado,
verifica inventário e SHA-256 de cada asset e injeta somente o host bridge
Windows no HTML portátil. Um gate da CI executa
`git status --porcelain -- ordax_studio` após o build e recusa qualquer
alteração na fonte do Runtime. Os snapshots do checkout permanecem apenas
para os consumidores de compatibilidade e testes já identificados, não são
input da distribuição Windows.

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
`ordax-platform` é transporte/autoridade remota. A separação é
contrato de segurança, não multiplicação de produtos.

## Identidade do Product Runtime no OrdaX OS (2026-10-18)

O Product Device Runtime existente agora aceita a entrada opcional
`device_credential_provider` em
`ProductDeviceControlPlane(config, device_credential_provider=...)`.
Somente o **Native Host autorizado** deve fornecer o objeto privado
`{"device_id", "device_token"}` lido pelo `ProductDeviceIdentityStore`
já existente no OrdaX OS. O Runtime verifica que o `device_id` corresponde
exatamente ao dispositivo de `AgentConfig`; a ausência, revogação ou
inconsistência da identidade bloqueia o Runtime sem fallback para o token
persistido de Windows. Nenhum novo token de dispositivo precisa ser salvo
em disco pelo OS.

A inicialização Windows, sem provider, continua lendo o token atual pelo
`resolve_token_path`, preservando instalações existentes. Esta mudança
não inicia serviço, não instancia um segundo Control Plane e não altera
OAuth, grants, lease, audit, Action Gateway ou execução no OS.
O pacote assinado do Runtime, health gate e sessão atestada continuam
pré-requisitos independentes para liberar transporte de ações.

## Nomenclatura interoperável — 2026-10-19

O repositório oficial do Product MCP e da autoridade PostgreSQL é `ordaxsystems/ordax-platform`, **não** um novo projeto `ordax-mcp`, e o OrdaX OS está em `ordaxsystems/ordax-os`. Nomes anteriores no histórico GitHub são compatibilidade de redirects, não owners ativos.

| Termo | Autoridade e fronteira |
| --- | --- |
| Conta OrdaX | sujeito do OAuth/Platform; não representa Profile Pack ou Space |
| Perfil do usuário / Profile Pack | preferências do usuário versus pacote técnico versionado do OS; são conceitos distintos |
| Space | identidade persistente `ordax_spaces.space_id`; o Studio/Runtime pode exibi-la, mas não criar uma segunda |
| Projeto de produto | `ordax_projects.project_id` vinculado ao Space, independente do checkout local |
| Projeto local | diretório/slug sob `ordax_dev_agent.projects`, com estado/segurança locais; só um vínculo explícito pode ligá-lo ao projeto de produto |
| Workspace | contexto de diretório e interface do Studio/Runtime, não sinônimo de Space da Platform |
| Target / Dispositivo | máquina autenticada/atestada; descoberta e observação não devem exigir Projeto/Space artificial |
| Grant | decisão remota por capability/cliente/Space/Projeto/dispositivo quando aplicável, sem substituir política local |
| MCP tool | porta pública da Platform; não é executor do Runtime nem aplicativo Studio |

`ordax_profile` é um nome público de compatibilidade da integração ChatGPT e devolve o subject da Conta, **não** o Perfil do OS. `computer_processes` observa processos do dispositivo sob grant próprio; `process_list` e `terminal_exec` são hoje ações **nativas por Projeto** e exigem scope canônico resolvido, associação de repositório/dispositivo e grants distintos. A exigência de projeto para essas ações nunca se propaga para autenticação, descoberta, presença ou observação do dispositivo.

O OS pode consumir o mesmo Runtime por contrato sem virar dependência de boot para Platform, Studio ou Flow. Essas convenções não confirmam implantação de migrations, nem aprovam a PR `ordax-platform#222` ou autorizam autoenrollment de projeto/grant.
