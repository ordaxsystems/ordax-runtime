"""Isolated ORDAX Studio DEV bridge to a manually authorized localhost Responses server.

This is NOT an official ChatGPT API or a ChatGPT session reader. The host does
not automate DOM/cookies; it uses an explicitly configured local adapter.
The bridge is dev-only, disabled unless ORDAX_STUDIO_DEV_CHAT_BRIDGE_URL is set.
"""
from __future__ import annotations
import os
from typing import Any
from urllib.parse import urlparse

import httpx
from .product_web_desktop import StudioProductApi


class DevBridgeError(Exception):
    pass


def _endpoint() -> str | None:
    raw = os.environ.get("ORDAX_STUDIO_DEV_CHAT_BRIDGE_URL", "").strip().rstrip("/")
    if not raw:
        return None
    parsed = urlparse(raw)
    if (
        parsed.scheme != "http" or parsed.hostname != "127.0.0.1"
        or parsed.username or parsed.password or parsed.query or parsed.fragment
        or parsed.path not in ("", "/", "/v1")
        or not parsed.port or not (1024 <= parsed.port <= 65535)
    ):
        raise DevBridgeError("O conector DEV aceita apenas http://127.0.0.1:PORTA/v1")
    return f"http://127.0.0.1:{parsed.port}/v1"


def _http() -> httpx.Client:
    # trust_env=False prevents HTTP(S)_PROXY from forwarding project data.
    # Token belongs to host environment, not the UI or to message history.
    token = os.environ.get("ORDAX_STUDIO_DEV_CHAT_BRIDGE_TOKEN", "").strip()
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    return httpx.Client(timeout=httpx.Timeout(75, connect=2), trust_env=False,
                        follow_redirects=False, headers=headers)


def _model_catalog() -> list[dict[str, str]]:
    base = _endpoint()
    if not base:
        return []
    try:
        with _http() as client:
            response = client.get(base + "/models", timeout=2)
            response.raise_for_status()
            rows = response.json().get("data")
    except (OSError, httpx.HTTPError, ValueError, TypeError):
        return []
    if not isinstance(rows, list):
        return []
    output = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        model_id = row.get("id")
        if isinstance(model_id, str) and model_id.startswith("chatgpt-web/") and len(model_id) <= 120:
            output.append({"id": model_id, "label": str(row.get("name") or model_id)[:120]})
    return output[:24]


def _response_text(data: dict[str, Any]) -> str:
    if isinstance(data.get("output_text"), str) and data["output_text"].strip():
        return data["output_text"].strip()
    chunks = []
    for item in data.get("output", []):
        if not isinstance(item, dict) or item.get("type") != "message" or item.get("role") != "assistant":
            continue
        for part in item.get("content", []):
            if not isinstance(part, dict):
                continue
            if part.get("type") == "output_text" and isinstance(part.get("text"), str):
                chunks.append(part["text"])
    text = "".join(chunks).strip()
    if not text:
        raise DevBridgeError("O provedor não retornou uma resposta de texto válida.")
    if len(text) > 200_000:
        raise DevBridgeError("Resposta excede o limite de armazenamento do Studio.")
    return text


class StudioDevChatApi(StudioProductApi):
    """Expose a *typed* send operation reusing the canonical assistant store."""

    def _assistant_defaults(self) -> tuple[str, str, str]:
        models = _model_catalog()
        return ("chatgpt-web-dev", "chatgpt-web", models[0]["id"] if models else "chatgpt-web/auto")

    def assistant_catalog(self) -> dict[str, Any]:
        base = super().assistant_catalog()
        if not base.get("ok"):
            return base
        data = dict(base.get("data") or {})
        providers = list(data.get("providers") or [])
        accounts = list(data.get("accounts") or [])
        models = _model_catalog()
        is_online = bool(models)
        accounts.append({"id": "chatgpt-web-dev", "label": "ChatGPT Web (DEV)", "connected": True})
        if not models:
            models = [{"id": "chatgpt-web/auto", "label": "Aguardando conexao ChatGPT Web"}]
        providers.append({
            "id": "chatgpt-web", "label": "ChatGPT Web (experimental)",
            "models": [{"id": model["id"], "label": model["label"],
                        "session_available": is_online, "can_send": is_online} for model in models],
        })
        data["accounts"] = accounts
        data["providers"] = providers
        data["send_supported"] = is_online
        data["send_summary"] = (
            "Ponte local ChatGPT Web disponível para teste; login e envio real ainda precisam ser validados."
            if is_online else
            "ChatGPT Web ainda não conectado. Abra o conector DEV e autentique-se no navegador. O Studio não usa cookies do Chrome."
        )
        base["data"] = data
        return base

    def assistant_send_message(self, conversation_id: str, text: str) -> dict[str, Any]:
        """One bounded text-only turn; no provider tool access or fallback model."""
        prompt = str(text or "").strip()
        if not (1 <= len(prompt) <= 25_000):
            return {"ok": False, "code": "invalid_prompt", "summary": "Mensagem vazia ou longa demais."}
        try:
            conversation = self.orchestrator.assistant_conversation(str(conversation_id))
            if conversation["project_slug"] != self.project or conversation.get("archived_at"):
                raise DevBridgeError("Chat não pertence ao projeto ativo.")
            if conversation["provider_id"] != "chatgpt-web" or conversation["account_id"] != "chatgpt-web-dev":
                raise DevBridgeError("Selecione uma conversa com ChatGPT Web · DEV.")
            models = {row["id"] for row in _model_catalog()}
            model = str(conversation["model_id"])
            if model not in models:
                raise DevBridgeError("Modelo não está disponível no conector autenticado.")
            history = self.orchestrator.assistant_messages(str(conversation_id), limit=30)
            # Bounded, non-executable conversation context. No project files or
            # tools are sent implicitly, even if available in OrdaX Runtime.
            inputs = [
                {"role": item["role"], "content": [{"type": "input_text", "text": item["content"][:8000]}]}
                for item in history[-16:] if item.get("role") in ("user", "assistant")
            ]
            inputs.append({"role": "user", "content": [{"type": "input_text", "text": prompt}]})
            with _http() as client:
                response = client.post(
                    _endpoint() + "/responses",
                    json={"model": model, "input": inputs, "stream": False, "store": False, "tools": []},
                )
                response.raise_for_status()
                body = response.json()
            if not isinstance(body, dict):
                raise DevBridgeError("Formato da resposta inválido.")
            answer = _response_text(body)
            self.orchestrator.add_assistant_message(str(conversation_id), role="user", content=prompt)
            self.orchestrator.add_assistant_message(str(conversation_id), role="assistant", content=answer)
            return {"ok": True, "summary": "Resposta recebida do conector", "data": {"conversation_id": conversation_id}}
        except DevBridgeError as error:
            return {"ok": False, "code": "chatgpt_web_dev_unavailable", "summary": str(error)}
        except (httpx.HTTPError, OSError, ValueError) as error:
            # Do not surface the daemon's error body, potentially containing tokens,
            # project context or account details.
            return {"ok": False, "code": "chatgpt_web_dev_transport", "summary":
                    "Conector ChatGPT Web indisponível. Verifique sessão, permissões e o processo local."}
