"""Serialized ORDAX Studio file-maintenance helpers for canonical MCP tools."""
from __future__ import annotations

import threading
from typing import Any

from ordax_dev_agent.execution_lock import ExecutionLock
from ordax_dev_agent.models import ActionResult
from ordax_dev_agent.project_file_actions import ProjectFileActions


_thread_lock = threading.Lock()


def execute_project_file_maintenance(
    agent,
    operation: str,
    payload: dict[str, Any],
) -> ActionResult:
    handlers = {
        "move": ProjectFileActions.project_text_move,
        "delete": ProjectFileActions.project_text_delete,
    }
    handler = handlers.get(operation)
    if handler is None:
        return ActionResult(False, f"unsupported project maintenance operation: {operation}")

    if not _thread_lock.acquire(blocking=False):
        return ActionResult(False, "ORDAX Studio file maintenance is busy", {"retryable": True})
    process_lock = ExecutionLock(agent.config.state_dir)
    try:
        if not process_lock.acquire():
            return ActionResult(False, "Another agent/MCP action is running", {"retryable": True})
        try:
            return handler(agent, payload)
        except (ValueError, FileNotFoundError, OSError) as error:
            return ActionResult(False, f"{type(error).__name__}: {error}")
        finally:
            process_lock.release()
    finally:
        _thread_lock.release()
