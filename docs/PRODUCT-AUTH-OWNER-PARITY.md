# Paridade da configuração pública de autenticação Product

**Owner SSOT**: `ordaxsystems/ordax-platform/control-plane/cloudflare/wrangler.toml`
em `[vars]`, que publica `PRODUCT_AUTH_ISSUER`,
`PRODUCT_AUTH_AUDIENCE`, `PRODUCT_AUTH_JWKS_URL` e
`SUPABASE_PUBLISHABLE_KEY`.

Um default anterior no `ordax_studio.product_auth` usava um
**outro projeto Supabase** e sua chave pública. A Conta OrdaX podia
autenticar no projeto equivocado, mas o token resultante ser rejeitado
por `/v3/product/session` devido a issuer/JWKS diferentes. Alterar a
interface do Studio não corrige esta incompatibilidade de identidade.

A correção concentra o snapshot público consumido pelo Runtime em
`ordax_studio/public_auth_metadata.py`. `product_auth._auth_config()`
passa a ler somente esse snapshot como default; **sem** segundo
autenticador, segredo, grant ou URL de produção embutida em outra
camada. Variáveis explícitas de provisionamento continuam permitidas
com validação HTTPS.

`ORDAX Runtime Contracts` faz checkout temporário da **revisão imutável
do owner Platform fixada em `control-plane-source.lock.json`**, a mesma
referência usada pelo empacotamento Windows. O pin passa pela validação
compartilhada de `ordax_dev_agent.control_plane_provenance` e o
teste Windows exige que o HEAD real do checkout e a versão da fundação
sejam idênticos aos do lock; ref móvel `main` não serve como prova
de compatibilidade do pacote. O teste compara **issuer, audience,
JWKS URL e publishable key reais** com o snapshot entregue e falha em
divergência, sem aceitar comparação por hostname isolado.

Esse é um **contrato de dependência versionável**, não transferência
de autoridade do Platform para Runtime. Quando o Platform alterar
issuer/key, a mudança deve ser coordenada: atualizar o único lock
consumidor do Runtime para o commit aprovado e revalidar a Runtime CI
antes de empacotar o app. A evolução independente de `Platform/main`
não altera silenciosamente uma revisão já pinada pelo Runtime. O
resultado de CI não comprova por si só que o deploy remoto aplicou
aquela configuração. A PR de entrada da sessão Electron usa
`sign_in_with_password` e `ProductRemoteClient.session` desse mesmo
owner; ela depende dessa correção para autenticação real.

A verificação de código não prova por si só que o deploy Cloudflare de
produção recebeu o mesmo `wrangler.toml`: fazer smoke real de conta,
revogação e dispositivo faz parte da homologação da issue #67. Nenhuma
alteração de tokens anteriores, migração de usuários, pairing ou
publicação foi executada.
