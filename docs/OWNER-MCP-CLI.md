# Administração Owner do OrdaX MCP — Runtime nativo

A administração das permissões MCP deve funcionar com o **Runtime**, sem
iniciar o aplicativo Studio, sem Node.js e sem manter uma autoridade paralela.

## Instalação

O ponto de entrada `ordax-owner-grants` é publicado por `pyproject.toml`.
No Windows instalado, o próprio Python privado do Runtime fornece
`runtime\\Scripts\\ordax-owner-grants.exe`. Em ambientes Python normais,
o comando também está disponível após instalar o pacote `ordax-runtime`.
Não requer um serviço MCP adicional.

## Comandos

```powershell
ordax-owner-grants list --device <DEVICE_UUID>
ordax-owner-grants authorize --device <DEVICE_UUID> --profile interactive-computer-control --days 30
ordax-owner-grants authorize --device <DEVICE_UUID> --profile full-computer-control --days 30
ordax-owner-grants revoke --device <DEVICE_UUID> --group <GRANT_GROUP_UUID>
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
`full-computer-control` exige Full Access ativado na política local.

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
