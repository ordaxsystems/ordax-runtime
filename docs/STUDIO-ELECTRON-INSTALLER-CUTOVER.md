# Corte real do instalador Windows para o Studio Conversation (Electron)

Proprietário da **interface e do bundle Electron**: `ordaxsystems/ordax-apps`.
Proprietário do **instalador, Runtime, identidade de dispositivo e execução**:
`ordaxsystems/ordax-runtime` com grants canônicos em `ordax-platform`.

## Substituição em um único produto

- `studio-source.lock.json` aponta para um commit Git **imutável** de
  `ordax-apps` e obtém versão de `apps/studio/app.json`. Não existe versão
  alternativa nem fallback para `apps/studio/src/index.html` no instalador.
- O builder de instalação utiliza o verificador independente
  `verify-studio-presentation-handoff.ps1`, que executa **o único builder
  de Electron de Apps**, verifica antes/depois todos os bytes SHA-256 e
  prepara o pacote em `stage/presentation`. Um erro interrompe o build.
- `ORDAX Studio.exe`, no nível superior da instalação, é **somente um
  supervisor Windows**. Primeiro assegura a continuidade de
  `ORDAX Runtime.exe` e então abre `presentation/ORDAX Studio.exe`.
  A Runtime Python privado não ganha uma segunda interface WebView2.
- O manifesto `product-manifest.json` prova
  `entrypoints.studio_ui=presentation\\ORDAX Studio.exe`,
  `presentation_host=electron`, versão e commit de origem, sem bearer.
- O pacote privado Python instala somente o Runtime. O extra legado `pywebview` deixou de fazer parte do produto Windows; Electron já fornece Chromium sem exigir .NET/WebView2 externo.
- O Inno Setup conserva seu AppId de upgrade e atalhos existentes, remove
  especificamente `workbench/` e os arquivos HTML/bridge da antiga
  apresentação em `ordax_studio/`, mas preserva perfis,
  `%LOCALAPPDATA%/OrdaX/Assistant-web`, journals, projetos e credenciais
  no armazenamento protegido. O Runtime sobrevive ao fechamento do Studio.

## Validação obrigatória para merge e promoção

1. Python `tests.test_windows_product_packaging`, contracts de proveniência,
   teste de fronteira e `tests.test_studio_presentation_handoff_contract`
   devem passar no Windows; `ORDAX Runtime Contracts` também.
2. No runner limpo Windows, instalar pacote gerado por
   `build-ordax-studio-product.ps1` e rodar
   `test-ordax-studio-install-smoke.ps1`. Esse smoke verifica processo
   Electron **filho do supervisor**, uma janela visível e responsiva,
   manifesto Product, entrada `conversation/src/index.html`, consistência
   exata de inventário SHA-256 do presenter, ausência da WPF antiga, e
   permanência do Runtime após fechar a UI.
3. Provar que o subprocesso **privado e instalado** de autenticação rejeita
   requisição malformada sem produzir token no stdout do cliente Web.
4. Executar upgrade sobre Runtime ativo/instalações antigas, recuperação,
   alias legado byte-idêntico e uninstall, sem remover pasta sem marcador
   de propriedade.
5. Fixar revisão de Apps que contenha correção de serialização
   `account-sign-in-guard.cjs`, para que dois IPCs simultâneos não iniciem
   dois subprocessos de login. Se ausente, o installer **falha**. Não permitir
   revisão de source não integrada à main canônica para publicação.
6. A assinatura de produção, o canal de release autenticado, conta e
   dispositivo real, ChatGPT Web real e controle do computador fora de
   fixture permanecem gates de homologação distintos: CI verde não
   equivale a release assinado nem E2E com usuário.

## Notas de segurança

A URL HTTP local `/health` não é credencial e não fornece comandos. O login
passa do renderer confiável ao Electron main por IPC, que usa o subprocesso
`ordax_studio.electron_account_session` **já pertencente ao Runtime**.
A conta é validada no Product ControlPlane, e os comandos usam exclusivamente
o `ProductRuntime` do Apps contra a API Product HTTPS com recibos,
identidade, grants e auditoria oficiais. O preview e ChatGPT Web nunca recebem
token, stdin de Python, acesso direto a arquivos ou novo executor local.

O teste de apresentação Windows prova um pacote com usuário/dispositivo
**simulados** e não faz login real na conta do usuário. Uma instalação
física deve ocorrer somente após a autenticação, o emparelhamento do
dispositivo e as provas de release.

## Entrada oficial Studio Central — 2026-10-11

O instalador oferece **ORDAX Studio** e **ORDAX Central** no menu Iniciar.
Ambos apontam para o mesmo supervisor assinado `{app}\\ORDAX Studio.exe`;
o atalho Central apenas solicita `--ordax-central` e não inicia outro
Runtime, cria credenciais ou concede grants. O supervisor transmite esse
sinal exclusivamente ao Electron canônico em `presentation`, mantendo o
processo notificante no mesmo Windows Job Object. Quando o Studio já está
aberto, o segundo launcher sinaliza o supervisor existente por um evento
Windows local de sessão; o Electron encaminha a navegação por seu lock de
instância única. Se o renderer ainda não está pronto, o pedido fica
pendente na memória do próprio Electron e é entregue após o carregamento.

A smoke do instalador testa atalho instalado, pedido Central com Studio
aberto e entrada Central a frio (argumento no processo Electron real,
janela visível e inventário intacto). Não confundir essas provas de
navegação com login Product/Relay real, múltiplas conversas de IA ou
instalação assinada no computador do usuário; tais critérios continuam
pendentes e sob os owners existentes.
