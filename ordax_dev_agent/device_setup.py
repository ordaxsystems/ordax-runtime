"""Durable Cloudflare v3 device enrollment and credential recovery."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
import socket
import subprocess
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlparse

import httpx

from .config import DEFAULT_CONTROL_PLANE_URL
from .device_credentials import resolve_pending_token_path, resolve_token_path
from .identity import machine_id


class SetupError(RuntimeError):
    pass


def private_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        sid = subprocess.run(
            ["whoami", "/user", "/fo", "csv", "/nh"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        import csv

        principal = next(csv.reader([sid.strip()]))[1]
        script = """
$ErrorActionPreference='Stop'
$current=Get-Acl -LiteralPath $env:ORDAX_SETUP_PRIVATE_PATH
$allowed=@($env:ORDAX_SETUP_SID,'S-1-5-18')
$rules=@($current.GetAccessRules($true,$true,[System.Security.Principal.SecurityIdentifier]))
if ($current.AreAccessRulesProtected -and $rules.Count -eq 2 -and
    @($rules | Where-Object { $_.IdentityReference.Value -notin $allowed -or $_.AccessControlType -ne 'Allow' -or $_.FileSystemRights -ne 'FullControl' }).Count -eq 0) { exit 0 }
$acl=New-Object System.Security.AccessControl.DirectorySecurity
$acl.SetAccessRuleProtection($true,$false)
foreach($id in @($env:ORDAX_SETUP_SID,'S-1-5-18')) {
  $sid=New-Object System.Security.Principal.SecurityIdentifier($id)
  $rule=New-Object System.Security.AccessControl.FileSystemAccessRule($sid,'FullControl','ContainerInherit,ObjectInherit','None','Allow')
  $acl.AddAccessRule($rule)
}
Set-Acl -LiteralPath $env:ORDAX_SETUP_PRIVATE_PATH -AclObject $acl
"""
        env = {k: v for k, v in os.environ.items() if k.lower() != "psmodulepath"}
        result = subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                script,
            ],
            env={
                **env,
                "ORDAX_SETUP_SID": principal,
                "ORDAX_SETUP_PRIVATE_PATH": str(path),
            },
            capture_output=True,
            timeout=120,
        )
        if result.returncode:
            raise SetupError("PRIVATE_ACL_FAILED")
    else:
        path.chmod(0o700)


def atomic_write(path: Path, value: str) -> None:
    temp = path.with_name(path.name + "." + secrets.token_hex(8) + ".tmp")
    try:
        with open(temp, "x", encoding="utf-8") as handle:
            if os.name != "nt":
                os.chmod(temp, 0o600)
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def atomic_json(path: Path, value: dict) -> None:
    atomic_write(path, json.dumps(value, indent=2))


def load_settings(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
        if not isinstance(value, dict) or not isinstance(value.get("projects", {}), dict):
            raise ValueError()
        return value
    except (ValueError, OSError):
        raise SetupError("SETTINGS_INVALID_PRESERVED") from None


def migrate_legacy_bridge_path(settings: dict, *, state: Path, home: Path) -> bool:
    raw = settings.get("bridge_path")
    if not isinstance(raw, str) or not raw.strip():
        return False

    configured = os.path.normcase(os.path.normpath(os.path.expandvars(raw.strip())))
    legacy = os.path.normcase(
        os.path.normpath(str(home / "Documents" / "github" / "mcp-blender"))
    )
    if configured != legacy:
        return False

    settings["bridge_path"] = str(state / "src")
    return True


def request(client, endpoint: str, body: dict, headers: dict) -> dict:
    try:
        response = client.post(endpoint, json=body, headers=headers)
        if response.status_code in (401, 403):
            return {"ok": False, "denied": response.status_code}
        if response.status_code >= 400:
            raise SetupError("CONTROL_PLANE_UNAVAILABLE")
        value = response.json()
        if not isinstance(value, dict) or value.get("ok") is not True:
            raise SetupError("CONTROL_PLANE_RESPONSE_INVALID")
        return value
    except (httpx.HTTPError, ValueError):
        raise SetupError("CONTROL_PLANE_UNAVAILABLE") from None


@contextmanager
def setup_lock(state: Path):
    with (state / ".device-setup.lock").open("a+b") as lock:
        if lock.seek(0, os.SEEK_END) == 0:
            lock.write(b"0")
            lock.flush()
        lock.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise SetupError("SETUP_ALREADY_RUNNING") from None
        try:
            yield
        finally:
            lock.seek(0)
            if os.name == "nt":
                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(lock, fcntl.LOCK_UN)


def configure(state: Path, **kwargs) -> dict:
    private_directory(state)
    with setup_lock(state):
        return _configure(state, **kwargs)


def _resolve_control_plane(control_plane_url: str | None) -> tuple[str, str]:
    raw = str(control_plane_url or DEFAULT_CONTROL_PLANE_URL).strip().rstrip("/")
    parsed = urlparse(raw)
    loopback = parsed.hostname in {"127.0.0.1", "localhost", "::1"}
    allowed_schemes = {"http", "https"} if loopback else {"https"}
    if not parsed.netloc or parsed.scheme not in allowed_schemes:
        raise SetupError("CONTROL_PLANE_URL_INVALID")
    return raw, raw + "/v3/device/setup"


def _configure(
    state: Path,
    *,
    interactive: bool = False,
    client=None,
    binding=None,
    home=None,
    control_plane_url: str | None = None,
    product_access_token: str | None = None,
) -> dict:
    settings_path = state / "agent-settings.json"
    settings = load_settings(settings_path)
    control_plane, endpoint = _resolve_control_plane(control_plane_url)
    binding = binding or machine_id()
    token = resolve_token_path(state)
    pending = resolve_pending_token_path(state)
    own_client = client is None
    client = client or httpx.Client(timeout=20, follow_redirects=False)

    try:
        identity = None
        for candidate in (token, pending):
            if not candidate.is_file():
                continue
            secret = candidate.read_text(encoding="utf-8-sig").strip()
            if not 32 <= len(secret) <= 512:
                continue
            identity = request(
                client,
                endpoint,
                {"operation": "identify", "machine_binding_sha256": binding},
                {"X-Ordax-Device-Token": secret},
            )
            if identity.get("ok"):
                if candidate == pending:
                    os.replace(pending, token)
                break
            if identity.get("denied") == 403:
                raise SetupError("MACHINE_BINDING_MISMATCH")

        if not identity or not identity.get("ok"):
            credential = str(product_access_token or "").strip()
            if not credential or len(credential) > 16_000:
                raise SetupError("PRODUCT_ACCOUNT_LOGIN_REQUIRED")
            if pending.is_file():
                secret = pending.read_text(encoding="utf-8").strip()
                if len(secret) != 64 or any(c not in "0123456789abcdef" for c in secret):
                    raise SetupError("PENDING_TOKEN_INVALID_PRESERVED")
            else:
                secret = secrets.token_hex(32)
                atomic_write(pending, secret)

            identity = request(
                client,
                endpoint,
                {
                    "operation": "enroll",
                    "machine_binding_sha256": binding,
                    "device_name": socket.gethostname(),
                    "token_sha256": hashlib.sha256(secret.encode()).hexdigest(),
                },
                {"Authorization": "Bearer " + credential},
            )
            if not identity.get("ok"):
                raise SetupError("USER_AUTHORIZATION_REQUIRED")
            os.replace(pending, token)

        import uuid

        device_id = str(uuid.UUID(identity["device_id"]))
        if identity.get("protocol") != "cloudflare-v3":
            raise SetupError("PROTOCOL_MISMATCH")

        settings.update(
            control_plane_url=control_plane,
            control_plane_protocol="cloudflare-v3",
            device_id=device_id,
        )

        effective_home = home or Path.home()
        migrate_legacy_bridge_path(settings, state=state, home=effective_home)

        projects = settings.setdefault("projects", {})
        root = effective_home / "Documents" / "github"
        for slug in ("cerco-no-interior-mvp", "dioramas-biblicos"):
            if (root / slug).is_dir() and slug not in projects:
                projects[slug] = {
                    "path": str(root / slug),
                    "apps": ["blender"],
                    "blender": {"scripts_dir": "automation/blender"},
                }
        if settings.get("default_project") not in projects and projects:
            settings["default_project"] = next(iter(projects))

        atomic_json(settings_path, settings)
        return {
            "ok": True,
            "device_id": device_id,
            "protocol": "cloudflare-v3",
            "control_plane_url": control_plane,
        }
    finally:
        if own_client:
            client.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--interactive", action="store_true")
    parser.add_argument(
        "--control-plane-url",
        default=os.environ.get("ORDAX_CONTROL_PLANE_URL", DEFAULT_CONTROL_PLANE_URL),
    )
    args = parser.parse_args()
    try:
        state = Path(os.environ["LOCALAPPDATA"]) / "OrdaX" / "DevAgent"
        result = configure(
            state,
            interactive=args.interactive,
            control_plane_url=args.control_plane_url,
        )
        print(json.dumps(result))
        return 0
    except SetupError as error:
        print(str(error))
        return 2
    except Exception:
        print("SETUP_FAILED")
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
