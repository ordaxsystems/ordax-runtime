"""One-click Windows onboarding for the independent OrdaX MCP installer.

This is presentation only: the existing Owner PKCE flow and canonical device
setup own credentials, identity, and grants. No second Runtime or transport.
The unsigned validation candidate must not be offered as a public release.
"""
from __future__ import annotations

import argparse
import http.client
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from collections.abc import Callable
from typing import Any

from ordax_core.owner_oauth import OwnerOAuthError, browser_authorization_code, exchange_owner_code
from ordax_connector.owner_grants import require_owner_mcp_device_identity, select_consented_owner_mcp_client
from ordax_dev_agent.device_setup import SetupError
from ordax_dev_agent.config import AgentConfig
from ordax_dev_agent.owner_grants_cli import eligible_full_control_grants, recoverable_discovery_clients, retain_existing_full_control_grant
from ordax_dev_agent.product_remote_client import ProductRemoteClient, ProductRemoteError
from ordax_dev_agent.owner_device_connect_cli import enroll_owner_device
from ordax_dev_agent.product_ingress import inspect_pinned_https_ingress

_TITLE = "OrdaX MCP"
_MB_YESNO = 0x0004
_MB_ICONQUESTION = 0x0020
_MB_ICONERROR = 0x0010
_MB_ICONINFORMATION = 0x0040
_MB_SETFOREGROUND = 0x10000
_IDYES = 6

_OAUTH_ERRORS = {
    "owner_oauth_rejected": "A autorizacao foi recusada. Nenhum dispositivo foi conectado.",
    "owner_oauth_timeout": "A autorizacao expirou. Abra Conectar dispositivo e tente novamente.",
    "owner_oauth_browser_unavailable": "Nao foi possivel abrir o navegador para autorizar este dispositivo.",
    "owner_oauth_callback_unavailable": "A porta local de retorno da autorizacao esta indisponivel.",
}
_SETUP_ERRORS = {
    "SETUP_ALREADY_RUNNING": "Uma conexao de dispositivo ja esta em andamento.",
    "PRODUCT_ACCOUNT_LOGIN_REQUIRED": "Entre na sua conta OrdaX e autorize este dispositivo.",
    "CONTROL_PLANE_UNAVAILABLE": "O servico OrdaX nao respondeu. Confira a rede e tente novamente.",
    "PRIVATE_ACL_FAILED": "Nao foi possivel proteger as credenciais locais deste usuario.",
}


def _show_message(message: str, flags: int) -> int:
    if sys.platform != "win32":
        raise RuntimeError("Windows onboarding only")
    import ctypes

    return int(ctypes.windll.user32.MessageBoxW(
        None, message, _TITLE, flags | _MB_SETFOREGROUND
    ))


def _browser_authorization() -> tuple[str, str]:
    # The browser callback must direct users back to OrdaX MCP, not a terminal.
    return browser_authorization_code(mcp_callback=True)


def connect_with_dialogs(
    *,
    show: Callable[[str, int], int] = _show_message,
    enroll: Callable[..., dict[str, Any]] = enroll_owner_device,
    authorize: Callable[[], tuple[str, str]] = _browser_authorization,
) -> int:
    """Offer explicit consent before Owner OAuth; never display a secret."""
    accepted = show(
        "Conectar este computador ao OrdaX MCP?\n\n"
        "O navegador sera aberto para autorizar o dispositivo. "
        "A conexao nao concede automaticamente acesso a arquivos, comandos "
        "ou controle do computador.",
        _MB_YESNO | _MB_ICONQUESTION,
    )
    if accepted != _IDYES:
        return 2

    try:
        result = enroll(auth_code=authorize)
        if (result.get("ok") is not True
                or result.get("runtime_activated") is not False
                or result.get("grants_issued") is not False
                or result.get("end_to_end_proven") is not False):
            raise SetupError("DEVICE_ENROLLMENT_RESULT_INVALID")
    except OwnerOAuthError as error:
        show(_OAUTH_ERRORS.get(error.code, "Autorizacao nao concluida. Tente novamente."),
             _MB_ICONERROR)
        return 1
    except SetupError as error:
        show(_SETUP_ERRORS.get(str(error), "Dispositivo nao conectado. Verifique a configuracao e tente novamente."),
             _MB_ICONERROR)
        return 1
    except Exception:
        # A pythonw.exe launch has no terminal: fail visibly, without showing
        # exception text, access tokens, paths, or other private diagnostics.
        show("Dispositivo nao conectado. Verifique a instalacao e tente novamente.",
             _MB_ICONERROR)
        return 1

    show(
        "Registro do dispositivo concluido.\n\n"
        "O Runtime deve confirmar sua prontidao separadamente. "
        "Permissoes e controle remoto exigem autorizacoes explicitas.",
        _MB_ICONINFORMATION,
    )
    return 0


