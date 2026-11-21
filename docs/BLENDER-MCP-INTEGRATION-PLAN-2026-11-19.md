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

Aprimoramento após prova física no Runtime 5cf78ad0 em 20/11/2026:

- O LiveBridge.start iniciou o Blender com a GPU nativa; a janela de erro
  OpenGL não apareceu nesse contexto e o companion nunca publicou presença.
  Após 74,12 segundos, retornou falha, sem declarar uma sessão pronta.
- Com o mesmo Runtime e o mesmo binário Blender 5.3.0, definir o ICD Lavapipe
  verificado diretamente no processo fez o companion publicar presença fresca
  em 30,81 segundos, protocolo 9. Pelo inbox do BlenderLiveBridge, as ações
  create_primitive e rig_create foram aprovadas, duplicata rejeitada e save
  aprovado com recibo durável. O arquivo .blend de 98.268 bytes foi reaberto
  por outro processo Blender em 3,7 segundos, com SHA-256 idêntico:
  1082bad624fe1177a44ef3259bc647b1e2e2da4a03001a4d4b5e909b0ba288e1.
- Correção: a presença dos dois arquivos **verificados** no estado do
  Runtime (AgentConfig.state_dir/blender-gpu/software-vulkan) é a seleção
  explícita do operador pelo driver de software. O LiveBridge usa
  --gpu-backend vulkan, VK_DRIVER_FILES e VK_ICD_FILENAMES **desde a primeira
  inicialização**, exclusivamente no processo Blender. Não depende mais da
  existência de uma janela de erro da GPU para escolher o driver já presente.
- Na ausência de driver verificado, o caminho nativo permanece inalterado.
  O fallback único baseado no aviso oficial ainda funciona se o driver
  passou a existir após a primeira inicialização. Nenhum driver é instalado,
  nenhum registro/PATH global é modificado e nenhum MCP ou supervisor é criado.
  Os testes exercitam seleção imediata e fallback único.

A correção não altera permissões de Project nem emite grants remotos.

**Limite da prova:** estas operações usaram o transporte local legítimo,
mas **não** demonstram ChatGPT → Product MCP → Platform grant → Device Agent.
Ainda não existe Project na Platform nem Project↔Device vinculado pelo OS
Project Catalog. O Runtime não deve inventar identidade project-N,
repositório verificado ou grants para simular esses gates. O servidor anuncia
dez ferramentas Blender, mas a conexão ChatGPT desta sessão não carregou
as dez definições de chamada.

## Isolamento do perfil Blender em Windows — 20/11/2026

Após instalação física de a2b2a3bc (transação committed), o mesmo
BlenderLiveBridge.start selecionou Vulkan por software, mas a GUI não
publicou companion em 90 s. O problema ocorreu tanto ao reabrir um .blend
quanto em cena padrão, produzindo processos sem heartbeat.

No controle físico, o MESMO executável Blender 5.3, Lavapipe validado,
companion e projeto iniciaram em 9,16 s ao receber somente no processo
BLENDER_USER_CONFIG e BLENDER_USER_SCRIPTS apontando a pastas isoladas.
Assim, a diferença verificada é o ambiente de usuário da inicialização,
não uma falta de suporte do driver nem um segundo transportador MCP.

O LiveBridge existente passa agora essas duas variáveis ao subprocesso
para uma única raiz de configuração privada por projeto, dentro do
state_dir canônico. O launcher não modifica preferências globais, GPU
do Windows, Runtime, grants ou identidade. Em timeout sem presença
atestada, encerra somente o PID que o start criou para evitar órfãos.

Este controle direto e os testes de regressão não substituem prova física
do novo código instalado. O gate público Product MCP/Platform/Project Catalog
segue separado, sem fabricar cadastro de projeto ou autorização.

## Windows Blender cold-start: OIDN SYCL enumeration — 20/11/2026

Prova física no DESKTOP-4UFL03B, com Runtime instalado pela cadeia canônica:

- Blender 5.3.0 sem personalização demorou ~49 segundos para concluir
  `blender.exe --version`, com somente 0,25 segundo de CPU e carregamento
  lento de DLLs incluindo OpenImageDenoise, python313 e ur_loader.
- A opção documentada pelo projeto Open Image Denoise `OIDN_DEVICE_SYCL=0`,
  aplicada **somente ao subprocesso Blender**, reduziu `--version` a
  0,3 segundo com código de saída 0. Referência:
  https://github.com/RenderKit/oidn/blob/master/README.md#environment-variables
- O arquivo original ordax-pr404-physical.blend foi reaberto no Blender
  5.3.0 headless, com cubo e armature Root → Spine → Head confirmados pela
  API real do Blender (código 0); essa execução ainda sem a opção levou 114,2 s.
- Com a opção temporária, o BlenderLiveBridge abriu a cena gráfica com o
  ICD Vulkan por software já verificado e publicou presença legítima em 42,19 s;
  o companion v9 confirmou identidade e fingerprint. Quando o heartbeat
  voltou a ficar fresco após uma pausa do viewport, `ping` e `inspect`
  retornaram recibos de sucesso em 0,36 s e 0,53 s. Nenhuma edição adicional.

