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

## Processos persistentes de dispositivo

O Runtime reutiliza o supervisor persistente canônico (`PersistentProcessActions`)
para os seis comandos `computer_process_{start,status,list,logs,write_stdin,stop}`.
O estado reside em `state_dir/device-processes/<device_id>`, **nunca** no
diretório `processes/<project>`, e cada recibo mantém a identidade do
dispositivo, verificada na leitura. Nenhum projeto GitHub é criado.

- Perfil Owner específico: `device-native-process`, diferente de
  `device-native-terminal` e de `project-native-process`.
- Política local obrigatória: `enabled && full_access`.
- O Control Plane atestado exige UUID, cliente/Owner vinculados, grant atual,
  `project=null` e operações limitadas antes da fila canônica.
- O supervisor continua responsável pelo processo filho, token de propriedade,
  handshake, logs persistentes, stdin limitado, parada cooperativa e retomada
  após reinício. Processos arbitrários do sistema ficam fora deste namespace.
- `process.*` continua usando o estado antigo por projeto sem migração
  implícita e sem herdar grants de dispositivo.
- CWD omitido usa a home do usuário Runtime. Um CWD explícito não-padrão deve
  ser absoluto e existente. A raiz de estado e a identidade do Runtime nunca
  são inferidas do argumento enviado pelo cliente.
- O CLI Owner faz o pré-voo do Full Access; somente a Platform pode efetivar
  grants mediante OAuth e consentimento humano.

A disponibilização ao usuário final requer CI das duas PRs, Runtime instalado
pelo mecanismo oficial, atualização do catálogo MCP no cliente e prova E2E
na própria conexão OrdaX. Nenhuma PR ativa grants por conta própria.

## Diagnóstico de compatibilidade Runtime (v1.31.0)

A ação já autorizada `computer_access_status` retorna `runtime_capabilities`
com `agent_version` e uma lista de `implemented_actions` obtida diretamente
pelo `ActionRegistry`, sem checar GitHub, Studio ou Space. Essa lista representa
**somente implementação local**: não representa grants OAuth, nem autorização
para executá-las, nem disponibilidade das ferramentas no catálogo carregado
pela conversa ChatGPT. A ausência da ação revela Runtime desatualizado.

Identidade do agente incrementada de 1.30.3 para 1.31.0; a versão de produto
Windows e a revisão do catálogo MCP continuam indicadores **independentes**,
não devem ser confundidos com a versão do executor instalado.
