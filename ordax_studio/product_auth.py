from __future__ import annotations

import hashlib
import os
import socket
from dataclasses import dataclass, replace
from typing import Any
from urllib.parse import urlparse

import httpx

from ordax_dev_agent.config import AgentConfig
from ordax_dev_agent.device_credentials import resolve_token_path
from ordax_dev_agent.device_setup import SetupError, configure as configure_device
from ordax_dev_agent.identity import machine_id

from .public_auth_metadata import (
    STUDIO_OAUTH_CLIENT_ID,
    STUDIO_OAUTH_REDIRECT_URI,
    SUPABASE_ORIGIN,
    SUPABASE_PUBLISHABLE_KEY,
)


@dataclass(frozen=True)
class ProductAuthSession:
    access_token: str
    email: str | None


@dataclass(frozen=True)
class ProductAccountError(RuntimeError):
    code: str
    message: str

    def __str__(self) -> str:
        return self.message


def _auth_config() -> tuple[str, str]:
    origin = str(os.environ.get("ORDAX_PRODUCT_AUTH_URL") or SUPABASE_ORIGIN).rstrip("/")
    publishable_key = str(
        os.environ.get("ORDAX_PRODUCT_AUTH_PUBLISHABLE_KEY") or SUPABASE_PUBLISHABLE_KEY
    ).strip()
    parsed = urlparse(origin)
    if parsed.scheme != "https" or not parsed.netloc:
        raise ProductAccountError(
            "product_auth_configuration_invalid",
            "A autenticação ORDAX não está configurada com HTTPS.",
        )
    if not publishable_key.startswith("sb_publishable_"):
        raise ProductAccountError(
            "product_auth_configuration_invalid",
            "A chave pública de autenticação ORDAX está ausente.",
        )
    return origin, publishable_key


def _product_mcp_base(config: AgentConfig, override: str | None = None) -> str:
    raw = str(override or config.product_mcp_url or "").strip().rstrip("/")
    parsed = urlparse(raw)
    loopback = parsed.hostname in {"127.0.0.1", "localhost", "::1"}
    allowed = {"http", "https"} if loopback else {"https"}
    if not parsed.netloc or parsed.scheme not in allowed:
        raise ProductAccountError(
            "product_mcp_url_invalid",
            "O endereço Product configurado neste computador é inválido.",
        )
    return raw


def sign_in_with_password(
    email: str,
    password: str,
    *,
    http: httpx.Client | None = None,
) -> ProductAuthSession:
    email = str(email or "").strip()
    password = str(password or "")
    if not email or len(email) > 320 or "@" not in email:
        raise ProductAccountError("product_auth_email_invalid", "Informe um e-mail válido.")
    if not password or len(password) > 2048:
        raise ProductAccountError("product_auth_password_invalid", "Informe a senha da sua conta ORDAX.")

    origin, publishable_key = _auth_config()
    owns_http = http is None
    client = http or httpx.Client(timeout=httpx.Timeout(20.0), follow_redirects=False)
    try:
        try:
            response = client.post(
                f"{origin}/auth/v1/token",
                params={"grant_type": "password"},
                headers={
                    "apikey": publishable_key,
                    "authorization": f"Bearer {publishable_key}",
                    "accept": "application/json",
                    "content-type": "application/json",
                    "user-agent": "ORDAX-Dev/0.4",
                },
                json={"email": email, "password": password},
            )
        except httpx.HTTPError as error:
            raise ProductAccountError(
                "product_auth_unavailable",
                "Não foi possível alcançar o serviço de autenticação ORDAX.",
            ) from error
        try:
            body: Any = response.json()
        except ValueError as error:
            raise ProductAccountError(
                "product_auth_invalid_response",
                "O serviço de autenticação retornou uma resposta inválida.",
            ) from error
        if response.status_code >= 400:
            raise ProductAccountError("product_auth_rejected", "E-mail ou senha não conferem.")
        if not isinstance(body, dict):
            raise ProductAccountError(
                "product_auth_invalid_response",
                "O serviço de autenticação retornou uma resposta inválida.",
            )
        access_token = body.get("access_token")
        if not isinstance(access_token, str) or not access_token or len(access_token) > 16_000:
            raise ProductAccountError(
                "product_auth_token_missing",
                "A autenticação não retornou uma sessão válida.",
            )
        user = body.get("user")
        authenticated_email = (
            user.get("email")
            if isinstance(user, dict) and isinstance(user.get("email"), str)
            else None
        )
        return ProductAuthSession(access_token=access_token, email=authenticated_email)
    finally:
        if owns_http:
            client.close()


