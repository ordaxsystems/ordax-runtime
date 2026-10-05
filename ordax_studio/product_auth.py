from __future__ import annotations

import os
from dataclasses import dataclass, replace
from typing import Any
from urllib.parse import urlparse

import httpx

from ordax_dev_agent.cloudflare_control_plane import CloudflareControlPlane
from ordax_dev_agent.config import AgentConfig
from ordax_dev_agent.device_setup import SetupError, configure as configure_device
from ordax_dev_agent.product_remote_client import ProductRemoteClient, ProductRemoteError

_DEFAULT_SUPABASE_ORIGIN = "https://eobcxuyvhkvdmkbaihwh.supabase.co"
_DEFAULT_SUPABASE_PUBLISHABLE_KEY = "sb_publishable_GQUBlAVTzgNtscw9iE5vLQ_GGtdmsL5"


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
    origin = str(
        os.environ.get("ORDAX_PRODUCT_AUTH_URL") or _DEFAULT_SUPABASE_ORIGIN
    ).rstrip("/")
    publishable_key = str(
        os.environ.get("ORDAX_PRODUCT_AUTH_PUBLISHABLE_KEY")
        or _DEFAULT_SUPABASE_PUBLISHABLE_KEY
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


def sign_in_with_password(
    email: str,
    password: str,
    *,
    http: httpx.Client | None = None,
) -> ProductAuthSession:
    email = str(email or "").strip()
    password = str(password or "")
    if not email or len(email) > 320 or "@" not in email:
        raise ProductAccountError(
            "product_auth_email_invalid",
            "Informe um e-mail válido.",
        )
    if not password or len(password) > 2048:
        raise ProductAccountError(
            "product_auth_password_invalid",
            "Informe a senha da sua conta ORDAX.",
        )

    origin, publishable_key = _auth_config()
    owns_http = http is None
    client = http or httpx.Client(
        timeout=httpx.Timeout(20.0),
        follow_redirects=False,
    )
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
            # Do not surface raw provider errors because they can contain details
            # that are useful for account enumeration or internal diagnostics.
            raise ProductAccountError(
                "product_auth_rejected",
                "E-mail ou senha não conferem.",
            )
        if not isinstance(body, dict):
            raise ProductAccountError(
                "product_auth_invalid_response",
                "O serviço de autenticação retornou uma resposta inválida.",
            )

        access_token = body.get("access_token")
        if (
            not isinstance(access_token, str)
            or not access_token
            or len(access_token) > 16_000
        ):
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
        return ProductAuthSession(
            access_token=access_token,
            email=authenticated_email,
        )
    finally:
        if owns_http:
            client.close()


def connect_existing_device(
    config: AgentConfig,
    email: str,
    password: str,
    *,
    auth_http: httpx.Client | None = None,
) -> dict[str, Any]:
    if not config.control_plane_url:
        raise ProductAccountError(
            "control_plane_unconfigured",
            "O ORDAX Runtime não possui Control Plane configurado.",
        )

    session = sign_in_with_password(email, password, http=auth_http)
    active_config = config
    enrolled_now = False
    if not config.device_id:
        try:
            enrollment = configure_device(
                config.state_dir,
                control_plane_url=config.control_plane_url,
                product_access_token=session.access_token,
            )
        except SetupError as error:
            raise ProductAccountError(
                "device_enrollment_failed",
                "A conta foi autenticada, mas não foi possível registrar este computador.",
            ) from error
        device_id = str(enrollment.get("device_id") or "")
        if not device_id:
            raise ProductAccountError(
                "device_enrollment_invalid",
                "O Control Plane não retornou uma identidade válida para este computador.",
            )
        active_config = replace(config, device_id=device_id)
        enrolled_now = True

    control: CloudflareControlPlane | None = None
    try:
        control = CloudflareControlPlane(active_config)
        pairing = control.create_product_pairing()
    except ProductAccountError:
        raise
    except Exception as error:
        raise ProductAccountError(
            "device_pairing_unavailable",
            "O Runtime local não conseguiu iniciar o pareamento da máquina.",
        ) from error
    finally:
        if control is not None:
            control.http.close()

    pairing_id = pairing.get("pairing_id") if isinstance(pairing, dict) else None
    pairing_secret = (
        pairing.get("pairing_secret") if isinstance(pairing, dict) else None
    )
    if not isinstance(pairing_id, str) or not isinstance(pairing_secret, str):
        raise ProductAccountError(
            "device_pairing_invalid",
            "O Control Plane retornou um pareamento inválido.",
        )

    try:
        with ProductRemoteClient(active_config.control_plane_url) as remote:
            link = remote.claim_device_pairing(
                session.access_token,
                pairing_id=pairing_id,
                pairing_secret=pairing_secret,
            )
    except ProductRemoteError as error:
        raise ProductAccountError(
            error.error_code,
            "A conta foi autenticada, mas o vínculo com este computador foi recusado.",
        ) from error
    except httpx.HTTPError as error:
        raise ProductAccountError(
            "product_pairing_unavailable",
            "A conta foi autenticada, mas o Control Plane está indisponível.",
        ) from error

    if str(link.get("device_id") or "") != str(active_config.device_id):
        raise ProductAccountError(
            "product_pairing_device_mismatch",
            "O Control Plane vinculou uma identidade de dispositivo inesperada.",
        )

    return {
        "email": session.email or email,
        "link": dict(link),
        "device_id": active_config.device_id,
        "enrolled_now": enrolled_now,
    }
