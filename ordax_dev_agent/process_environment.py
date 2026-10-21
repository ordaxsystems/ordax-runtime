"""Keep a frozen native Runtime's private loader paths out of system tools."""
from __future__ import annotations

import os
import sys


def external_process_environment(overrides: dict[str, str] | None = None) -> dict[str, str]:
    environment = os.environ.copy()
    if getattr(sys, "frozen", False) and sys.platform == "linux":
        original = environment.get("LD_LIBRARY_PATH_ORIG")
        if original is None:
            environment.pop("LD_LIBRARY_PATH", None)
        else:
            environment["LD_LIBRARY_PATH"] = original
    if overrides:
        environment.update(overrides)
    return environment
