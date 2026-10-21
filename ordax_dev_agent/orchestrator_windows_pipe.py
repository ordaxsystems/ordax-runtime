"""Windows-only, user-restricted local pipe owned by the Runtime launcher.

No TCP listener, path supplied by a model, CRT descriptor tricks or helper
executable. Authenticate the exact child PID before the first protocol byte.
"""
from __future__ import annotations

import ctypes as c
from ctypes import wintypes as w
import json
import os
import threading
import time
import uuid

from .orchestrator_app_bridge import OrchestratorBridgeUnavailable, _canonical


class _Overlapped(c.Structure):
    _fields_ = [("Internal", c.c_size_t), ("InternalHigh", c.c_size_t),
                ("Offset", w.DWORD), ("OffsetHigh", w.DWORD), ("hEvent", w.HANDLE)]


class _SecurityAttributes(c.Structure):
    _fields_ = [("nLength", w.DWORD), ("lpSecurityDescriptor", c.c_void_p), ("bInheritHandle", w.BOOL)]


class _TokenUser(c.Structure):
    _fields_ = [("Sid", c.c_void_p), ("Attributes", w.DWORD)]


class _FileInfo(c.Structure):
    _fields_ = [("attributes", w.DWORD), ("creation", w.FILETIME), ("access", w.FILETIME),
                ("write", w.FILETIME), ("volume", w.DWORD), ("size_high", w.DWORD),
                ("size_low", w.DWORD), ("links", w.DWORD), ("index_high", w.DWORD), ("index_low", w.DWORD)]


