# Administração Owner do OrdaX MCP — Runtime nativo

A administração das permissões MCP deve funcionar com o **Runtime**, sem
iniciar o aplicativo Studio, sem Node.js e sem manter uma autoridade paralela.

## Instalação

O ponto de entrada `ordax-owner-grants` é publicado por `pyproject.toml`.
No instalador Windows, o Runtime embarca Python isolado e o comando canônico é
`runtime\\python.exe -m ordax_dev_agent.owner_grants_cli`.
O build Windows testa essa chamada diretamente no Python privado. Em uma instalação
Python padrão, `ordax-owner-grants` também fica disponível via console script.
Não requer um serviço MCP adicional.

## Registro inicial deste computador (após instalação ou formatação)

O Runtime pode abrir o OAuth Owner existente e registrar **somente a máquina local**
sem depender da UI do Studio:

```powershell
& '.\runtime\python.exe' -m ordax_dev_agent.owner_device_connect_cli --connect
```

No checkout de desenvolvimento, use o Python virtualenv do Runtime. O OAuth
PKCE exige aprovação explícita no navegador; o código é trocado por token em
memória e entregue ao **mesmo** `device_setup.configure` canônico. Nenhum token
OAuth é impresso, aceito na linha de comando ou armazenado. O único estado
persistente é a credencial de dispositivo local protegida e o `device_id`
validado pela Platform. A operação não inicia um Runtime adicional, não
concede permissões e não prova presença online nem `capability_digest`.

Depois do registro, o Supervisor proprietário deve iniciar o Runtime único,
publicar a presença e conferir os grants deste dispositivo e do cliente OAuth
exato. Nunca copiar token/identidade de outro computador após formatação.

## Comandos

```powershell
& '.\\runtime\\python.exe' -m ordax_dev_agent.owner_grants_cli list --device <DEVICE_UUID>
& '.\\runtime\\python.exe' -m ordax_dev_agent.owner_grants_cli diagnose --device <DEVICE_UUID> --client <CONSENTED_CLIENT_UUID>
& '.\\runtime\\python.exe' -m ordax_dev_agent.owner_grants_cli authorize --device <DEVICE_UUID> --profile interactive-computer-control --days 30
& '.\\runtime\\python.exe' -m ordax_dev_agent.owner_grants_cli authorize --device <DEVICE_UUID> --profile device-native-terminal --days 30
& '.\\runtime\\python.exe' -m ordax_dev_agent.owner_grants_cli authorize --device <DEVICE_UUID> --profile full-computer-control --days 30
& '.\\runtime\\python.exe' -m ordax_dev_agent.owner_grants_cli revoke --device <DEVICE_UUID> --group <GRANT_GROUP_UUID>
```

Se a conta possuir mais de um cliente OAuth ativo, especifique
`--client <CLIENT_UUID>` nas mutações. Perfis de projeto exigem
`--space <SPACE_UUID> --project <PROJECT_UUID>`; os perfis de computador
são **device-scoped**, não exigem GitHub nem Space.

Cada invocação inicia um login OAuth PKCE público pelo navegador padrão,
com callback local em `127.0.0.1:8766`. O login exige ação explícita do
proprietário e o token existe apenas na memória do processo. O Runtime
não persiste credenciais, não salva códigos OAuth e não concede grants
a si mesmo. O callback usa a identificação OAuth pública já registrada,
agora mantida em `ordax_core.product_auth_metadata`; a interface Studio
permanece compatível com essa SSOT.

O `ProductRemoteClient` do Runtime é o único cliente HTTP, usando:
`GET /v3/owner/remote-authority`,
`POST /v3/owner/remote-grant-groups`,
`DELETE /v3/owner/remote-grant-groups/{id}`.
PostgreSQL/Platform permanece autoridade final, inclusive revogação.
`device-native-terminal` e `full-computer-control` exigem Full Access ativado na política local. Ambos passam pela autoridade canônica PostgreSQL/Platform, com OAuth Owner explícito. O primeiro concede somente execução de terminal no dispositivo, sem GitHub/Space, e não habilita outras famílias de ações.

