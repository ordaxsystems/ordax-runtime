"""ActionRegistry integration for the persistent ORDAX agent orchestrator."""
from __future__ import annotations

from typing import Any

from ordax_core import OrchestratorStore

from .models import ActionResult


class AgentOrchestrationActions:
    def _orchestrator_store_instance(self) -> OrchestratorStore:
        store = getattr(self, "_ordax_orchestrator_store", None)
        if store is None:
            store = OrchestratorStore(self._memory_store_instance().db_path)
            self._ordax_orchestrator_store = store
        return store

    def orchestrator_status(self, payload: dict[str, Any]) -> ActionResult:
        project = self._project(payload)
        data = self._orchestrator_store_instance().status(project.slug)
        return ActionResult(True, f"Orchestrator status ready for {project.slug}", data)

    def orchestrator_agent_create(self, payload: dict[str, Any]) -> ActionResult:
        project = self._project(payload)
        data = self._orchestrator_store_instance().create_agent(
            project.slug,
            str(payload.get("name") or ""),
            str(payload.get("role") or ""),
            parent_agent_id=payload.get("parent_agent_id") or None,
        )
        return ActionResult(True, f"Agent {data['name']} created", data)

    def orchestrator_agent_state(self, payload: dict[str, Any]) -> ActionResult:
        data = self._orchestrator_store_instance().set_agent_state(
            str(payload.get("agent_id") or ""),
            str(payload.get("state") or ""),
        )
        return ActionResult(True, f"Agent {data['name']} is {data['state']}", data)

    def orchestrator_goal_create(self, payload: dict[str, Any]) -> ActionResult:
        project = self._project(payload)
        store = self._orchestrator_store_instance()
        agent = store.get_agent(str(payload.get("agent_id") or ""))
        if agent["project_slug"] != project.slug:
            return ActionResult(False, "agent does not belong to the requested project")
        data = store.create_goal(
            agent["id"],
            str(payload.get("title") or ""),
            str(payload.get("description") or ""),
            priority=int(payload.get("priority", 50)),
        )
        return ActionResult(True, f"Goal created: {data['title']}", data)

    def orchestrator_goal_state(self, payload: dict[str, Any]) -> ActionResult:
        data = self._orchestrator_store_instance().update_goal(
            str(payload.get("goal_id") or ""),
            state=str(payload.get("state") or ""),
        )
        return ActionResult(True, f"Goal is now {data['state']}", data)

    def orchestrator_session_start(self, payload: dict[str, Any]) -> ActionResult:
        store = self._orchestrator_store_instance()
        agent = store.get_agent(str(payload.get("agent_id") or ""))
        project = self._project({"project": agent["project_slug"]})
        if project.slug != agent["project_slug"]:
            return ActionResult(False, "agent project is unavailable")
        data = store.start_session(
            agent["id"],
            goal_id=payload.get("goal_id") or None,
            provider=str(payload.get("provider") or "openai"),
            model=str(payload.get("model") or ""),
            context_window_tokens=int(payload.get("context_window_tokens") or 0),
            rollover_ratio=float(payload.get("rollover_ratio", 0.80)),
            predecessor_session_id=payload.get("predecessor_session_id") or None,
        )
        return ActionResult(True, f"Agent session started for {agent['name']}", data)

    def orchestrator_session_usage(self, payload: dict[str, Any]) -> ActionResult:
        data = self._orchestrator_store_instance().record_usage(
            str(payload.get("session_id") or ""),
            input_tokens=int(payload.get("input_tokens", 0)),
            output_tokens=int(payload.get("output_tokens", 0)),
        )
        return ActionResult(
            True,
            "Session usage recorded; rollover recommended" if data["should_rollover"] else "Session usage recorded",
            data,
        )

    def _checkpoint_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        store = self._orchestrator_store_instance()
        session = store.session(str(payload.get("session_id") or ""))
        agent = store.get_agent(session["agent_id"])
        project = self._project({"project": agent["project_slug"]})
        return {
            "summary": str(payload.get("summary") or ""),
            "next_action": str(payload.get("next_action") or ""),
            "completed": payload.get("completed") or [],
            "blockers": payload.get("blockers") or [],
            "changed_paths": payload.get("changed_paths") or [],
            "git_state": self._memory_store_instance().git_state(project.root),
        }

    def _publish_project_continuity(self, checkpoint: dict[str, Any]) -> dict[str, Any]:
        store = self._orchestrator_store_instance()
        agent = store.get_agent(str(checkpoint.get("agent_id") or ""))
        project = self._project({"project": agent["project_slug"]})
        return self._memory_store_instance().update_project_state(
            project.slug,
            project.root,
            str(checkpoint.get("summary") or ""),
            next_action=str(checkpoint.get("next_action") or ""),
            completed=checkpoint.get("completed") or [],
            blockers=checkpoint.get("blockers") or [],
            changed_paths=checkpoint.get("changed_paths") or [],
            source="orchestrator",
            source_ref=str(checkpoint.get("id") or "") or None,
        )

    def orchestrator_session_checkpoint(self, payload: dict[str, Any]) -> ActionResult:
        checkpoint = self._orchestrator_store_instance().checkpoint(
            str(payload.get("session_id") or ""),
            **self._checkpoint_payload(payload),
        )
        data = dict(checkpoint)
        data["project_continuity"] = self._publish_project_continuity(checkpoint)
        return ActionResult(True, "Agent session checkpoint saved", data)

    def orchestrator_session_rotate(self, payload: dict[str, Any]) -> ActionResult:
        store = self._orchestrator_store_instance()
        session_id = str(payload.get("session_id") or "")
        current = store.session(session_id)
        if not current["should_rollover"] and not bool(payload.get("force", False)):
            return ActionResult(
                False,
                "session has not reached the rollover threshold",
                {
                    "session_id": session_id,
                    "estimated_tokens": current["estimated_tokens"],
                    "rollover_at_tokens": current["rollover_at_tokens"],
                },
            )
        data = store.rotate_session(
            session_id,
            **self._checkpoint_payload(payload),
        )
        data["project_continuity"] = self._publish_project_continuity(data["checkpoint"])
        return ActionResult(True, "Agent session rotated with continuity checkpoint", data)

    def orchestrator_continuation(self, payload: dict[str, Any]) -> ActionResult:
        data = self._orchestrator_store_instance().continuation_bundle(
            str(payload.get("session_id") or "")
        )
        return ActionResult(True, "Continuation bundle ready", data)

    def orchestrator_message_send(self, payload: dict[str, Any]) -> ActionResult:
        data = self._orchestrator_store_instance().send_message(
            str(payload.get("sender_agent_id") or ""),
            str(payload.get("recipient_agent_id") or ""),
            str(payload.get("content") or ""),
            kind=str(payload.get("kind") or "report"),
            correlation_id=payload.get("correlation_id") or None,
        )
        return ActionResult(True, "Agent message queued", data)

    def orchestrator_inbox(self, payload: dict[str, Any]) -> ActionResult:
        messages = self._orchestrator_store_instance().inbox(
            str(payload.get("agent_id") or ""),
            unread_only=bool(payload.get("unread_only", True)),
            limit=int(payload.get("limit", 100)),
        )
        return ActionResult(True, "Agent inbox ready", {"messages": messages})

    def orchestrator_message_read(self, payload: dict[str, Any]) -> ActionResult:
        updated = self._orchestrator_store_instance().mark_message_read(
            str(payload.get("agent_id") or ""),
            int(payload.get("message_id")),
        )
        if not updated:
            return ActionResult(False, "Unread message not found for agent")
        return ActionResult(True, "Agent message marked read", {"message_id": int(payload.get("message_id"))})


    def orchestrator_work_enqueue(self, payload: dict[str, Any]) -> ActionResult:
        project = self._project(payload)
        store = self._orchestrator_store_instance()
        agent = store.get_agent(str(payload.get("agent_id") or ""))
        if agent["project_slug"] != project.slug:
            return ActionResult(False, "agent does not belong to the requested project")
        data = store.enqueue_work(
            agent["id"],
            str(payload.get("title") or ""),
            str(payload.get("instruction") or ""),
            goal_id=payload.get("goal_id") or None,
            priority=int(payload.get("priority", 50)),
            delay_seconds=float(payload.get("delay_seconds", 0)),
            max_attempts=int(payload.get("max_attempts", 3)),
        )
        return ActionResult(True, "Agent work item queued", data)

    def orchestrator_work_claim(self, payload: dict[str, Any]) -> ActionResult:
        data = self._orchestrator_store_instance().claim_next_work(
            str(payload.get("agent_id") or ""),
            str(payload.get("runner_id") or ""),
            lease_seconds=int(payload.get("lease_seconds", 300)),
        )
        if data is None:
            return ActionResult(True, "No work ready", {"work": None})
        return ActionResult(True, "Agent work item leased", {"work": data})

    def orchestrator_work_heartbeat(self, payload: dict[str, Any]) -> ActionResult:
        data = self._orchestrator_store_instance().heartbeat_work(
            str(payload.get("work_id") or ""),
            str(payload.get("runner_id") or ""),
            lease_seconds=int(payload.get("lease_seconds", 300)),
        )
        return ActionResult(True, "Agent work lease renewed", data)

    def orchestrator_work_complete(self, payload: dict[str, Any]) -> ActionResult:
        data = self._orchestrator_store_instance().complete_work(
            str(payload.get("work_id") or ""),
            str(payload.get("runner_id") or ""),
            result=str(payload.get("result") or ""),
        )
        return ActionResult(True, "Agent work item completed", data)

    def orchestrator_work_fail(self, payload: dict[str, Any]) -> ActionResult:
        data = self._orchestrator_store_instance().fail_work(
            str(payload.get("work_id") or ""),
            str(payload.get("runner_id") or ""),
            error=str(payload.get("error") or ""),
            retryable=bool(payload.get("retryable", True)),
            retry_delay_seconds=float(payload.get("retry_delay_seconds", 30)),
        )
        return ActionResult(True, f"Agent work item is {data['state']}", data)

    def orchestrator_work_list(self, payload: dict[str, Any]) -> ActionResult:
        project = self._project(payload)
        raw_states = payload.get("states")
        states = raw_states if isinstance(raw_states, list) else None
        data = self._orchestrator_store_instance().list_work(
            project.slug,
            agent_id=payload.get("agent_id") or None,
            states=states,
            limit=int(payload.get("limit", 200)),
        )
        return ActionResult(True, "Agent work queue ready", {"work": data})
