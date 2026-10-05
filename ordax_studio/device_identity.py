from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from ordax_dev_agent.cloudflare_control_plane import CloudflareControlPlane
from ordax_dev_agent.config import AgentConfig
from ordax_dev_agent.device_credentials import resolve_token_path


@dataclass(frozen=True, slots=True)
class DeviceIdentityStatus:
    configured: bool
    device_id: str | None
    token_present: bool
    control_plane_url: str | None
    can_create_pairing: bool
    state: str
    summary: str

    def public(self) -> dict[str, Any]:
        return asdict(self)


def inspect_device_identity(config: AgentConfig) -> DeviceIdentityStatus:
    token_file = resolve_token_path(config.state_dir)
    token_present = token_file.is_file() and token_file.stat().st_size > 0
    device_id = str(config.device_id).strip() if config.device_id else None
    configured = bool(device_id and token_present and config.control_plane_url)

    if configured:
        state = "provisioned"
        summary = "Dispositivo ORDAX provisionado"
    elif device_id and not token_present:
        state = "credential-missing"
        summary = "Identidade encontrada, mas a credencial local do dispositivo está ausente"
    elif token_present and not device_id:
        state = "identity-missing"
        summary = "Credencial local encontrada, mas o identificador do dispositivo está ausente"
    else:
        state = "unprovisioned"
        summary = "Este Windows ainda não possui uma identidade de dispositivo ORDAX"

    return DeviceIdentityStatus(
        configured=configured,
        device_id=device_id,
        token_present=token_present,
        control_plane_url=config.control_plane_url,
        can_create_pairing=configured,
        state=state,
        summary=summary,
    )


def create_device_pairing(config: AgentConfig) -> dict[str, Any]:
    status = inspect_device_identity(config)
    if not status.can_create_pairing:
        raise RuntimeError(status.summary)

    control = CloudflareControlPlane(config)
    try:
        pairing = control.create_product_pairing()
    finally:
        control.http.close()

    return {
        "ok": True,
        "identity": status.public(),
        "pairing": {
            "pairing_id": pairing["pairing_id"],
            "pairing_secret": pairing["pairing_secret"],
            "expires_at": pairing["expires_at"],
        },
        "note": (
            "O código é de uso único, expira rapidamente e não concede ações por si só. "
            "A vinculação ainda exige uma sessão autenticada do produto ORDAX."
        ),
    }
