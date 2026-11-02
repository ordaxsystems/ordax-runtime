from __future__ import annotations

import hashlib
import socket
import subprocess
import uuid


def machine_id() -> str:
    raw = ""

    try:
        result = subprocess.run(
            [
                "reg",
                "query",
                r"HKLM\SOFTWARE\Microsoft\Cryptography",
                "/v",
                "MachineGuid",
            ],
            capture_output=True,
            text=True,
            timeout=10,
            shell=False,
        )
        if result.returncode == 0:
            for line in result.stdout.splitlines():
                if "MachineGuid" in line:
                    raw = line.split()[-1].strip()
                    break
    except Exception:
        raw = ""

    if not raw:
        raw = f"{socket.gethostname()}:{uuid.getnode()}"

    # Never send raw Windows MachineGuid. The Edge Function hashes this again,
    # while this local hash avoids exposing the platform identifier in transit.
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()

def desktop_machine_binding() -> str:
    """Stable, domain-separated instance binding for the OrdaX Desktop product.

    The Windows agent uses machine_id() directly.  PostgreSQL intentionally
    enforces one device credential per binding, so the native Desktop must
    never enroll with the Windows agent's binding or rotate its token.
    """
    physical = machine_id()
    return hashlib.sha256(
        b"ordax.product.device-binding/1\0ordax-desktop\0"
        + physical.encode("ascii")
    ).hexdigest()