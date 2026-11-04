# OrdaX MCP — distribuicao Windows independente

## Produto e owner

**OrdaX MCP** e o nome apresentado ao usuario para conectar um computador
Windows a clientes autorizados (ChatGPT e outros). **ordax-runtime** permanece
o repositorio e executor unico. A Platform e a autoridade de Conta, OAuth,
dispositivos, MCP, grants e revogacao; o Studio pertence ao repositorio
`ordax-apps` e sera **um consumidor opcional** do Runtime, nunca sua fonte
de instalacao nem sua interface obrigatoria.

O candidato independente e
`OrdaX-MCP-Setup-<versao-do-ordax-runtime>-x64.exe`. Ele usa o **mesmo**
`packaging/windows/ordax_launcher.c`, com
`DORDAX_RUNTIME_LAUNCHER=1`, e inclui o Python privado, os executores
de capacidades e o CLI de autorizacao Owner. Nao empacota Electron,
`presentation` ou source `ordax-apps`. O processo visivel ao sistema
continua `ORDAX Runtime.exe`; o produto apresentado e `OrdaX MCP`.

O comando Python `ordax-mcp` e o exemplo `config/product-mcp.example.json`
apontam somente para `ordax_dev_agent.product_mcp_server:main`, com
identidade MCP `ordax-mcp`. As aliases de servidor remoto com nomes
`ordax-product-mcp` e `ordax-studio-remote-mcp` foram retiradas. Isso
nao muda a autoridade de grants na Platform, nem reinicia o Runtime
instalado. Os comandos opcionais do Studio devem ser migrados com seus
consumidores antes da retirada do restante do pacote Studio.

## Perfil de host do OrdaX Desktop (fonte, sem ativacao)

O Desktop Windows do OrdaX OS e um host do **mesmo produto OrdaX**,
nao o agente de controle do Windows. A entrada assinada/pacote canonico do
Runtime aceita um perfil **explicito** por ambiente:

- `ORDAX_RUNTIME_HOST_PROFILE=ordax-desktop`;
- `ORDAX_DESKTOP_USER_DATA` = userData privado do Electron OrdaX;
- `ORDAX_AGENT_STATE_DIR` = `<userData>/native-state/product-runtime`.

A inscricao **Owner OAuth** deve ser concluida nessa pasta, com ID
de dispositivo e token distintos dos do agente auxiliar do Windows.
O comando `ordax-owner-device-connect --connect` reconhece o perfil de host
exato, rejeita qualquer estado fora dessa raiz, usa um `home` privado
para impedir importacao automatica de projetos do Windows e registra o
nome `OrdaX Desktop (<nome-do-computador>)`. O navegador exige a aprovacao
Owner explicita, sem credenciais no argv. O cadastro usa o seletor de
entrada ja canonico do mesmo Worker PostgreSQL: se e somente se a conexao
TCP/DNS falhar antes de enviar a requisicao para `mcp.ordax.com.br`,
pode tentar `ordax-mcp.ordaxsystems.workers.dev`. Nenhum HTTP 401/403/5xx,
ReadTimeout ou confirmacao ambigua autoriza repeticao do POST de enrollment.

O seletor `TrustedProductIngress` aceita o fallback **apenas** quando
`ConnectTimeout` indica falha anterior à entrega ou `ConnectError`
carrega uma causa verificável de DNS/TCP (incluindo códigos de rede Winsock
do Windows). Um `ConnectError` genérico, erro de permissão local ou
falha TLS/validacao de certificado, mesmo encapsulada em várias exceções,
**não autoriza mudar o hostname nem repetir o POST**. O mesmo vale quando
a entrada de contingência estava temporariamente selecionada e sua
verificação TLS falha: propagar a falha, não disfarçá-la sondando a
entrada principal. O timeout TCP real observado no Windows permanece
elegível para o mesmo Worker alternativo, sem criar nova autoridade,
identidade, token ou override de certificado.

Falta
credencial, existe link de pasta, ou IDs/transport divergem? Falha
fechada sem publicar uma falsa disponibilidade MCP.

