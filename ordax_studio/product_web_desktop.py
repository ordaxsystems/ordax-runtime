from __future__ import annotations

import os
import subprocess
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx

from ordax_dev_agent.actions import ActionRegistry
from ordax_dev_agent.config import AgentConfig
from ordax_dev_agent.computer_filesystem_actions import computer_access_management_status

from .blender_connection import prepare_blender_connection
from .instance_lock import SingleInstanceLock
from .product_auth import (
    ProductAccountError,
    ProductAuthSession,
    connect_existing_device,
    sign_in_with_password,
)
from ordax_dev_agent.product_remote_client import ProductRemoteClient, ProductRemoteError
from .web_desktop import APP_NAME, StudioApi


def _restart_packaged_runtime_after_enrollment() -> bool:
    """Restart the packaged runtime after first device enrollment on Windows."""
    root_raw = str(os.environ.get("ORDAX_PACKAGED_ROOT") or "").strip()
    if os.name != "nt" or not root_raw:
        return False
    root = Path(root_raw).resolve()
    runtime = root / "ORDAX Runtime.exe"
    if not runtime.is_file():
        return False

    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        event_modify_state = 0x0002
        synchronize = 0x00100000
        wait_object_0 = 0
        event = kernel32.OpenEventW(event_modify_state, False, "Local\\ORDAXRuntimeShutdown")
        if event:
            try:
                kernel32.SetEvent(event)
            finally:
                kernel32.CloseHandle(event)
        mutex = kernel32.OpenMutexW(synchronize, False, "Local\\ORDAXRuntime")
        if mutex:
            try:
                result = kernel32.WaitForSingleObject(mutex, 5000)
                if result == wait_object_0:
                    kernel32.ReleaseMutex(mutex)
            finally:
                kernel32.CloseHandle(mutex)
        subprocess.Popen(
            [str(runtime)], cwd=str(root), stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True,
        )
        time.sleep(0.25)
        return True
    except (OSError, AttributeError):
        return False


_OWNER_REMOTE_COMPUTER_GRANT_MODES = frozenset({
    "computer-observe",
    "full-computer-control",
    "interactive-computer-control",
    "computer-filesystem",
    "computer-clipboard",
    "computer-process-control",
})
_OWNER_PROJECT_BROWSER_GRANT_MODE = "project-browser-automation"
_OWNER_APP_INTELLIGENCE_GRANT_MODE = "app-intelligence-read"


