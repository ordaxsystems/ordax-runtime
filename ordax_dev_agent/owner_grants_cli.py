"""Installed Runtime Owner grant CLI; no Studio UI, Node, or parallel authority.

Only the authenticated canonical Product Owner API may mutate a grant.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from typing import Any

from ordax_core.owner_oauth import OwnerOAuthError, browser_authorization_code, exchange_owner_code
from ordax_dev_agent.config import AgentConfig
from ordax_dev_agent.computer_filesystem_actions import (
    computer_access_management_status,
    update_computer_access_policy,
)
from ordax_dev_agent.product_remote_client import ProductRemoteClient, ProductRemoteError
from ordax_dev_agent.product_gateway import PRODUCT_ACTIONS

_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\Z", re.I)
# The Control Plane owns the profile catalog and validates each scope.
# Do not duplicate that catalog in a per-client allowlist.
_PROFILE_KEY = re.compile(r"[a-z][a-z0-9._-]{1,79}\Z")


def _uuid(value: str) -> str:
    if not _UUID.fullmatch(value):
        raise argparse.ArgumentTypeError("expected canonical UUID")
    return value.lower()


def _profile(value: str) -> str:
    if not _PROFILE_KEY.fullmatch(value):
        raise argparse.ArgumentTypeError("invalid grant profile key")
    return value


def _days(value: str) -> int:
    try:
        days = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("days must be an integer") from exc
    if not 1 <= days <= 365:
        raise argparse.ArgumentTypeError("days must be between 1 and 365")
    return days


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="ordax-owner-grants", description="Manage authorized OrdaX MCP grants without the Studio UI")
    commands = parser.add_subparsers(dest="operation", required=True)
    for name in ("list", "authorize", "revoke", "diagnose", "dev-enable",
                 "central-targets", "central-selections", "central-select"):
        sub = commands.add_parser(name)
        sub.add_argument("--device", required=True, type=_uuid)
        if name != "list":
            sub.add_argument("--client", type=_uuid)
        if name == "central-select":
            sub.add_argument("--challenge", required=True, type=_uuid)
            sub.add_argument("--confirm", action="store_true", help="Confirm this exact Owner session-device choice")
        if name == "authorize":
            sub.add_argument("--profile", required=True, type=_profile)
            sub.add_argument("--space", type=_uuid)
            sub.add_argument("--project", type=_uuid)
        if name in {"authorize", "dev-enable"}:
            sub.add_argument("--days", type=_days, default=30)
        if name == "revoke":
            sub.add_argument("--group", type=_uuid, required=True)
    args = parser.parse_args(argv)
    if args.operation == "central-select" and not args.client:
        parser.error("central-select requires the exact OAuth client with --client")
    if args.operation == "central-select" and not args.confirm:
        parser.error("central-select requires explicit --confirm")
    if args.operation == "authorize":
        if bool(args.space) != bool(args.project):
            parser.error("project grants require both --space and --project")
        # Profile scope and supported profile names are validated by the
        # authenticated Control Plane, the sole profile catalog authority.
    return args


def select_client(authority: dict[str, Any], requested: str | None) -> str:
    clients = authority.get("clients", [])
    matching = [
        str(c["client_id"]).lower() for c in clients
        if isinstance(c, dict) and isinstance(c.get("client_id"), str)
        and _UUID.fullmatch(c["client_id"])
        and (requested is None or c["client_id"].lower() == requested)
    ]
    if len(matching) != 1:
        raise ValueError("select an exact consented OAuth client with --client" if matching else "no consented OAuth client matched")
    return matching[0]


# This diagnostic inventory is not a second profile catalog: it selects the
# end-to-end actions required for basic Remote Desktop Commander parity.
# Valid grant capabilities still come solely from the authenticated Control Plane.
_DIAGNOSTIC_ACTIONS = (
    "computer.windows",
    "computer.screenshot",
    "computer.click",
    "computer.terminal_exec",
    "computer.process_start",
    "computer.process_list",
    "computer.text_read",
    "computer.text_write",
)


def diagnose_owner_mcp_readiness(
    authority: dict[str, Any], client_id: str, policy: dict[str, Any],
) -> dict[str, Any]:
    """Explain device MCP gaps without granting access or asserting E2E success."""
    if not isinstance(policy, dict):
        raise ValueError("invalid_local_computer_policy")
    local_enabled = policy.get("enabled") is True
    full_access = policy.get("full_access") is True
    now = datetime.now(timezone.utc)
    device_id = authority.get("device_id")
    if not isinstance(device_id, str) or not _UUID.fullmatch(device_id):
        raise ValueError("owner_authority_device_invalid")
    allowed: set[tuple[str, str]] = set()
    for group in authority.get("grant_groups", []):
        if not isinstance(group, dict) or str(group.get("client_id", "")).lower() != client_id:
            continue
        # The canonical Owner RPC includes consent, device and scope evidence.
        # A revoked OAuth consent or an unrelated device/Project is not a grant.
        if (
            group.get("client_consent_active") is not True
            or group.get("scope_kind") != "device"
            or str(group.get("device_id", "")).lower() != device_id.lower()
            or group.get("space_id") is not None
            or group.get("project_id") is not None
        ):
            continue
        until = group.get("valid_until")
        if until is not None:
            if not isinstance(until, str):
                continue
            try:
                expiry = datetime.fromisoformat(until.replace("Z", "+00:00"))
            except ValueError:
                continue
            if expiry.tzinfo is None or expiry <= now:
                continue
        capabilities = group.get("capabilities")
        if not isinstance(capabilities, list):
            continue
        for item in capabilities:
            if not isinstance(item, dict):
                continue
            capability = item.get("capability")
            access_mode = item.get("access_mode", item.get("accessMode"))
            if isinstance(capability, str) and access_mode in ("read", "write"):
                allowed.add((capability, access_mode))
    checks = [
        {
            "capability": name,
            "requiredAccessMode": (
                None if name not in PRODUCT_ACTIONS
                else "read" if PRODUCT_ACTIONS[name].effect == "read" else "write"
            ),
            "declaredByInstalledCode": name in PRODUCT_ACTIONS,
            "clientGranted": (
                name in PRODUCT_ACTIONS
                and (name, "read" if PRODUCT_ACTIONS[name].effect == "read" else "write") in allowed
            ),
        }
        for name in _DIAGNOSTIC_ACTIONS
    ]
    missing_code = [c["capability"] for c in checks if not c["declaredByInstalledCode"]]
    missing_grants = [c["capability"] for c in checks if not c["clientGranted"]]
    ready = local_enabled and full_access and not missing_code and not missing_grants
    return {
        "schema": "ordax.owner-mcp-readiness/1",
        "state": "preflight-ready-unproven" if ready else "blocked",
        "client_id": client_id,
        "localPolicyEnabled": local_enabled,
        "localFullAccess": full_access,
        "capabilities": checks,
        "missingInstalledCode": missing_code,
        "missingClientGrants": missing_grants,
        "endToEndProven": False,
        "grantMutationPerformed": False,
        "note": "Only actual authorized MCP actions on the installed device establish end-to-end readiness.",
    }


def perform(args: argparse.Namespace, token: str, remote: ProductRemoteClient,
            *, policy_checker=computer_access_management_status,
            policy_updater=update_computer_access_policy,
            config: AgentConfig | None = None) -> dict[str, Any]:
    authority = remote.owner_remote_authority(token, device_id=args.device)
    if args.operation == "list":
        return {"device_id": args.device, "clients": authority["clients"], "grant_groups": authority["grant_groups"]}
    client = select_client(authority, args.client)
    if args.operation == "central-targets":
        return remote.owner_central_session_targets(token, client_id=client)
    if args.operation == "central-selections":
        return remote.owner_central_session_selections(token, client_id=client)
    if args.operation == "central-select":
        # Only this explicit Owner command can ask Platform to choose the
        # previously granted device for one exact pending native challenge.
        # This is NOT attestation, a grant change, or remote action execution.
        if args.confirm is not True:
            raise ValueError("owner_central_selection_explicit_confirmation_required")
        return remote.owner_select_central_device(
            token, client_id=client, challenge_id=args.challenge, device_id=args.device,
        )
    if args.operation == "diagnose":
        return diagnose_owner_mcp_readiness(authority, client, policy_checker(
            config if config is not None else AgentConfig.from_env()))
    if args.operation == "dev-enable":
        # Local opt-in only: an authenticated owner cannot remotely flip the
        # filesystem policy of an unrelated device. Use its own installed Runtime.
        local = config if config is not None else AgentConfig.from_env()
        local_device_id = str(local.device_id or "").lower()
        if local_device_id != args.device:
            raise ValueError("development_access_must_run_on_target_device")
        previous = policy_checker(local)
        revision = previous.get("revision") if isinstance(previous, dict) else None
        if not isinstance(revision, str) or not revision:
            raise ValueError("computer_access_revision_unavailable")
        updated = policy_updater(local, {
            "enabled": True,
            "full_access": True,
            "full_filesystem": True,
            "expected_revision": revision,
        })
        if not isinstance(updated, dict) or any(
            updated.get(key) is not True
            for key in ("enabled", "full_access", "full_filesystem")
        ):
            raise ValueError("development_local_access_not_enabled")
        # No independent authority: the exact OAuth client and already
        # authenticated Owner still pass through the canonical grant API.
        expires = (datetime.now(timezone.utc) + timedelta(days=args.days)).isoformat()
        grant = remote.replace_owner_remote_grant_group(
            token, client_id=client, device_id=args.device,
            profile_key="full-computer-control",
            space_id=None, project_id=None, valid_until=expires,
        )
        return {
            "ok": grant.get("ok") is True,
            "device_id": args.device,
            "profile_key": "full-computer-control",
            "valid_until": expires,
            "local_policy": {
                key: updated.get(key)
                for key in ("enabled", "full_access", "full_filesystem")
            },
            "grant": grant,
            "end_to_end_proven": False,
        }
    if args.operation == "authorize":
        if args.profile in {"full-computer-control", "device-native-terminal", "device-native-process"}:
            policy = policy_checker(config if config is not None else AgentConfig.from_env())
            if not policy.get("enabled") or not policy.get("full_access"):
                raise ValueError("local_full_access_required")
        expires = (datetime.now(timezone.utc) + timedelta(days=args.days)).isoformat()
        return remote.replace_owner_remote_grant_group(
            token, client_id=client, device_id=args.device, profile_key=args.profile,
            space_id=args.space, project_id=args.project, valid_until=expires,
        )
    groups = authority["grant_groups"]
    owned = [g for g in groups if isinstance(g, dict)
             and str(g.get("grant_group_id", "")).lower() == args.group
             and str(g.get("client_id", "")).lower() == client]
    if len(owned) != 1:
        raise ValueError("owner_grant_group_not_found")
    return remote.revoke_owner_remote_grant_group(token, client_id=client, grant_group_id=args.group)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        code, verifier = browser_authorization_code()
        token = exchange_owner_code(code, verifier)  # Memory only; never persisted or printed
        config = AgentConfig.from_env()
        with ProductRemoteClient(config.product_mcp_url) as remote:
            result = perform(args, token, remote, config=config)
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0
    except OwnerOAuthError as exc:
        print(f"OrdaX Owner OAuth: {exc.code}", file=sys.stderr)
    except ProductRemoteError as exc:
        print(f"OrdaX Owner API: {exc.error_code}", file=sys.stderr)
    except (OSError, ValueError) as exc:
        print(f"OrdaX Owner: {exc}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
