"""Resource-scoped concurrency for canonical Runtime actions.

No scheduler, grant authority, or second queue lives here. The coordinator only
decides which already-authorized local actions may overlap and reuses the
cross-process ExecutionLock primitive so local MCP and Product dispatch obey the
same conflict boundary.
"""
from __future__ import annotations

import os
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .execution_lock import ExecutionLock


_MAX_EXECUTION_SLOTS = 8

_GLOBAL_EXCLUSIVE_ACTIONS = frozenset({
    "agent.update",
    "agent.resilience_repair",
})

_GUI_EXCLUSIVE_ACTIONS = frozenset({
    "computer.mouse_move",
    "computer.drag",
    "computer.clipboard_write",
    "computer.launch_app",
    "computer.focus_window",
    "computer.click",
    "computer.type",
    "computer.hotkey",
    "computer.scroll",
})

_DEVICE_FILE_ACTION_FIELDS = {
    "computer.file_stat": ("path",),
    "computer.text_read": ("path",),
    "computer.text_write": ("path",),
    "computer.text_patch": ("path",),
    "computer.directory_create": ("path",),
}

_DESTRUCTIVE_DEVICE_PATH_ACTIONS = frozenset({
    "computer.path_move",
    "computer.path_remove",
})

_PROJECT_FILE_ACTION_FIELDS = {
    "project.text_read": ("path",),
    "project.text_read_batch": ("paths",),
    "project.text_write": ("path",),
    "project.text_patch": ("path",),
    "workspace.file_stat": ("path",),
    "workspace.text_read": ("path",),
    "workspace.text_write": ("path",),
    "workspace.text_patch": ("path",),
}


def _normalized_fragment(value: Any) -> str:
    if isinstance(value, (list, tuple)):
        return "|".join(sorted(_normalized_fragment(item) for item in value))
    raw = str(value or ".").strip()
    if not raw:
        raw = "."
    return os.path.normcase(os.path.normpath(os.path.expanduser(raw)))


@dataclass
class ActionConcurrencyLease:
    thread_locks: list[threading.Lock]
    process_locks: list[ExecutionLock]
    slot_locks: list[ExecutionLock]
    on_release: Callable[[], None] | None = None
    released: bool = False

    def release(self) -> None:
        if self.released:
            return
        for lock in reversed(self.process_locks):
            lock.release()
        for lock in reversed(self.slot_locks):
            lock.release()
        for lock in reversed(self.thread_locks):
            lock.release()
        self.thread_locks.clear()
        self.process_locks.clear()
        self.slot_locks.clear()
        self.released = True
        if self.on_release is not None:
            self.on_release()


