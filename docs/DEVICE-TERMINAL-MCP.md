# OrdaX MCP — terminal vinculado ao dispositivo

## Escopo e SSOT

O comando `computer_terminal_exec` executa no dispositivo autorizado sem exigir
Space, projeto GitHub ou slug local. É uma capacidade **distinta** da já
existente `terminal_exec`, que continua restrita a projetos versionados.

- `computer.terminal_exec`: escopo **device**; perfil Owner
  `device-native-terminal` ou `full-computer-control`; política local
  `enabled=true` e `full_access=true` obrigatórias.
- `terminal.exec`: escopo **project**; mantém o vínculo obrigatório de
  projeto/repositório, com o perfil `project-native-terminal`.
- O Control Plane (PostgreSQL + catálogo da Platform) é autoridade final
  de consentimento, grants, revogação e modos read/write. O Runtime valida
  novamente a política local e as entradas, sem se autorizar sozinho.
- Ambos reutilizam `DeveloperActions._run_terminal_payload` e o executor
  `process_runner.run_command`: limites de argumentos, ambiente e timeout
  de 1 a 1800 segundos; stdout/stderr limitados; encerramento do processo
  e descendentes em timeout, sem retornar argv ou variáveis de ambiente.
- Diretório padrão no modo device: home do usuário do Runtime. `cwd`
  explícito deve ser absoluto e um diretório existente; caminhos relativos
  não serão convertidos em projetos sintéticos.
- Por definição, terminal com consentimento do Owner roda com permissões
  do usuário do sistema operacional. O usuário deve compreender que scripts
  podem modificar quaisquer recursos aos quais esse usuário possua acesso.
- O novo grant não está implícito em `computer-filesystem`, em permissões
  apenas de observação ou no vínculo de projeto.

## Gates

1. Ações de projeto não perdem as suas validações de repository binding.
2. Requisição sem grant não entra na fila de execução.
3. Escopo device exige `project=null` e `space_id=null`.
4. Assinatura/capabilities do Runtime precisam concordar com Platform.
5. Em produção, só habilitar após atualizar Runtime e publicar Platform,
   obter consentimento Owner e provar execução pelo próprio MCP.
6. Não modificar o instalador nem o computador por cópia manual.
