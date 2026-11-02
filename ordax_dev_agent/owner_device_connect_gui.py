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
from pathlib import Path
from collections.abc import Callable
from typing import Any

from ordax_core.owner_oauth import OwnerOAuthError, browser_authorization_code
from ordax_dev_agent.device_setup import SetupError
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
            show(
                "OrdaX MCP ja esta ativo e conectado.\\n\\n"
                "Permissoes de controle continuam sujeitas aos grants do Owner.",
                _MB_ICONINFORMATION,
            )
            return 0
        if before == "running":
            show(
                "OrdaX MCP ja esta em execucao, mas a conexao ainda nao foi "
                "confirmada.\\n\\nUse Verificar conexao ou Conectar dispositivo "
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
            "Nao foi possivel solicitar a ativacao do OrdaX MCP.\\n"
            "Confira a instalacao e use Verificar conexao no menu Iniciar.\\n"
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
    show(message + "\\n\\nO atalho nao instala outro agente nem concede permissoes.", _MB_ICONINFORMATION)
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
    args = parser.parse_args(argv)
    if sys.platform != "win32":
        return 1
    if args.ativar:
        return activate_with_dialogs()
    if args.verificar_rede:
        return diagnose_with_dialogs()
    return connect_with_dialogs()


if __name__ == "__main__":
    raise SystemExit(main())