def _observe_local_runtime() -> str:
    """Observe one private, loopback-only Runtime; never infer Owner grants."""
    connection = http.client.HTTPConnection("127.0.0.1", 8765, timeout=1.5)
    try:
        connection.request("GET", "/status")
        response = connection.getresponse()
        if response.status != 200:
            return "unavailable"
        body = response.read(131073)
        if len(body) > 131072:
            return "unavailable"
        status = json.loads(body)
        runtime = status.get("runtime") if isinstance(status, dict) else None
        if not isinstance(runtime, dict):
            return "unavailable"
        if (runtime.get("state") in {"ready", "busy"}
                and runtime.get("paired") is True
                and runtime.get("transport_state") == "connected"
                and runtime.get("product_dispatch_ready") is True):
            return "connected"
        return "running" if isinstance(runtime.get("state"), str) else "unavailable"
    except (OSError, ValueError, TypeError):
        return "unavailable"
    finally:
        connection.close()


def _launch_canonical_runtime() -> None:
    """Start only the installer-owned launcher; its OS mutex prevents duplicates."""
    if sys.platform != "win32":
        raise RuntimeError("Windows only")
    local_data = os.environ.get("LOCALAPPDATA")
    if not local_data:
        raise RuntimeError("Canonical Windows profile unavailable")
    root = Path(local_data) / "Programs" / "OrdaX MCP"
    private_gui = root / "runtime" / "pythonw.exe"
    launcher = root / "ORDAX Runtime.exe"
    # Do not allow a source checkout or an unrelated Python host to launch
    # an arbitrary executable by using this first-party UI entrypoint.
    if os.path.normcase(os.path.realpath(sys.executable)) != os.path.normcase(
        os.path.realpath(private_gui)
    ):
        raise RuntimeError("Canonical private GUI host required")
    for path in (root, private_gui.parent, private_gui, launcher):
        if path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction()):
            raise RuntimeError("Linked Runtime installation rejected")
    if not private_gui.is_file() or not launcher.is_file():
        raise RuntimeError("Installed Runtime launcher unavailable")
    subprocess.Popen(
        [str(launcher)], cwd=str(root), shell=False, close_fds=True,
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def activate_with_dialogs(
    *,
    show: Callable[[str, int], int] = _show_message,
    observe: Callable[[], str] = _observe_local_runtime,
    launch: Callable[[], None] = _launch_canonical_runtime,
    select_session: Callable[[], int] | None = None,
) -> int:
    """Desktop/Start shortcut: idempotent Runtime activation with visible status.

    Do not enroll a device, change grants, start another daemon or claim remote
    readiness without the existing Runtime's paired/dispatch evidence.
    """
    try:
        before = observe()
        if before not in {"connected", "running", "unavailable"}:
            raise ValueError("Invalid local Runtime status")
        if before == "connected":
            choice = show(
                "OrdaX MCP ja esta ativo e conectado.\n\n"
                "Esta conexao NAO significa que o ChatGPT tenha acesso. "
                "Se houver um pedido de sessao pendente, deseja abrir a "
                "confirmacao oficial do proprietario agora?\n\n"
                "Escolha Nao para apenas verificar o status. "
                "Nenhuma permissao sera criada automaticamente.",
                _MB_YESNO | _MB_ICONQUESTION,
            )
            if choice != _IDYES:
                return 0
            # No Owner authentication or remote permission is attempted
            # without the second, explicit approval on this first-party GUI.
            # Resolve the existing GUI function at call-time, never import or
            # start a competing control process.
            return (
                select_session() if select_session is not None
                else select_chatgpt_session_with_dialogs()
            )
        if before == "running":
            show(
                "OrdaX MCP ja esta em execucao, mas a conexao ainda nao foi "
                "confirmada.\n\nUse Verificar conexao ou Conectar dispositivo "
                "no menu Iniciar, se necessario.",
                _MB_ICONINFORMATION,
            )
            return 0
        launch()  # One official Windows launcher, guarded by the native mutex.
        after = observe()
        if after not in {"connected", "running", "unavailable"}:
            raise ValueError("Invalid local Runtime status")
    except Exception:
        show(
            "Nao foi possivel solicitar a ativacao do OrdaX MCP.\n"
            "Confira a instalacao e use Verificar conexao no menu Iniciar.\n"
            "Nenhuma permissao foi alterada.",
            _MB_ICONERROR,
        )
        return 1
    if after == "connected":
        message = "OrdaX MCP esta ativo e conectado."
    elif after == "running":
        message = (
            "OrdaX MCP foi iniciado, mas ainda nao confirmou a conexao. "
            "Consulte Verificar conexao se persistir."
        )
    else:
        message = (
            "Inicializacao do OrdaX MCP solicitada. A prontidao ainda nao "
            "foi confirmada. Confira a conexao no menu Iniciar."
        )
    show(message + "\n\nO atalho nao instala outro agente nem concede permissoes.", _MB_ICONINFORMATION)
    return 0


def _central_selection_tickets(remote: object, token: str, device_id: str, authority: dict, now: datetime) -> list[tuple[str, str, str]]:
    """Read Owner-consented clients and exact pending challenges, never grants."""
    require_owner_mcp_device_identity(authority, device_id=device_id)
    clients = authority.get("clients")
    if not isinstance(clients, list) or not 1 <= len(clients) <= 5:
        raise ValueError("Owner client catalog must have between one and five clients")
    choices: list[tuple[str, str, str]] = []
    seen: set[tuple[str, str]] = set()
    for client in clients:
        if not isinstance(client, dict):
            raise ValueError("Invalid consented OAuth client")
        client_id = select_consented_owner_mcp_client(
            authority, device_id=device_id, requested_client_id=client.get("client_id"),
        )
        targets = remote.owner_central_session_targets(token, client_id=client_id)
        if not any(
            item.get("device_id") == device_id
            for item in targets["targets"] if isinstance(item, dict)
        ):
            continue  # No existing device grant for this OAuth client.
        pending = remote.owner_central_session_selections(token, client_id=client_id)
        for item in pending["pending_selections"]:
            challenge = item["challenge_id"]
            expiry = datetime.fromisoformat(item["expires_at"].replace("Z", "+00:00"))
            if expiry.tzinfo is None:
                raise ValueError("Owner selection deadline lacks time zone")
            if expiry <= now:
                continue
            key = (client_id, challenge)
            if key in seen or len(choices) >= 25:
                raise ValueError("Ambiguous or oversized Owner selection catalog")
            seen.add(key)
            choices.append((client_id, challenge, item["expires_at"]))
    return choices


def select_chatgpt_session_with_dialogs(
    *,
    show: Callable[[str, int], int] = _show_message,
    authorize: Callable[[], tuple[str, str]] = _browser_authorization,
    exchange: Callable[[str, str], str] = exchange_owner_code,
    load_config: Callable[[], AgentConfig] = AgentConfig.from_env,
    client_factory: Callable[..., ProductRemoteClient] = ProductRemoteClient,
    clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> int:
    """One explicit Owner GUI flow for an already-granted ChatGPT MCP client.

    The device is fixed to this installation. A selected pending ticket is
    sent to canonical Product Owner authority; only the Native device can
    attest the session, and this flow never creates or updates grants.
    """
    consent = show(
        "Selecionar este computador para uma sessao do OrdaX MCP?\n\n"
        "O navegador sera aberto para autenticar o proprietario. "
        "Somente clientes OAuth ja autorizados e pedidos pendentes serao "
        "exibidos. Nenhuma permissao sera concedida automaticamente.",
        _MB_YESNO | _MB_ICONQUESTION,
    )
    if consent != _IDYES:
        return 2
    try:
        config = load_config()
        device_id = str(config.device_id or "")
        if not device_id:
            raise ValueError("Device is not enrolled")
        code, verifier = authorize()
        token = exchange(code, verifier)
        if not isinstance(token, str) or not token:
            raise ValueError("Owner OAuth token missing")
        with client_factory(config.product_mcp_url) as remote:
            authority = remote.owner_remote_authority(token, device_id=device_id)
            choices = _central_selection_tickets(
                remote, token, device_id, authority, clock(),
            )
            if not choices:
                show(
                    "Nenhum pedido pendente elegivel para este computador.\n\n"
                    "No ChatGPT, solicite uma operacao OrdaX MCP neste dispositivo "
                    "e abra este atalho novamente para confirmar o pedido.",
                    _MB_ICONINFORMATION,
                )
                return 2
            for client_id, challenge, expires_at in choices:
                if show(
                    "CONFIRMAR SELECAO DO DISPOSITIVO\n\n"
                    f"Computador: {device_id}\n"
                    f"Cliente OAuth: {client_id}\n"
                    f"Pedido: {challenge}\n"
                    f"Expira: {expires_at}\n\n"
                    "Confirmar este cliente e este pedido especificos? "
                    "A autorizacao ainda depende da atestacao do dispositivo "
                    "e das permissoes ja existentes.",
                    _MB_YESNO | _MB_ICONQUESTION,
                ) != _IDYES:
                    continue
                # OAuth and the Windows confirmation dialog can take longer
                # than the short-lived Platform ticket. Never submit a known
                # expired challenge, even when it was valid at enumeration.
                deadline = datetime.fromisoformat(
                    expires_at.replace("Z", "+00:00")
                )
                if deadline.tzinfo is None or deadline <= clock():
                    show(
                        "Este pedido de sessao expirou antes da confirmacao. "
                        "Solicite uma nova operacao no ChatGPT e tente novamente.",
                        _MB_ICONINFORMATION,
                    )
                    continue
                result = remote.owner_select_central_device(
                    token, client_id=client_id, challenge_id=challenge,
                    device_id=device_id,
                )
                if (
                    result.get("status") != "pending"
                    or result.get("attested") is not False
                    or result.get("dispatch_authorized") is not False
                ):
                    raise ValueError("Unexpected Owner selection result")
                show(
                    "Selecao enviada para a autoridade OrdaX.\n\n"
                    "A sessao ainda nao esta atestada. "
                    "O dispositivo deve confirmar o pedido antes do "
                    "controle remoto ficar disponivel. "
                    "Nenhuma nova permissao foi concedida.",
                    _MB_ICONINFORMATION,
                )
                return 0
        return 2  # All candidates explicitly declined.
    except OwnerOAuthError:
        show("Autenticacao do proprietario nao concluida.", _MB_ICONERROR)
    except ProductRemoteError:
        show("Nao foi possivel confirmar o pedido na Platform OrdaX.", _MB_ICONERROR)
    except Exception:
        # Never show tokens, file paths or untrusted API exception details.
        show(
            "Selecao nao concluida. Verifique a instalacao, "
            "as autorizacoes do Owner e o pedido no ChatGPT.",
            _MB_ICONERROR,
        )
    return 1


def retain_full_control_with_dialogs(
    *,
    show: Callable[[str, int], int] = _show_message,
    authorize: Callable[[], tuple[str, str]] = _browser_authorization,
    exchange: Callable[[str, str], str] = exchange_owner_code,
    load_config: Callable[[], AgentConfig] = AgentConfig.from_env,
    client_factory: Callable[..., ProductRemoteClient] = ProductRemoteClient,
    clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    retain: Callable[..., dict[str, Any]] = retain_existing_full_control_grant,
) -> int:
    """Allow the Owner to retain access until revoked, never silently grant it."""
    if show(
        "Verificar autorizacoes existentes do OrdaX MCP?\n\n"
        "O navegador sera aberto para autenticar o proprietario. "
        "O acesso completo podera ficar sem data de vencimento ou "
        "ser autorizado novamente quando o grant anterior tiver expirado, "
        "somente para um cliente OAuth que ainda tenha mcp-discovery.\n\n"
        "Nada sera alterado sem outra confirmacao explicita.",
        _MB_YESNO | _MB_ICONQUESTION,
    ) != _IDYES:
        return 2
    try:
        config = load_config()
        device_id = str(config.device_id or "")
        if not device_id:
            raise ValueError("Device not enrolled")
        code, verifier = authorize()
        token = exchange(code, verifier)
        if not isinstance(token, str) or not token:
            raise ValueError("Owner OAuth token missing")
        with client_factory(config.product_mcp_url) as remote:
            authority = remote.owner_remote_authority(token, device_id=device_id)
            current_time = clock()
            existing = eligible_full_control_grants(
                authority, device_id=device_id, now=current_time,
            )
            candidates = [grant for grant in existing if grant[2] is not None]
            # Several OAuth clients may be consented. A permanent grant for
            # client A must not hide client B's expired grant or the Owner's
            # ability to recover client B through its independent discovery.
            recovery = recoverable_discovery_clients(
                authority, device_id=device_id, now=current_time,
            )
            if not candidates and not recovery:
                if any(grant[2] is None for grant in existing):
                    show(
                        "Este computador ja possui acesso completo sem "
                        "vencimento para um cliente OAuth autorizado.\n\n"
                        "Ele permanecera valido ate revogacao pelo "
                        "proprietario, sujeito a identidade e politica local. "
                        "Nenhuma alteracao foi realizada.",
                        _MB_ICONINFORMATION,
                    )
                    return 0
                show(
                    "Nenhum acesso completo ativo nem cliente de descoberta "
                    "elegivel para recuperacao.\n\n"
                    "Acesso expirado sem autorizacao de descoberta precisa "
                    "de novo consentimento por meio do Owner.",
                    _MB_ICONINFORMATION,
                )
                return 2
            options = [
                (client_id, expires.isoformat(), False, "")
                for client_id, _group_id, expires in candidates
            ] + [
                (client_id, "expirada ou inexistente", True, client_name)
                for client_id, client_name in recovery
            ]
            for client_id, expiry_label, restore, client_name in options:
                approved = show(
                    "AUTORIZAR ACESSO AO COMPUTADOR ATE REVOGAR\n\n"
                    f"Dispositivo: {device_id}\n"
                    f"Cliente OAuth: {client_id}\n"
                    + (f"Nome do cliente: {client_name}\n" if client_name else "")
                    + f"Autorizacao atual: {expiry_label}\n\n"
                    + "Deseja conceder ou manter o perfil full-computer-control "
                    "SEM VENCIMENTO ate revogar? Isso permite controle de "
                    "mouse, teclado, terminal e arquivos. Nenhum outro "
                    "cliente sera autorizado.",
                    _MB_YESNO | _MB_ICONQUESTION,
                )
                if approved != _IDYES:
                    continue
                result = retain(
                    authority, device_id=device_id, client_id=client_id,
                    token=token, remote=remote, config=config,
                    now=current_time,
                    **({"restore_from_discovery": True} if restore else {}),
                )
                if (
                    result.get("ok") is not True
                    or result.get("grantMutationPerformed") is not True
                    or result.get("endToEndProven") is not False
                ):
                    raise ValueError("Owner retention result invalid")
                show(
                    "Acesso sem vencimento confirmado pela Platform OrdaX.\n"
                    "Permanece ate revogacao pelo proprietario.\n\n"
                    "Isto nao atesta uma sessao ChatGPT. "
                    "Se necessario, utilize Autorizar ChatGPT no menu Iniciar.",
                    _MB_ICONINFORMATION,
                )
                return 0
        return 2
    except OwnerOAuthError:
        show("Autenticacao do proprietario nao concluida.", _MB_ICONERROR)
    except ProductRemoteError:
        show("Nao foi possivel consultar ou manter o acesso na Platform.", _MB_ICONERROR)
    except Exception:
        # Preserve fail-closed semantics without leaking untrusted server
        # details, credentials, tokens or private installation paths.
        show(
            "Alteracao nao concluida. Confira a instalacao e as "
            "permissoes locais antes de tentar novamente.",
            _MB_ICONERROR,
        )
    return 1


def revoke_full_control_with_dialogs(
    *,
    show: Callable[[str, int], int] = _show_message,
    authorize: Callable[[], tuple[str, str]] = _browser_authorization,
    exchange: Callable[[str, str], str] = exchange_owner_code,
    load_config: Callable[[], AgentConfig] = AgentConfig.from_env,
    client_factory: Callable[..., ProductRemoteClient] = ProductRemoteClient,
    clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> int:
    """Explicit Owner revocation of ONE installed device/client access grant.

    A successful HTTP deletion is not sufficient proof: confirm in the same
    canonical Owner inventory that the specific grant group is absent.
    """
    if show(
        "Gerenciar revogacao de controle do computador?\n\n"
        "O navegador autenticara o proprietario. Nenhum grant sera "
        "revogado sem confirmar separadamente o cliente e a permissao.",
        _MB_YESNO | _MB_ICONQUESTION,
    ) != _IDYES:
        return 2
    try:
        config = load_config()
        device_id = str(config.device_id or "")
        if not device_id:
            raise ValueError("Device is not enrolled")
        code, verifier = authorize()
        token = exchange(code, verifier)
        if not isinstance(token, str) or not token:
            raise ValueError("Owner token unavailable")
        with client_factory(config.product_mcp_url) as remote:
            authority = remote.owner_remote_authority(
                token, device_id=device_id,
            )
            grants = eligible_full_control_grants(
                authority, device_id=device_id, now=clock(),
            )
            if not grants:
                show(
                    "Nenhum grant ativo full-computer-control para revogar "
                    "neste dispositivo. Nenhuma alteracao foi realizada.",
                    _MB_ICONINFORMATION,
                )
                return 2
            for client_id, group_id, expires in grants:
                # Never mutate a group whose server-side id was omitted or
                # which belongs to a different device or OAuth client.
                if not group_id:
                    raise ValueError("Owner grant group identity missing")
                expiry = (
                    "sem vencimento" if expires is None
                    else expires.isoformat()
                )
                approved = show(
                    "REVOGAR CONTROLE DO COMPUTADOR\n\n"
                    f"Dispositivo: {device_id}\n"
                    f"Cliente OAuth: {client_id}\n"
                    f"Grant: {group_id}\n"
                    f"Validade atual: {expiry}\n\n"
                    "Deseja REVOGAR o acesso completo deste cliente? "
                    "O Runtime podera continuar online, mas mouse, teclado, "
                    "terminal e arquivos perderao esta autorizacao. "
                    "Outros clientes e grants de descoberta nao mudam.",
                    _MB_YESNO | _MB_ICONQUESTION,
                )
                if approved != _IDYES:
                    continue
                receipt = remote.revoke_owner_remote_grant_group(
                    token, client_id=client_id, grant_group_id=group_id,
                )
                if (
                    not isinstance(receipt, dict)
                    or receipt.get("ok") is not True
                    or str(receipt.get("grant_group_id", "")).lower()
                        != group_id.lower()
                    or type(receipt.get("revoked_capabilities")) is not int
                    or receipt["revoked_capabilities"] < 1
                ):
                    raise ValueError("Owner revocation receipt not confirmed")
                observed = remote.owner_remote_authority(
                    token, device_id=device_id,
                )
                require_owner_mcp_device_identity(
                    observed, device_id=device_id,
                )
                rows = observed.get("grant_groups")
                if not isinstance(rows, list) or any(
                    not isinstance(item, dict) for item in rows
                ):
                    raise ValueError("Owner revocation readback invalid")
                if any(
                    str(item.get("grant_group_id", "")).lower() == group_id.lower()
                    for item in rows
                ):
                    raise ValueError("Owner revocation not persisted")
                # An unrelated new group for the same OAuth client could
                # have been created concurrently. Never claim that computer
                # control was removed until the canonical view confirms no
                # active full-control group remains for this exact client.
                if any(
                    str(item.get("client_id", "")).lower() == client_id.lower()
                    and str(item.get("device_id", "")).lower() == device_id.lower()
                    and item.get("profile_key") == "full-computer-control"
                    and item.get("scope_kind") == "device"
                    for item in rows
                ):
                    raise ValueError("Owner revocation access still active")
                show(
                    "Revogacao confirmada pela Platform OrdaX.\n"
                    f"Cliente: {client_id}\n\n"
                    "Nenhum outro cliente ou dispositivo foi alterado. "
                    "Esta confirmacao nao significa que a sessao do "
                    "ChatGPT esteja ativa.",
                    _MB_ICONINFORMATION,
                )
                return 0
        return 2
    except OwnerOAuthError:
        show(
            "Autenticacao do proprietario nao concluida. "
            "Nenhuma revogacao foi confirmada.",
            _MB_ICONERROR,
        )
    except ProductRemoteError:
        show(
            "Nao foi possivel confirmar a revogacao na Platform. "
            "Verifique as autorizacoes antes de repetir.",
            _MB_ICONERROR,
        )
    except Exception:
        show(
            "Revogacao nao confirmada. Consulte as autorizacoes na "
            "Platform antes de tentar novamente.",
            _MB_ICONERROR,
        )
    return 1


def _format_ingress_observation(label: str, record: object) -> str:
    if not isinstance(record, dict):
        raise ValueError("Untrusted ingress diagnostic shape")
    if set(record) != {"transport", "httpStatus"}:
        raise ValueError("Untrusted ingress diagnostic keys")
    transport, status = record["transport"], record["httpStatus"]
    if transport == "https_responded" and type(status) is int and 100 <= status <= 599:
        return f"{label}: HTTPS respondeu (HTTP {status})."
    if transport == "unavailable" and status is None:
        return f"{label}: sem resposta HTTPS."
    raise ValueError("Untrusted ingress diagnostic result")


def diagnose_with_dialogs(
    *,
    show: Callable[[str, int], int] = _show_message,
    inspect: Callable[[], dict[str, object]] = inspect_pinned_https_ingress,
) -> int:
    """User-invoked diagnostics only; never mutate identity or suggest grants."""
    try:
        result = inspect()
        if not isinstance(result, dict) or set(result) != {
            "schema", "primary", "restricted",
            "mcpReady", "authenticated", "grantsAuthorized",
        } or result["schema"] != "ordax.product-mcp-https-observation/1" or any(
            result[key] is not False
            for key in ("mcpReady", "authenticated", "grantsAuthorized")
        ):
            raise ValueError("Untrusted ingress diagnostic authority")
        primary = _format_ingress_observation("Servidor principal", result["primary"])
        restricted = _format_ingress_observation("Acesso de contingencia", result["restricted"])
    except Exception:
        show(
            "Nao foi possivel verificar a conectividade. Tente novamente."
            "\nNenhum dispositivo ou permissao foi alterado.",
            _MB_ICONERROR,
        )
        return 1

    show(
        "Diagnostico de rede do OrdaX MCP\n\n"
        + primary + "\n" + restricted + "\n\n"
        "Esta verificacao apenas observa respostas HTTPS. "
        "Ela nao comprova cadastro, login, prontidao do MCP ou autorizacao "
        "para controlar o computador.\n\n"
        "Se o servidor principal falhar, consulte sua conexao de rede "
        "antes de tentar conectar o dispositivo.",
        _MB_ICONINFORMATION,
    )
    return 0 if result["primary"]["transport"] == "https_responded" else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ordax-mcp-connect")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--verificar-rede", action="store_true",
        help="verificar somente HTTPS sem autorizar ou conectar dispositivos",
    )
    mode.add_argument(
        "--ativar", action="store_true",
        help="ativar o unico Runtime instalado e mostrar estado local",
    )
    mode.add_argument(
        "--selecionar-chatgpt", action="store_true",
        help="confirmar explicitamente um pedido de sessao ChatGPT ja autorizado",
    )
    mode.add_argument(
        "--manter-acesso", action="store_true",
        help="manter explicitamente um grant full-computer-control ate revogar",
    )
    mode.add_argument(
        "--revogar-acesso", action="store_true",
        help="revogar explicitamente o grant de controle de um cliente OAuth",
    )
    args = parser.parse_args(argv)
    if sys.platform != "win32":
        return 1
    if args.ativar:
        return activate_with_dialogs()
    if args.selecionar_chatgpt:
        return select_chatgpt_session_with_dialogs()
    if args.manter_acesso:
        return retain_full_control_with_dialogs()
    if args.revogar_acesso:
        return revoke_full_control_with_dialogs()
    if args.verificar_rede:
        return diagnose_with_dialogs()
    return connect_with_dialogs()


if __name__ == "__main__":
    raise SystemExit(main())
