# Plano canônico: Blender + OrdaX MCP (2026-11-19)

## Autoridades e proibição de duplicação

OrdaX Runtime é o dono de ações Blender e executor local. OrdaX Platform é
a autoridade de MCP remoto público, sessões OAuth, grants, fila e auditoria.
OrdaX Apps/Studio é dono da interface, e OrdaX OS apenas do host nativo.
Nunca criar segunda instância de MCP público, controle de acesso, fila,
banco ou sistema de projetos. O antigo repositório legado chamado mcp-blender
não é a base deste trabalho e já foi aposentado no cutover do Runtime.

## Inventário na main

A main já oferece companion bpy, LiveBridge, adaptação de janelas, inspeção,
snapshot, geometria, modificadores, booleanos, materiais, câmera, luzes,
animação de transformação, render/captura, exportações, checkpoints e scripts
de projeto. Nenhum desses componentes deve ser recriado. Ferramenta no código
não comprova exposição ao endpoint público nem execução física.

## Arquitetura

ChatGPT -> Product MCP OrdaX Platform -> sessão/atestado/grant/queue ->
OrdaX Runtime -> BlenderLiveBridge -> companion bpy dentro do Blender ->
resposta com resultados duráveis -> mesmo caminho de retorno.

Não abrir novo servidor HTTP ou nova autoridade dentro do Blender.
O fingerprint de bundle identifica companion desatualizado e requer
reinício controlado; comandos com resultado incerto usam command_id para
consulta antes de qualquer repetição.

## Fases e gates

A. Base existente e primeiro incremento:
- Introduzir rig_create, ferramenta tipada para criar armature + hierarquia
  de ossos (parent antes de child), validação compartilhada host/companion.
- Publicar ação no ActionRegistry, ferramenta no MCP stdio local e função
  encaminhadora no Product MCP handlers; exposição no worker público é outro
  gate, não deve ser presumida.
- Atualizar fingerprint e testes, sem alterar sessões em execução de outros agentes.
- No Blender físico: criar rig, checar ossos, salvar e reabrir, testar erro de
  nome repetido, falha parcial e recuperação.

B. Ampliar operações tipadas prioritárias:
- Consultar hierarquia e pose bones; vincular mesh/armature e weights.
- Criar animação de pose e keyframes; aplicar constraints e NLA.
- Expor Geometry Nodes, UV, materiais e exportações adicionais só quando
  houver benefício não coberto por ações existentes.
- Validar Blender 4.2 LTS e 5.x; testar sem build geral do OS.

C. Produção 3D ponta a ponta:
- Prompt -> cena -> objetos/rig -> materiais -> câmera/luz -> render
  multiview -> .blend salvo -> GLB/FBX/USD quando disponível.
- Nomes e identidades estáveis, checksum SHA-256 de artefatos, checkpoints,
  resumo estruturado do estado, progresso/cancelamento e recuperação.
- Usar fila e recibos existentes; não criar scheduler paralelo no addon.

D. Alinhamento do MCP público:
- Conferir ferramentas Blender efetivamente presentes no Product MCP.
- Projetar somente ações novas com capability e grant de dispositivo/projeto
  já existentes, sem duplicar políticas. No DEV usar autorização ampla
  existente para máquinas próprias sem confirmações repetitivas.
- Validar E2E remoto via ChatGPT -> Platform -> Runtime -> Blender -> resposta.
- Manter Remote Desktop Commander exclusivo para recuperar o OrdaX MCP.

E. Publicação e regressão:
- Unit tests dos contratos, smoke Blender real e provas dos estados
  online/offline/desatualizado. Review do diff; CI proporcional.
- Integrar na main após gates verdes, atualizar pelo instalador canônico.
- Se o Blender não estiver instalado, registrar bloqueio real e nunca
  inventar prova de funcionamento.
- Reforços extras para MVP público apenas na autoridade OrdaX Platform.

## Critérios de aceite

Uma só infraestrutura e um só ActionRegistry. Inputs inválidos rejeitados
antes de mutações, nenhuma armature parcialmente criada após falha, sem
sobrescrever objeto com nome igual. Companion declara a capacidade e mantém
identidade de projeto. Blender real cria, salva e reabre o rig. Product MCP
remoto executa a mesma ação e devolve evidência, não apenas presença online.
