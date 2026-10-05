"""Managed transports for isolated Unity game-asset Editor companions."""
from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any, Callable

from .models import ActionResult


GAME_ASSET_COMPANION_PROTOCOL = "ordax-game-assets-v1"
GAME_ASSET_ANIMATION_COMPANION_PROTOCOL = "ordax-game-assets-animation-v1"


def _fresh(path: Path, max_age_seconds: float = 8.0) -> bool:
    try:
        age = time.time() - path.stat().st_mtime
        return 0 <= age <= max_age_seconds
    except OSError:
        return False


def _install_managed_companion(
    project,
    *,
    source_filename: str,
    target_filename: str,
    protocol: str,
    class_marker: str,
    label: str,
) -> tuple[Path, bool]:
    source = Path(__file__).parent / "assets" / source_filename
    if not source.is_file():
        raise FileNotFoundError(f"packaged Unity {label} companion is missing")
    target = project.path(
        f"Assets/OrdaX/Editor/{target_filename}", must_exist=False
    )
    content = source.read_bytes()
    before = target.read_bytes() if target.is_file() else None
    if before is not None and before != content:
        try:
            existing = before.decode("utf-8-sig")
        except UnicodeDecodeError as error:
            raise ValueError(
                f"existing Unity {label} companion is not valid UTF-8; refusing overwrite"
            ) from error
        markers = (class_marker, protocol, "namespace OrdaX.EditorTools")
        if not all(marker in existing for marker in markers):
            raise ValueError(
                f"existing Unity {label} companion is not OrdaX-managed; refusing overwrite"
            )
    target.parent.mkdir(parents=True, exist_ok=True)
    changed = before != content
    if changed:
        temporary = target.with_name(target.name + f".ordax-{uuid.uuid4().hex}.tmp")
        try:
            temporary.write_bytes(content)
            if temporary.read_bytes() != content:
                raise OSError(f"temporary Unity {label} companion verification failed")
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)
    return target, changed


def _request_managed_companion(
    *,
    project,
    refresh_editor: Callable[[dict[str, Any]], ActionResult],
    action: str,
    payload: dict[str, Any],
    timeout_seconds: float,
    source_filename: str,
    target_filename: str,
    protocol: str,
    class_marker: str,
    state_subdir: str,
    label: str,
    transport: str,
) -> ActionResult:
    try:
        companion, changed = _install_managed_companion(
            project,
            source_filename=source_filename,
            target_filename=target_filename,
            protocol=protocol,
            class_marker=class_marker,
            label=label,
        )
    except (ValueError, OSError, FileNotFoundError) as error:
        return ActionResult(False, str(error))

    root = project.root / "Library" / "OrdaXAgent" / state_subdir
    presence = root / "presence.json"
    if changed or not _fresh(presence):
        refreshed = refresh_editor(
            {
                "project": project.slug,
                "force": True,
                "wait_seconds": min(120.0, timeout_seconds),
            }
        )
        if not refreshed.ok:
            return ActionResult(
                False,
                f"Unity {label} companion was installed but Editor refresh was not confirmed",
                {
                    "retryable": True,
                    "companion_path": str(companion),
                    "refresh": refreshed.data,
                },
            )
        deadline = time.monotonic() + min(timeout_seconds, 120.0)
        while time.monotonic() < deadline and not _fresh(presence):
            time.sleep(0.25)
    if not _fresh(presence):
        return ActionResult(
            False,
            f"Unity {label} companion did not become ready",
            {
                "retryable": True,
                "companion_path": str(companion),
                "presence_path": str(presence),
            },
        )

    inbox = root / "inbox"
    responses = root / "responses"
    inbox.mkdir(parents=True, exist_ok=True)
    responses.mkdir(parents=True, exist_ok=True)
    command_id = uuid.uuid4().hex
    command_path = inbox / f"{command_id}.json"
    temp_path = inbox / f"{command_id}.tmp"
    response_path = responses / f"{command_id}.json"
    command = {"id": command_id, "action": action, **payload}
    temp_path.write_text(json.dumps(command), encoding="utf-8")
    temp_path.replace(command_path)

    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if response_path.is_file():
            try:
                response = json.loads(response_path.read_text(encoding="utf-8-sig"))
            except (OSError, json.JSONDecodeError) as error:
                return ActionResult(False, f"Unity {label} response is invalid: {error}")
            finally:
                response_path.unlink(missing_ok=True)
            if not isinstance(response, dict):
                return ActionResult(False, f"Unity {label} response must be an object")
            if response.get("protocol") != protocol:
                return ActionResult(
                    False,
                    f"Unity {label} companion protocol is incompatible",
                    {"retryable": True, "response": response},
                )
            if not bool(response.get("ok")):
                return ActionResult(
                    False,
                    str(response.get("summary") or f"Unity {label} action failed"),
                    response,
                )
            return ActionResult(
                True,
                str(response.get("summary") or f"Unity {label} action completed"),
                {"transport": transport, **response},
            )
        time.sleep(0.2)

    command_path.unlink(missing_ok=True)
    return ActionResult(
        False,
        f"Unity {label} action timed out; inspect Editor state before retrying",
        {
            "retryable": True,
            "outcome_unknown": True,
            "command_id": command_id,
            "action": action,
        },
    )


def install_game_asset_companion(project) -> tuple[Path, bool]:
    return _install_managed_companion(
        project,
        source_filename="OrdaXGameAssetAgent.cs",
        target_filename="OrdaXGameAssetAgent.cs",
        protocol=GAME_ASSET_COMPANION_PROTOCOL,
        class_marker="class OrdaXGameAssetAgent",
        label="game asset",
    )


def request_game_asset_companion(
    *,
    project,
    refresh_editor: Callable[[dict[str, Any]], ActionResult],
    action: str,
    payload: dict[str, Any],
    timeout_seconds: float,
) -> ActionResult:
    return _request_managed_companion(
        project=project,
        refresh_editor=refresh_editor,
        action=action,
        payload=payload,
        timeout_seconds=timeout_seconds,
        source_filename="OrdaXGameAssetAgent.cs",
        target_filename="OrdaXGameAssetAgent.cs",
        protocol=GAME_ASSET_COMPANION_PROTOCOL,
        class_marker="class OrdaXGameAssetAgent",
        state_subdir="game-assets",
        label="game asset telemetry",
        transport="unity-game-asset-companion",
    )


def install_game_asset_animation_companion(project) -> tuple[Path, bool]:
    return _install_managed_companion(
        project,
        source_filename="OrdaXGameAssetAnimationAgent.cs",
        target_filename="OrdaXGameAssetAnimationAgent.cs",
        protocol=GAME_ASSET_ANIMATION_COMPANION_PROTOCOL,
        class_marker="class OrdaXGameAssetAnimationAgent",
        label="game asset animation",
    )


def request_game_asset_animation_companion(
    *,
    project,
    refresh_editor: Callable[[dict[str, Any]], ActionResult],
    action: str,
    payload: dict[str, Any],
    timeout_seconds: float,
) -> ActionResult:
    return _request_managed_companion(
        project=project,
        refresh_editor=refresh_editor,
        action=action,
        payload=payload,
        timeout_seconds=timeout_seconds,
        source_filename="OrdaXGameAssetAnimationAgent.cs",
        target_filename="OrdaXGameAssetAnimationAgent.cs",
        protocol=GAME_ASSET_ANIMATION_COMPANION_PROTOCOL,
        class_marker="class OrdaXGameAssetAnimationAgent",
        state_subdir="game-assets-animation",
        label="game asset animation",
        transport="unity-game-asset-animation-companion",
    )
