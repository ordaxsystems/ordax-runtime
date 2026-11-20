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


## Prova física Windows — 19/11/2026 (DEV)

No dispositivo autorizado DESKTOP-4UFL03B, a Intel HD Graphics 2500 não
suporta o OpenGL 4.3 exigido pelo Blender 5.3. O Blender 5.3 **original** foi
executado com Mesa3D 26.3.0 Lavapipe (Vulkan por software) **somente na
variável de ambiente do processo**, sem trocar o driver do Windows.

Proveniência do software de teste:
- Pacote Mesa3D Windows MSVC 26.3.0 (pal1000/mesa-dist-win):
  SHA-256 c505fcc77e8f63a09d7ddac086591d2f542c0698d37fc9e3094685a34d0d9991.
- vulkan_lvp.dll x64: SHA-256
  1c178d0e2a5fe0a3e7b9b85c49e78c0a8facdd0625903dfcda3d483b629f3b21.
- O ICD lvp_icd.x86_64.json aponta somente para o driver vizinho,
  declara library_arch=64 e Vulkan 1.4.

Evidência GUI real (não apenas CI/headless):
1. Blender 5.3.0 iniciou em janela interativa com viewport via Vulkan;
2. o companion **instalado** publicou presença fresca, fingerprint vigente e
   protocolo 9 (51 operações internas);
3. BlenderLiveBridge.request enviou operações pelo inbox canônico:
   create_primitive aprovado, rig_create aprovado com Root→Spine→Head,
   duplicata rejeitada e save aprovado;
4. o resultado do rig foi recuperado por BlenderLiveBridge.result como recibo durável;
5. outro processo Blender 5.3 reabriu o arquivo gerado pelo bridge com
   código de saída 0 em 3,12 segundos, preservando cubo, rig e parentesco.
   O .blend tem 98.258 bytes e SHA-256
   ff0b98ea45cec5ca741b53898437218ce12f57a4e1c0784567004f1fe72309dd.

Novo comportamento canônico: somente **quando o processo Blender recém-aberto
mostra a janela oficial de GPU incompatível**, o LiveBridge pode tentar
**uma vez** um ICD Mesa3D local previamente provisionado em
AgentConfig.state_dir/blender-gpu/software-vulkan/ com os arquivos
lvp_icd.x86_64.json e vulkan_lvp.dll. O Runtime verifica o manifesto
e SHA-256 da DLL antes do fallback. O segundo processo recebe
--gpu-backend vulkan, VK_DRIVER_FILES e VK_ICD_FILENAMES apenas nele próprio.
Não instala drivers, não modifica PATH/registro Windows, não expõe scripts ou
dispositivos remotos adicionais e não cria outro MCP.

**Limite da prova:** estas operações usaram o transporte local legítimo,
mas **não** demonstram ChatGPT → Product MCP → Platform grant → Device Agent.
Ainda não existe Project na Platform nem Project↔Device vinculado pelo OS
Project Catalog. O Runtime não deve inventar identidade project-N,
repositório verificado ou grants para simular esses gates. O servidor anuncia
dez ferramentas Blender, mas a conexão ChatGPT desta sessão não carregou
as dez definições de chamada.
