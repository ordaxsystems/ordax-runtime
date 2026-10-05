from __future__ import annotations

import os
import stat
import uuid
from dataclasses import dataclass
from pathlib import Path


LOCAL_DEVICE_ID_FILE = "local-device-id"


class LocalDeviceIdentityError(RuntimeError):
    pass


def _parse_device_id(raw: str) -> str:
    value = raw.strip()
    try:
        parsed = uuid.UUID(value)
    except (ValueError, AttributeError, TypeError):
        raise LocalDeviceIdentityError("LOCAL_DEVICE_ID_INVALID_PRESERVED") from None
    canonical = str(parsed)
    if value.lower() != canonical:
        raise LocalDeviceIdentityError("LOCAL_DEVICE_ID_INVALID_PRESERVED")
    return canonical


def _read_existing(path: Path) -> str:
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        fd = os.open(path, flags)
    except OSError as error:
        raise LocalDeviceIdentityError("LOCAL_DEVICE_ID_READ_FAILED") from error
    try:
        mode = os.fstat(fd).st_mode
        if not stat.S_ISREG(mode):
            raise LocalDeviceIdentityError("LOCAL_DEVICE_ID_NOT_REGULAR")
        with os.fdopen(fd, "r", encoding="utf-8", closefd=False) as handle:
            raw = handle.read(256)
            if handle.read(1):
                raise LocalDeviceIdentityError("LOCAL_DEVICE_ID_INVALID_PRESERVED")
        return _parse_device_id(raw)
    finally:
        os.close(fd)


def load_or_create_local_device_id(state_dir: Path) -> str:
    """Return the Runtime-owned stable local device id.

    This identity exists independently from Control Plane enrollment. It is not
    a secret and must not be replaced by a paired remote `device_id` after the
    user connects an account. Existing invalid state is preserved and fails
    closed instead of being silently regenerated.
    """

    state_dir.mkdir(parents=True, exist_ok=True)
    path = state_dir / LOCAL_DEVICE_ID_FILE
    if path.exists():
        return _read_existing(path)

    value = str(uuid.uuid4())
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        fd = os.open(path, flags, 0o600)
    except FileExistsError:
        return _read_existing(path)
    except OSError as error:
        raise LocalDeviceIdentityError("LOCAL_DEVICE_ID_CREATE_FAILED") from error

    try:
        try:
            if os.name != "nt":
                os.fchmod(fd, 0o600)
            payload = (value + "\n").encode("utf-8")
            written = 0
            while written < len(payload):
                count = os.write(fd, payload[written:])
                if count <= 0:
                    raise OSError("local device identity write made no progress")
                written += count
            os.fsync(fd)
        finally:
            os.close(fd)
    except Exception:
        # Close the handle before cleanup. Windows does not reliably permit
        # unlinking an open file, and masking the original write failure would
        # make recovery harder to diagnose.
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    return value


@dataclass(frozen=True, slots=True)
class WindowsRuntimeDeviceIdentity:
    local_device_id: str
    paired_device_id: str | None

    @property
    def local_studio_target_ids(self) -> frozenset[str]:
        # Local device-owner requests always use the Runtime-owned local id.
        # The paired id belongs to remote/account routing and is deliberately
        # not accepted as an alias for local owner authority.
        return frozenset({self.local_device_id})


def resolve_windows_runtime_device_identity(config) -> WindowsRuntimeDeviceIdentity:
    return WindowsRuntimeDeviceIdentity(
        local_device_id=load_or_create_local_device_id(config.state_dir),
        paired_device_id=(str(config.device_id).strip() if config.device_id else None),
    )
