from __future__ import annotations

from pathlib import Path


_TOKEN_FILE = "device-token.cloudflare-v3.txt"


def token_path(state_dir: Path) -> Path:
    return state_dir / _TOKEN_FILE


def pending_token_path(state_dir: Path) -> Path:
    path = token_path(state_dir)
    return path.with_name(path.name + ".pending-setup")


def resolve_pending_token_path(state_dir: Path) -> Path:
    return pending_token_path(state_dir)


def resolve_token_path(state_dir: Path) -> Path:
    return token_path(state_dir)