class WindowsApi:
    def __init__(self):
        if os.name != "nt":
            raise OrchestratorBridgeUnavailable("host_windows_required")
        self.kernel = c.WinDLL("kernel32", use_last_error=True)
        self.advapi = c.WinDLL("advapi32", use_last_error=True)
        specs = {
            "CreateNamedPipeW": ([w.LPCWSTR, w.DWORD, w.DWORD, w.DWORD, w.DWORD, w.DWORD, w.DWORD, c.POINTER(_SecurityAttributes)], w.HANDLE),
            "CreateEventW": ([c.c_void_p, w.BOOL, w.BOOL, w.LPCWSTR], w.HANDLE),
            "CloseHandle": ([w.HANDLE], w.BOOL),
            "LocalFree": ([c.c_void_p], c.c_void_p),
            "GetCurrentProcess": ([], w.HANDLE),
            "ConnectNamedPipe": ([w.HANDLE, c.POINTER(_Overlapped)], w.BOOL),
            "GetNamedPipeClientProcessId": ([w.HANDLE, c.POINTER(w.ULONG)], w.BOOL),
            "WaitForSingleObject": ([w.HANDLE, w.DWORD], w.DWORD),
            "CancelIoEx": ([w.HANDLE, c.POINTER(_Overlapped)], w.BOOL),
            "GetOverlappedResult": ([w.HANDLE, c.POINTER(_Overlapped), c.POINTER(w.DWORD), w.BOOL], w.BOOL),
            "ReadFile": ([w.HANDLE, c.c_void_p, w.DWORD, c.POINTER(w.DWORD), c.POINTER(_Overlapped)], w.BOOL),
            "WriteFile": ([w.HANDLE, c.c_void_p, w.DWORD, c.POINTER(w.DWORD), c.POINTER(_Overlapped)], w.BOOL),
            "CreateFileW": ([w.LPCWSTR, w.DWORD, w.DWORD, c.c_void_p, w.DWORD, w.DWORD, w.HANDLE], w.HANDLE),
            "GetFileInformationByHandle": ([w.HANDLE, c.POINTER(_FileInfo)], w.BOOL),
        }
        for name, (arguments, result) in specs.items():
            function = getattr(self.kernel, name)
            function.argtypes, function.restype = arguments, result
        for name, arguments in {
            "OpenProcessToken": [w.HANDLE, w.DWORD, c.POINTER(w.HANDLE)],
            "GetTokenInformation": [w.HANDLE, c.c_int, c.c_void_p, w.DWORD, c.POINTER(w.DWORD)],
            "ConvertSidToStringSidW": [c.c_void_p, c.POINTER(w.LPWSTR)],
            "ConvertStringSecurityDescriptorToSecurityDescriptorW": [w.LPCWSTR, w.DWORD, c.POINTER(c.c_void_p), c.c_void_p],
        }.items():
            function = getattr(self.advapi, name)
            function.argtypes, function.restype = arguments, w.BOOL

    def user_security_descriptor(self):
        token, size, sid_text, descriptor = w.HANDLE(), w.DWORD(), w.LPWSTR(), c.c_void_p()
        if not self.advapi.OpenProcessToken(self.kernel.GetCurrentProcess(), 0x0008, c.byref(token)):
            raise OrchestratorBridgeUnavailable("host_pipe_security_unavailable")
        try:
            self.advapi.GetTokenInformation(token, 1, None, 0, c.byref(size))
            if not 0 < size.value <= 65536:
                raise OrchestratorBridgeUnavailable("host_pipe_security_unavailable")
            buffer = c.create_string_buffer(size.value)
            if not self.advapi.GetTokenInformation(token, 1, buffer, size, c.byref(size)):
                raise OrchestratorBridgeUnavailable("host_pipe_security_unavailable")
            sid = c.cast(buffer, c.POINTER(_TokenUser)).contents.Sid
            if not self.advapi.ConvertSidToStringSidW(sid, c.byref(sid_text)):
                raise OrchestratorBridgeUnavailable("host_pipe_security_unavailable")
            # Protected DACL. Only this exact Windows user, no Everyone/anonymous.
            sddl = f"D:P(A;;GA;;;{sid_text.value})"
            if not self.advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW(sddl, 1, c.byref(descriptor), None):
                raise OrchestratorBridgeUnavailable("host_pipe_security_unavailable")
            return descriptor
        finally:
            if sid_text:
                self.kernel.LocalFree(c.cast(sid_text, c.c_void_p))
            self.kernel.CloseHandle(token)

    def pin_file(self, path, *, directory=False):
        # Deny deletion/rename; files also deny writes for the entire session.
        handle = self.kernel.CreateFileW(str(path), 0x80000000, 3 if directory else 1,
            None, 3, 0x00200000 | (0x02000000 if directory else 0x08000000), None)
        if handle in (None, c.c_void_p(-1).value):
            raise OrchestratorBridgeUnavailable("host_installation_unavailable")
        info = _FileInfo()
        if not self.kernel.GetFileInformationByHandle(handle, c.byref(info)) or info.attributes & 0x400 or bool(info.attributes & 0x10) != directory:
            self.kernel.CloseHandle(handle)
            raise OrchestratorBridgeUnavailable("host_installation_unsafe")
        return handle

    def hash_handle(self, handle):
        import hashlib
        digest, data, count = hashlib.sha256(), c.create_string_buffer(65536), w.DWORD()
        while True:
            if not self.kernel.ReadFile(handle, data, len(data), c.byref(count), None):
                raise OrchestratorBridgeUnavailable("host_installation_unavailable")
            if count.value == 0:
                return digest.hexdigest()
            digest.update(data.raw[:count.value])