Essa composicao usa o mesmo `ordax_dev_agent.main.run`, nao um segundo
dispatcher, mas o seu `ActionRegistry` declara somente
`agent.status` na primeira fatia: nao expor Win32, terminal, browser,
workspace ou controle de arquivos Windows por causa do nome "OrdaX OS".
Se a Surface apresentar presença, ela nao pode transformar isso em
`capability_digest`, grant ou acao autorizada. Novas capacidades devem
vir dos adaptadores Native do OS, com comprovacao da fronteira de Space.

A invocacao canônica deve ser feita **somente** pelo Supervisor de
`ordax-os` a partir de um pacote verificado. Esta fonte não instala
o Runtime no Electron, não cria credenciais automaticamente e não faz
prova de execução MCP no OrdaX Desktop. Enquanto a custódia/trust do
runtime component não estiver pronta, a ativação permanece bloqueada.
O launcher Windows mantém `Local\\ORDAXRuntime` como mutex de instância
unica: não assumir que uma identidade Windows auxiliar e uma identidade
Desktop podem executar simultaneamente sem contrato de coexistência
ou handoff atestado. Na execução final, o OS é o dono do lifecycle.

## Estado real — candidato de validacao, nao instalador publico

`scripts/windows/build-ordax-mcp.ps1` consome exclusivamente o Runtime
desta revisao e `control-plane-source.lock.json` existente. O endpoint
e validado pelo resolver canonico. O workflow
`.github/workflows/windows-mcp-candidate.yml` prova build x64,
pacote privado, instalacao, CLI, Runtime e desinstalacao, em runner Windows
descartavel. Publica apenas artefato de curta retencao da CI, sem release,
assinatura falsa ou deploy de servico. **Este candidato nao esta autorizado
para distribuicao publica** ate existir assinador Authenticode/Publisher
aprovado e validacao E2E completa de onboarding/consentimento/dispositivo.

O candidato independente passa pela mesma validação no PR e, **somente
quando arquivos relevantes do MCP mudam**, é reconstruído a partir da
`main` consolidada. Ambos os eventos compartilham uma lista única de
caminhos e são serializados por ref, sem cancelar um build já em execução.
A lista única de caminhos é derivada das raízes reais de pacotes Python
instalados por `pip install` no candidato, conforme
`pyproject.toml:[tool.setuptools.packages.find].include`. O trigger do
Windows observa cada raiz inteira (`/**`), pois editar
`remote_protocol.py`, `product_device_control_plane.py` ou
`terminal_outbox.py` altera os bytes empacotados mesmo que o nome não
conste da antiga lista de entrypoints. A CI compara todas as raízes
exatamente com esse SSOT e protege também o script de proveniência
do endpoint. Alterações apenas em documentação e testes de protocolos
não alteram o pacote e não justificam uma compilação de instalador.

Cada candidato da `main` contém `runtime_source_commit` igual ao SHA
verificado pelo runner e expira após três dias; não se promove um
candidato do SHA da PR (anterior ao squash merge) como se fosse de
`main`. O acionamento manual permanece possível. Nenhum desses
artefatos é uma release pública ou uma assinatura Authenticode.


O candidato independente possui agora um atalho **Conectar dispositivo**
no menu Iniciar. Ele abre uma interface Windows minima, executada pelo
`pythonw.exe` privado do instalador, que pede confirmacao antes de chamar o
**mesmo** fluxo Owner OAuth PKCE e o `device_setup` canonico do CLI.
Nao usa Node.js, `npx`, `irm | iex`, shell interativo ou um segundo agente.
O retorno do navegador identifica OrdaX MCP, sem expor credenciais no aviso.
Cancelamento e erro nao concedem acesso nem iniciam Runtime alternativo.

