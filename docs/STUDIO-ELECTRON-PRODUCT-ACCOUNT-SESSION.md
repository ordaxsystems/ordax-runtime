# Sessão da Conta OrdaX no Studio Electron — OAuth de navegador v2

A Conta OrdaX no Studio Windows usa **OAuth 2.1 Authorization Code + PKCE no navegador padrão**. O Studio não contém formulário de senha OrdaX e não deve voltar a implementar login por e-mail/senha no renderer, IPC ou host Electron.

Fluxo canônico:

`Studio → Runtime privado → navegador padrão → Supabase OAuth/consentimento OrdaX → callback loopback → Runtime → enrollment/pairing → Studio`

Se o navegador já possui uma sessão OrdaX válida, a página web deve aproveitar essa sessão e apresentar somente a etapa de continuar/autorizar. Se não possui, o login acontece **na página web**, nunca dentro do Studio.

## Cliente desktop

O cliente first-party é público e não possui segredo:

- nome: `ORDAX Studio`;
- client id público: `202fd3e1-b058-4531-8750-82c57e4b6ca0`;
- redirect URI exata: `http://127.0.0.1:8766/oauth/callback`;
- grants: `authorization_code`, `refresh_token`;
- scopes iniciais: `openid email offline_access`;
- `token_endpoint_auth_method=none`;
- PKCE: `S256` obrigatório pelo cliente.

O client id e a redirect URI são metadata pública e ficam em `ordax_studio/public_auth_metadata.py`. Nenhum `client_secret` deve ser criado, empacotado ou solicitado para o aplicativo desktop.

## Fronteira de segurança

`ordax_studio.electron_account_session` é uma entrada single-shot exclusiva do Python privado instalado. O processo principal Electron envia somente:

```json
{"schema":"ordax.studio-product-account-session/1","operation":"browser-sign-in"}
```

E-mail e senha são campos inválidos nesse protocolo. O renderer expõe apenas uma ação sem argumentos para iniciar a conexão.

O Runtime privado:

1. gera `state` criptograficamente aleatório e `code_verifier` PKCE;
2. deriva `code_challenge` SHA-256;
3. abre o navegador padrão no authorize endpoint oficial;
4. escuta somente `127.0.0.1:8766` e somente `/oauth/callback`;
5. exige `state` exato e código único;
6. troca o código diretamente no token endpoint usando o `code_verifier`, sem segredo;
7. confirma o subject com `/v3/product/session` e conserva a origem Product que respondeu ? valida??o;
8. registra o dispositivo, quando necess?rio, pelo `/v3/device/setup` can?nico;
9. cria e reivindica o pairing pelo fluxo Product existente;
10. devolve ao processo principal Electron a origem Product validada junto com a identidade do dispositivo, para que chamadas Owner posteriores n?o inventem nem infiram outro host.

O callback loopback devolve apenas uma página estática informando para voltar ao Studio. Possui CSP restrita e não carrega scripts ou recursos externos.

## Token e grants

O access token fica somente em memória. Ele cruza apenas o pipe privado `stdout` do Runtime filho para o processo principal Electron para alimentar o cliente Product já existente. Não deve aparecer no renderer, ChatGPT Web, preview, logs, argv, URL, storage ou diagnósticos.

Login, enrollment e pairing **não criam grants de Computer Control**. Ações remotas continuam condicionadas aos grants e políticas existentes. `supports_remote_actions=false` no MCP canônico não é alterado por este fluxo.

## Compatibilidade

`sign_in_with_password()` permanece apenas como compatibilidade interna para fluxos antigos/testes que ainda possuam owner explícito. O Studio Electron instalado não usa esse método. Novo código de UI não pode depender dele.

O fluxo fail-closed quando o callback está ocupado, o navegador não abre, o state diverge, o código expira, o OAuth é negado, o Product subject não é canônico, o enrollment falha ou o pairing é recusado.

## Testes obrigatórios

- `test_studio_browser_oauth.py`: PKCE S256, state, callback loopback, ausência de client secret e troca do código;
- `test_studio_electron_account_session.py`: protocolo sem senha, subject Product, enrollment e framing privado;
- `test_studio_product_account.py`: pairing e sessão browser sem password grant;
- Apps `canonical-account.test.mjs`: renderer/IPC não aceitam credenciais e o token não chega à UI;
- Apps `central.test.mjs`: UI não contém `productEmail`/`productPassword`.

O instalador Windows só deve promover esta mudança após o source lock do Studio apontar para o merge correspondente e os contratos Runtime/Windows Product Build estarem verdes.
