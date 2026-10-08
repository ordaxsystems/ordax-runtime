# Studio host adapter — source canônico

A interface portátil e os manifestos do **OrdaX Studio** pertencem exclusivamente a `ordaxsystems/ordax-apps/apps/studio`.

Este pacote `ordax_studio` contém apenas APIs/transportes/adaptadores necessários ao **OrdaX Runtime Windows**, não o source da UI. O pipeline `scripts/windows/build-ordax-studio-product.ps1` exige o checkout exato de `studio-source.lock.json` e materializa HTML/assets apenas dentro do `site-packages/ordax_studio` privado do instalador.

Não existe fallback para UI histórica, `latest`, cópia no checkout Runtime, second source ou iframe. A interface instalada deve funcionar offline a partir do pacote versionado. O comando console histórico `ordax-studio-web` não é mais distribuído. O script Windows `scripts/windows/ordax-studio-start.ps1` inicia somente o launcher do produto instalado, sem executar Python da working tree. Preservar o host-bridge, grants e MCP stdio necessários ao Runtime.

O shell experimental Tkinter (`ordax_studio.desktop`) e o instalador-source `scripts/windows/ordax-studio-install.ps1` foram aposentados; não criar atalhos baseados em clone Git/Python para o produto. A instalação e o upgrade pertencem exclusivamente ao instalador Inno Setup de `ordax-runtime`, com UI importada de `ordax-apps` pelo commit fixo. A remoção não afeta os comandos CLI de diagnóstico, MCP stdio, APIs de projetos, nem os mecanismos de migração segura de instalações históricas.
