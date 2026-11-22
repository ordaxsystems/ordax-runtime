"""Consult OrdaX OS's existing Aura/Intelligence service from the Product Runtime.

This is a scoped Native transport adapter, not a second model, inference engine,
context store, Memory owner, account provider or remote permission authority.
The OS derives the current account/Space and decides which Memory is authorized.
"""
from __future__ import annotations

import json
import re
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request, build_opener

from .desktop_surface_actions import _RejectRedirects, _native_endpoint
from .models import ActionResult

INTENTS = frozenset({"ask", "explain", "summarize", "diagnose"})
REQUEST_SCHEMA = "ordax.desktop-intelligence-request/1"
REPLY_SCHEMA = "ordax.desktop-intelligence-reply/1"
RESPONSE_SCHEMA = "ordax.intelligence-response/1"
ID = re.compile(r"^[a-f0-9]{32}$")
MAX_RESPONSE_BYTES = 135000
MAX_PROMPT_CHARS = 32000


def _request_body(payload: dict) -> dict:
    if type(payload) is not dict or set(payload) - {"prompt", "intent", "maxTokens"}:
        raise ValueError("Only an OrdaX Intelligence prompt, intent and token limit are allowed")
    prompt = payload.get("prompt")
    if type(prompt) is not str or not prompt.strip() or "\0" in prompt:
        raise ValueError("Native Intelligence prompt must be nonempty text")
    prompt = prompt.strip()
    if len(prompt) > MAX_PROMPT_CHARS:
        raise ValueError("Native Intelligence prompt exceeds its bound")
    intent = payload.get("intent", "ask")
    if type(intent) is not str or intent not in INTENTS:
        raise ValueError("Unsupported OrdaX Intelligence intent")
    max_tokens = payload.get("maxTokens", 512)
    if type(max_tokens) is not int or not 1 <= max_tokens <= 2048:
        raise ValueError("Native Intelligence token limit is invalid")
    return {
        "schema": REQUEST_SCHEMA,
        "request": {
            "intent": intent,
            "prompt": prompt,
            # Context is selected by the OS's exact Aura/Identity/Memory ports.
            "context": [],
            "maxTokens": max_tokens,
        },
    }


def desktop_intelligence_respond(payload: dict) -> ActionResult:
    try:
        body = _request_body(payload)
    except (ValueError, TypeError) as error:
        return ActionResult(False, str(error))
    endpoint = _native_endpoint()
    if endpoint is None:
        return ActionResult(False, "Verified OrdaX Native Intelligence transport unavailable")
    url, token = endpoint
    # This URL was first verified by the existing canonical Desktop Surface
    # grant helper. No caller-controlled URL, redirect, proxy or cloud model.
    target = url.removesuffix("/desktop-surface-state") + "/desktop-intelligence-request"
    request = Request(
        target, method="POST",
        headers={"X-OrdaX-Product-Bridge-Token": token,
                 "Accept": "application/json",
                 "Content-Type": "application/json"},
        data=json.dumps(body, separators=(",", ":"), ensure_ascii=False).encode("utf-8"),
    )
    try:
        with build_opener(ProxyHandler({}), _RejectRedirects()).open(
            request, timeout=130.0,
        ) as reply:
            if reply.status != 200 or "application/json" not in reply.headers.get(
                "Content-Type", ""
            ).lower():
                raise ValueError("Native Intelligence result not confirmed")
            raw = reply.read(MAX_RESPONSE_BYTES + 1)
        if len(raw) > MAX_RESPONSE_BYTES:
            raise ValueError("Native Intelligence result too large")
        response = json.loads(raw.decode("utf-8"))
        if (type(response) is not dict or
            set(response) != {"schema", "requestId", "outcome", "response"} or
            response["schema"] != REPLY_SCHEMA or
            type(response["requestId"]) is not str or
            not ID.fullmatch(response["requestId"]) or
            response["outcome"] != "completed"):
            raise ValueError("Native Intelligence receipt invalid")
        answer = response["response"]
        if (type(answer) is not dict or
            set(answer) != {"schema", "text", "engineId", "modelId", "authority"} or
            answer["schema"] != RESPONSE_SCHEMA or answer["authority"] != "none" or
            any(type(answer.get(field)) is not str or not answer[field].strip()
                for field in ("text", "engineId", "modelId")) or
            len(answer["text"]) > 131072 or len(answer["engineId"]) > 80 or
            len(answer["modelId"]) > 160 or "\0" in answer["text"]):
            raise ValueError("Native Intelligence response invalid")
        return ActionResult(True, "OrdaX Intelligence consultation completed", {
            "schema": REPLY_SCHEMA, "requestId": response["requestId"],
            "text": answer["text"], "engineId": answer["engineId"],
            "modelId": answer["modelId"], "authority": "none",
        })
    except (HTTPError, URLError, OSError, ValueError, TypeError,
            UnicodeError, json.JSONDecodeError):
        # A timeout or disconnect does not prove that the shared engine did not
        # start. Never retry, duplicate a model or claim a fabricated answer.
        return ActionResult(False, "OrdaX Native Intelligence outcome unknown; do not retry automatically", {
            "error_code": "ordax_intelligence_outcome_unknown",
            "outcome": "unknown",
        })
