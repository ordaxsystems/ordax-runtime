# Administracao Owner pelo Runtime (sem Studio)

O OrdaX MCP nao pode conceder a si mesmo novas permissoes. O administrador
autenticado utiliza o cliente de linha de comando do Runtime, que chama apenas
a autoridade canonica do Control Plane.

## Requisitos

- Node.js 20+ no computador administrador (CLI de desenvolvimento).
- Computador previamente vinculado a conta OrdaX.
- Um cliente MCP com consentimento OAuth ativo.
- Autorizacao do proprietario pelo navegador (OAuth PKCE) a cada sessao.
- Nenhum token e persistido ou impresso pelo comando.
- O aplicativo Studio nao precisa estar instalado ou aberto.

## Comandos

```powershell
node scripts/owner-mcp-grants.mjs list --device <DEVICE_UUID>
node scripts/owner-mcp-grants.mjs authorize --device <DEVICE_UUID> --profile full-computer-control --days 30
node scripts/owner-mcp-grants.mjs revoke --device <DEVICE_UUID> --group <GRANT_GROUP_UUID>
```

Se houver varios clientes OAuth autorizados, use `--client <CLIENT_UUID>`.
Perfis project-continuity e project-browser-automation exigem
`--space <SPACE_UUID> --project <PROJECT_UUID>`; acesso direto ao
computador nao exige Space nem projeto GitHub.

O comando usa GET /v3/owner/remote-authority,
POST /v3/owner/remote-grant-groups e
DELETE /v3/owner/remote-grant-groups/{id}. Nao escreve no banco,
nao altera o Runtime local para fabricar grants, nem substitui
os contratos canônicos e a revogacao.

## Limites e evolucao

Este comando usa o cliente OAuth publico de primeira parte registrado atualmente.
O identificador e o callback deverao ser migrados para metadados publicos
compartilhados quando houver registro de cliente OAuth independente do Studio.
Isso NAO exige o aplicativo Studio em execucao, mas preserva o cliente OAuth
legado temporariamente.

Para distribuicao a usuarios sem Node, esta interface deve ser empacotada na
experiencia Owner do Runtime. Nao incluir Node como pre-requisito permanente
de usuarios finais. Alteracoes no pacote dependem de validacao da CI.

Testes: `node --test tests/test_owner_mcp_grants.mjs`.
