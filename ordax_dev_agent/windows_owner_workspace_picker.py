"""Native Windows Owner-session dialogs for Project-First workspace selection.

This is a UI adapter for WorkspaceResourceSelection, NOT a grant issuer, an MCP
tool, an HTTP route or a Windows service action. The trusted interactive host
must provide the canonical repository identity and an authenticated Platform
grant verifier to the existing broker before a resource can ever be read.

The selected directory is returned ONLY inside the Runtime process. Never
forward it to Lab SQLite, the remote Platform, the model or an MCP payload.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import os
from pathlib import Path
import threading
import uuid

from .project_workspace_port import ProjectWorkspaceDenied
from .projects import Project

_CLSID_FILE_OPEN_DIALOG = "dc1c5a9c-e88a-4dde-a5a1-60f82a20aef7"
_IID_FILE_OPEN_DIALOG = "d57c7288-d4ad-4768-be02-9d969532d960"
_IID_SHELL_ITEM = "43826d1e-e718-42ee-bc55-a1e261c37bfe"
_SIGDN_FILESYSPATH = 0x80058000
_CANCELLED = 0x800704C7
_FOS_PICKFOLDERS = 0x20
_FOS_FORCEFILESYSTEM = 0x40
_FOS_PATHMUSTEXIST = 0x800
_FOS_NOCHANGEDIR = 0x8
_FOS_DONTADDTORECENT = 0x02000000
_MB_YESNO = 0x4
_MB_ICONWARNING = 0x30
_MB_DEFBUTTON2 = 0x100
_MB_SETFOREGROUND = 0x10000


class _Guid(ctypes.Structure):
    _fields_ = [
        ("Data1", ctypes.c_uint32),
        ("Data2", ctypes.c_uint16),
        ("Data3", ctypes.c_uint16),
        ("Data4", ctypes.c_ubyte * 8),
    ]

    @classmethod
    def parse(cls, value: str) -> "_Guid":
        return cls.from_buffer_copy(uuid.UUID(value).bytes_le)


class _UserObjectFlags(ctypes.Structure):
    _fields_ = [
        ("fInherit", wintypes.BOOL),
        ("fReserved", wintypes.BOOL),
        ("dwFlags", wintypes.DWORD),
    ]


def _require_interactive_owner_desktop() -> None:
    """Reject session 0, noninteractive desktops and non-Windows processes.

    Checking a session does NOT authenticate the Owner. The caller must already
    be the trusted Owner host; remote/model requests must not call this module.
    """
    if os.name != "nt" or threading.current_thread() is not threading.main_thread():
        raise ProjectWorkspaceDenied("workspace_owner_desktop_required")
    try:
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        user = ctypes.WinDLL("user32", use_last_error=True)
        kernel.GetCurrentProcessId.argtypes = []
        kernel.GetCurrentProcessId.restype = wintypes.DWORD
        kernel.ProcessIdToSessionId.argtypes = [
            wintypes.DWORD, ctypes.POINTER(wintypes.DWORD),
        ]
        kernel.ProcessIdToSessionId.restype = wintypes.BOOL
        session_id = wintypes.DWORD(0)
        if not kernel.ProcessIdToSessionId(kernel.GetCurrentProcessId(),
                                          ctypes.byref(session_id)):
            raise ProjectWorkspaceDenied("workspace_owner_desktop_required")
        if session_id.value == 0:
            raise ProjectWorkspaceDenied("workspace_owner_desktop_required")
        user.GetProcessWindowStation.argtypes = []
        user.GetProcessWindowStation.restype = wintypes.HANDLE
        user.GetUserObjectInformationW.argtypes = [
            wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p,
            wintypes.DWORD, ctypes.POINTER(wintypes.DWORD),
        ]
        user.GetUserObjectInformationW.restype = wintypes.BOOL
        flags = _UserObjectFlags()
        needed = wintypes.DWORD(0)
        station = user.GetProcessWindowStation()
        if not station or not user.GetUserObjectInformationW(
            station, 1, ctypes.byref(flags), ctypes.sizeof(flags),
            ctypes.byref(needed),
        ) or not (flags.dwFlags & 0x1):
            raise ProjectWorkspaceDenied("workspace_owner_desktop_required")
    except (OSError, AttributeError, ValueError):
        raise ProjectWorkspaceDenied("workspace_owner_desktop_required") from None


def _invoke(interface: ctypes.c_void_p, index: int, types: tuple, *args):
    """Invoke a documented COM vtable slot using stdcall, without a shell."""
    if not interface.value:
        raise ProjectWorkspaceDenied("workspace_picker_unavailable")
    slots = ctypes.cast(
        interface, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))
    ).contents
    method = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, *types)(
        slots[index],
    )
    return method(interface, *args)


def _check_hresult(code: int) -> None:
    if code < 0:
        raise ProjectWorkspaceDenied("workspace_picker_unavailable")


def _native_choose_directory(project: Project) -> Path | None:
    """IFileOpenDialog/FOS_PICKFOLDERS with root as initial shell location.

    Cancelling returns None. Any COM failure fails closed. No PowerShell,
    legacy browse dialog, shell command, registry edit, temporary file or TCP.
    """
    ole = ctypes.WinDLL("ole32", use_last_error=True)
    shell = ctypes.WinDLL("shell32", use_last_error=True)
    ole.CoInitializeEx.argtypes = [ctypes.c_void_p, wintypes.DWORD]
    ole.CoInitializeEx.restype = ctypes.c_long
    ole.CoCreateInstance.argtypes = [
        ctypes.POINTER(_Guid), ctypes.c_void_p, wintypes.DWORD,
        ctypes.POINTER(_Guid), ctypes.POINTER(ctypes.c_void_p),
    ]
    ole.CoCreateInstance.restype = ctypes.c_long
    ole.CoUninitialize.argtypes = []
    ole.CoUninitialize.restype = None
    ole.CoTaskMemFree.argtypes = [ctypes.c_void_p]
    ole.CoTaskMemFree.restype = None
    shell.SHCreateItemFromParsingName.argtypes = [
        ctypes.c_wchar_p, ctypes.c_void_p, ctypes.POINTER(_Guid),
        ctypes.POINTER(ctypes.c_void_p),
    ]
    shell.SHCreateItemFromParsingName.restype = ctypes.c_long

    result = ole.CoInitializeEx(None, 2)  # COINIT_APARTMENTTHREADED
    _check_hresult(result)  # RPC_E_CHANGED_MODE fails, never switches apartments
    dialog = ctypes.c_void_p()
    initial = ctypes.c_void_p()
    chosen = ctypes.c_void_p()
    raw = ctypes.c_void_p()
    try:
        clsid = _Guid.parse(_CLSID_FILE_OPEN_DIALOG)
        iid = _Guid.parse(_IID_FILE_OPEN_DIALOG)
        _check_hresult(ole.CoCreateInstance(
            ctypes.byref(clsid), None, 1, ctypes.byref(iid),
            ctypes.byref(dialog),
        ))
        options = wintypes.DWORD()
        _check_hresult(_invoke(
            dialog, 10, (ctypes.POINTER(wintypes.DWORD),),
            ctypes.byref(options),
        ))
        options.value |= (_FOS_PICKFOLDERS | _FOS_FORCEFILESYSTEM
                          | _FOS_PATHMUSTEXIST | _FOS_NOCHANGEDIR
                          | _FOS_DONTADDTORECENT)
        _check_hresult(_invoke(
            dialog, 9, (wintypes.DWORD,), options.value,
        ))
        _check_hresult(_invoke(
            dialog, 17, (ctypes.c_wchar_p,),
            "OrdaX — selecionar a pasta do projeto",
        ))
        shell_iid = _Guid.parse(_IID_SHELL_ITEM)
        _check_hresult(shell.SHCreateItemFromParsingName(
            str(project.root), None, ctypes.byref(shell_iid),
            ctypes.byref(initial),
        ))
        _check_hresult(_invoke(dialog, 12, (ctypes.c_void_p,), initial))
        status = _invoke(dialog, 3, (wintypes.HWND,), None)
        if status & 0xFFFFFFFF == _CANCELLED:
            return None
        _check_hresult(status)
        _check_hresult(_invoke(
            dialog, 20, (ctypes.POINTER(ctypes.c_void_p),),
            ctypes.byref(chosen),
        ))
        _check_hresult(_invoke(
            chosen, 5, (wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p)),
            _SIGDN_FILESYSPATH, ctypes.byref(raw),
        ))
        if not raw.value:
            raise ProjectWorkspaceDenied("workspace_picker_unavailable")
        return Path(ctypes.wstring_at(raw.value))
    finally:
        if raw.value:
            ole.CoTaskMemFree(raw)
        for item in (chosen, initial, dialog):
            if item.value:
                _invoke(item, 2, ())
        ole.CoUninitialize()


def _native_confirm_owner(project: Project, root: Path) -> bool:
    """Second explicit Owner decision; default is NO and X is denial."""
    user = ctypes.WinDLL("user32", use_last_error=True)
    user.MessageBoxW.argtypes = [
        wintypes.HWND, ctypes.c_wchar_p, ctypes.c_wchar_p, wintypes.UINT,
    ]
    user.MessageBoxW.restype = ctypes.c_int
    message = (
        "Autorizar somente a seleção local deste projeto?\n\n"
        f"Projeto: {project.slug}\n"
        f"Pasta: {root}\n\n"
        "A seleção NÃO concede acesso remoto. Um grant separado na "
        "Platform ainda será obrigatório para qualquer leitura.\n\n"
        "Escolha Sim para confirmar ou Não para cancelar."
    )
    answer = user.MessageBoxW(
        None, message, "OrdaX — confirmar pasta do projeto",
        _MB_YESNO | _MB_ICONWARNING | _MB_DEFBUTTON2 | _MB_SETFOREGROUND,
    )
    if answer == 0:
        raise ProjectWorkspaceDenied("workspace_confirmation_unavailable")
    return answer == 6  # IDYES; IDNO and window closure deny


class WindowsOwnerWorkspacePicker:
    """Callbacks for a trusted, already authenticated interactive host ONLY.

    Construct with the host and inject choose_directory/confirm_owner into
    WorkspaceResourceSelection. Not registered in an MCP, Worker, service,
    generic Computer Control action or untrusted renderer.
    """

    def choose_directory(self, project: Project) -> Path | None:
        _require_interactive_owner_desktop()
        if not isinstance(project, Project):
            raise ProjectWorkspaceDenied("workspace_registered_project_unavailable")
        return _native_choose_directory(project)

    def confirm_owner(self, project: Project, root: Path) -> bool:
        _require_interactive_owner_desktop()
        if not isinstance(project, Project) or not isinstance(root, Path):
            raise ProjectWorkspaceDenied("workspace_confirmation_unavailable")
        return _native_confirm_owner(project, root)