class ActionConcurrencyCoordinator:
    """Acquire only the locks required by one local action.

    Ordinary actions occupy one of a bounded set of cross-process slots plus
    resource-specific locks. Runtime replacement/repair acquires every slot, so
    it remains exclusive without forcing unrelated work through one global lane.
    """

    def __init__(self, state_dir: Path, *, max_slots: int = _MAX_EXECUTION_SLOTS):
        if type(max_slots) is not int or not 2 <= max_slots <= 32:
            raise ValueError("max_slots must be between 2 and 32")
        self.state_dir = state_dir
        self.max_slots = max_slots
        self._lock_guard = threading.Lock()
        self._thread_locks: dict[str, threading.Lock] = {}
        self._active_count = 0

    def _lease_released(self) -> None:
        with self._lock_guard:
            self._active_count = max(0, self._active_count - 1)

    def is_busy(self) -> bool:
        with self._lock_guard:
            return self._active_count > 0

    def _thread_lock(self, key: str) -> threading.Lock:
        with self._lock_guard:
            return self._thread_locks.setdefault(key, threading.Lock())

    @staticmethod
    def _project_slug(
        payload: dict[str, Any],
        project_resolver: Callable[[dict[str, Any]], Any],
    ) -> str:
        try:
            project = project_resolver(payload)
            slug = str(getattr(project, "slug", "") or "")
            if slug:
                return slug
        except Exception:
            pass
        return str(payload.get("project") or "default")

    def _resource_keys(
        self,
        action: str,
        payload: dict[str, Any],
        project_resolver: Callable[[dict[str, Any]], Any],
    ) -> tuple[bool, tuple[str, ...]]:
        if action in _GLOBAL_EXCLUSIVE_ACTIONS:
            return True, ()

        if action in _GUI_EXCLUSIVE_ACTIONS:
            return False, ("computer:gui-interactive",)

        if action in _DESTRUCTIVE_DEVICE_PATH_ACTIONS:
            return False, ("computer:filesystem-destructive",)

        fields = _DEVICE_FILE_ACTION_FIELDS.get(action)
        if fields is not None:
            values = tuple(_normalized_fragment(payload.get(field)) for field in fields)
            return False, tuple(f"computer:file:{value}" for value in values)

        if action == "computer.terminal_exec":
            return False, (
                f"computer:terminal:{_normalized_fragment(payload.get('cwd'))}",
            )

        if action == "computer.process_start":
            return False, (
                f"computer:process-start:{_normalized_fragment(payload.get('cwd'))}",
            )
        if action in {"computer.process_write_stdin", "computer.process_stop"}:
            return False, (
                f"computer:process:{payload.get('process_id') or 'unknown'}",
            )
        if action == "computer.terminate_process":
            return False, (f"computer:pid:{payload.get('pid') or 'unknown'}",)

        if action.startswith("computer.transfer_"):
            transfer_id = payload.get("transfer_id")
            if transfer_id:
                return False, (f"computer:transfer:{transfer_id}",)
            return False, ("computer:transfer:create",)

        project_prefixes = (
            "project.", "workspace.", "git.", "terminal.", "process.",
            "blender.", "unity.", "browser.", "preview.", "artifact.",
        )
        if action.startswith(project_prefixes):
            project = self._project_slug(payload, project_resolver)
            fields = _PROJECT_FILE_ACTION_FIELDS.get(action)
            if fields is not None:
                keys: list[str] = []
                for field in fields:
                    value = payload.get(field)
                    if isinstance(value, (list, tuple)):
                        keys.extend(
                            f"project:{project}:file:{_normalized_fragment(item)}"
                            for item in value
                        )
                    else:
                        keys.append(
                            f"project:{project}:file:{_normalized_fragment(value)}"
                        )
                return False, tuple(sorted(set(keys)))
            if action == "terminal.exec":
                return False, (
                    f"project:{project}:terminal:{_normalized_fragment(payload.get('cwd'))}",
                )
            if action == "process.start":
                return False, (
                    f"project:{project}:process-start:{_normalized_fragment(payload.get('cwd'))}",
                )
            if action in {"process.write_stdin", "process.stop"}:
                return False, (
                    f"project:{project}:process:{payload.get('process_id') or 'unknown'}",
                )
            return False, (f"project:{project}:{action.split('.', 1)[0]}",)

        # Unknown mutating domains remain conservative rather than silently
        # expanding concurrency beyond a resource boundary we cannot identify.
        return True, ()

    def _acquire_slots(self, *, exclusive: bool) -> list[ExecutionLock] | None:
        acquired: list[ExecutionLock] = []
        if exclusive:
            for index in range(self.max_slots):
                lock = ExecutionLock(self.state_dir, f"execution-slot:{index}")
                if not lock.acquire():
                    for held in reversed(acquired):
                        held.release()
                    return None
                acquired.append(lock)
            return acquired

        for index in range(self.max_slots):
            lock = ExecutionLock(self.state_dir, f"execution-slot:{index}")
            if lock.acquire():
                return [lock]
        return None

    def try_acquire(
        self,
        action: str,
        payload: dict[str, Any],
        *,
        project_resolver: Callable[[dict[str, Any]], Any],
    ) -> ActionConcurrencyLease | None:
        exclusive, keys = self._resource_keys(action, payload, project_resolver)

        thread_locks: list[threading.Lock] = []
        process_locks: list[ExecutionLock] = []
        slot_locks: list[ExecutionLock] = []
        success = False
        try:
            for key in sorted(set(keys)):
                lock = self._thread_lock(key)
                if not lock.acquire(blocking=False):
                    return None
                thread_locks.append(lock)

            for key in sorted(set(keys)):
                lock = ExecutionLock(self.state_dir, f"resource:{key}")
                if not lock.acquire():
                    return None
                process_locks.append(lock)

            acquired_slots = self._acquire_slots(exclusive=exclusive)
            if acquired_slots is None:
                return None
            slot_locks.extend(acquired_slots)
            with self._lock_guard:
                self._active_count += 1
            success = True
            return ActionConcurrencyLease(
                thread_locks,
                process_locks,
                slot_locks,
                on_release=self._lease_released,
            )
        finally:
            if not success:
                for lock in reversed(process_locks):
                    lock.release()
                for lock in reversed(thread_locks):
                    lock.release()
