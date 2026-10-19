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
