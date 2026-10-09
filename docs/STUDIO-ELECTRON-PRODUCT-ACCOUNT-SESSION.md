# Sessão da Conta OrdaX no Studio Electron — contrato privado v1

A autenticação **continua sob autoridade do Runtime**:
`ordax_studio.product_auth.sign_in_with_password` valida a conta usando
Supabase Auth, e `ordax_dev_agent.product_remote_client.ProductRemoteClient`
confirma `GET /v3/product/session` na plataforma canônica. A identidade,
grants, autorização de comandos e trilhas de auditoria **continuam** na
plataforma e no gateway Product. Nenhum segundo autenticador ou action
executor foi criado.

O módulo `ordax_studio.electron_account_session` é uma entrada
**single-shot**, exclusiva do subprocesso iniciado pelo **processo principal
Electron instalado**. Usa um envelope JSON de uma única chamada pelo
**stdin** (`schema=ordax.studio-product-account-session/1`,
`operation=sign-in`, `email`, `password`) e envia uma resposta única
pelo **stdout**. Não abre socket, não aceita rota arbitrária, não vincula
novo dispositivo, não cria grants e não disponibiliza o antigo método
`workbench_bridge._invoke`.

Os dados de login **nunca** devem ser passados via argv, URL, log, variável
de ambiente, storage ou resposta HTTP. O resultado é destinado
exclusivamente ao processo principal Electron, contém o access token **em
memória de processo** para o cliente Product já existente do Apps e não
pode ser entregue ao renderer/ChatGPT Web/previews/plugins. A saída de erro
omite credenciais, corpo do provedor e traceback.

Verificações: tamanho de entrada até 3 KiB, JSON sem chaves duplicadas,
envelope e operação exatos, controle ASCII de campos, token limitado,
confirmação do subject Product. A verificação do subject tem de ser feita
**antes** de entregar a sessão ao Studio. Falha de autenticação equivale a
sem sessão.

`python -m unittest tests.test_studio_electron_account_session -v`

A chamada depende do Runtime **efetivamente instalado**. O App deve usar
apenas Python privado do diretório de instalação comprovado e abrir
subprocesso com `-m ordax_studio.electron_account_session`, nunca carregar
a biblioteca diretamente de um repositório arbitrário. Em qualquer cenário
sem instalação confiável, UI/product executa `fail-closed` e conserva
conversas locais e o ChatGPT Web.

Esse contrato prepara a migração da issue Runtime #67, mas **não**
promove a UI Electron ao Inno Setup nem declara equivalência da ponte
WorkBench WebView2. Compatibilidade de runtime/versão, expiração,
revogação, logout, upgrades e smoke do instalador final permanecem
condições de homologação. O Studio não deve permitir trocar identidade
enquanto existirem operações com status incerto, enviadas ou não concluídas.
