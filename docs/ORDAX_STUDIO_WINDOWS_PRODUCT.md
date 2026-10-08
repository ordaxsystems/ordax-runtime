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

As ações tipadas `computer.*` de arquivos preservam uma fronteira independente:
estado privado, política, identidade do dispositivo e source executável do Runtime
não são arquivos editáveis por esses grants, mesmo com Full Access local.
Busca e listagem não percorrem essas raízes; mover/remover uma pasta ancestral
também é bloqueado. Mudanças de política e atualização usam os ports do owner.
Essa proteção das APIs de arquivos não transforma acesso a terminal ou controle
interativo do desktop em sandbox do Windows.

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

## App Intelligence no produto Windows

O instalador compila o manifesto canônico `apps/studio/ai/manifest.json` da revisão de `ordax-apps` fixada em `studio-source.lock.json` para um registry local do produto.

O Runtime valida e carrega esse registry uma única vez. Clientes autorizados consultam:

- `intelligence.app_catalog` para identidade, versão e ids de intents;
- `intelligence.app_detail` para o manifesto declarativo de um `app_id` exato.

Essas ações não executam o app, não chamam modelo, não acessam GitHub por requisição e não herdam Computer Control. O acesso remoto usa o grant owner separado `app-intelligence-read`.


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

## Publicação Windows: proveniência e assinatura

Uma execução de PR de `Windows Product Build` produz **candidato de instalador para validação**, não um release público. A publicação pública ocorre exclusivamente por tag e segue gates na ordem:

1. resolver `studio-source.lock.json` da própria revisão tagueada, mantendo `ordaxsystems/ordax-apps` como SSOT do source Studio;
2. exigir que a tag seja exatamente `v<version>` do lock, com um único `ORDAX-Studio-Setup-<version>-x64.exe`;
3. verificar que `SHA256SUMS.txt` possui exatamente um hash SHA-256 válido desse instalador e que os bytes reais conferem;
4. exigir assinatura Authenticode válida, timestamp confiável e cadeia não autoassinada;
5. publicar **somente se a tag não tiver release anterior**, sem sobrescrever binários já distribuídos.

O gate de proveniência é `scripts/windows/assert-release-provenance.ps1`, com cenários positivos e negativos exercitados no Windows CI. O verificador da assinatura permanece `scripts/windows/assert-release-authenticode.ps1`. A etapa de publicação faz checkout da revisão marcada pela tag antes de chamar os scripts; nenhum verificador é inferido de artefatos de outro job.

**Dependência operacional externa:** atualmente o pipeline possui verificação de assinatura, mas não provisiona nem executa um assinador de produção. Portanto **não há release público pronto apenas porque o build e o smoke passaram**. Não adicionar certificados autoassinados, chaves privadas em source/CI ou publicar candidato sem o gate. Integrar um serviço de assinatura autorizado, com custódia/rotação/auditoria, pertence ao processo de release da plataforma e exige configuração real separada. Também não presumir que o computador do usuário foi atualizado quando há apenas package candidate CI.

## Identidade autorizada do publicador Windows

**Assinatura válida não implica assinatura autorizada pelo OrdaX.** O gate `scripts/windows/assert-release-authenticode.ps1` verifica, além da validade Authenticode, timestamp e rejeição de certificado autoassinado, que o thumbprint do certificado emissor esteja na lista de publicadores aprovada para distribuição ORDAX.

- SSOT da identidade de assinatura: variável de ambiente GitHub `ORDAX_RELEASE_SIGNER_THUMBPRINTS` no ambiente de deployment `windows-production-release`. Seu valor deve ser um thumbprint SHA-1 de certificado (40 caracteres hexadecimais), ou uma lista de até quatro separados por vírgula para rotação controlada. Thumbprint identifica o certificado, **não** substitui SHA-256 do instalador.
- A promoção da tag usa `environment: windows-production-release`. Os responsáveis precisam configurar explicitamente as regras de proteção/revisão do ambiente nas configurações do GitHub; declarar o nome do ambiente no YAML **não cria aprovação obrigatória por si só**.
- Variável ausente, lista inválida/duplicada, certificado diferente, assinatura inválida/ausente, timestamp ausente e certificado autoassinado bloqueiam publicação, sem fallback para qualquer outro signer Windows confiável.
- A identidade aprovada deve vir do certificado real da organização, verificada por processo independente, nunca ser inferida do próprio binário recebido. A manutenção e rotação dessa identidade cabem ao owner do processo de release do Runtime. Não gravar chaves privadas ou tokens de signing em código, workflows, logs ou arquivos de teste.
- Testes de assinatura em `tests/test_release_signer_identity.py` usam dados simulados **apenas para provar a política do gate**; não substituem assinatura real nem smoke dos bytes assinados.

