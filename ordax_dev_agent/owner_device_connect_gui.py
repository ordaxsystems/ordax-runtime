"""One-click Windows onboarding for the independent OrdaX MCP installer.

This is presentation only: the existing Owner PKCE flow and canonical device
setup own credentials, identity, and grants. No second Runtime or transport.
The unsigned validation candidate must not be offered as a public release.
"""
from __future__ import annotations

import argparse
import http.client
from datetime import datetime, timezone
import json
import os
import re
import sys
from collections.abc import Callable
from typing import Any

from ordax_core.owner_oauth import OwnerOAuthError, browser_authorization_code, exchange_owner_code
from ordax_connector.owner_grants import (
    require_owner_mcp_device_identity,
    select_consented_owner_mcp_client,
)
from ordax_dev_agent.config import AgentConfig
from ordax_dev_agent.device_setup import SetupError
from ordax_dev_agent.owner_grants_cli import parse_args as owner_cli_args, perform as owner_cli_perform
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


def inspect_local_runtime_status() -> dict[str, object]:
    """Read only the fixed loopback health endpoint; never infer remote authority.

    No OAuth token, device credential, cookie, custom URL or command is sent.
    A forged or invalid local HTTP response cannot claim an attested MCP session.
    """
    unavailable = {"responded": False, "healthy": None, "state": None, "version": None}
    connection = http.client.HTTPConnection("127.0.0.1", 8765, timeout=1.5)
    try:
        connection.request("GET", "/health", headers={"Accept": "application/json"})
        response = connection.getresponse()
        if response.status != 200 or not response.getheader("Content-Type", "").lower().startswith("application/json"):
            return unavailable
        raw = response.read(4097)
        if len(raw) > 4096:
            return unavailable
        data = json.loads(raw.decode("utf-8"))
        if not isinstance(data, dict) or type(data.get("ok")) is not bool:
            return unavailable
        state = data.get("state")
        version = data.get("version")
        if not isinstance(state, str) or not re.fullmatch(r"[a-z][a-z0-9-]{0,31}", state):
            return unavailable
        if not isinstance(version, str) or not re.fullmatch(r"[0-9]+(?:\.[0-9]+){1,3}", version):
            return unavailable
        return {"responded": True, "healthy": data["ok"], "state": state, "version": version}
    except (OSError, ValueError, UnicodeError, http.client.HTTPException):
        return unavailable
    finally:
        connection.close()


def _format_local_observation(record: object) -> str:
    if not isinstance(record, dict) or set(record) != {"responded", "healthy", "state", "version"}:
        raise ValueError("Invalid local observation")
    if record["responded"] is False and all(record[key] is None for key in ("healthy", "state", "version")):
        return "Estado local: porta de diagnostico indisponivel ou resposta invalida."
    if (record["responded"] is not True or type(record["healthy"]) is not bool
            or not isinstance(record["state"], str)
            or not re.fullmatch(r"[a-z][a-z0-9-]{0,31}", record["state"])
            or not isinstance(record["version"], str)
            or not re.fullmatch(r"[0-9]+(?:\.[0-9]+){1,3}", record["version"])):
        raise ValueError("Invalid local observation")
    condition = "sem erro reportado" if record["healthy"] else "com alerta reportado"
    return (f"Estado local: porta de diagnostico respondeu ({condition}; "
            f"v{record['version']}; {record['state']}).")


