# Studio — ChatGPT Web no chat nativo (experimento isolado)

**Objetivo:** usar o chat nativo do OrdaX Studio, sem transformar o Codex em UI ou motor obrigatório. O plugin remoto `ORDAX for ChatGPT` e o navegador WebView2 permanecem independentes.

## Estado real de 2026-10-08

- O DEV em `Desktop/ORDAX-Studio-DEV` contém UI de chats nativos, botão Enviar associado ao novo método `assistant_send_message`, armazenamento existente do `OrchestratorStore` e fallback no navegador.
- O módulo `ordax_studio/dev_chatgpt_bridge.py` **não automatiza a página web**, não lê cookies nem credenciais e nunca recebe acesso genérico às ferramentas. Ele só faz solicitações de texto a um serviço HTTP local explicitamente configurado, compatível com Responses.
- Porta: variável de ambiente `ORDAX_STUDIO_DEV_CHAT_BRIDGE_URL`, formato estrito `http://127.0.0.1:PORTA/v1`. Nunca permite URL externa ou redirecionamento. Token opcional no ambiente privado do host, jamais na UI.
- Inicialização guardada por `ORDAX_STUDIO_DEV_NATIVE_CHAT=1`; sem essa variável, a interface do Runtime é a canônica existente e não ativa o experimento.
- Modelos só são ofertados como enviados quando `GET /v1/models` retorna slugs `chatgpt-web/`; sem ponte online, sem confirmação de sessão, não declarar chat pronto.
- O envio escolhe apenas o modelo exato selecionado, não faz fallback e grava as mensagens no histórico existente **após** obter resposta válida. Isola por projeto e conversa; não envia arquivos nem comandos.
- **6/6 testes de bridge local** aprovados no Windows com servidor HTTP simulado. Esses testes **não** acessaram modelo real.
- Instaladas dependências da referência MIT `miuuyy/codex-chatgpt-web` em uma cópia isolada. O launcher do navegador da referência falhou na inicialização com `Browser idle document did not commit within 10000ms`, sem alcançar uma sessão ChatGPT autenticada. Portanto, **não houve envio real à conta ChatGPT**.
- O canal Browser UI/WebView2 visível segue disponível; não representa backend de chat nativo.

## Limitações e requisitos de release

Esta integração **não é oficial** e usa um projeto cuja automação de navegador é sujeita a mudanças e aos termos da OpenAI. **Não liberar em produção nem anunciar suporte comercial** sem caminho autorizado, teste E2E com login consentido, validação de conta/modelos, cancelamento, streaming, limpeza de contexto, isolamento de tokens e revisão de segurança. O projeto tem direito de solicitar uso humano da página web pelo navegador separado.

## Owners e SSOT

- Chat UI: `ordax-apps/apps/studio`, não duplicar shell.
- Estado e ações: `ordax-runtime` e `OrchestratorStore` existentes; não criar nova base de conversas.
- Navegador: host WebView2 separado do documento privilegiado do Studio.
- Plugin externo: `ordax-platform/plugins/ordax-chatgpt`, independente do provedor do chat nativo.
- Em produção, o Runtime deve usar contratos tipados, consentimento, observabilidade e grants canônicos.
