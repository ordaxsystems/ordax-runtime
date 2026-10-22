# Product Runtime: compatibilidade com a Independent Base do OrdaX

A compilação isolada de CPython **3.14.7** pelo Buildroot musl foi comprovada no
OrdaX OS, PR [#1860](https://github.com/ordaxsystems/ordax-os/pull/1860),
workflow [#39670242743](https://github.com/ordaxsystems/ordax-os/actions/runs/39670242743).
O recibo confirma 56 ELF e execução no kernel Linux do runner com imports
SSL, zlib e JSON confinados ao componente. Não comprova que o
`ordax-product-agent` completo esteja instalado ou executável.

O lock oficial `native/ordax-product-agent/requirements.lock` foi **resolvido
para Python 3.12**, enquanto a ABI do componente independente atual é
**CPython 3.14**. Isso não pode ser corrigido importando wheels do Alpine ou
substituindo pacotes por versões arbitrárias do Buildroot.

## Fonte única e prova sem rede

O script `tools/ci/audit_buildroot_lock.py` compara os locks **originais do
Runtime** com os arquivos `package/python-*/Config.in` e `.mk` da árvore
**exata do Buildroot 2026.08 que o OrdaX OS já baixou e verificou pelo
SHA-256**. Não carrega uma cópia das versões upstream, não executa Python
do target, não instala pacotes e não modifica o source tree.

```sh
python3 tools/ci/audit_buildroot_lock.py \
  --buildroot /caminho/do/buildroot-2026.08/verificado

# Gate estrito: retorna código 1 enquanto houver desvios.
python3 tools/ci/audit_buildroot_lock.py \
  --buildroot /caminho/do/buildroot-2026.08/verificado \
  --require-compatible
```

Além da diferença Python 3.12/3.14, diferenças comprovadas nos fontes
Buildroot **2026.08** são:

| Pacote | Runtime lock | Buildroot |
|---|---:|---:|
| httpx | 0.28.1 | 0.28.1 |
| websockets | 15.0.1 | 16.0 |
| Pillow | 11.3.0 | 12.0.0 |
| cryptography | 50.0.2 | 48.0.0 |
| pydantic-core | 2.50.1 | 2.46.3 |
| cffi | 2.1.1 | 2.0.0 |
| MCP SDK | 1.30.0 | ausente como `python-mcp` |

O resultado guarda o hash do `requirements.lock` e do `bootstrap.lock`,
reporta cada versão ausente/incompatível e **verifica todas as entradas**
do lock, inclusive pacotes compartilhados com ferramentas de build. A simples
presença de um pacote em `bootstrap.lock` não prova que ele seja dispensável
em execução; esses nomes são informados separadamente como
`build_tool_overlap_names`, sem excluí-los. O auditor preserva explicitamente `native_dependency_closure_proven=false`,
`runtime_agent_packaged=false` e `device_install_authorized=false`.

Regras para o próximo passo: recompilar dependências nativas com a toolchain
Buildroot selecionada pelo OS; providenciar uma receita Buildroot canônica
para o MCP SDK e demais pacotes ausentes, com fontes/versões/hashes explícitos;
reconciliar a resolução do lock para Python 3.14; auditar todo ELF/`DT_NEEDED`
e executar o **mesmo Runtime canônico** antes de assinar e instalar.
O contrato de ABI auditado não autoriza outra distribuição Linux, runtime,
fila, token, heartbeat ou supervisor.