class WindowsOrchestratorPipe:
    """One private byte channel with bounded overlapped I/O and no retries."""
    def __init__(self, *, timeout_seconds=15.0):
        if type(timeout_seconds) not in (float, int) or not 0 < timeout_seconds <= 30:
            raise OrchestratorBridgeUnavailable("host_pipe_deadline_required")
        self.api, self.timeout = WindowsApi(), timeout_seconds
        self.name = "\\\\.\\pipe\\ordax.orchestrator." + str(uuid.uuid4())
        self._lock, self._closed, self._bound = threading.RLock(), False, False
        descriptor = self.api.user_security_descriptor()
        attributes = _SecurityAttributes(c.sizeof(_SecurityAttributes), descriptor, False)
        try:
            # Duplex, OVERLAPPED, FIRST_PIPE_INSTANCE; byte mode, reject remote.
            self.handle = self.api.kernel.CreateNamedPipeW(self.name, 3 | 0x40000000 | 0x00080000,
                0x8, 1, 65536, 65536, 0, c.byref(attributes))
        finally:
            self.api.kernel.LocalFree(descriptor)
        if self.handle in (None, c.c_void_p(-1).value):
            raise OrchestratorBridgeUnavailable("host_private_pipe_unavailable")

    def close(self):
        with self._lock:
            if not self._closed:
                self._closed = True
                self.api.kernel.CancelIoEx(self.handle, None)
                self.api.kernel.CloseHandle(self.handle)

    def _io(self, operation, deadline, data=None):
        event = self.api.kernel.CreateEventW(None, True, False, None)
        if not event:
            raise OrchestratorBridgeUnavailable("host_private_pipe_unavailable")
        overlap, count = _Overlapped(hEvent=event), w.DWORD()
        # Keep buffer/OVERLAPPED alive until completion OR cancellation completes.
        buffer = c.create_string_buffer(data) if data is not None else c.create_string_buffer(65536)
        try:
            if operation == "connect":
                ok = self.api.kernel.ConnectNamedPipe(self.handle, c.byref(overlap))
            else:
                fn = self.api.kernel.WriteFile if operation == "write" else self.api.kernel.ReadFile
                ok = fn(self.handle, buffer, len(data) if data is not None else len(buffer), c.byref(count), c.byref(overlap))
            error = c.get_last_error() if not ok else 0
            if operation == "connect" and error == 535:  # client already connected
                return b""
            if not ok and error != 997:
                raise OrchestratorBridgeUnavailable("host_private_pipe_unavailable")
            if ok and operation == "connect":
                return b""
            remaining = max(0, int((deadline - time.monotonic()) * 1000))
            if self.api.kernel.WaitForSingleObject(event, remaining) != 0:
                self.api.kernel.CancelIoEx(self.handle, c.byref(overlap))
                # The local NPFS operation must complete before freeing its memory.
                self.api.kernel.GetOverlappedResult(self.handle, c.byref(overlap), c.byref(count), True)
                raise OrchestratorBridgeUnavailable("host_private_pipe_timeout")
            if not self.api.kernel.GetOverlappedResult(self.handle, c.byref(overlap), c.byref(count), False):
                raise OrchestratorBridgeUnavailable("host_private_pipe_unavailable")
            if operation == "write" and count.value != len(data):
                raise OrchestratorBridgeUnavailable("host_private_pipe_unavailable")
            return buffer.raw[:count.value]
        finally:
            self.api.kernel.CloseHandle(event)

    def bind_child(self, child):
        if self._closed or self._bound or child.poll() is not None:
            raise OrchestratorBridgeUnavailable("host_private_pipe_unavailable")
        try:
            self._io("connect", time.monotonic() + self.timeout)
            pid = w.ULONG()
            if not self.api.kernel.GetNamedPipeClientProcessId(self.handle, c.byref(pid)) or pid.value != child.pid or child.poll() is not None:
                raise OrchestratorBridgeUnavailable("host_pipe_peer_rejected")
            self._bound = True
        except Exception:
            self.close()
            raise

    def __call__(self, message):
        if self._closed or not self._bound or not self._lock.acquire(blocking=False):
            raise OrchestratorBridgeUnavailable("host_private_pipe_unavailable")
        try:
            encoded = _canonical(message) + b"\n"
            if len(encoded) > 65536:
                raise OrchestratorBridgeUnavailable("host_frame_oversized")
            deadline, received = time.monotonic() + self.timeout, bytearray()
            self._io("write", deadline, encoded)
            while not received.endswith(b"\n"):
                chunk = self._io("read", deadline)
                if not chunk or len(received) + len(chunk) > 524288 or b"\n" in chunk[:-1]:
                    raise OrchestratorBridgeUnavailable("host_frame_invalid")
                received.extend(chunk)
            result = json.loads(received.decode("utf-8", errors="strict"))
            if type(result) is not dict:
                raise OrchestratorBridgeUnavailable("host_frame_invalid")
            return result
        except Exception:
            self.close()
            raise OrchestratorBridgeUnavailable("host_private_pipe_unavailable") from None
        finally:
            self._lock.release()
