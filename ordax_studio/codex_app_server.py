"""Codex App Server stdio transport for the Windows ORDAX Runtime.

This is deliberately a host-only, protocol-level client. It never owns user
login, grants, tool permissions or Studio UI state. Do not expose request
to the portable Studio bridge: a separate policy-authorized provider adapter
must constrain Codex operations before product activation.
"""

from __future__ import annotations

import json
import queue
import subprocess
import threading
from collections.abc import Mapping, Sequence
from typing import Any


_MAX_JSONL_BYTES = 4 * 1024 * 1024


class CodexTransportError(RuntimeError):
    """Transport/protocol failure, without sensitive process output."""


class CodexRemoteError(CodexTransportError):
    """JSON-RPC error returned by Codex App Server."""

    def __init__(self, code: int, message: str):
        self.code = code
        super().__init__(f"Codex app-server RPC failed (code {code}): {message}")


class CodexAppServerTransport:
    """Bounded JSONL RPC transport, not an authorized execution API.

    The owning Runtime passes an explicit command/environment, including any
    provider credential obtained through its account flow. No tokens/cookies
    are read here and process stderr is never copied into logs or the UI.

    Server-initiated requests are denied by default, not auto-approved. This
    prevents an untrusted provider from silently receiving host authority.
    """

    def __init__(
        self,
        command: Sequence[str],
        *,
        environment: Mapping[str, str],
        cwd: str | None = None,
        timeout: float = 15.0,
        notification_capacity: int = 1024,
    ) -> None:
        if not command or any(not isinstance(part, str) or not part for part in command):
            raise ValueError("command must be a nonempty argv sequence")
        if not isinstance(environment, Mapping) or any(
            not isinstance(key, str) or not isinstance(value, str)
            for key, value in environment.items()
        ):
            raise ValueError("environment must be an explicit string mapping")
        if timeout <= 0 or notification_capacity < 1:
            raise ValueError("timeout and notification_capacity must be positive")
        self._command = tuple(command)
        self._environment = dict(environment)
        self._cwd = cwd
        self._timeout = timeout
        self._process: subprocess.Popen[str] | None = None
        self._reader: threading.Thread | None = None
        self._write_lock = threading.Lock()
        self._pending_lock = threading.Lock()
        self._pending: dict[int, queue.Queue[object]] = {}
        self._next_id = 0
        self._notifications: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=notification_capacity)
        self._dead: CodexTransportError | None = None
        self._closing = threading.Event()

    def start(self, *, version: str) -> None:
        """Launch, initialize, and send the required initialized notification."""
        if self._process is not None:
            raise CodexTransportError("transport already started")
        if not version or not isinstance(version, str):
            raise ValueError("a real host version is required")
        try:
            process = subprocess.Popen(
                self._command,
                cwd=self._cwd,
                env=self._environment,  # Never inherit arbitrary Runtime secrets.
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                errors="strict",
                bufsize=1,
                shell=False,
            )
        except OSError as exc:
            raise CodexTransportError("could not start Codex app-server") from exc
        self._process = process
        self._reader = threading.Thread(target=self._read_messages, daemon=True, name="ordax-codex-stdio")
        self._reader.start()
        try:
            self.request(
                "initialize",
                {"clientInfo": {"name": "ordax_studio", "title": "ORDAX Studio", "version": version}},
            )
            self.notify("initialized")
        except BaseException:
            self.close()
            raise

    def _send(self, packet: dict[str, Any]) -> None:
        wire = json.dumps(packet, ensure_ascii=False, separators=(",", ":")) + "\n"
        if len(wire.encode("utf-8")) > _MAX_JSONL_BYTES:
            raise CodexTransportError("Codex message exceeds transport limit")
        with self._write_lock:
            if self._dead is not None:
                raise self._dead
            process = self._process
            if self._closing.is_set() or process is None or process.stdin is None or process.poll() is not None:
                raise CodexTransportError("Codex app-server is not running")
            try:
                process.stdin.write(wire)
                process.stdin.flush()
            except (OSError, UnicodeError, ValueError) as exc:
                raise CodexTransportError("Codex app-server write failed") from exc

    def request(self, method: str, params: Mapping[str, Any] | None = None) -> Any:
        """Internal RPC primitive. Never pass this method directly to Studio UI."""
        if not isinstance(method, str) or not method or not isinstance(params, (Mapping, type(None))):
            raise ValueError("invalid RPC method/params")
        response: queue.Queue[object] = queue.Queue(maxsize=1)
        with self._pending_lock:
            if self._dead is not None:
                raise self._dead
            self._next_id += 1
            request_id = self._next_id
            self._pending[request_id] = response
        try:
            self._send({"id": request_id, "method": method, "params": dict(params or {})})
            try:
                packet = response.get(timeout=self._timeout)
            except queue.Empty as exc:
                raise CodexTransportError("Codex app-server request timed out") from exc
            if isinstance(packet, BaseException):
                raise packet
            if not isinstance(packet, dict):
                raise CodexTransportError("invalid Codex app-server response")
            if "error" in packet:
                error = packet["error"]
                if not isinstance(error, dict):
                    raise CodexTransportError("invalid Codex app-server error")
                # Avoid surfacing arbitrary remote/server error strings that can
                # contain secret-bearing paths or credential material.
                code = error.get("code", -32000)
                raise CodexRemoteError(code if isinstance(code, int) else -32000, "request rejected")
            if "result" not in packet:
                raise CodexTransportError("missing Codex app-server result")
            return packet["result"]
        finally:
            with self._pending_lock:
                self._pending.pop(request_id, None)

    def notify(self, method: str, params: Mapping[str, Any] | None = None) -> None:
        if not isinstance(method, str) or not method or not isinstance(params, (Mapping, type(None))):
            raise ValueError("invalid RPC notification")
        packet: dict[str, Any] = {"method": method}
        if params is not None:
            packet["params"] = dict(params)
        self._send(packet)

    def next_notification(self, *, timeout: float | None = None) -> dict[str, Any] | None:
        """Consume one provider event; caller maps it to typed, authorized UX."""
        if self._dead is not None and self._notifications.empty():
            raise self._dead
        try:
            return self._notifications.get(timeout=timeout)
        except queue.Empty:
            if self._dead is not None:
                raise self._dead
            return None

    def _read_messages(self) -> None:
        assert self._process is not None and self._process.stdout is not None
        stream = self._process.stdout
        try:
            while not self._closing.is_set():
                line = stream.readline(_MAX_JSONL_BYTES + 1)
                if not line:
                    break
                if len(line.encode("utf-8")) > _MAX_JSONL_BYTES or not line.endswith("\n"):
                    raise CodexTransportError("oversized or incomplete Codex app-server message")
                try:
                    packet = json.loads(line)
                except (json.JSONDecodeError, UnicodeError) as exc:
                    raise CodexTransportError("malformed Codex app-server JSONL") from exc
                if not isinstance(packet, dict):
                    raise CodexTransportError("non-object Codex app-server packet")
                request_id = packet.get("id")
                if request_id is not None and "method" in packet:
                    # No permissive fallback. The canonical OrdaX grant and
                    # owner-approval path is not implemented by this transport.
                    self._send({"id": request_id, "error": {"code": -32601, "message": "Host approval unavailable"}})
                elif isinstance(request_id, int) and not isinstance(request_id, bool):
                    with self._pending_lock:
                        pending = self._pending.get(request_id)
                    if pending is not None:
                        pending.put_nowait(packet)
                elif isinstance(packet.get("method"), str):
                    try:
                        self._notifications.put_nowait(packet)
                    except queue.Full as exc:
                        raise CodexTransportError("Codex event queue overflow; transport halted") from exc
                else:
                    raise CodexTransportError("unrecognized Codex app-server packet")
        except (CodexTransportError, OSError, UnicodeError, ValueError) as exc:
            self._abort(CodexTransportError(str(exc)))
        finally:
            if not self._closing.is_set():
                self._abort(CodexTransportError("Codex app-server disconnected"))

    def _abort(self, failure: CodexTransportError) -> None:
        with self._pending_lock:
            if self._dead is not None:
                return
            self._dead = failure
            requests = list(self._pending.values())
            self._pending.clear()
        for waiting in requests:
            try:
                waiting.put_nowait(failure)
            except queue.Full:
                pass
        process = self._process
        if process is not None and process.poll() is None:
            try:
                process.terminate()
            except OSError:
                pass

    def close(self) -> None:
        """Stop only the process owned by this client, without affecting peers."""
        if self._closing.is_set():
            return
        self._closing.set()
        self._abort(CodexTransportError("Codex transport closed"))
        process = self._process
        if process is not None:
            if process.poll() is None:
                try:
                    process.terminate()
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=2)
                except OSError:
                    pass
            if process.stdin is not None:
                process.stdin.close()
            if process.stdout is not None:
                process.stdout.close()
        reader = self._reader
        if reader is not None and reader is not threading.current_thread():
            reader.join(timeout=2)

    def __enter__(self) -> CodexAppServerTransport:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()
