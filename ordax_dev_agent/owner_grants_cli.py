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
from ordax_dev_agent.computer_filesystem_actions import computer_access_management_status
from ordax_dev_agent.product_remote_client import ProductRemoteClient, ProductRemoteError

_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\Z", re.I)
_DEVICE_PROFILES = frozenset({
    "computer-observe", "computer-filesystem", "computer-clipboard",
    "computer-process-control", "interactive-computer-control",
    "full-computer-control", "app-intelligence-read", "computer-text-edit",
})
_PROJECT_PROFILES = frozenset({
    "project-browser-automation", "project-continuity",
    "project-native-terminal", "project-native-process",
})


def _uuid(value: str) -> str:
    if not _UUID.fullmatch(value):
        raise argparse.ArgumentTypeError("expected canonical UUID")
    return value.lower()


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
    for name in ("list", "authorize", "revoke"):
        sub = commands.add_parser(name)
        sub.add_argument("--device", required=True, type=_uuid)
        if name != "list":
            sub.add_argument("--client", type=_uuid)
        if name == "authorize":
            sub.add_argument("--profile", required=True, choices=sorted(_DEVICE_PROFILES | _PROJECT_PROFILES))
            sub.add_argument("--space", type=_uuid)
            sub.add_argument("--project", type=_uuid)
            sub.add_argument("--days", type=_days, default=30)
        if name == "revoke":
            sub.add_argument("--group", type=_uuid, required=True)
    args = parser.parse_args(argv)
    if args.operation == "authorize":
        project_scope = args.profile in _PROJECT_PROFILES
        if project_scope != bool(args.space or args.project):
            parser.error("the selected profile and project scope do not match")
        if project_scope and (not args.space or not args.project):
            parser.error("project grants require both --space and --project")
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


def perform(args: argparse.Namespace, token: str, remote: ProductRemoteClient,
            *, policy_checker=computer_access_management_status, config: AgentConfig | None = None) -> dict[str, Any]:
    authority = remote.owner_remote_authority(token, device_id=args.device)
    if args.operation == "list":
        return {"device_id": args.device, "clients": authority["clients"], "grant_groups": authority["grant_groups"]}
    client = select_client(authority, args.client)
    if args.operation == "authorize":
        if args.profile == "full-computer-control":
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