def exchange_authorization_code(
    code: str,
    code_verifier: str,
    *,
    http: httpx.Client | None = None,
) -> ProductAuthSession:
    code = str(code or "")
    code_verifier = str(code_verifier or "")
    if not code or len(code) > 4096 or "\r" in code or "\n" in code:
        raise ProductAccountError("product_oauth_code_invalid", "A autorização do navegador é inválida.")
    if not 43 <= len(code_verifier) <= 128 or not all(
        ch.isalnum() or ch in "-._~" for ch in code_verifier
    ):
        raise ProductAccountError("product_oauth_verifier_invalid", "A verificação PKCE é inválida.")

    origin, _publishable_key = _auth_config()
    owns_http = http is None
    client = http or httpx.Client(timeout=httpx.Timeout(20.0), follow_redirects=False)
    try:
        try:
            response = client.post(
                f"{origin}/auth/v1/oauth/token",
                headers={
                    "accept": "application/json",
                    "content-type": "application/x-www-form-urlencoded",
                    "user-agent": "ORDAX-Studio/0.15",
                },
                data={
                    "grant_type": "authorization_code",
                    "client_id": STUDIO_OAUTH_CLIENT_ID,
                    "redirect_uri": STUDIO_OAUTH_REDIRECT_URI,
                    "code": code,
                    "code_verifier": code_verifier,
                },
            )
        except httpx.HTTPError as error:
            raise ProductAccountError(
                "product_oauth_unavailable",
                "Não foi possível concluir a autorização ORDAX.",
            ) from error
        try:
            body: Any = response.json()
        except ValueError as error:
            raise ProductAccountError(
                "product_oauth_invalid_response",
                "A autorização ORDAX retornou uma resposta inválida.",
            ) from error
        if response.status_code >= 400 or not isinstance(body, dict):
            raise ProductAccountError(
                "product_oauth_rejected",
                "A autorização ORDAX foi recusada ou expirou.",
            )
        access_token = body.get("access_token")
        if (
            not isinstance(access_token, str)
            or not access_token
            or len(access_token) > 16_000
            or "\r" in access_token
            or "\n" in access_token
        ):
            raise ProductAccountError(
                "product_oauth_token_missing",
                "A autorização não retornou uma sessão válida.",
            )
        email: str | None = None
        try:
            userinfo = client.get(
                f"{origin}/auth/v1/oauth/userinfo",
                headers={"authorization": f"Bearer {access_token}", "accept": "application/json"},
            )
            info: Any = userinfo.json()
            if userinfo.status_code < 400 and isinstance(info, dict):
                candidate = info.get("email")
                if isinstance(candidate, str) and 0 < len(candidate) <= 320:
                    email = candidate
        except (httpx.HTTPError, ValueError):
            email = None
        return ProductAuthSession(access_token=access_token, email=email)
    finally:
        if owns_http:
            client.close()


def _revalidate_existing_device(
    config: AgentConfig,
    session: ProductAuthSession,
    *,
    product_mcp_url: str | None = None,
    http: httpx.Client | None = None,
) -> str:
    token_path = resolve_token_path(config.state_dir)
    try:
        raw_device_token = token_path.read_text(encoding="utf-8-sig").strip()
    except OSError as error:
        raise ProductAccountError(
            "device_credential_missing",
            "A identidade local deste computador precisa ser recriada.",
        ) from error
    if not 32 <= len(raw_device_token) <= 512:
        raise ProductAccountError(
            "device_credential_invalid",
            "A identidade local deste computador é inválida.",
        )

    body = {
        "operation": "enroll",
        "machine_binding_sha256": machine_id(),
        "device_name": socket.gethostname(),
        "device_kind": str(os.environ.get("ORDAX_PRODUCT_DEVICE_KIND", "desktop")).strip().lower(),
        "channel": str(os.environ.get("ORDAX_PRODUCT_CHANNEL", "development")).strip().lower(),
        "token_sha256": hashlib.sha256(raw_device_token.encode()).hexdigest(),
    }
    product_base = _product_mcp_base(config, product_mcp_url)
    owns_http = http is None
    client = http or httpx.Client(timeout=20, follow_redirects=False)
    try:
        try:
            response = client.post(
                product_base + "/v3/device/setup",
                headers={
                    "authorization": "Bearer " + session.access_token,
                    "accept": "application/json",
                    "content-type": "application/json",
                },
                json=body,
            )
        except httpx.HTTPError as error:
            raise ProductAccountError(
                "device_enrollment_unavailable",
                "A conta foi autenticada, mas o serviço Product está indisponível.",
            ) from error
        try:
            result: Any = response.json()
        except ValueError as error:
            raise ProductAccountError(
                "device_enrollment_invalid_response",
                "O serviço Product retornou uma resposta inválida ao validar este computador.",
            ) from error
        if response.status_code >= 400 or not isinstance(result, dict) or result.get("ok") is not True:
            code = result.get("error") if isinstance(result, dict) else None
            if code == "device_owner_mismatch":
                raise ProductAccountError(
                    "device_owner_mismatch",
                    "Este computador já pertence a outra Conta ORDAX.",
                )
            raise ProductAccountError(
                str(code or "device_enrollment_failed"),
                "A conta foi autenticada, mas não foi possível validar este computador.",
            )
        device_id = result.get("device_id")
        if not isinstance(device_id, str) or not device_id:
            raise ProductAccountError(
                "device_enrollment_invalid",
                "O serviço Product não retornou uma identidade válida para este computador.",
            )
        return device_id
    finally:
        if owns_http:
            client.close()


