# ORDAX Studio — produto instalado

## Direção atual

O **ORDAX Studio** é a superfície de produto para projetos, ferramentas, dispositivos e capabilities do ORDAX. Ele é provider-neutral: não incorpora um segundo ChatGPT, não depende de Codex e não cria variantes de Runtime por provedor.

No Windows, o Studio usa um host persistente `ORDAX Runtime.exe`. ChatGPT, Grok e outros clientes autorizados entram por conectores na borda do produto e reutilizam o mesmo Control Plane, grants e capacidades tipadas.

```text
ChatGPT / Grok / outro cliente autorizado
              |
              | conector/protocolo autenticado
              v
ORDAX Control Plane (Cloudflare)
              |
              | conexão de saída persistente
              v
ORDAX Runtime no Windows
              |
              +-- projetos / arquivos / Git / preview
              +-- Computer Control / processos
              +-- Blender / Unity / adapters
```

O app Windows mostra projetos, status, memória/checkpoints, Git, arquivos, preview, Computer Control, adapters e a saúde da conexão. O **ORDAX Runtime** inicia com o Windows e continua online mesmo com a janela fechada.

O boundary de conectores está em [ORDAX_PROVIDER_CONNECTORS.md](ORDAX_PROVIDER_CONNECTORS.md).

## Runtime headless e shell desktop

O Device Agent e o ActionRegistry formam um núcleo **headless**. Eles não dependem de `pywebview` nem da shell gráfica do Windows para carregar ou executar capabilities tipadas.

A dependência `pywebview` pertence ao extra Python `desktop`. O instalador Windows instala esse extra explicitamente e continua validando a shell gráfica no smoke de produto.

Essa separação prepara o produto para o OrdaX OS sem criar dois Studios:

- **Windows:** Studio consome `ORDAX Runtime`, porque o Windows não fornece os services/ports da plataforma OrdaX;
- **OrdaX OS:** `studio` é um app first-party que consome os ports públicos da plataforma e não empacota uma segunda cópia de Identity, Memory, Intelligence, permissions, sync, updater ou trust.

O destino arquitetural do app está em `washingtonmsdj/ordax-apps/docs/STUDIO-BOUNDARY.md`. O source atual não deve ser copiado para `ordax-apps` antes dos gates de package/lifecycle e do cutover de fonte de verdade.

## Nome do produto e compatibilidade Windows

A identidade pública do app é **ORDAX Studio**.

O launcher Windows existente `ORDAX Dev.exe` e o nome atual do instalador permanecem, por enquanto, como superfície de compatibilidade com instalações anteriores. Eles não definem uma arquitetura `Dev` separada e não justificam dependências de Codex. A migração física para `ORDAX Studio.exe` é acompanhada separadamente para preservar upgrade/uninstall, AppId, shutdown cooperativo e testes de instalação real.

## Sem licença/assinatura nesta etapa

A versão atual não possui plano pago, licença ou gate de assinatura. O vínculo de Conta ORDAX é autenticação/vínculo de dispositivo quando necessário ao acesso remoto; ele não conecta automaticamente um provedor de IA e não desbloqueia tiers de IA.

## Conectores externos

O conector atual do ChatGPT se chama **ORDAX for ChatGPT** e sua fonte fica em `plugins/ordax-chatgpt/`. Ele aponta para o MCP de produção:

`https://ordax-control-plane-v3.ordax-ac1ca1b50d09.workers.dev/mcp`

O conector é uma integração de provider, não o núcleo do Studio. Um futuro **ORDAX for Grok**, Claude, Gemini ou outro conector deve reutilizar o mesmo boundary de ação e autorização em vez de duplicar capacidades locais.

Provider/product identity pode ser registrada como metadata autenticada para auditoria, revogação e UX, mas não concede autoridade por si só.

## Regras

- Studio e Runtime são provider-neutral;
- o MCP/protocolo ORDAX é cliente-neutro;
- nenhuma UI de chat é necessária no host;
- nenhum Browser Companion é necessário;
- nenhum navegador privado é instalado pelo ORDAX;
- o Device Agent não depende da janela aberta;
- permissões e auditoria continuam no Runtime/Control Plane;
- provider connectors não podem contornar grants ou policy local;
- Blender e Unity são adapters do mesmo host, não produtos separados;
- Codex não possui papel estrutural e, se suportado, é apenas outro cliente autorizado.

## Modos de IA e cotas

O Studio não possui um segundo cérebro, memória ou roteador independente do OrdaX OS. O contrato canônico de IA está em [ORDAX_INTELLIGENCE_MODES.md](ORDAX_INTELLIGENCE_MODES.md).

Regras de cota/custo pertencem ao conector/modo específico do provedor e devem ser apresentadas antes da ativação quando aplicável. Nenhum modo pago/limitado pode ser habilitado silenciosamente como fallback.

Conta ORDAX, assinatura ORDAX, conexão de provider e consumo de IA são conceitos separados.
