# ORDAX Studio para Windows

## Produto

A identidade user-facing é **ORDAX Studio**.

A distribuição Windows canônica passa a ser:

`ORDAX-Studio-Setup-<versão>-x64.exe`

Ela instala:

- **ORDAX Studio.exe** — launcher principal que abre a Workbench do ORDAX Studio;
- **ORDAX Dev.exe** — alias legado/compatível temporário, byte-idêntico ao launcher principal, mantido somente para atalhos/automação de instalações anteriores;
- **ORDAX Runtime.exe** — host persistente e provider-neutral que conecta o computador ao Control Plane.

O runtime privado inclui CPython e dependências do produto, sem alterar o PATH do usuário.

`ORDAX Dev.exe` não é um produto separado, não possui implementação própria e não indica dependência de Codex. Durante a janela de migração ele existe apenas como alias dos mesmos bytes de `ORDAX Studio.exe`. A remoção futura desse alias exige prova de que não há instalações/atalhos suportados que ainda dependam dele.

## Compatibilidade de upgrade

A migração preserva o mesmo Inno Setup AppId histórico:

`{0D31F22D-8451-4CF4-9E34-F0D4D857F55F}`

Isso mantém o upgrade in-place das instalações existentes. Em instalação nova, o diretório padrão passa a ser `Programs\ORDAX Studio`; upgrades podem continuar no diretório previamente registrado pelo instalador antigo, sem mover estado por conta própria.

O shutdown cooperativo continua usando `Local\ORDAXStudioShutdown` para a UI e `Local\ORDAXRuntimeShutdown` para o Runtime. O instalador também possui fallback explícito para encerrar um `ORDAX Dev.exe` histórico que não exponha o evento cooperativo.

Atalhos antigos `ORDAX Dev` são removidos durante a atualização e substituídos por atalhos `ORDAX Studio`. O estado do dispositivo, identidade, projetos e dados do Runtime não é apagado pela troca de branding.

## Identidade e integrações

A identidade do produto é a **Conta ORDAX**. Hoje ela é autenticada por Supabase Auth e é usada para vincular dispositivos, Spaces e grants.

O **Cloudflare** é infraestrutura do Control Plane/MCP e não exige login do usuário final.

O **GitHub** é opcional e deve ser tratado como provedor de projetos/remotos. Ele não substitui a Conta ORDAX e o plugin GitHub de um cliente de IA não é a ponte do ORDAX.

Clientes de IA são integrações separadas. O fluxo genérico é:

```text
cliente de IA autorizado
        │
        └─ conector ORDAX do provider
             │
             └─ Control Plane / protocolo ORDAX
                  │
                  └─ ORDAX Runtime no PC
```

`ORDAX for ChatGPT` é uma instância desse padrão. Um futuro `ORDAX for Grok` deve seguir o mesmo boundary e não criar outro Runtime.

Contratos detalhados:

- `docs/ORDAX_IDENTITY_AND_PROVIDERS.md`;
- `docs/ORDAX_PROVIDER_CONNECTORS.md`.

## Uso diário

1. instalar `ORDAX-Studio-Setup-<versão>-x64.exe`;
2. o Runtime inicia automaticamente com o Windows;
3. abrir `ORDAX Studio.exe` para ver projetos, Git, previews, memória, Computer Control e status;
4. conectar um cliente/provider autorizado quando quiser usar uma IA externa com as ferramentas ORDAX;
5. a janela do Studio pode ser fechada sem derrubar a conexão do Runtime.

Não existe dependência estrutural de Codex, chat embutido, Browser Companion ou navegador gerenciado.

## Provider neutrality

`ORDAX Runtime.exe` não deve conter branches de execução por provider. O mesmo action handler executa a mesma capability autorizada independentemente de a solicitação ter vindo de ChatGPT, Grok ou outro cliente.

Provider metadata serve para autenticação, política, auditoria, revogação e UX; nunca para conceder autoridade implícita.

## Gates de produto

O pipeline `.github/workflows/windows-product-build.yml` deve provar em cada mudança relevante:

- build da Workbench nativa;
- build do instalador `ORDAX Studio`;
- instalação limpa com `ORDAX Studio.exe` como primário;
- alias `ORDAX Dev.exe` byte-idêntico, sem segunda implementação;
- Runtime action-ready;
- abertura real da Workbench pelo launcher Studio;
- upgrade sobre Runtime ativo;
- aposentadoria de processo histórico `ORDAX Dev.exe` sem evento cooperativo;
- preservação dos entrypoints após upgrade;
- desinstalação e remoção do autorun do Runtime.

A compatibilidade legada existe para permitir uma migração correta; ela não redefine o nome atual do produto.
