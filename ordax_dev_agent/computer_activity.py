from __future__ import annotations

import ctypes
import json
import os
import threading
import time
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterator


_ACTIVITY_SCHEMA = "ordax.computer-activity/1"
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


@dataclass(frozen=True)
class ProductExecutionProvenance:
    """Verified Product caller provenance bound only during local execution."""

    request_id: str
    subject_id: str
    device_id: str
    space_id: str | None
    grant_id: str


_PRODUCT_EXECUTION_PROVENANCE: ContextVar[ProductExecutionProvenance | None] = ContextVar(
    "ordax_product_execution_provenance",
    default=None,
)


@contextmanager
def bind_product_execution_provenance(
    *,
    request_id: str,
    subject_id: str,
    device_id: str,
    space_id: str | None,
    grant_id: str,
) -> Iterator[None]:
    """Bind verified Product provenance for the synchronous local dispatch only."""

    token = _PRODUCT_EXECUTION_PROVENANCE.set(
        ProductExecutionProvenance(
            request_id=request_id,
            subject_id=subject_id,
            device_id=device_id,
            space_id=space_id,
            grant_id=grant_id,
        )
    )
    try:
        yield
    finally:
        _PRODUCT_EXECUTION_PROVENANCE.reset(token)


def _windows_process_identity(pid: int) -> str | None:
    if os.name != "nt":
        return None

    class FileTime(ctypes.Structure):
        _fields_ = [("low", ctypes.c_uint32), ("high", ctypes.c_uint32)]

    kernel32 = ctypes.windll.kernel32
    kernel32.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
    kernel32.OpenProcess.restype = ctypes.c_void_p
    kernel32.GetProcessTimes.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(FileTime),
        ctypes.POINTER(FileTime),
        ctypes.POINTER(FileTime),
        ctypes.POINTER(FileTime),
    ]
    kernel32.GetProcessTimes.restype = ctypes.c_int
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel32.CloseHandle.restype = ctypes.c_int

    handle = kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
    if not handle:
        return None
    creation = FileTime()
    exit_time = FileTime()
    kernel = FileTime()
    user = FileTime()
    try:
        if not kernel32.GetProcessTimes(
            handle,
            ctypes.byref(creation),
            ctypes.byref(exit_time),
            ctypes.byref(kernel),
            ctypes.byref(user),
        ):
            return None
    finally:
        kernel32.CloseHandle(handle)
    created = (int(creation.high) << 32) | int(creation.low)
    return f"windows:{int(pid)}:{created}"


def _proc_process_identity(pid: int) -> str | None:
    stat = Path(f"/proc/{int(pid)}/stat")
    try:
        text = stat.read_text(encoding="utf-8")
    except OSError:
        return None
    closing = text.rfind(")")
    if closing < 0:
        return None
    tail = text[closing + 2 :].split()
    if len(tail) <= 19:
        return None
    return f"proc:{int(pid)}:{tail[19]}"


def _process_identity(pid: int) -> str | None:
    if pid <= 0:
        return None
    if os.name == "nt":
        return _windows_process_identity(pid)
    identity = _proc_process_identity(pid)
    if identity is not None:
        return identity
    try:
        os.kill(pid, 0)
    except (OSError, AttributeError):
        return None
    return f"pid:{pid}"


def _atomic_json_write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    encoded = (json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")
    try:
        with temporary.open("wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


class ComputerActivityTracker:
    """Cross-process, payload-free live Computer Control activity tracker."""

    def __init__(
        self,
        state_dir: Path,
        *,
        clock: Callable[[], float] = time.time,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._clock = clock
        self._monotonic = monotonic
        self._lock = threading.Lock()
        self._active: dict[str, dict[str, Any]] = {}
        self._last_completed: dict[str, Any] | None = None
        self._pid = os.getpid()
        self._process_identity = _process_identity(self._pid) or f"pid:{self._pid}"
        self._instance_id = uuid.uuid4().hex
        self._root = (Path(state_dir) / "computer-activity").resolve()
        self._state_path = self._root / f"tracker-{self._pid}-{self._instance_id}.json"

    @staticmethod
    def _public(record: dict[str, Any]) -> dict[str, Any]:
        return {key: value for key, value in record.items() if not key.startswith("_")}

    def _state_payload_locked(self) -> dict[str, Any]:
        return {
            "schema": _ACTIVITY_SCHEMA,
            "process": {
                "pid": self._pid,
                "identity": self._process_identity,
                "instance_id": self._instance_id,
            },
            "updated_at_unix_ms": int(self._clock() * 1000),
            "active_operations": [self._public(item) for item in self._active.values()],
            "last_completed": dict(self._last_completed) if self._last_completed is not None else None,
        }

    def _persist_locked(self) -> None:
        _atomic_json_write(self._state_path, self._state_payload_locked())

    def start(self, action: str) -> str:
        operation_id = uuid.uuid4().hex
        provenance = _PRODUCT_EXECUTION_PROVENANCE.get()
        record: dict[str, Any] = {
            "operation_id": operation_id,
            "action": action,
            "source": "product_remote" if provenance is not None else "local_runtime",
            "started_at_unix_ms": int(self._clock() * 1000),
            "request_id": provenance.request_id if provenance is not None else None,
            "device_id": provenance.device_id if provenance is not None else None,
            "space_id": provenance.space_id if provenance is not None else None,
            "grant_id": provenance.grant_id if provenance is not None else None,
            "_started_monotonic": self._monotonic(),
        }
        with self._lock:
            self._active[operation_id] = record
            self._persist_locked()
        return operation_id

    def finish(self, operation_id: str, *, result_ok: bool | None) -> None:
        with self._lock:
            record = self._active.pop(operation_id, None)
            if record is None:
                return
            completed = self._public(record)
            completed.update(
                {
                    "ended_at_unix_ms": int(self._clock() * 1000),
                    "duration_ms": max(
                        0,
                        int((self._monotonic() - float(record["_started_monotonic"])) * 1000),
                    ),
                    "result_ok": result_ok,
                }
            )
            self._last_completed = completed
            self._persist_locked()

    @staticmethod
    def _read_tracker(path: Path) -> dict[str, Any] | None:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(payload, dict) or payload.get("schema") != _ACTIVITY_SCHEMA:
            return None
        process = payload.get("process")
        if not isinstance(process, dict):
            return None
        pid = process.get("pid")
        identity = process.get("identity")
        if type(pid) is not int or not isinstance(identity, str):
            return None
        current_identity = _process_identity(pid)
        if current_identity != identity:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
            return None
        return payload

    def snapshot(self) -> dict[str, Any]:
        active: list[dict[str, Any]] = []
        completed: list[dict[str, Any]] = []
        self._root.mkdir(parents=True, exist_ok=True)
        for path in self._root.glob("tracker-*.json"):
            payload = self._read_tracker(path)
            if payload is None:
                continue
            operations = payload.get("active_operations")
            if isinstance(operations, list):
                active.extend(item for item in operations if isinstance(item, dict))
            last = payload.get("last_completed")
            if isinstance(last, dict):
                completed.append(last)
        active.sort(key=lambda item: (int(item.get("started_at_unix_ms") or 0), str(item.get("operation_id") or "")))
        completed.sort(key=lambda item: int(item.get("ended_at_unix_ms") or 0), reverse=True)
        return {
            "telemetry_version": 1,
            "observed_at_unix_ms": int(self._clock() * 1000),
            "active": bool(active),
            "active_count": len(active),
            "active_operations": active,
            "last_completed": completed[0] if completed else None,
        }