class StudioProductApi(StudioApi):
    """Windows product surface layered over the canonical Studio API.

    The base Studio API remains provider-neutral. Product owner grants are
    managed only through the canonical MCP/PostgreSQL grant-group authority.
    """

    def __init__(self, agent: ActionRegistry | None = None):
        super().__init__(agent)
        self._product_session: ProductAuthSession | None = None
        self._product_device_id: str | None = None

    def _product_session_required(self) -> ProductAuthSession:
        session = getattr(self, "_product_session", None)
        if not isinstance(session, ProductAuthSession):
            raise ProductAccountError(
                "product_auth_session_required",
                "Conecte sua conta ORDAX nesta sessão para gerenciar autorizações remotas.",
            )
        return session

    def _current_product_device_id(self) -> str:
        device_id = str(
            getattr(self, "_product_device_id", None)
            or getattr(self.agent.config, "device_id", None)
            or ""
        ).strip()
        if not device_id:
            raise ProductAccountError(
                "product_device_unavailable",
                "Este computador ainda não possui identidade ORDAX ativa.",
            )
        return device_id

    @staticmethod
    def _safe_product_error(error: ProductRemoteError) -> dict[str, Any]:
        return {
            "ok": False,
            "code": error.error_code,
            "summary": "A autoridade Product recusou a operação de autorização remota.",
        }

    @staticmethod
    def _grant_expiry(expires_days: int, code: str) -> str:
        try:
            days = int(expires_days)
        except (TypeError, ValueError) as error:
            raise ProductAccountError(code, "A validade deve ser informada em dias inteiros.") from error
        if not 1 <= days <= 365:
            raise ProductAccountError(code, "A validade deve ficar entre 1 e 365 dias.")
        return (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()

    @staticmethod
    def _select_owner_mcp_client(
        authority: dict[str, Any],
        requested_client_id: str | None,
    ) -> dict[str, Any]:
        clients = [item for item in authority.get("clients", []) if isinstance(item, dict)]
        requested = str(requested_client_id or "").strip().lower()
        if requested:
            clients = [item for item in clients if str(item.get("client_id") or "").lower() == requested]
        if not clients:
            raise ProductAccountError(
                "product_mcp_client_unavailable",
                "Nenhum cliente MCP com consentimento ativo corresponde a esta conta.",
            )
        if len(clients) != 1:
            raise ProductAccountError(
                "product_mcp_client_required",
                "Há mais de um cliente MCP autorizado; selecione explicitamente o cliente correto.",
            )
        return clients[0]

    @staticmethod
    def _profile_groups(
        authority: dict[str, Any],
        profiles: set[str] | frozenset[str],
    ) -> list[dict[str, Any]]:
        return [
            dict(item) for item in authority.get("grant_groups", [])
            if isinstance(item, dict) and str(item.get("profile_key") or "") in profiles
        ]

    @staticmethod
    def _grant_for_revoke(
        authority: dict[str, Any],
        grant_group_id: str,
        profiles: set[str] | frozenset[str],
    ) -> dict[str, Any]:
        group_id = str(grant_group_id or "").strip().lower()
        matches = [
            group for group in StudioProductApi._profile_groups(authority, profiles)
            if str(group.get("grant_group_id") or "").lower() == group_id
        ]
        if len(matches) != 1:
            raise ProductAccountError(
                "owner_grant_group_not_found",
                "A autorização informada não pertence ao perfil e dispositivo atuais.",
            )
        return matches[0]

    def connect_product_account(self, email: str, password: str) -> dict[str, Any]:
        try:
            session = sign_in_with_password(email, password)
            data = connect_existing_device(
                self.agent.config,
                email,
                password,
                session=session,
            )
        except ProductAccountError as error:
            return {"ok": False, "code": error.code, "summary": error.message}
        except Exception as error:
            return {
                "ok": False,
                "code": "product_account_unexpected_error",
                "summary": f"{type(error).__name__}: não foi possível conectar a conta ORDAX",
            }
        self._product_session = session
        self._product_device_id = str(data.get("device_id") or "") or None
        if data.get("enrolled_now"):
            data["runtime_restarted"] = _restart_packaged_runtime_after_enrollment()
            data["runtime_restart_required"] = not data["runtime_restarted"]
        return {"ok": True, "summary": "Conta ORDAX conectada a este computador", "data": data}

    def assistant_catalog(self) -> dict[str, Any]:
        result = super().assistant_catalog()
        if not result.get("ok"):
            return result
        data = dict(result.get("data") or {})
        accounts = [{
            "id": "ordax",
            "label": "Conta ORDAX",
            "connected": isinstance(getattr(self, "_product_session", None), ProductAuthSession),
        }]
        accounts.extend(list(data.get("accounts") or []))
        data["accounts"] = accounts
        result["data"] = data
        return result

    def _assistant_defaults(self) -> tuple[str, str, str]:
        account_id = (
            "ordax"
            if isinstance(getattr(self, "_product_session", None), ProductAuthSession)
            else "local"
        )
        return (account_id, "openai", "gpt-4o")

    def _owner_authority(self) -> tuple[ProductAuthSession, str, dict[str, Any]]:
        session = self._product_session_required()
        device_id = self._current_product_device_id()
        with ProductRemoteClient(str(self.agent.config.product_mcp_url or "")) as remote:
            authority = remote.owner_remote_authority(session.access_token, device_id=device_id)
        return session, device_id, authority

    def remote_computer_grants(self) -> dict[str, Any]:
        try:
            _session, device_id, authority = self._owner_authority()
            grants = self._profile_groups(authority, _OWNER_REMOTE_COMPUTER_GRANT_MODES)
        except ProductAccountError as error:
            return {"ok": False, "code": error.code, "summary": error.message}
        except ProductRemoteError as error:
            return self._safe_product_error(error)
        except (ValueError, httpx.HTTPError) as error:
            return {
                "ok": False, "code": "product_remote_unavailable",
                "summary": f"{type(error).__name__}: autorização remota indisponível",
            }
        return {
            "ok": True,
            "summary": "Autorizações remotas carregadas",
            "data": {
                "device_id": device_id,
                "clients": authority["clients"],
                "grant_groups": grants,
                "available_modes": sorted(_OWNER_REMOTE_COMPUTER_GRANT_MODES),
            },
        }

    def authorize_remote_computer_grant(
        self,
        mode: str,
        client_id: str | None = None,
        expires_days: int = 30,
    ) -> dict[str, Any]:
        normalized_mode = str(mode or "").strip()
        if normalized_mode not in _OWNER_REMOTE_COMPUTER_GRANT_MODES:
            return {
                "ok": False,
                "code": "owner_device_grant_mode_not_allowed",
                "summary": "Perfil de autorização remota não permitido nesta interface.",
            }
        if normalized_mode == "full-computer-control":
            try:
                local_policy = computer_access_management_status(self.agent.config)
            except (OSError, ValueError):
                return {
                    "ok": False,
                    "code": "local_computer_access_unavailable",
                    "summary": "A política local de acesso ao computador está indisponível. Corrija as configurações antes de autorizar acesso total remoto.",
                }
            if not bool(local_policy.get("enabled")) or not bool(local_policy.get("full_access")):
                return {
                    "ok": False,
                    "code": "local_full_access_required",
                    "summary": "Ative e salve Full Access local antes de autorizar acesso total remoto.",
                }
        try:
            expires_at = self._grant_expiry(expires_days, "owner_device_grant_expiry_invalid")
            session = self._product_session_required()
            device_id = self._current_product_device_id()
            with ProductRemoteClient(str(self.agent.config.product_mcp_url or "")) as remote:
                authority = remote.owner_remote_authority(session.access_token, device_id=device_id)
                client = self._select_owner_mcp_client(authority, client_id)
                created = remote.replace_owner_remote_grant_group(
                    session.access_token,
                    client_id=str(client.get("client_id") or ""),
                    device_id=device_id,
                    profile_key=normalized_mode,
                    valid_until=expires_at,
                )
        except ProductAccountError as error:
            return {"ok": False, "code": error.code, "summary": error.message}
        except ProductRemoteError as error:
            return self._safe_product_error(error)
        except (ValueError, httpx.HTTPError) as error:
            return {
                "ok": False, "code": "product_remote_unavailable",
                "summary": f"{type(error).__name__}: autorização remota indisponível",
            }
        return {"ok": True, "summary": "Perfil remoto autorizado", "data": created}

    def revoke_remote_computer_grant(self, grant_group_id: str) -> dict[str, Any]:
        try:
            session = self._product_session_required()
            device_id = self._current_product_device_id()
            with ProductRemoteClient(str(self.agent.config.product_mcp_url or "")) as remote:
                authority = remote.owner_remote_authority(session.access_token, device_id=device_id)
                group = self._grant_for_revoke(
                    authority, grant_group_id, _OWNER_REMOTE_COMPUTER_GRANT_MODES,
                )
                result = remote.revoke_owner_remote_grant_group(
                    session.access_token,
                    client_id=str(group.get("client_id") or ""),
                    grant_group_id=str(group.get("grant_group_id") or ""),
                )
        except ProductAccountError as error:
            return {"ok": False, "code": error.code, "summary": error.message}
        except ProductRemoteError as error:
            return self._safe_product_error(error)
        except (ValueError, httpx.HTTPError) as error:
            return {
                "ok": False, "code": "product_remote_unavailable",
                "summary": f"{type(error).__name__}: autorização remota indisponível",
            }
        return {"ok": True, "summary": "Autorização remota revogada", "data": result}

    def remote_app_intelligence_grants(self) -> dict[str, Any]:
        try:
            _session, device_id, authority = self._owner_authority()
            grants = self._profile_groups(authority, frozenset({_OWNER_APP_INTELLIGENCE_GRANT_MODE}))
        except ProductAccountError as error:
            return {"ok": False, "code": error.code, "summary": error.message}
        except ProductRemoteError as error:
            return self._safe_product_error(error)
        except (ValueError, httpx.HTTPError) as error:
            return {
                "ok": False, "code": "product_remote_unavailable",
                "summary": f"{type(error).__name__}: autorização de inteligência dos apps indisponível",
            }
        return {
            "ok": True,
            "summary": "Autorizações de inteligência dos apps carregadas",
            "data": {
                "device_id": device_id,
                "clients": authority["clients"],
                "grant_groups": grants,
                "available_modes": [_OWNER_APP_INTELLIGENCE_GRANT_MODE],
            },
        }

    def authorize_remote_app_intelligence_grant(
        self,
        client_id: str | None = None,
        expires_days: int = 30,
    ) -> dict[str, Any]:
        try:
            expires_at = self._grant_expiry(expires_days, "owner_intelligence_grant_expiry_invalid")
            session = self._product_session_required()
            device_id = self._current_product_device_id()
            with ProductRemoteClient(str(self.agent.config.product_mcp_url or "")) as remote:
                authority = remote.owner_remote_authority(session.access_token, device_id=device_id)
                client = self._select_owner_mcp_client(authority, client_id)
                created = remote.replace_owner_remote_grant_group(
                    session.access_token,
                    client_id=str(client.get("client_id") or ""),
                    device_id=device_id,
                    profile_key=_OWNER_APP_INTELLIGENCE_GRANT_MODE,
                    valid_until=expires_at,
                )
        except ProductAccountError as error:
            return {"ok": False, "code": error.code, "summary": error.message}
        except ProductRemoteError as error:
            return self._safe_product_error(error)
        except (ValueError, httpx.HTTPError) as error:
            return {
                "ok": False, "code": "product_remote_unavailable",
                "summary": f"{type(error).__name__}: autorização de inteligência dos apps indisponível",
            }
        return {"ok": True, "summary": "Leitura de inteligência dos apps autorizada", "data": created}

    def revoke_remote_app_intelligence_grant(self, grant_group_id: str) -> dict[str, Any]:
        try:
            session = self._product_session_required()
            device_id = self._current_product_device_id()
            with ProductRemoteClient(str(self.agent.config.product_mcp_url or "")) as remote:
                authority = remote.owner_remote_authority(session.access_token, device_id=device_id)
                group = self._grant_for_revoke(
                    authority, grant_group_id, frozenset({_OWNER_APP_INTELLIGENCE_GRANT_MODE}),
                )
                result = remote.revoke_owner_remote_grant_group(
                    session.access_token,
                    client_id=str(group.get("client_id") or ""),
                    grant_group_id=str(group.get("grant_group_id") or ""),
                )
        except ProductAccountError as error:
            return {"ok": False, "code": error.code, "summary": error.message}
        except ProductRemoteError as error:
            return self._safe_product_error(error)
        except (ValueError, httpx.HTTPError) as error:
            return {
                "ok": False, "code": "product_remote_unavailable",
                "summary": f"{type(error).__name__}: autorização de inteligência dos apps indisponível",
            }
        return {"ok": True, "summary": "Autorização de inteligência dos apps revogada", "data": result}

    def remote_project_browser_grants(self) -> dict[str, Any]:
        try:
            _session, device_id, authority = self._owner_authority()
            grants = self._profile_groups(authority, frozenset({_OWNER_PROJECT_BROWSER_GRANT_MODE}))
        except ProductAccountError as error:
            return {"ok": False, "code": error.code, "summary": error.message}
        except ProductRemoteError as error:
            return self._safe_product_error(error)
        except (ValueError, httpx.HTTPError) as error:
            return {
                "ok": False, "code": "product_remote_unavailable",
                "summary": f"{type(error).__name__}: autorização do navegador indisponível",
            }
        return {
            "ok": True,
            "summary": "Autorizações do navegador gerenciado carregadas",
            "data": {
                "device_id": device_id,
                "project": str(self.project or "").strip(),
                "clients": authority["clients"],
                "grant_groups": grants,
                "available_modes": [_OWNER_PROJECT_BROWSER_GRANT_MODE],
                "requires_canonical_project_scope": True,
            },
        }

    def authorize_remote_project_browser_grant(
        self,
        client_id: str | None = None,
        space_id: str | None = None,
        project_id: str | None = None,
        expires_days: int = 30,
    ) -> dict[str, Any]:
        if not str(space_id or "").strip() or not str(project_id or "").strip():
            return {
                "ok": False,
                "code": "canonical_project_scope_required",
                "summary": "A autorização do navegador exige Space e projeto canônicos; o slug local não é autoridade.",
            }
        try:
            expires_at = self._grant_expiry(expires_days, "owner_project_grant_expiry_invalid")
            session = self._product_session_required()
            device_id = self._current_product_device_id()
            with ProductRemoteClient(str(self.agent.config.product_mcp_url or "")) as remote:
                authority = remote.owner_remote_authority(session.access_token, device_id=device_id)
                client = self._select_owner_mcp_client(authority, client_id)
                created = remote.replace_owner_remote_grant_group(
                    session.access_token,
                    client_id=str(client.get("client_id") or ""),
                    device_id=device_id,
                    profile_key=_OWNER_PROJECT_BROWSER_GRANT_MODE,
                    space_id=str(space_id),
                    project_id=str(project_id),
                    valid_until=expires_at,
                )
        except ProductAccountError as error:
            return {"ok": False, "code": error.code, "summary": error.message}
        except ProductRemoteError as error:
            return self._safe_product_error(error)
        except (ValueError, httpx.HTTPError) as error:
            return {
                "ok": False, "code": "product_remote_unavailable",
                "summary": f"{type(error).__name__}: autorização do navegador indisponível",
            }
        return {
            "ok": True,
            "summary": "Navegador remoto autorizado para o escopo canônico informado",
            "data": created,
        }

    def revoke_remote_project_browser_grant(self, grant_group_id: str) -> dict[str, Any]:
        try:
            session = self._product_session_required()
            device_id = self._current_product_device_id()
            with ProductRemoteClient(str(self.agent.config.product_mcp_url or "")) as remote:
                authority = remote.owner_remote_authority(session.access_token, device_id=device_id)
                group = self._grant_for_revoke(
                    authority, grant_group_id, frozenset({_OWNER_PROJECT_BROWSER_GRANT_MODE}),
                )
                result = remote.revoke_owner_remote_grant_group(
                    session.access_token,
                    client_id=str(group.get("client_id") or ""),
                    grant_group_id=str(group.get("grant_group_id") or ""),
                )
        except ProductAccountError as error:
            return {"ok": False, "code": error.code, "summary": error.message}
        except ProductRemoteError as error:
            return self._safe_product_error(error)
        except (ValueError, httpx.HTTPError) as error:
            return {
                "ok": False, "code": "product_remote_unavailable",
                "summary": f"{type(error).__name__}: autorização do navegador indisponível",
            }
        return {"ok": True, "summary": "Autorização remota do navegador revogada", "data": result}

    def browser_list(self) -> dict[str, Any]:
        return self._result(self.agent.execute("browser.list", {"project": self.project}))

    def browser_start(
        self,
        url: str | None = None,
        headless: bool = False,
        wait_seconds: float = 3.0,
    ) -> dict[str, Any]:
        arguments: dict[str, Any] = {
            "project": self.project,
            "headless": bool(headless),
            "wait_seconds": float(wait_seconds),
        }
        normalized_url = str(url or "").strip()
        if normalized_url:
            arguments["url"] = normalized_url
        return self._result(self.agent.execute("browser.start", arguments))

    def browser_status(self, session_id: str) -> dict[str, Any]:
        return self._result(self.agent.execute(
            "browser.status",
            {"project": self.project, "session_id": str(session_id or "").strip()},
        ))

    def browser_navigate(
        self,
        session_id: str,
        url: str,
        wait_seconds: float = 3.0,
    ) -> dict[str, Any]:
        return self._result(self.agent.execute(
            "browser.navigate",
            {
                "project": self.project,
                "session_id": str(session_id or "").strip(),
                "url": str(url or "").strip(),
                "wait_seconds": float(wait_seconds),
            },
        ))

    def browser_snapshot(self, session_id: str, max_elements: int = 200) -> dict[str, Any]:
        return self._result(self.agent.execute(
            "browser.snapshot",
            {
                "project": self.project,
                "session_id": str(session_id or "").strip(),
                "max_elements": int(max_elements),
            },
        ))

    def browser_screenshot(
        self,
        session_id: str,
        width: int = 1280,
        height: int = 800,
    ) -> dict[str, Any]:
        return self._result(self.agent.execute(
            "browser.screenshot",
            {
                "project": self.project,
                "session_id": str(session_id or "").strip(),
                "width": int(width),
                "height": int(height),
            },
        ))

    def browser_stop(self, session_id: str) -> dict[str, Any]:
        return self._result(self.agent.execute(
            "browser.stop",
            {"project": self.project, "session_id": str(session_id or "").strip()},
        ))

    def blender_prepare(self) -> dict[str, Any]:
        return prepare_blender_connection(self.agent, self.project, wait_seconds=4.0)

    def blender_install_bridge(self) -> dict[str, Any]:
        project = self.agent.projects[self.project]
        if "blender" not in project.apps:
            return {"ok": False, "summary": "Blender não está habilitado neste projeto"}
        installed = self.agent.execute("blender.adoption_install", {})
        if not installed.ok:
            return self._result(installed)
        connection = self.blender_prepare()
        return {
            "ok": True,
            "summary": installed.summary,
            "data": {
                "installation": installed.data,
                "connection": connection.get("data", {}),
            },
        }

    def blender_instances(self) -> dict[str, Any]:
        return self._result(self.agent.execute("blender.instances", {}))

    def blender_adopt(self, pid: int, allow_blank: bool = False) -> dict[str, Any]:
        try:
            selected_pid = int(pid)
        except (TypeError, ValueError):
            return {"ok": False, "summary": "PID do Blender inválido"}
        if selected_pid <= 0:
            return {"ok": False, "summary": "PID do Blender inválido"}

        adopted = self.agent.execute(
            "blender.adopt",
            {
                "project": self.project,
                "pid": selected_pid,
                "allow_blank": bool(allow_blank),
                "wait_seconds": 8.0,
            },
        )
        if not adopted.ok:
            return self._result(adopted)
        presence = adopted.data.get("presence") or {}
        return {
            "ok": True,
            "summary": adopted.summary,
            "data": {
                "state": "adopted_blank" if allow_blank else "adopted",
                "project": self.project,
                "pid": selected_pid,
                "file": presence.get("file"),
                "can_start": False,
                "can_capture": True,
                "requires_restart": False,
            },
        }

    def blender_start(self) -> dict[str, Any]:
        project = self.agent.projects[self.project]
        if "blender" not in project.apps:
            return {"ok": False, "summary": "Blender não está habilitado neste projeto"}

        started = self.agent.execute(
            "blender.live_start",
            {"project": self.project, "wait_seconds": 60.0},
        )
        if not started.ok:
            return self._result(started)

        return prepare_blender_connection(
            self.agent,
            self.project,
            wait_seconds=4.0,
        )


def main() -> int:
    try:
        import webview
    except ImportError as error:
        raise SystemExit("pywebview is required for ORDAX Dev") from error

    config = AgentConfig.from_env()
    lock = SingleInstanceLock(config.state_dir / "studio-web.lock")
    if not lock.acquire():
        return 0
    try:
        api = StudioProductApi(ActionRegistry(config))
        html = Path(__file__).with_name("studio_product.html").resolve()
        if not html.is_file():
            raise SystemExit(
                "UI canonica do Studio nao instalada. Use o instalador oficial do ORDAX Studio; "
                "o source do Runtime nao inclui HTML/assets portateis."
            )
        webview.create_window(
            APP_NAME,
            url=html.as_uri(),
            js_api=api,
            width=1600,
            height=960,
            min_size=(1180, 720),
        )
        webview.start(gui="edgechromium", debug=False)
        return 0
    finally:
        lock.release()


if __name__ == "__main__":
    raise SystemExit(main())
