from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import httpx

from ordax_core.orchestrator import OrchestratorStore
from ordax_dev_agent.actions import ActionRegistry
from ordax_dev_agent.config import AgentConfig
from ordax_dev_agent.computer_filesystem_actions import (
    computer_access_management_status,
    update_computer_access_policy,
)

from .blender_connection import prepare_blender_connection
from .instance_lock import SingleInstanceLock

APP_NAME = "ORDAX Studio"


class StudioApi:
    def __init__(self, agent: ActionRegistry | None = None):
        self.agent = agent or ActionRegistry(AgentConfig.from_env())
        self.store = self.agent._memory_store_instance()
        self.orchestrator = OrchestratorStore(self.store.db_path)
        # Keep one usable project for diagnostics/API compatibility, but do not
        # mutate the user's persisted active project merely by opening Studio.
        self.project = self.agent.select_available_project()
        self.session_id: int | None = None

    @staticmethod
    def _result(result) -> dict[str, Any]:
        return {"ok": result.ok, "summary": result.summary, "data": result.data}

    def _project_card(self, project) -> dict[str, Any]:
        card = project.public()
        card["preview_mode"] = self.agent._preview_mode(project)
        if not project.root.is_dir():
            card["repository"] = {"is_repository": False, "available": False}
            return card
        repository = self.agent.execute("git.repository_info", {"project": project.slug})
        card["repository"] = repository.data if repository.ok else {
            "is_repository": False, "error": repository.summary,
        }
        return card

    def projects_catalog(self) -> dict[str, Any]:
        result = self.agent.execute("workspace.repository_catalog", {})
        if not result.ok:
            return {"ok": False, "summary": result.summary, "projects": []}
        startup = self.startup_project()
        return {
            "ok": True,
            **result.data,
            "startup_project": startup.get("project") if startup.get("ok") else None,
        }

    def startup_project(self) -> dict[str, Any]:
        requested = str(os.environ.get("ORDAX_STUDIO_OPEN_PROJECT") or "").strip()
        if not requested:
            return {"ok": True, "project": None}
        try:
            selected = self.agent.select_available_project(requested)
        except Exception as error:
            return {"ok": False, "summary": f"{type(error).__name__}: {error}", "project": None}
        return {"ok": True, "project": selected}

    def _activate(self, slug: str) -> None:
        project = self.agent._project({"project": slug})
        self.project = slug
        self.store.set_active_project(slug, project.root)
        resumed = self.agent.execute("session.resume", {"project": slug})
        if resumed.ok:
            self.session_id = int(resumed.data["session_id"])

    def bootstrap(self) -> dict[str, Any]:
        # bootstrap means a caller is actually entering this project context.
        if self.session_id is None:
            self._activate(self.project)
        project = self.agent.projects[self.project]
        context = self.store.context(project.slug, project.root)
        preview = self.agent.execute("project.preview_status", {"project": self.project})
        health = self.agent.execute("agent.project_health", {"project": self.project})
        modeling = (
            self.agent.execute("blender.live_modeling_schema", {"project": self.project})
            if "blender" in project.apps
            else None
        )
        return {
            "product": APP_NAME,
            "project": self._project_card(project),
            "projects": [self._project_card(item) for item in self.agent.projects.values()],
            "session_id": self.session_id,
            "memory": {
                "memories": context.get("memories", []),
                "tasks": context.get("tasks", []),
                "checkpoints": context.get("checkpoints", []),
            },
            "preview": self._result(preview),
            "health": self._result(health),
            "modeling": self._result(modeling) if modeling is not None else {"ok": True, "data": None},
        }

    def select_project(self, slug: str) -> dict[str, Any]:
        try:
            selected = self.agent.select_available_project(slug)
            self._activate(selected)
            return {"ok": True, "data": self.bootstrap()}
        except Exception as error:
            return {"ok": False, "summary": f"{type(error).__name__}: {error}"}

    def inventory(self) -> dict[str, Any]:
        return self._result(self.agent.execute("project.inventory", {
            "project": self.project, "max_depth": 5, "max_entries": 900,
        }))

    def read_file(self, path: str) -> dict[str, Any]:
        return self._result(self.agent.execute("project.text_read", {
            "project": self.project, "path": path,
        }))

    def save_file(self, path: str, content: str, expected_sha256: str) -> dict[str, Any]:
        return self._result(self.agent.execute("project.text_write", {
            "project": self.project, "path": path, "content": content,
            "expected_sha256": expected_sha256,
        }))

    def preview_status(self) -> dict[str, Any]:
        return self._result(self.agent.execute("project.preview_status", {"project": self.project}))

    def blender_prepare(self) -> dict[str, Any]:
        return prepare_blender_connection(self.agent, self.project, wait_seconds=4.0)

    def preview_start(self) -> dict[str, Any]:
        return self._result(self.agent.execute("project.preview_start", {"project": self.project}))

    def preview_stop(self) -> dict[str, Any]:
        return self._result(self.agent.execute("project.preview_stop", {"project": self.project}))

    def preview_capture(self) -> dict[str, Any]:
        captured = self.agent.execute("project.preview_capture", {"project": self.project})
        return self._result(captured)

    def preview_logs(self, max_bytes: int = 32768) -> dict[str, Any]:
        return self._result(self.agent.execute("project.preview_logs", {
            "project": self.project, "max_bytes": max_bytes,
        }))

    def preview_image(self) -> dict[str, Any]:
        status = self.agent.execute("project.preview_status", {"project": self.project})
        if not status.ok or not status.data.get("latest_image"):
            return {"ok": False, "summary": "Nenhuma imagem de preview disponível"}
        image = status.data["latest_image"]
        result = self.agent.execute("artifact.preview", {
            "project": self.project,
            **image["artifact_preview_payload"],
            "thumbnail": True,
            "max_width": 1600,
            "max_height": 1200,
        })
        if not result.ok:
            return self._result(result)
        return {
            "ok": True,
            "data": {
                "src": f"data:{result.data['mime_type']};base64,{result.data['base64']}",
                "artifact": image.get("path"),
                "revision": image.get("modified_at_ns"),
            },
        }

    def execution_status(self) -> dict[str, Any]:
        processes = self.agent.execute("process.list", {"project": self.project})
        browsers = self.agent.execute("browser.list", {"project": self.project})
        preview = self.agent.execute("project.preview_status", {"project": self.project})

        process_items: list[dict[str, Any]] = []
        if processes.ok:
            for item in processes.data.get("processes", []):
                if not isinstance(item, dict):
                    continue
                process_items.append({
                    key: item.get(key)
                    for key in (
                        "process_id",
                        "state",
                        "running",
                        "ownership_valid",
                        "manager_pid",
                        "child_pid",
                        "cwd",
                        "argv",
                        "started_at_unix",
                    )
                    if key in item
                })

        browser_items: list[dict[str, Any]] = []
        if browsers.ok:
            for item in browsers.data.get("sessions", []):
                if not isinstance(item, dict):
                    continue
                browser_items.append({
                    key: item.get(key)
                    for key in (
                        "session_id",
                        "running",
                        "ownership_valid",
                        "url",
                        "title",
                        "browser_pid",
                        "created_at_unix",
                    )
                    if key in item
                })

        preview_data = preview.data if preview.ok and isinstance(preview.data, dict) else {}
        runtime = preview_data.get("runtime") if isinstance(preview_data.get("runtime"), dict) else {}
        return {
            "ok": True,
            "data": {
                "project": self.project,
                "processes": process_items,
                "browsers": browser_items,
                "preview": {
                    "mode": preview_data.get("mode"),
                    "url": preview_data.get("url"),
                    "runtime": {
                        key: runtime.get(key)
                        for key in ("state", "running", "pid", "url_ready")
                        if key in runtime
                    },
                    "has_latest_image": bool(preview_data.get("latest_image")),
                },
            },
        }

    def task_add(self, title: str) -> dict[str, Any]:
        title = str(title or "").strip()
        if not title:
            return {"ok": False, "summary": "A tarefa não pode ficar vazia"}
        return self._result(self.agent.execute("memory.task_add", {
            "project": self.project, "title": title,
        }))

    def checkpoint(self, summary: str) -> dict[str, Any]:
        return self._result(self.agent.execute("memory.checkpoint", {
            "project": self.project, "summary": summary,
        }))

    def ai_sessions_status(self) -> dict[str, Any]:
        try:
            data = self.orchestrator.status(self.project)
            continuations = []
            for session in data.get("active_sessions", []):
                continuation = self.orchestrator.continuation_bundle(str(session["id"]))
                continuations.append(continuation)
            data["continuations"] = continuations
        except Exception as error:
            return {"ok": False, "summary": f"{type(error).__name__}: {error}", "data": {}}
        return {"ok": True, "data": data}

    def computer_access_settings(self) -> dict[str, Any]:
        try:
            data = computer_access_management_status(self.agent.config)
        except Exception as error:
            return {
                "ok": False,
                "summary": f"{type(error).__name__}: {error}",
                "data": {},
            }
        return {"ok": True, "summary": "Política local carregada", "data": data}

    def save_computer_access_settings(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(payload, dict):
            return {"ok": False, "summary": "Configuração de acesso inválida", "data": {}}
        try:
            data = update_computer_access_policy(self.agent.config, payload)
        except Exception as error:
            return {
                "ok": False,
                "summary": f"{type(error).__name__}: {error}",
                "data": {},
            }
        return {
            "ok": True,
            "summary": "Política local de acesso ao computador atualizada",
            "data": data,
        }

    def product_status(self) -> dict[str, Any]:
        config = self.agent.config
        resilience = self.agent.execute("agent.resilience_status", {"timeout_seconds": 5})
        resilience_data = resilience.data.get("resilience", {}) if resilience.ok else {}
        scheduled = resilience_data.get("scheduled_task") or {}
        local_health = resilience_data.get("local_health") or {}
        runtime_status: dict[str, Any] = {}
        try:
            local_response = httpx.get(
                "http://127.0.0.1:8765/status",
                timeout=1.5,
                follow_redirects=False,
            )
            local_payload = (
                local_response.json()
                if local_response.is_success
                and local_response.headers.get("content-type", "").startswith("application/json")
                else {}
            )
            if isinstance(local_payload, dict) and isinstance(local_payload.get("runtime"), dict):
                runtime_status = dict(local_payload["runtime"])
        except (httpx.HTTPError, ValueError):
            runtime_status = {}

        transport_state = str(runtime_status.get("transport_state") or "unknown")
        runtime_state = str(runtime_status.get("state") or "unknown")
        device_agent = {
            "ok": bool(scheduled.get("exists")) and str(scheduled.get("state") or "").lower() == "running" and bool(local_health),
            "configured": bool(config.device_id),
            "scheduled_task_exists": bool(scheduled.get("exists")),
            "scheduled_task_state": scheduled.get("state"),
            "local_health": bool(local_health),
            "runtime_reachable": bool(runtime_status),
            "runtime_state": runtime_state,
            "paired": bool(runtime_status.get("paired")),
            "transport_state": transport_state,
            "last_transport_error": runtime_status.get("last_transport_error"),
            "last_transport_recovered_at": runtime_status.get("last_transport_recovered_at"),
            "summary": "Device Agent ativo" if local_health else (resilience.summary if not resilience.ok else "Device Agent sem health local"),
        }

        base_url = str(config.control_plane_url or "").rstrip("/")
        remote_mcp = {
            "ok": False,
            "endpoint": f"{base_url}/mcp" if base_url else None,
            "oauth": False,
            "typed_actions_v2": False,
            "summary": "Control Plane não configurado",
        }
        if base_url:
            try:
                response = httpx.get(f"{base_url}/health", timeout=4.0, follow_redirects=False)
                payload = response.json() if response.headers.get("content-type", "").startswith("application/json") else {}
                capabilities = payload.get("capabilities") if isinstance(payload, dict) else []
                capabilities = capabilities if isinstance(capabilities, list) else []
                remote_ok = bool(response.is_success and isinstance(payload, dict) and payload.get("ok") is True)
                remote_mcp.update({
                    "ok": remote_ok,
                    "oauth": bool(isinstance(payload, dict) and payload.get("product_auth_configured")),
                    "typed_actions_v2": "product_typed_actions_v2" in capabilities,
                    "summary": "Remote MCP online" if remote_ok else f"Remote MCP HTTP {response.status_code}",
                })
            except (httpx.HTTPError, ValueError) as error:
                remote_mcp["summary"] = f"Remote MCP indisponível: {type(error).__name__}"

        action_names = set(self.agent.names)
        desktop_actions = sorted(
            name for name in action_names
            if name.startswith(("computer.", "terminal.", "browser.", "process."))
        )
        core_desktop = {
            "computer.windows", "computer.active_window", "computer.screenshot",
            "computer.focus_window", "computer.click", "computer.scroll",
            "computer.type", "computer.hotkey", "computer.processes",
            "computer.access_status", "computer.directory_list", "computer.text_read",
            "computer.screen_info", "computer.mouse_move", "computer.drag",
            "computer.clipboard_read", "computer.clipboard_write", "computer.launch_app",
        }
        computer_control = {
            "ok": os.name == "nt" and core_desktop.issubset(action_names),
            "platform_supported": os.name == "nt",
            "action_count": len(desktop_actions),
            "screen_and_windows": all(name in action_names for name in (
                "computer.windows", "computer.active_window", "computer.screenshot",
                "computer.focus_window", "computer.screen_info",
            )),
            "input": all(name in action_names for name in (
                "computer.click", "computer.scroll", "computer.type",
                "computer.hotkey", "computer.mouse_move", "computer.drag",
            )),
            "filesystem": all(name in action_names for name in (
                "computer.access_status", "computer.directory_list", "computer.text_read",
            )),
            "clipboard_and_apps": all(name in action_names for name in (
                "computer.clipboard_read", "computer.clipboard_write", "computer.launch_app",
            )),
            "terminal": "terminal.exec" in action_names,
            "summary": (
                "Computer Control disponível via grants MCP"
                if os.name == "nt" and core_desktop.issubset(action_names)
                else "Computer Control parcial ou indisponível"
            ),
        }

        project_health = self.agent.execute("agent.project_health", {"project": self.project})
        adapters = project_health.data.get("adapters", {}) if project_health.ok else {}
        blender = adapters.get("blender") or {}
        blender_live = {
            "ok": bool(blender.get("enabled") and blender.get("state") == "ready"),
            "enabled": bool(blender.get("enabled")),
            "state": blender.get("state") or "disabled",
            "blender_version": blender.get("blender_version"),
            "file": blender.get("file"),
            "summary": blender.get("summary") or ("Blender não habilitado neste projeto" if not blender.get("enabled") else "Blender Live indispon?vel"),
        }
        return {
            "ok": True,
            "data": {
                "device_agent": device_agent,
                "remote_mcp": remote_mcp,
                "computer_control": computer_control,
                "blender_live": blender_live,
            },
        }

    def health(self) -> dict[str, Any]:
        return self._result(self.agent.execute("agent.project_health", {"project": self.project}))

    def briefing(self) -> dict[str, Any]:
        return self._result(self.agent.execute("agent.project_briefing", {"project": self.project}))

    def search(self, query: str, max_results: int = 50) -> dict[str, Any]:
        return self._result(self.agent.execute("project.search_text", {
            "project": self.project, "query": query, "max_results": max_results,
        }))

    def git_diff(self) -> dict[str, Any]:
        return self._result(self.agent.execute("git.diff", {"project": self.project}))

    def memory_context(self) -> dict[str, Any]:
        return self._result(self.agent.execute("memory.context", {"project": self.project}))


def main() -> int:
    try:
        import webview
    except ImportError as error:
        raise SystemExit("pywebview is required for ORDAX Studio WebView shell") from error
    config = AgentConfig.from_env()
    lock = SingleInstanceLock(config.state_dir / "studio-web.lock")
    if not lock.acquire():
        return 0
    try:
        api = StudioApi(ActionRegistry(config))
        html = Path(__file__).with_name("studio.html").resolve()
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
