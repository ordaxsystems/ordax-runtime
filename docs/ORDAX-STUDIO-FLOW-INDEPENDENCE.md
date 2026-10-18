# Independência entre OrdaX Studio, Flow e OS

**Decisão (2026-10-17):** OrdaX Studio e OrdaX Flow são aplicativos autônomos. Não são componentes do `ordax-os`, não exigem esse sistema operacional e não devem depender de seu boot, código interno, instalação ou atualização. Remover qualquer produto não afeta os demais.

## Studio — conta e trabalho local

O Studio conecta o usuário ao ChatGPT por integração MCP oficial, usando **a conta ChatGPT do próprio usuário**, quando aplicável. O Runtime age somente nos dispositivos autorizados e concede acesso a arquivos, projetos, Git, processos e ferramentas mediante grants específicos, confirmação para ações sensíveis e auditoria. Não usa credenciais de uma conta ChatGPT central do Flow.

Histórico e memória são associados ao **projeto OrdaX** com checkpoints, proveniência, decisões e pendências, não a uma única conversa do ChatGPT. O Studio consome os contratos de identidade, permissão e memória da OrdaX Platform / Intelligence, sem criar um segundo banco ou sincronizador concorrente. A recuperação entre chats deve ser feita através de MCP autorizado, sem automação furtiva nem leitura oculta de chats.

## Flow — independente do Studio

Flow é serviço multiusuário com conta OrdaX, cotas individuais e IA da central via **API autorizada**, sem login próprio ChatGPT. Identificação e isolamento por usuário+projeto+requisição. Flow e Studio não importam códigos uns dos outros e não compartilham banco interno; integração somente via APIs públicas e autorização explícita.

SSOT de implementação do Flow: https://github.com/ordaxsystems/ordax-flow (privado). SSOT de contratos compartilhados: OrdaX Platform. Este arquivo documenta somente os limites do Studio, sem duplicar esquemas ou módulos de identidade.