def _setup_error_to_product(error: SetupError) -> ProductAccountError:
    code = str(error or "device_enrollment_failed").strip() or "device_enrollment_failed"
    messages = {
        "PRIVATE_ACL_FAILED": "O ORDAX não conseguiu proteger a identidade local deste computador.",
        "SETUP_ALREADY_RUNNING": "O registro deste computador já está em andamento. Tente novamente em instantes.",
        "SETTINGS_INVALID_PRESERVED": "A configuração local do ORDAX está inválida e foi preservada para recuperação.",
        "CONTROL_PLANE_URL_INVALID": "O endereço do Control Plane configurado neste computador é inválido.",
        "PRODUCT_MCP_URL_INVALID": "O endereço Product configurado neste computador é inválido.",
        "CONTROL_PLANE_UNAVAILABLE": "A conta foi autenticada, mas o serviço Product está indisponível.",
        "CONTROL_PLANE_RESPONSE_INVALID": "O serviço Product retornou uma resposta inválida ao registrar este computador.",
        "MACHINE_BINDING_MISMATCH": "A identidade local pertence a outro computador e foi preservada.",
        "PRODUCT_ACCOUNT_LOGIN_REQUIRED": "A sessão da Conta OrdaX não está disponível para registrar este computador.",
        "USER_AUTHORIZATION_REQUIRED": "A Conta OrdaX não autorizou o registro deste computador.",
        "PROTOCOL_MISMATCH": "O serviço Product respondeu com um protocolo incompatível.",
        "DEVICE_KIND_INVALID": "O tipo deste dispositivo está configurado de forma inválida.",
        "PRODUCT_CHANNEL_INVALID": "O canal Product deste dispositivo está configurado de forma inválida.",
    }
    return ProductAccountError(
        code,
        messages.get(code, "A conta foi autenticada, mas não foi possível registrar este computador."),
    )


def connect_existing_device(
    config: AgentConfig,
    email: str | None = None,
    password: str | None = None,
    *,
    auth_http: httpx.Client | None = None,
    session: ProductAuthSession | None = None,
    product_mcp_url: str | None = None,
) -> dict[str, Any]:
    if not config.product_mcp_url and not product_mcp_url:
        raise ProductAccountError(
            "product_mcp_unconfigured",
            "O ORDAX Runtime n?o possui o endpoint Product configurado.",
        )
    product_base = _product_mcp_base(config, product_mcp_url)
    if session is None:
        if not isinstance(email, str) or not isinstance(password, str):
            raise ProductAccountError(
                "product_auth_session_required",
                "Abra a Conta ORDAX no navegador para continuar.",
            )
        active_session = sign_in_with_password(email, password, http=auth_http)
    else:
        active_session = session

    previous_device_id = str(config.device_id or "")
    try:
        enrollment = configure_device(
            config.state_dir,
            product_mcp_url=product_base,
            product_access_token=active_session.access_token,
        )
    except SetupError as error:
        raise _setup_error_to_product(error) from error

    device_id = str(enrollment.get("device_id") or "")
    if not device_id:
        raise ProductAccountError(
            "device_enrollment_invalid",
            "O serviço Product não retornou uma identidade válida para este computador.",
        )

    validated_config = replace(config, device_id=device_id, product_mcp_url=product_base)
    validated_device_id = _revalidate_existing_device(
        validated_config,
        active_session,
        product_mcp_url=product_base,
    )
    if validated_device_id != device_id:
        raise ProductAccountError(
            "device_identity_changed",
            "A Conta ORDAX retornou uma identidade diferente para este computador.",
        )

    return {
        "email": active_session.email or (email.strip() if isinstance(email, str) else None),
        "device_id": device_id,
        "enrolled_now": previous_device_id != device_id,
    }
