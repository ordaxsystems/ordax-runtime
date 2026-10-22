"""Canonical Git subprocess environment for read-only Runtime observations.

Never inherit Git-specific environment overrides from a desktop, agent or CLI.
Callers may supply only their own explicit typed Git operation and local root.
"""
from __future__ import annotations

import os


def readonly_git_environment() -> dict[str, str]:
    environment = {
        name: value for name, value in os.environ.items()
        if not name.upper().startswith("GIT_")
    }
    environment.update({
        "GIT_OPTIONAL_LOCKS": "0",
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_NO_REPLACE_OBJECTS": "1",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
    })
    return environment
