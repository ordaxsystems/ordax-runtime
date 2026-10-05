from __future__ import annotations

from typing import Any

from ordax_core import MemoryStore

from .models import ActionResult


class MemoryActions:
    def _memory_store_instance(self) -> MemoryStore:
        store = getattr(self, "_ordax_memory_store", None)
        if store is None:
            store = MemoryStore()
            self._ordax_memory_store = store
        return store

    def memory_status(self, payload: dict[str, Any]) -> ActionResult:
        status = self._memory_store_instance().status()
        return ActionResult(True, "Persistent memory is available", status)

    def memory_context(self, payload: dict[str, Any]) -> ActionResult:
        project = self._project(payload)
        store = self._memory_store_instance()
        data = store.context(project.slug, project.root)
        data["context_path"] = str(store.write_context(project.slug, project.root))
        return ActionResult(True, f"Persistent context loaded for {project.slug}", data)

    def memory_remember(self, payload: dict[str, Any]) -> ActionResult:
        project = self._project(payload)
        content = str(payload.get("content") or "").strip()
        kind = str(payload.get("kind") or "note").strip()[:64] or "note"
        memory_id = self._memory_store_instance().remember(project.slug, project.root, content, kind)
        return ActionResult(True, f"Memory #{memory_id} saved for {project.slug}", {"memory_id": memory_id})

    def memory_task_add(self, payload: dict[str, Any]) -> ActionResult:
        project = self._project(payload)
        task_id = self._memory_store_instance().add_task(
            project.slug, project.root, str(payload.get("title") or "")
        )
        return ActionResult(True, f"Task #{task_id} saved for {project.slug}", {"task_id": task_id})

    def memory_task_toggle(self, payload: dict[str, Any]) -> ActionResult:
        project = self._project(payload)
        try:
            task_id = int(payload.get("task_id"))
        except (TypeError, ValueError) as error:
            raise ValueError("task_id must be an integer") from error
        done = self._memory_store_instance().toggle_task(project.slug, project.root, task_id)
        return ActionResult(True, f"Task #{task_id} updated", {"task_id": task_id, "done": done})

    def memory_checkpoint(self, payload: dict[str, Any]) -> ActionResult:
        project = self._project(payload)
        checkpoint_id = self._memory_store_instance().checkpoint(
            project.slug, project.root, str(payload.get("summary") or "")
        )
        return ActionResult(
            True,
            f"Checkpoint #{checkpoint_id} saved for {project.slug}",
            {"checkpoint_id": checkpoint_id},
        )


    def continuity_get(self, payload: dict[str, Any]) -> ActionResult:
        project = self._project(payload)
        state = self._memory_store_instance().project_state(project.slug, project.root)
        return ActionResult(
            True,
            f"Durable continuity loaded for {project.slug}",
            {"project": project.slug, "state": state},
        )

    def continuity_update(self, payload: dict[str, Any]) -> ActionResult:
        project = self._project(payload)
        for field in ("completed", "blockers", "changed_paths"):
            value = payload.get(field)
            if value is not None and not isinstance(value, list):
                return ActionResult(False, f"{field} must be a list", {"field": field})
        store = self._memory_store_instance()
        state = store.update_project_state(
            project.slug,
            project.root,
            str(payload.get("summary") or ""),
            next_action=str(payload.get("next_action") or ""),
            completed=payload.get("completed") or [],
            blockers=payload.get("blockers") or [],
            changed_paths=payload.get("changed_paths") or [],
            source="manual",
        )
        return ActionResult(
            True,
            f"Durable continuity updated for {project.slug}",
            {"project": project.slug, "state": state},
        )

    def handoff_create(self, payload: dict[str, Any]) -> ActionResult:
        project = self._project(payload)
        data = self._memory_store_instance().create_handoff(
            project.slug,
            project.root,
            str(payload.get("summary") or ""),
            next_action=str(payload.get("next_action") or ""),
            completed=payload.get("completed") if isinstance(payload.get("completed"), list) else [],
            blockers=payload.get("blockers") if isinstance(payload.get("blockers"), list) else [],
            changed_paths=payload.get("changed_paths") if isinstance(payload.get("changed_paths"), list) else [],
            ttl_hours=int(payload.get("ttl_hours", 24)),
        )
        return ActionResult(
            True,
            f"Handoff {data['handoff_id']} created for {project.slug}",
            data,
        )

    def handoff_get(self, payload: dict[str, Any]) -> ActionResult:
        project = self._project(payload)
        data = self._memory_store_instance().get_handoff(
            project.slug,
            project.root,
            str(payload.get("handoff_id") or ""),
        )
        return ActionResult(
            True,
            f"Handoff {data['handoff_id']} loaded for {project.slug}",
            data,
        )

    def session_resume(self, payload: dict[str, Any]) -> ActionResult:
        project = self._project(payload)
        data = self._memory_store_instance().start_session(project.slug, project.root)
        data["capabilities"] = {"apps": list(project.apps), "action_groups": sorted({name.split(".", 1)[0] for name in self.names})}
        return ActionResult(True, f"Session #{data['session_id']} resumed for {project.slug}", data)

    def session_finish(self, payload: dict[str, Any]) -> ActionResult:
        try:
            session_id = int(payload.get("session_id"))
        except (TypeError, ValueError) as error:
            raise ValueError("session_id must be an integer") from error
        ended = self._memory_store_instance().finish_session(session_id)
        if not ended:
            return ActionResult(False, f"Open session not found: {session_id}", {"session_id": session_id})
        return ActionResult(True, f"Session #{session_id} finished", {"session_id": session_id})
