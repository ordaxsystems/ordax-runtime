from __future__ import annotations

import subprocess
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

from ordax_dev_agent.product_device_control_plane import ProductDeviceControlPlane
from ordax_dev_agent.projects import (
    canonical_github_repository_full_name,
    resolve_project_for_repository,
)


def _git(*args: str, cwd: Path) -> None:
    completed = subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr or completed.stdout)


def _config(tmp_path: Path):
    root = tmp_path / "ordax-gpt"
    root.mkdir()
    _git("init", cwd=root)
    _git("remote", "add", "origin", "git@github.com:ordaxsystems/ordax-gpt.git", cwd=root)
    return SimpleNamespace(
        projects={
            "ordax-gpt-local": {
                "path": str(root.resolve()),
                "apps": [],
                "allowed_branches": [],
            },
        },
        hordax_path=tmp_path / "unused",
    )


def _control_plane(config):
    control = object.__new__(ProductDeviceControlPlane)
    control.config = config
    return control


def test_canonical_github_repository_full_name_accepts_https_ssh_and_ssh_url() -> None:
    assert canonical_github_repository_full_name(
        "https://github.com/OrdaXSystems/ordax-gpt.git"
    ) == "ordaxsystems/ordax-gpt"
    assert canonical_github_repository_full_name(
        "git@github.com:OrdaXSystems/ordax-gpt.git"
    ) == "ordaxsystems/ordax-gpt"
    assert canonical_github_repository_full_name(
        "ssh://git@github.com/OrdaXSystems/ordax-gpt.git"
    ) == "ordaxsystems/ordax-gpt"
    assert canonical_github_repository_full_name("https://example.com/ordaxsystems/ordax-gpt.git") is None
    assert canonical_github_repository_full_name("ordax-gpt") is None


def test_resolve_project_for_repository_uses_git_origin_not_slug(tmp_path: Path) -> None:
    config = _config(tmp_path)
    project = resolve_project_for_repository(config, "ordaxsystems/ordax-gpt")
    assert project.slug == "ordax-gpt-local"


def test_product_project_scope_rebinds_untrusted_payload_project(tmp_path: Path) -> None:
    config = _config(tmp_path)
    control = _control_plane(config)
    project_id = str(uuid.uuid4())
    payload = {
        "project": "attacker-selected-slug",
        "summary": "checkpoint",
        "_ordax_project_scope": {
            "project_id": project_id,
            "repository_full_name": "ordaxsystems/ordax-gpt",
        },
    }

    rebound = control._bind_project_scope(
        {"capability": "continuity.update", "project_id": project_id},
        payload,
    )

    assert rebound["project"] == "ordax-gpt-local"
    assert rebound["summary"] == "checkpoint"
    assert "_ordax_project_scope" not in rebound
    assert "_ordax_project_scope" not in payload


def test_product_project_scope_rejects_project_id_mismatch(tmp_path: Path) -> None:
    config = _config(tmp_path)
    control = _control_plane(config)
    with pytest.raises(RuntimeError, match="project scope id mismatch"):
        control._bind_project_scope(
            {"capability": "continuity.get", "project_id": str(uuid.uuid4())},
            {
                "_ordax_project_scope": {
                    "project_id": str(uuid.uuid4()),
                    "repository_full_name": "ordaxsystems/ordax-gpt",
                },
            },
        )


def test_product_project_scope_rejects_missing_attestation(tmp_path: Path) -> None:
    config = _config(tmp_path)
    control = _control_plane(config)
    with pytest.raises(RuntimeError, match="attestation is missing"):
        control._bind_project_scope(
            {"capability": "agent.project_briefing", "project_id": str(uuid.uuid4())},
            {},
        )


def test_product_project_scope_rejects_scope_on_unrelated_action(tmp_path: Path) -> None:
    config = _config(tmp_path)
    control = _control_plane(config)
    with pytest.raises(RuntimeError, match="attestation is unexpected"):
        control._bind_project_scope(
            {"capability": "computer.active_window", "project_id": None},
            {
                "_ordax_project_scope": {
                    "project_id": str(uuid.uuid4()),
                    "repository_full_name": "ordaxsystems/ordax-gpt",
                },
            },
        )