def diagnose_with_dialogs(
    *,
    show: Callable[[str, int], int] = _show_message,
    inspect: Callable[[], dict[str, object]] = inspect_pinned_https_ingress,
    inspect_local: Callable[[], dict[str, object]] = inspect_local_runtime_status,
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

    try:
        local = _format_local_observation(inspect_local())
    except Exception:
        local = "Estado local: nao foi possivel validar."

    show(
        "Diagnostico do OrdaX MCP\n\n"
        + primary + "\n" + restricted + "\n" + local + "\n\n"
        "As verificacoes observam HTTPS e a porta de estado local. "
        "A resposta local nao comprova cadastro, login, prontidao do MCP ou autorizacao "
        "para controlar o computador.\n\n"
        "Se o servidor principal falhar, consulte sua conexao de rede "
        "antes de tentar conectar o dispositivo.",
        _MB_ICONINFORMATION,
    )
    return 0 if result["primary"]["transport"] == "https_responded" else 1



def list_local_owner_session_candidates(
    remote: ProductRemoteClient, token: str, config: AgentConfig,
    *, now: datetime | None = None,
) -> list[dict[str, str]]:
    """Read pending Owner tickets for THIS enrolled Windows device only.

    The Control Plane owns every grant/eligibility decision; this is a
    presentation projection and cannot create a binding or infer attestation.
    """
    device_id = ProductRemoteClient._uuid(config.device_id, "device_id")
    authority = remote.owner_remote_authority(token, device_id=device_id)
    require_owner_mcp_device_identity(authority, device_id=device_id)
    clients = authority["clients"]
    if not clients or len(clients) > 25:
        raise ValueError("owner_gui_client_inventory_invalid")
    seen_clients: set[str] = set()
    candidates: list[dict[str, str]] = []
    clock = now or datetime.now(timezone.utc)
    if clock.tzinfo is None:
        raise ValueError("owner_gui_clock_invalid")

    for item in clients:
        client_id = select_consented_owner_mcp_client(
            authority, device_id=device_id, requested_client_id=item.get("client_id"),
        )
        if client_id in seen_clients:
            raise ValueError("owner_gui_duplicate_oauth_client")
        seen_clients.add(client_id)
        # An idle client has no Owner decision to make. Avoid a second
        # canonical SQL projection for each consented but inactive client.
        pending = remote.owner_central_session_selections(token, client_id=client_id)
        if not pending["pending_selections"]:
            continue
        targets = remote.owner_central_session_targets(token, client_id=client_id)
        if not any(target.get("device_id", "").lower() == device_id
                   for target in targets["targets"]):
            continue
        for ticket in pending["pending_selections"]:
            try:
                expiration = datetime.fromisoformat(
                    ticket["expires_at"].replace("Z", "+00:00")
                )
            except ValueError as exc:
                raise ValueError("owner_gui_ticket_expiry_invalid") from exc
            if expiration.tzinfo is None:
                raise ValueError("owner_gui_ticket_expiry_invalid")
            if expiration <= clock:
                continue
            name = item.get("client_name")
            safe_name = re.sub(r"[^A-Za-z0-9 ._-]", "", name)[:64] \
                if isinstance(name, str) else ""
            candidates.append({
                "device_id": device_id,
                "client_id": client_id,
                "client_name": safe_name or "Cliente OAuth",
                "challenge_id": ticket["challenge_id"],
                "expires_at": expiration.isoformat(),
            })
            if len(candidates) > 8:
                # Never make the Owner click through an unbounded queue.
                raise ValueError("owner_gui_too_many_pending_tickets")
    if len({item["challenge_id"] for item in candidates}) != len(candidates):
        raise ValueError("owner_gui_duplicate_pending_ticket")
    return candidates


def select_session_with_dialogs(
    *,
    show: Callable[[str, int], int] = _show_message,
    authorize: Callable[[], tuple[str, str]] = _browser_authorization,
    exchange: Callable[[str, str], str] = exchange_owner_code,
    config_provider: Callable[[], AgentConfig] = AgentConfig.from_env,
    remote_factory: Callable[[str], ProductRemoteClient] = ProductRemoteClient,
    select: Callable[..., dict[str, Any]] = owner_cli_perform,
) -> int:
    """Native Windows UI over the canonical CLI Owner selection authority.

    The Owner picks an exact pending challenge, device and OAuth client.
    This never creates grants or dispatches work. The selected Runtime must
    subsequently attest the session using its own Native device credential.
    """
    accepted = show(
        "Selecionar ESTE computador para uma sessao OrdaX pendente?\n\n"
        "A conta Owner sera autenticada no navegador. A escolha de cada "
        "solicitacao exigira confirmacao separada, sem conceder "
        "permissoes ou executar comandos.",
        _MB_YESNO | _MB_ICONQUESTION,
    )
    if accepted != _IDYES:
        return 2

    try:
        # This Start-menu UI belongs to standalone Windows MCP. OrdaX Desktop
        # has its own Product Device/OS Supervisor and must use its Native UI.
        if os.environ.get("ORDAX_RUNTIME_HOST_PROFILE") is not None:
            raise ValueError("owner_gui_standalone_windows_only")
        config = config_provider()
        code, verifier = authorize()
        token = exchange(code, verifier)  # Owner token exists only in memory
        with remote_factory(config.product_mcp_url) as remote:
            candidates = list_local_owner_session_candidates(remote, token, config)
            if not candidates:
                show(
                    "Nenhuma solicitacao pendente para este computador.\n"
                    "Inicie uma acao pelo cliente MCP e tente novamente.",
                    _MB_ICONINFORMATION,
                )
                return 2
            for candidate in candidates:
                answer = show(
                    "O proprietario confirma esta escolha exata?\n\n"
                    "Computador: ESTE Windows\n"
                    f"Dispositivo: {candidate['device_id']}\n"
                    f"Cliente: {candidate['client_name']} ({candidate['client_id']})\n"
                    f"Solicitacao: {candidate['challenge_id']}\n"
                    f"Expira: {candidate['expires_at']}\n\n"
                    "Compare o ID da solicitacao com o apresentado no cliente.\n"
                    "Sim apenas para a solicitacao desejada. "
                    "Nenhum grant sera criado ou ampliado.",
                    _MB_YESNO | _MB_ICONQUESTION,
                )
                if answer != _IDYES:
                    continue
                args = owner_cli_args([
                    "central-select", "--device", candidate["device_id"],
                    "--client", candidate["client_id"],
                    "--challenge", candidate["challenge_id"], "--confirm",
                ])
                result = select(args, token, remote, config=config)
                if (not isinstance(result, dict)
                        or result.get("status") != "pending"
                        or result.get("attested") is not False
                        or result.get("dispatch_authorized") is not False
                        or result.get("challenge_id") != candidate["challenge_id"]
                        or result.get("device_id") != candidate["device_id"]):
                    raise ValueError("owner_gui_selection_not_verified")
                show(
                    "Escolha registrada na Platform.\n\n"
                    "A sessao ainda aguarda a confirmacao do Runtime "
                    "nativo deste dispositivo. Nenhum grant foi alterado.",
                    _MB_ICONINFORMATION,
                )
                return 0
    except OwnerOAuthError as error:
        show(_OAUTH_ERRORS.get(error.code, "Autorizacao Owner nao concluida."),
             _MB_ICONERROR)
        return 1
    except (ProductRemoteError, ValueError, OSError, TypeError):
        # A lost POST response may be ambiguous: never replay an Owner choice.
        show(
            "Nao foi possivel comprovar a selecao.\n"
            "Verifique novamente as solicitacoes antes de repetir. "
            "Nenhuma acao MCP foi iniciada por esta interface.",
            _MB_ICONERROR,
        )
        return 1
    except Exception:
        # pythonw has no stderr. Never surface tokens, paths or raw errors.
        show("A selecao nao foi comprovada. Consulte o estado da Platform.",
             _MB_ICONERROR)
        return 1
    show("Nenhuma solicitacao foi selecionada.", _MB_ICONINFORMATION)
    return 2


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ordax-mcp-connect")
    options = parser.add_mutually_exclusive_group()
    options.add_argument(
        "--verificar-rede", action="store_true",
        help="verificar HTTPS e estado local sem autorizar ou conectar dispositivos",
    )
    options.add_argument(
        "--selecionar-sessao", action="store_true",
        help="confirmar uma solicitacao Owner pendente neste computador",
    )
    args = parser.parse_args(argv)
    if sys.platform != "win32":
        return 1
    if args.verificar_rede:
        return diagnose_with_dialogs()
    if args.selecionar_sessao:
        return select_session_with_dialogs()
    return connect_with_dialogs()


if __name__ == "__main__":
    raise SystemExit(main())
