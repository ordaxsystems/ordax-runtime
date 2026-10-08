# Studio host adapter — source canônico

A interface portátil e os manifestos do **OrdaX Studio** pertencem exclusivamente a `ordaxsystems/ordax-apps/apps/studio`.

Este pacote `ordax_studio` contém apenas APIs/transportes/adaptadores necessários ao **OrdaX Runtime Windows**, não o source da UI. O pipeline `scripts/windows/build-ordax-studio-product.ps1` exige o checkout exato de `studio-source.lock.json` e materializa HTML/assets apenas dentro do `site-packages/ordax_studio` privado do instalador.

Não existe fallback para UI histórica, `latest`, cópia no checkout Runtime, second source ou iframe. A interface instalada deve funcionar offline a partir do pacote versionado. Executar `ordax-studio-web` diretamente no source não inicia uma UI obsoleta e informa o launcher correto. Preservar o host-bridge, grants e MCP stdio necessários ao Runtime.
