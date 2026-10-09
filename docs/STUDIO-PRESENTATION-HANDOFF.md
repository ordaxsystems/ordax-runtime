# Handoff verificado do Studio Electron (candidato, sem promoção)

Owner de interface, conversas e CSS: **`ordaxsystems/ordax-apps`**.
Owner de Runtime, instalador, upgrade, origem confiável da execução e permissões:
**`ordaxsystems/ordax-runtime`**.

A UI do Studio atualmente distribuída pelo instalador Inno Setup deste
repositório é `apps/studio/src/index.html`, via host WPF/WebView2 e
`studio-source.lock.json` no release. O Studio Conversation 0.14.1 foi
implementado e testado em `ordax-apps/apps/studio/conversation` com host
Electron no `tools/assistant-host`. Esses dois hosts não são uma mesma
interface nem há paridade demonstrada entre suas pontes para o Runtime.

## O que está implementado

A workflow `Studio Canonical Presentation Handoff` executa em um Windows
limpo com **duas revisões de origem separadas**:

1. O checkout fixo da própria PR do Runtime fornece **somente** o script de
   verificação `verify-studio-presentation-handoff.ps1`.
2. Um checkout independente do `ordaxsystems/ordax-apps/main` fornece **um**
   snapshot Git, cujo commit imutável é capturado no log antes do build. Esse
   checkout é um **candidato de compatibilidade**, não uma atualização do
   lock de release.
3. O script verifica que os manifests `app.json`, `ai/manifest.json`,
   `actions/manifest.json` e o pacote do host têm a **mesma versão**,
   controlada exclusivamente por `apps/studio/app.json`. Exige árvore de
   código limpa. Usa o lock do npm, `setup:native` e o **builder existente**
   `build:windows`; não reimplementa nem renomeia o Electron no Runtime.
4. O verificador independente do Apps valida o pacote inicial. O Runtime copia
   esse pacote para **uma pasta temporária isolada** e confere **novamente**
   SHA-256 de cada arquivo com inventário exato, entrada de Electron,
   proprietário, versão, campos de candidato e ausência de segundo Runtime.
   Qualquer desvio reprova o teste.
5. Imprime `ORDAX_STUDIO_PRESENTATION_HANDOFF=PASS` e
   `ORDAX_STUDIO_PRESENTATION_RELEASE=NOT_PROMOTED`. Não há upload do pacote,
   alteração do instalador em produção, assinatura, DNS, perfil, conta,
   execução na máquina do usuário ou downgrade do frontend canônico.

Assim fica validado que o **pipeline consumidor** consegue buscar, montar e
verificar os bytes da UI de conversa do owner Apps, sem manter cópia fonte.

## O que ainda falta para o instalador oficial — bloqueio #67

- Decidir a transição do launcher/host nativo: instalar apenas **um** host de
  apresentação e preservar o Runtime próprio. Não embarcar Workbench WPF e
  Electron como interfaces concorrentes nem sacrificar grants/execução local.
- Adaptar a ponte do Studio Electron ao Runtime local instalado, com identidade
  verificada, perfis/contas, atualização, revogação, preview e prova de que
  não há caminho secundário de execução.
- Alterar **juntos** a versão/revisão do `studio-source.lock.json`, o staging,
  o launcher, a instalação e o smoke **do executável instalado**; não apenas
  atualizar o número da versão. Manter os testes de assinatura, upgrade,
  recuperação/rollback, perfis e Runtime persistente.
- Executar um smoke de sessão real ChatGPT, acesso ao computador autorizado,
  rede e áudio em Windows/OrdaX OS antes de afirmar homologação completa.

A workflow aqui **não é release gate de publicação** nem ativa o frontend novo
no instalador antigo. Ao fazer cutover, o mesmo fluxo de bytes deve passar a
compor a instalação oficial e o smoke do produto deve verificar que a janela
principal é `conversation/src/index.html` e que o Runtime permanece funcional.
Até lá, o `Windows Product Build` antigo não prova que distribui o Studio
0.14.1; ele comprova somente a sua revisão travada 0.5.10.
