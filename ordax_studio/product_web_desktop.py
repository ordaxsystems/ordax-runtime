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
    "interactive-computer-control",
    "computer-filesystem",
    "computer-clipboard",
    "computer-process-control",
})


class StudioProductApi(StudioApi):
    """Windows product surface layered over the canonical Studio API.

    The base Studio API remains provider-neutral. Account authentication,
    device ownership and interactive desktop lifecycle operations live here so
    development hosts do not need to own or store Product credentials.
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
                "Conecte sua conta ORDAX nesta sess?o para gerenciar autoriza??es remotas.",
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
                "Este computador ainda n?o possui identidade ORDAX ativa.",
            )
        return device_id

    @staticmethod
    def _safe_product_error(error: ProductRemoteError) -> dict[str, Any]:
        return {
            "ok": False,
            "code": error.error_code,
            "summary": "O Control Plane recusou a opera??o de autoriza??o remota.",
        }

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
            return {
                "ok": False,
                "code": error.code,
                "summary": error.message,
            }
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
        return {
            "ok": True,
            "summary": "Conta ORDAX conectada a este computador",
            "data": data,
        }

    def remote_computer_grants(self) -> dict[str, Any]:
        try:
            session = self._product_session_required()
            device_id = self._current_product_device_id()
            with ProductRemoteClient(str(self.agent.config.control_plane_url or "")) as remote:
                links = [
                    link for link in remote.device_links(session.access_token)
                    if str(link.get("device_id") or "") == device_id
                ]
                grants = [
                    grant for grant in remote.device_computer_grants(session.access_token)
                    if str(grant.get("device_id") or "") == device_id
                ]
        except ProductAccountError as error:
            return {"ok": False, "code": error.code, "summary": error.message}
        except ProductRemoteError as error:
            return self._safe_product_error(error)
        except (ValueError, httpx.HTTPError) as error:
            return {
                "ok": False,
                "code": "product_remote_unavailable",
                "summary": f"{type(error).__name__}: autoriza??o remota indispon?vel",
            }
        return {
            "ok": True,
            "summary": "Autoriza??es remotas carregadas",
            "data": {
                "device_id": device_id,
                "links": links,
                "grants": grants,
                "available_modes": sorted(_OWNER_REMOTE_COMPUTER_GRANT_MODES),
            },
        }

    def authorize_remote_computer_grant(
        self,
        mode: str,
        link_id: str | None = None,
        expires_days: int = 30,
    ) -> dict[str, Any]:
        normalized_mode = str(mode or "").strip()
        if normalized_mode not in _OWNER_REMOTE_COMPUTER_GRANT_MODES:
            return {
                "ok": False,
                "code": "owner_device_grant_mode_not_allowed",
                "summary": "Perfil de autoriza??o remota n?o permitido nesta interface.",
            }
        try:
            days = int(expires_days)
        except (TypeError, ValueError):
            return {
                "ok": False,
                "code": "owner_device_grant_expiry_invalid",
                "summary": "A validade deve ser informada em dias inteiros.",
            }
        if not 1 <= days <= 365:
            return {
                "ok": False,
                "code": "owner_device_grant_expiry_invalid",
                "summary": "A validade deve ficar entre 1 e 365 dias.",
            }

        try:
            session = self._product_session_required()
            device_id = self._current_product_device_id()
            with ProductRemoteClient(str(self.agent.config.control_plane_url or "")) as remote:
                links = [
                    link for link in remote.device_links(session.access_token)
                    if str(link.get("device_id") or "") == device_id
                ]
                requested_link = str(link_id or "").strip()
                if requested_link:
                    links = [link for link in links if str(link.get("link_id") or "") == requested_link]
                if len(links) != 1:
                    code = "product_device_link_not_found" if not links else "product_device_link_ambiguous"
                    summary = (
                        "Nenhum v?nculo ativo desta conta corresponde a este computador."
                        if not links else
                        "H? mais de um v?nculo ativo para este computador; selecione o v?nculo explicitamente."
                    )
                    return {"ok": False, "code": code, "summary": summary}
                selected_link = str(links[0].get("link_id") or "")
                expires_at = (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()
                created = remote.create_device_computer_grant(
                    session.access_token,
                    link_id=selected_link,
                    mode=normalized_mode,
                    expires_at=expires_at,
                )
        except ProductAccountError as error:
            return {"ok": False, "code": error.code, "summary": error.message}
        except ProductRemoteError as error:
            return self._safe_product_error(error)
        except (ValueError, httpx.HTTPError) as error:
            return {
                "ok": False,
                "code": "product_remote_unavailable",
                "summary": f"{type(error).__name__}: autoriza??o remota indispon?vel",
            }
        return {
            "ok": True,
            "summary": "Perfil remoto autorizado",
            "data": created,
        }

    def revoke_remote_computer_grant(self, grant_id: str) -> dict[str, Any]:
        grant_id = str(grant_id or "").strip()
        if not grant_id or len(grant_id) > 128:
            return {
                "ok": False,
                "code": "owner_device_grant_id_invalid",
                "summary": "Identificador de autoriza??o inv?lido.",
            }
        try:
            session = self._product_session_required()
            with ProductRemoteClient(str(self.agent.config.control_plane_url or "")) as remote:
                result = remote.revoke_device_computer_grant(session.access_token, grant_id)
        except ProductAccountError as error:
            return {"ok": False, "code": error.code, "summary": error.message}
        except ProductRemoteError as error:
            return self._safe_product_error(error)
        except (ValueError, httpx.HTTPError) as error:
            return {
                "ok": False,
                "code": "product_remote_unavailable",
                "summary": f"{type(error).__name__}: autoriza??o remota indispon?vel",
            }
        return {
            "ok": True,
            "summary": "Autoriza??o remota revogada",
            "data": result,
        }

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