A interface completa de gerenciamento de conexao e politicas **ainda e
pendencia**: o atalho apenas solicita cadastro da identidade e informa
explicitamente que isso nao comprova prontidao nem emite grants. O Runtime
permanece sob o ciclo de vida existente. Os testes Windows validam a existencia
e o alvo exato do atalho, importacao do codigo de onboarding, casos de recusa e
remocao do atalho na desinstalacao. Isso nao prova OAuth real em um dispositivo
fisico, nem autoriza release publico antes de assinatura e gates E2E. O aplicativo do ChatGPT e um conector que acessa
`https://mcp.ordax.com.br/mcp`; **nao** e um segundo Runtime e nao
precisa de `npx` ou Node.js no Windows. A conexao remota requer
autenticacao Owner, vinculacao do dispositivo e grants explicitos.

## Primeiro uso no Windows — fluxo explícito

No instalador com assistente normal, a tela final oferece a opção
**Conectar este computador ao OrdaX MCP**, executando o mesmo
`owner_device_connect_gui` privado do atalho do menu Iniciar. O usuário
pode desmarcar a opção. A janela informa antes do navegador que o
registro **não** concede acesso automático a arquivos, terminal ou
controle do computador. Só a aprovação Owner OAuth dá início ao cadastro;
capabilities e grants são decisões separadas da Platform.

Instalações `/SILENT` e `/VERYSILENT`, inclusive CI, nunca abrem a
janela de autorização. O Runtime único continua supervisionado pelo
mesmo launcher. Isso não substitui a aplicação completa de gerenciamento
de conexões e revogação, que segue pendente.

### Inicialização, supervisão e reconexão — Windows sem OrdaX OS