## Escolha de dispositivo para sessão Central (fonte candidata, OFF)

Os comandos abaixo pertencem **ao mesmo CLI e OAuth Owner PKCE**. São integração de
source com a Platform [PR #293](https://github.com/ordaxsystems/ordax-platform/pull/293)
e continuam **inoperantes em produção** enquanto os dois flags governados
`CENTRAL_OWNER_TARGET_PREFLIGHT_ENABLED` / `CENTRAL_OWNER_SELECTION_ENABLED`
estiverem desligados e o schema SQL permanecer em `proposals/`.
Não os use como instrução para transferir grants entre clientes.

```powershell
& '.\\runtime\\python.exe' -m ordax_dev_agent.owner_grants_cli central-targets --device <DEVICE_UUID> --client <CLIENTE_OAUTH_EXATO>
& '.\\runtime\\python.exe' -m ordax_dev_agent.owner_grants_cli central-selections --device <DEVICE_UUID> --client <CLIENTE_OAUTH_EXATO>
& '.\\runtime\\python.exe' -m ordax_dev_agent.owner_grants_cli central-select --device <DEVICE_UUID> --client <CLIENTE_OAUTH_EXATO> --challenge <DESAFIO_DA_SESSAO> --confirm
```

`central-targets` apenas relata candidatos pelas permissões existentes;
`central-selections` lista somente challenges esperando a escolha do Owner.
`central-select` **exige** `--client`, `--device`, `--challenge` e
`--confirm`; a opção só existe para uma decisão deliberada do proprietário,
depois de identificar o desafio correto. A operação envia `POST` autenticado
ao **Platform canônico**, sem criar grants nem executar ação MCP. A resposta
`status=pending`, `attested=false`, `dispatch_authorized=false`
**não** significa que o Runtime confirmou: só o dispositivo exato com sua
credencial Native pode fazer a confirmação; um novo comando com outro dispositivo
não pode roubar o challenge selecionado.

O CLI começa consultando o inventário oficial da autoridade Owner para
validar o cliente OAuth consentido. Caso haja mais de um cliente, a seleção
sem `--client` exato é proibida; **jamais** reutilizar os grants do cliente
A para B. Um Notebook ainda autorizado não deve ser revogado somente para
facilitar o uso do Desktop. A Platform escolhe por challenge e cliente,
não pelo `device_id` dos argumentos do modelo. Sem migração aplicada e
prova real de atestação, o comportamento esperado é falhar fechado.

**Aceite físico ainda pendente:** UX de identificação do challenge sem hash
do marcador privado, Runtime instalado atualizado, Owner login PKCE válido,
dois dispositivos autorizados, Runtime presence/digest, challenge confirmado,
`computer_access_status` read-only com receipt e revogação exercitada.
Não tratar merge de cliente HTTP ou CI como comprovação E2E.

## Ativação explícita para desenvolvimento (sem abrir MCP público)

No **próprio notebook OrdaX**, com o Runtime atualizado a partir do repositório
e o `device_id` da instalação validado, execute:

```powershell
& '.\\runtime\\python.exe' -m ordax_dev_agent.owner_grants_cli dev-enable --device 0a563924-e4d1-4328-bcb0-f73820c92e1b --client <CLIENTE_OAUTH_CONSENTIDO> --days 30
```

O comando usa **um único login Owner OAuth PKCE** e:
1. Confirma que o dispositivo solicitado é exatamente o dispositivo instalado,
   nunca o desktop de administração ou outro computador.
2. Ativa `computer_access.enabled`, `full_access` e `full_filesystem`
   na política local usando o escritor atômico com revisão SHA-256.
3. Solicita `full-computer-control` ao Control Plane para o cliente OAuth
   consentido, dispositivo exato e prazo de validade informado.
4. Informa que prontidão local e concessão **não** comprovam execução MCP;
   o teste real ainda exige sessão confiável atestada pelo Runtime.

Isso libera o conjunto de ações de computador implementadas no Runtime, inclusive
terminal e processos, mas **não** elimina o proprietário autenticado, revogação,
auditoria, controles do host, escopos de Projects, nem proteções de integridade
das credenciais e fontes executáveis do próprio Runtime. O modo não é ativado
automaticamente por deploy ou pela simples presença do notebook.

Se o Control Plane negar o grant depois que a política local for salva, essa
política continuará configurada para desenvolvimento. A ação remota seguirá
negada até existir um grant Owner válido; examine o retorno, ajuste o cliente
consentido e só então repita o comando. Para desativar o modo local, use o
controle de acesso Owner da instalação; para revogar a autorização remota, use
`ordax-owner-grants revoke`. Não remova a credencial do dispositivo.

## Diagnóstico de prontidão para substituir o Remote

`diagnose` usa o mesmo OAuth Owner e apenas **GET /v3/owner/remote-authority**.
Confere a política local do computador, as ações declaradas pelo pacote do
Runtime em execução e os grants retornados pelo Control Plane para um **cliente
OAuth exato**. Se houver mais de um cliente, informe `--client` para não
atribuir permissões de outro aplicativo à conversa atual. Nunca concede ou
revoga grants.

O resultado identifica `missingInstalledCode` e `missingClientGrants`
para ações representativas de janelas, screenshot, clique, terminal genérico,
processos e arquivos. Grupos expirados, de outro cliente, de outro dispositivo, com
`client_consent_active` diferente de `true` ou com `scope_kind` diferente de
`device` não contam como grants genéricos do dispositivo. Grupos vinculados
a Project/Space tampouco contam. A escolha de
perfis/ações autorizadas continua pertencendo exclusivamente à Platform:
esse diagnóstico **não** é um catálogo de perfis nem uma autoridade paralela.

`preflight-ready-unproven` significa **somente** que código declarado,
política local e grant remoto correspondem. `endToEndProven` é sempre
`false`: somente uma operação MCP real, autorizada e executada pelo Runtime
instalado pode comprovar paridade. Nem a contagem anunciada pelo servidor
MCP nem a CI verde comprovam que as ferramentas estejam disponíveis na
conversa atual. Para usar as novas ações, o binário instalado deve incorporar
essas ações e o cliente ChatGPT deve expô-las na sessão.

## Validação

```powershell
python -m unittest discover -s tests -p test_owner_grants_cli.py
python -m unittest discover -s tests -p test_studio_browser_oauth.py
```

Para Python privado do Windows, adicionar o checkout a `sys.path`
explicitamente nos testes locais (ou testar em venv). Não confundir
pacotes instalados anteriormente com o código fonte em teste.

Este comando **não** autoriza automaticamente o dispositivo. Em ambiente
novo, o navegador deve confirmar o OAuth do proprietário. A prova final
precisa testar `computer_launch_app` via OrdaX MCP depois de um grant
real, e não executar o aplicativo por um segundo conector.


## OrdaX OS Desktop Windows — ação de observação MCP (2026-10-31)

O Runtime **canônico** do OrdaX Desktop, supervisionado pelo OS, permite
somente `agent.status` e `computer.access_status` (leitura da política
local, sem habilitá-la). O perfil `computer-observe` da Platform deve ser
concedido por OAuth Owner ao **cliente ChatGPT exato e ao Product Device do
Desktop**, sem emprestar grants de um agente Windows auxiliar ou notebook.
A escolha transacional do dispositivo (`central-select`) é uma etapa
distinta de consentimento, não um grant nem uma prova de execução.

A primeira prova física em Windows demonstrou que a sessão MCP alcança
o Runtime e recebe o erro verdadeiro `action not allowed:
computer.access_status` na revisão que publicava apenas `agent.status`.
Esta correção permite a leitura canônica sem ampliar terminal, arquivos,
processos mutáveis ou GUI do Desktop e não substitui o adaptador Windows
auxiliar. A conclusão só existe com uma chamada MCP que retorne
`ok=true` e recibo de ação executada no dispositivo; publicação da
ferramenta, CI, presença, `pending`, `queued` ou atestação isolada não
comprovam E2E. A issue de implementação e evidência física continua
[ordax-os#1985](https://github.com/ordaxsystems/ordax-os/issues/1985).
