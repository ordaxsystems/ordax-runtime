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