**Correção canônica:** no `launch()` existente de `BlenderLiveBridge.start`,
quando um ICD Lavapipe previamente verificado é selecionado, passar
`OIDN_DEVICE_SYCL=0` exclusivamente no ambiente de `subprocess.Popen`.
Lavapipe é renderização CPU, portanto não expõe um GPU SYCL correspondente
para denoise acelerado. Sem ICD validado, o caminho nativo mantém toda a
seleção SYCL normal do usuário. Não mudar o PATH, registro, drivers,
preferências do Blender, serviço MCP ou ambiente global. O teste de
regressão cobre seleção inicial, fallback e preservação de ambiente nativo.

**Limite da prova:** o heartbeat pode ficar temporariamente desatualizado
em viewport por software, apesar do PID permanecer vivo. O bridge bloqueou
corretamente uma tentativa de ping sem presença fresca, sem enfileirar
comando. Quando a presença voltou, ambos os comandos de leitura passaram.
Esse perfil não constitui, por si só, a homologação do caminho público
ChatGPT → Platform grants → Device Agent → Blender sem Project registrado.

## Inspeção tipada de armature — 20/11/2026

Incremento sem ferramenta ou transportador adicional: a ação existente
`blender.live_object_inspect` retorna, somente para objetos ARMATURE, uma
seção `armature` com `bone_count`, `bones_truncated` e até 128 ossos
em ordem determinística. Cada osso contém nome, parent, head/tail locais,
comprimento, flag de deformação e, se disponível, dados de pose (posição,
rotação conforme rotation_mode, escala, posição mundial da cabeça/cauda,
constraints limitadas a 16). A inspeção não altera cena nem cria execução
de Python livre na superfície MCP.

O teste real já existente `tests/blender_real_rig_e2e.py` foi ampliado:
verifica o resultado `object_inspect` via recibo durável, pai/filho e
pose, antes de salvar e após reabrir o arquivo .blend. Não cria novo smoke
nem instala outro Blender. Compilação sintática dos arquivos alterados:
aprovada. Testes locais `test_blender_rigging_contracts`: 12/12;
`test_blender_companion_bundle`: 57/57. `git diff --check`: aprovado.

Limitação física reproduzida: tentativa headless Blender 5.3 com GPU
nativa e OIDN_DEVICE_SYCL=0 ficou sem iniciar o script e terminou por
timeout após 180 s, sem gerar evidência. **Não considerar smoke físico
aprovado**. A variante validada com Vulkan/Lavapipe deve ocorrer pelo
lançamento canônico do Runtime ou pela CI Linux, preservando o diretório
protegido de estado do agente. Não há prova nova do caminho remoto
ChatGPT → Platform → Device nem de aplicação da alteração no Runtime
instalado até esse gate ser executado.

## Reexecução física no Windows via OrdaX MCP — 20/11/2026

No dispositivo autorizado DESKTOP-4UFL03B, o cliente ChatGPT executou
computer.terminal_exec pela Platform e recebeu os resultados do Runtime
instalado em 47bc44dc83fb361f2309d80b1c065aa075d2ca66.
O teste físico usa o **teste canônico já existente**
tests/blender_real_rig_e2e.py; não cria bridge, agente, projeto,
MCP, testes ou drivers novos.

- Blender original 5.3.0, em --background, --gpu-backend vulkan,
  perfil isolado (BLENDER_USER_CONFIG/BLENDER_USER_SCRIPTS) e somente
  no processo OIDN_DEVICE_SYCL=0, VK_DRIVER_FILES e
  VK_ICD_FILENAMES apontando para o ICD já instalado.
- Lavapipe vulkan_lvp.dll: SHA-256
  1c178d0e2a5fe0a3e7b9b85c49e78c0a8facdd0625903dfcda3d483b629f3b21,
  correspondente ao hash pinado em blender_live_bridge.py. Manifesto
  ICD versão 1.0.1, library_arch=64, Vulkan 1.4.363.
- A primeira execução física passou pelo Blender, mas terminou com
  código 2 porque o comando de teste **omitiu**, por erro de invocação,
  os cinco argumentos --ordax-* que a CI já exige. Não houve mudança
  necessária no código do teste nem sucesso inferido dessa tentativa.
- A execução corrigida reproduziu os argumentos do workflow
  .github/workflows/blender-real-e2e.yml, preservando o mesmo workspace:
  fase create código 0 em **13,12 s** e fase verify (outro
  processo Blender) código 0 em **3,95 s**.
- rig_create criou três ossos Root → Spine → Head; object_inspect
  confirmou a hierarquia, contagem e pose. Nomes duplicados e osso de
  comprimento zero foram rejeitados sem mutação parcial. O .blend
  foi salvo e reaberto com mesmo conteúdo, hierarquia e recibos duráveis.
- Arquivo real ordax-rig-e2e.blend: SHA-256
  6216881ae86099a1e34f41ef66da1752ed40c11c5d7db3efb18fb6976c8afc24.
  Recibos evidence-create.json, evidence-verify.json, logs e arquivo
  .blend preservados no estado existente
  DevAgent/blender-e2e/physical-20261120-bc49b114.

**Gate separado ainda pendente:** o sucesso do Blender via comando
autorizado computer.terminal_exec **não demonstra** que ferramentas
tipadas Blender estão invocáveis pelo Product MCP nesta conversa nem que
existe um Project do OS/Platform vinculado ao dispositivo. O catálogo
local de projetos consultado estava vazio. Não inventar Project/grant,
não inferir homologação do roteamento remoto de ações Blender e não
reexecutar as ações mutáveis sem consulta aos recibos existentes.
