"""One-click Windows onboarding for the independent OrdaX MCP installer.

This is presentation only: the existing Owner PKCE flow and canonical device
setup own credentials, identity, and grants. No second Runtime or transport.
The unsigned validation candidate must not be offered as a public release.
"""
from __future__ import annotations

import sys
from collections.abc import Callable
from typing import Any

from ordax_core.owner_oauth import OwnerOAuthError, browser_authorization_code
from ordax_dev_agent.device_setup import SetupError
from ordax_dev_agent.owner_device_connect_cli import enroll_owner_device

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


def main() -> int:
    if sys.platform != "win32":
        return 1
    return connect_with_dialogs()


if __name__ == "__main__":
    raise SystemExit(main())