A ausência atual de um serviço de assinatura de produção segue registrada na issue #49. O build de PR ainda produz somente candidato interno; não iniciar publicação por tag até a assinatura e o teste do artefato assinado estarem integrados ao workflow autorizado.

## Interface única e remoção dos snapshots históricos

O proprietário exclusivo de UI, estilos, scripts e manifestos do Studio é `ordaxsystems/ordax-apps/apps/studio`. O Runtime possui somente host/adapters e o `studio-source.lock.json`; não distribui mais `ordax_studio/studio.html`, `studio_product.html` nem `ordax_studio/assets/**` como source.

O build Windows verifica a revisão exata do lock, materializa HTML e assets **exclusivamente no site-packages privado do staging**, compara inventário e SHA-256 e executa instalação/upgrade/uninstall em Windows. Em um Runtime clonado sem esse instalador, não existe UI local de fallback; isso é intencional e fail-closed. Os testes visuais/funcionais portáteis pertencem a `ordax-apps/apps/studio/tests`; os testes do Runtime cobrem APIs, política local, bridge, isolamento e pacote Windows.

O comando histórico `ordax-studio-web` foi aposentado. A entrada de conveniência `scripts/windows/ordax-studio-start.ps1` consulta exclusivamente a localização registrada pelo **Inno Setup AppId canônico** do usuário no Registro do Windows e lança o `ORDAX Studio.exe` instalado. Não presume a pasta default, não inicia HTML do repositório nem força instalação de candidato de CI. O executável instalado e o Runtime continuam possuindo versionamento independente.

Compatibilidades de upgrade estritamente necessárias — como o executável `ORDAX Dev.exe` byte-idêntico ao `ORDAX Studio.exe` — permanecem somente pelo contrato explícito de migração Windows, sem uma segunda implementação funcional. Sua retirada exige prova de upgrade de instalações antigas.

## Aposentadoria de instaladores e shells paralelos

O instalador Inno Setup `packaging/windows/ordax-studio.iss` e o pipeline `windows-product-build.yml` são os únicos proprietários da distribuição Windows. O script experimental `scripts/windows/ordax-studio-install.ps1` foi removido: ele criava um ambiente Python/Git gerenciado, lançava um HTML local e recriava atalhos `ORDAX Dev` fora do instalador canônico, com estado e ciclo de vida paralelos.

Também foram removidos a interface Tkinter `ordax_studio/desktop.py`, o seu preview `ordax_studio/preview.py`, o comando `ordax-studio desktop` e o executável Python `ordax-studio-desktop`. Não existe mais um segundo shell desktop a manter/testar. O comando `ordax-studio` permanece como CLI de diagnóstico/contexto e `ordax-mcp` continua atendendo o mesmo Runtime; o host Windows usa a UI versionada de `ordax-apps`.

O script `scripts/windows/ordax-studio-start.ps1` é apenas lançador do produto registrado com o **AppId Inno Setup** do usuário: não instala nada, não clona Git, não cria agendadores/atalhos e não inicia o source Python. Sem produto instalado, falha com orientação para obter uma distribuição oficial assinada.

Compatibilidade de upgrade é diferente de manter UI duplicada: a migração pelo instalador canônico ainda reconhece `ORDAX Dev.exe`, atalhos e tarefas do runtime histórico **somente para aposentá-los sem perda de dados**. Não voltar a registrar essas entradas no computador.

## Verificação dos bytes finais antes da publicação

O único teste de ponta a ponta Windows é `scripts/windows/test-ordax-studio-install-smoke.ps1`, invocado pelo **mesmo contrato** em duas etapas do workflow `windows-product-build.yml`:

1. Em `install-smoke`, a revisão exata da PR/tag usa o candidato do job de build para provar instalação limpa, encerramento/upgrade do Runtime ativo, migração e desinstalação.
2. Em `publish-release` (somente tag com ambiente protegido), o job verifica o source lock, a versão/tag e o SHA-256 do instalador final, exige assinatura Authenticode válida com timestamp e **certificado autorizado**, e **executa novamente o mesmo teste sobre o arquivo assinado que será publicado**. Após o smoke, recalcula o hash/confronta `SHA256SUMS.txt` e a proveniência antes do `gh release create`, que não sobrescreve releases existentes.

Não existe uma segunda implementação do smoke nem publicação baseada apenas em build de candidato. Se um serviço de assinatura alterar os bytes, seu resultado assinado, o checksum final e a assinatura precisam estar disponíveis **antes** de `publish-release`, e precisam permanecer inalterados até a publicação. Uma assinatura adicionada após o smoke de candidato não satisfaz o gate de release.

**Ainda pendente (issue #49):** contratar e integrar um assinador Authenticode de produção com custódia autorizada; configurar proteção/revisão do GitHub Environment. O fluxo atual continua falhando fechado diante de candidato sem assinatura e não publica releases apenas porque o CI de PR passou. A introdução desses controles não significa que um serviço de assinatura já esteja operacional.