**Contrato vigente desde as PRs [#313](https://github.com/ordaxsystems/ordax-runtime/pull/313) e [#314](https://github.com/ordaxsystems/ordax-runtime/pull/314).**
A instalação canônica permanece em `%LOCALAPPDATA%\\Programs\\OrdaX MCP`,
com um único `ORDAX Runtime.exe` e o mutex `Local\\ORDAXRuntime`.
`packaging/windows/ordax-mcp.iss` registra **uma tarefa por usuário** no
Agendador do Windows, chamada `OrdaX MCP`, com início no logon, manutenção
periódica de um minuto, execução interativa limitada ao usuário atual e
`MultipleInstances=IgnoreNew`. A tarefa aciona
`scripts/windows/ordax-mcp-lifecycle.ps1 -Operation Launch` e acompanha
o **mesmo launcher**; não cria agente, identidade, grants nem credenciais
novas. A entrada antiga `HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run`
é removida **depois** do registro validado da tarefa, para evitar dois
owners de inicialização. A desinstalação retira a tarefa.

O launcher continua responsável pelo mutex e pelo restart progressivo do
Python. O watchdog Windows já existente em
`scripts/windows/ordax-agent-watchdog.ps1` é reutilizado no pacote e
aponta para a tarefa `OrdaX MCP`; antes de parar um processo, confere PID,
instante de criação e caminho do executável para impedir ação sobre PID
reutilizado. A tarefa recupera o **launcher** quando ele encerra, inclusive
sem novo logon. Enquanto um instalador canônico estiver substituindo os
arquivos, o supervisor não deve concorrer com o upgrade. Não há segundo
Runtime ou serviço permanente adicional.

O menu Iniciar mantém **Iniciar ou reativar OrdaX MCP** (launcher canônico).
O atalho **OrdaX MCP** (menu Iniciar e Área de Trabalho, quando selecionada)
abre apenas o diagnóstico no Python privado; não cadastra dispositivo nem
concede acesso. A UI Owner é separada. O host `ordax-desktop` e o USB têm
seus próprios supervisores/identidades; **não** reutilizam a credencial
da instalação Windows independente.

**Disponibilidade:** nenhuma implementação impede fisicamente um intervalo
de desconexão durante reinicialização de processo, atualização de binário,
ausência de rede ou indisponibilidade da Platform. O requisito é recuperar
automaticamente quando as condições voltarem, sem pedido de novo login ou
nova instalação. `online` e heartbeat de presença não equivalem a sessão
central autorizada ou ação despachável: verificar também
`product_dispatch_ready`, grant, sessão e recibo MCP independente.

### Atualização segura a partir do próprio MCP

O instalador Inno detecta a marca herdada
`ORDAX_RUNTIME_SUPERVISED=1` e **recusa antes do quiesce** uma
autossubstituição disparada de dentro do Job Object do Runtime. Esse
caminho poderia matar o próprio instalador quando o agente encerrasse.
Não retirar a marca, forçar `SendInput`, usar outro agente nem executar
um segundo setup paralelo para contornar a recusa.

O handoff existente, `scripts/windows/ordax-mcp-lifecycle.ps1` com
`-Operation Upgrade`, exige `-InstallerPath`, `-InstallerSha256` e
`-ExpectedCommit`. O operador/fluxo que o chama deve validar antes a
**origem autorizada do artefato**: a igualdade do hash enviado pelo
chamador, sozinha, não autentica um executável de terceiro. O script
restringe o nome/formato e tamanho do pacote, os metadados exatos
`ORDAX`/`OrdaX MCP` (removendo apenas padding de VERSIONINFO),
compara SHA-256 antes e depois do staging e cria um journal na raiz
`%LOCALAPPDATA%\\OrdaX\\updates\\mcp\\<transactionId>`.

O executor copiado para a transação passa para a **tarefa temporária**
`OrdaX MCP Verified Upgrade`, fora do Job Object do Runtime. A tarefa
revalida o journal e o SHA, executa o setup pelo PID exato (espera limitada,
sem aguardar o Runtime permanente lançado pelo Inno), verifica o
`runtime_source_commit` instalado e consulta a prontidão local antes
de registrar `committed`. A escrita do journal usa substituição atômica
compatível com Windows PowerShell 5.1 e caminho explícito de backup.
As fases observáveis são `staged`, `applying`, `committed` ou
`recovery-required` em caso de falha; **não** interpretar
`recovery-required` como rollback concluído. Uma transação concluída
não pode ser repetida. A tarefa temporária só deve ser removida após
confirmar o estado terminal; a tarefa permanente `OrdaX MCP` permanece.

A verificação de retomada agora consulta `http://127.0.0.1:8765/readiness`,
em vez do `/status` completo, que poderia bloquear ao inspecionar
aplicativos/adapters opcionais mesmo com o Runtime saudável. O endpoint
leve retorna `local_dispatch_ready` e o motivo da prontidão local sem
credenciais ou grants. A mesma implementação em Python é usada pelo OrdaX
Native; o Supervisor OS permanece responsável pelo ciclo de vida.
Ver [contrato canônico de Device](PRODUCT-DEVICE-PRESENCE-CLIENT.md).

A instalação deve preservar a identidade/dispositivo/grants e retornar
a `ready/connected` **sem intervenção humana**. O fluxo e os bloqueios
são de **candidato de validação privado**, não equivalem a assinatura
Authenticode nem permitem publicação pública. O gateway/contrato
`computer_type` da Platform e qualquer `central_session_pending`
exigem homologação própria.

Esse ciclo é específico do Windows independente; não altera os
supervisores Native de `ordax-os` nem cria contrato de coexistência
com o Studio legado.

## Prova física de supervisão e atualização — 2026-11-04

A investigação e os recibos estão na
[issue #311](https://github.com/ordaxsystems/ordax-runtime/issues/311).
A [PR #313](https://github.com/ordaxsystems/ordax-runtime/pull/313)
introduziu o supervisor, o handoff e o watchdog no Runtime; a
[PR #314](https://github.com/ordaxsystems/ordax-runtime/pull/314)
corrigiu o padding `VERSIONINFO` do Inno e a substituição de journal no
Windows PowerShell 5.1. Ambas estão integradas à `main`. Revisão
canônica após a segunda integração:
`ad032bcebb14cb10f8e8de84ddfee5d0b422a189`.

No Windows físico `DESKTOP-4UFL03B` (Windows 10 x64), o instalador
privado da `main` anterior (`0ffb1ccec57efb0e9a931c12970fc5a09d20d14a`)
foi obtido do run oficial
[`41861204113`](https://github.com/ordaxsystems/ordax-runtime/actions/runs/41861204113):
ZIP SHA-256 `63b5ca38c1809bdd28ecde783bf9e3f78628fba0101959e8894e4f7c94c63b0f`;
setup SHA-256
`1820924d9e5624d6a8dad182b3daa0c2c22f16031ee3704216258d18a4c45404`.
A instalação única terminou com código 0, manifesto no SHA esperado,
tarefa `OrdaX MCP` registrada com dois gatilhos e
entrada antiga de autostart ausente.

**Crash controlado:** launcher PID `17444` encerrado apenas quando o
Runtime estava ocioso; o mesmo supervisor iniciou automaticamente
launcher PID `13552`, sem reinício do computador, nova identidade,
re-enrollment, grants ou uso do teclado/mouse global.
O estado voltou a `ready`, `paired=true`,
`transport_state=connected` e `product_dispatch_ready=true`.
Essa é uma prova de recuperação em máquina real, não de sessão pública
permanente ou disponibilidade sem interrupções.

**Upgrade independente:** a transação
`7fcf126e230746238c65b73811f65a7d` avançou
`staged → applying → committed`, com o instalador fora do Job Object
que estava sendo atualizado; a sessão local retornou a
`ready/connected/true`. A tarefa temporária foi removida **após**
o recibo `committed`; permaneceu um launcher canônico e a tarefa
permanente `Running`. As duas correções de compatibilidade da PR #314
foram aplicadas ao script físico para concluir o teste. Ao final dessa
prova, **o manifesto binário ainda apontava para `0ffb1cce`**;
o script do lifecycle já incluía as correções incorporadas depois na
`ad032bce`. Essa primeira prova **não** comprovava que a instalação inteira estava
reempacotada no novo commit; o alinhamento posterior está registrado abaixo.

**Alinhamento físico concluído na `ad032bce` (mesmo dia):**
o run oficial da `main`
[`41872593233`](https://github.com/ordaxsystems/ordax-runtime/actions/runs/41872593233)
passou nos jobs Windows de contrato e instalador. O ZIP
`ordax-mcp-windows-candidate-ad032bcebb14cb10f8e8de84ddfee5d0b422a189` foi verificado no Windows
com SHA-256 `bf3234d482369eb2bc4b762cda3611ded1fbc549fa30fbcb95b87c6cfe29babc`;
o setup `OrdaX-MCP-Setup-0.4.8-x64.exe` tinha SHA-256
`b7cde178f3399dda53374a78e4d1b7f5e0ab4d19e30272f9d5f43db050ac3b4c`.
O handoff independente criou a transação
`2a61d375339540149965c4077d270dc7`, que encerrou em
`committed`. A checagem física final confirmou
`runtime_source_commit=ad032bcebb14cb10f8e8de84ddfee5d0b422a189`,
`ready/connected/product_dispatch_ready=true`, tarefa permanente
`OrdaX MCP` em `Running`, exatamente um launcher, nenhuma
entrada `HKCU Run` antiga e remoção da tarefa temporária após
o recibo. Não foi preciso reativar o dispositivo manualmente.
Esta prova alinha **binário e script** ao commit consolidado, mas
não substitui a homologação pública de ferramentas e grants.

**Gates separados:** o controle remoto independente permaneceu
disponível como observador, mas a presença do dispositivo ou o endpoint
local `/status` não atestam `central_session_pending`, grant,
capacidade pública ou o esquema publicado de `computer_type`.
Validar esses fatores de ponta a ponta pela Platform e pelo
MCP público, sem transformar heartbeat em autorização.
O instalador segue privado/não assinado e não deve ser anunciado
como release pública.

## Histórico: primeiro teste físico de entrada Windows — 2026-11-01

O teste físico no Windows 10 x64 autorizado usou o **candidato privado
de CI**, não uma release pública: workflow GitHub Actions
`41302165556`, artefato `13081327874`, versão `0.4.8`.
O ZIP passou no digest SHA-256
`954963d913a4afa8005889a09ec7d7ae6d14c00f9aaabe9918cb967f92c40fbe`
e o executável de setup no digest SHA-256
`1629b814d7a5d6a73a7bba112d3d08390d4fb29280a77f5314be4058eda56f4b`.
O Windows confirmou `Get-AuthenticodeSignature = NotSigned`.
O instalador recebeu marca de origem Internet (ZoneId 3) e foi iniciado
**sem argumentos silenciosos ou remoção da proteção de reputação**,
mas o assistente não apresentou uma janela utilizável no desktop remoto.
A tentativa foi encerrada sem concluir instalação, alterar grants,
enrollment, arquivos do usuário ou estado do Runtime. Não houve listener
local na porta 8765, logo não existe prova E2E de ação MCP.

Observação de rede no mesmo Windows, independente da instalação:
`mcp.ordax.com.br` resolveu DNS, mas **ambos os endereços IPv4**
retornaram timeout ao conectar diretamente à porta 443; `github.com`
e o domínio de contingência `ordax-mcp.ordaxsystems.workers.dev`
aceitaram TCP/443. Um `GET /mcp` no domínio de contingência retornou
HTTP 404, o que **não** demonstra autenticação, capacidade ou que uma
requisição MCP válida funcionaria. Essas observações não autorizam
alterar endpoints/pins nem improvisar fallback no cliente.

**Gates remanescentes:** #49 (assinador Authenticode e bytes finais
confiáveis), #61 (prontidão/roteamento canônico da Platform), e uma
execução física completa com instalador final assinado, aceite explícito
Owner OAuth, presença, `capability_digest`, grants e ações reais
autorizadas. Não se declara conexão concluída ou release homologada
enquanto esses gates permanecerem abertos.

### Diagnóstico no menu Iniciar — HTTPS e Runtime local, sem autorização

A pasta **OrdaX MCP** do menu Iniciar oferece duas ações separadas,
construídas a partir do mesmo `pythonw.exe` privado e do mesmo módulo
`owner_device_connect_gui`:

- **Conectar dispositivo**: solicita consentimento do proprietário e usa
  o fluxo canônico Owner OAuth, cadastro e grants independentes.
- **Verificar conexão**: somente quando clicada, executa dois
  `GET /v3/device/setup` sem token, cookie, dispositivo ou corpo, usando
  HTTPS verificado nos hosts **fixos** já previstos no seletor canônico:
  `mcp.ordax.com.br` e
  `ordax-mcp.ordaxsystems.workers.dev`. Também consulta exclusivamente
  `GET http://127.0.0.1:8765/health`, o status read-only do próprio
  Runtime instalado. Não altera o roteamento, o ingress ou a identidade.

A consulta local usa somente a biblioteca HTTP padrão, endereço e porta
fixos, timeout de 1,5 segundo, leitura limitada a 4 KiB, validação estrita
do JSON e **nenhuma credencial**. Falha de transporte, redirecionamento,
resposta excessiva ou formato inválido são apresentados como status local
não verificável, sem expor exceções. A tela distingue resposta HTTP da
Platform, resposta da porta de status local e autorização efetiva.

Uma resposta HTTP, inclusive 200, **nunca** comprova a identidade
criptográfica do processo local, a sessão atestada, `capability_digest`,
execução de ação MCP ou grants. O campo `ok` local é somente saúde do
servidor de status. Essa opção não abre OAuth nem altera arquivos, chaves,
sessão ou permissões. Não há polling ou consulta em background.
Se o servidor principal falhar, o diagnóstico não trata uma resposta
apenas local como conexão remota bem-sucedida.

Na verificação somente de leitura da conta Cloudflare OrdaX em
01/11/2026, `mcp.ordax.com.br` estava associado ao Worker
`ordax-mcp`, domínio habilitado, com um deployment atribuído a
100% desde 30/10/2026. Isso comprova **configuração do Worker**,
não acesso TCP, resposta autenticada ou readiness em todos os clientes.
O timeout TCP/443 no Windows físico permanece uma falha real a
investigar por rota/rede e Platform, sem override local do endpoint.

### Seleção Owner na interface Windows (fonte candidata)

O atalho **Selecionar dispositivo para sessão** executa o mesmo
`owner_device_connect_gui` com `--selecionar-sessao`. A sequência exige:

1. Consentimento explícito antes de abrir OAuth Owner PKCE no navegador.
2. Resolução da identidade da instalação Windows local (sem usar o
   Product Device distinto do OrdaX Desktop).
3. Consulta à autoridade Owner e aos endpoints canônicos
   `central-session-targets` / `central-session-selections`, para
   clientes OAuth previamente consentidos e elegíveis.
4. Exibição do **cliente OAuth exato**, `device_id`, `challenge_id`
   e expiração; o proprietário confirma uma solicitação específica.
5. Reutilização literal de `owner_grants_cli.perform(central-select)`
   para a única alteração, que é selecionar o alvo no desafio existente.

A escolha **não** concede grants, não confirma o challenge com credenciais
nativas, não despacha trabalho e não representa prontidão MCP. O Runtime
selecionado ainda deve atestar o desafio na Platform. Todas as decisões de
autorização permanecem no Control Plane; não existe cache de tokens,
segundo agente, polling automático ou renovação silenciosa.

Solicitações vencidas, ausência de alvo elegível, resposta inconsistente
ou fila excessiva falham fechadas. Se a resposta ao POST for perdida, a
interface **não repete a seleção**; o usuário deverá consultar novamente.
Essa apresentação é específica do Windows, mas a autenticação Owner e
o comando canônico são compartilháveis por uma interface Native no
OrdaX OS USB, com identidade e Supervisor próprios.

A implementação nesta árvore ainda precisa ser incorporada a uma
compilação verificada, assinatura e prova física interativa de OAuth,
escolha de dispositivo e confirmação Runtime. O código-fonte ou os testes
não significam que o atalho já exista na instalação atual, nem que o
recurso esteja disponível em todas as instalações públicas.

## Proveniencia do Python privado — SSOT Windows

Os dois empacotadores em `ordax-runtime` usam exclusivamente
`packaging/windows/embedded-python.lock.json` e o verificador
`scripts/windows/get-locked-embedded-python.ps1`. O lock fixa versao,
arquitetura, nome, URL HTTPS oficial e SHA-256 do arquivo
`python-3.12.10-embeddable-amd64.zip`, conforme o manifesto publico do
Python.org `windows-3.12.10.json`. Arquivos presentes em cache sao
verificados novamente **antes** de qualquer extracao. Cache adulterado ou
lock divergente bloqueia o build, sem redownload silencioso nem origem
alternativa. Arquivo ausente e baixado para nome temporario, validado e
entao movido ao cache.

A CI executa verificacao negativa offline com ZIP de cache adulterado e
prova a construcao do candidato MCP. Essa prova de integridade da base
Python **nao** equivale a reproducibilidade total das dependencias Python,
assinatura Authenticode, consentimento Owner real ou release publico.
Essas etapas permanecem com suas autoridades e gates proprios.

## Coexistencia e migracao com instalacoes Studio existentes

O instalador Studio historico usa a raiz
`%LOCALAPPDATA%\\Programs\\ORDAX` e mantem um Runtime proprio, com
AppId Inno preexistente. O candidato independente usa **outra** raiz e
AppId; sua instalacao e bloqueada se detectar aquele Studio ou Runtime,
em vez de iniciar duas instancias concorrentes, sobrescrever arquivos,
destruir identidade ou compartilhar raiz de desinstalacao. O Windows
mutex `Local\\ORDAXRuntime` continua sendo o unico owner local.

Para coexistencia definitiva, deve-se migrar o instalador do Studio para
**descobrir e consumir** o OrdaX MCP instalado, e provar upgrade
in-place, rollback, estado e desinstalacao com uma so identidade executora
e nenhum conflito de processo. Essa migracao e responsabilidade coordenada
entre os owners `ordax-runtime` (host/instalacao) e `ordax-apps`
(UI Studio), sem fazer do Studio dependencia do MCP.

## Escopo

Nao alterar `ordax-os` USB Dev PR #2243, `ordax-flow` ou `ordax-gpt`
por motivo deste empacotamento; nao inferir que um dispositivo offline
foi atualizado por uma build de CI.
