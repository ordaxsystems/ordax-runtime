# Product MCP — transferência atômica de arquivos

O Runtime é o owner das ações de transferência e staging. A Platform publica as ferramentas, valida argumentos e resolve OAuth, atestação do dispositivo e grants. Nenhuma destas operações ignora a política local de diretórios.

1. `computer_transfer_begin(path, size_bytes, sha256, create?, expected_sha256?)` reserva uma sessão temporária. Arquivo novo exige `create=true`; destino existente exige SHA-256 atual.
2. `computer_transfer_chunk(transfer_id, offset, text_utf8 | data_base64)` acrescenta blocos de até 48 KiB. Escolher **exatamente um** formato: `text_utf8` para documentos/código, Base64 para binários. Repetição idêntica não duplica bytes; offset divergente é rejeitado.
3. `computer_transfer_status` indica offset para retomada. Sessões expiram após 24 h; no máximo oito sessões ativas por agente.
4. `computer_transfer_commit` valida tamanho, SHA-256 completo e hash original antes de substituir o destino por rename atômico de um temporário na mesma unidade. Nunca publica arquivo parcial.
5. `computer_transfer_abort` descarta staging não confirmado.

Limites: transferência de 32 MiB por sessão; blocos decodificados até 48 KiB, texto UTF-8 até 32.768 caracteres por bloco. O gateway mantém limite global de 1 MiB, os limites específicos de ações e validações de grants. Nunca usar `append` no destino como substituto desta transação. Não ativar publicação pública sem comprovar Worker, grants e Runtime físico compatíveis.
