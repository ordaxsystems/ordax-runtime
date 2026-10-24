from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from urllib.parse import urlsplit

from .config import DEFAULT_PRODUCT_MCP_URL


LOCK_SCHEMA = "ordax.control-plane-source-lock/1"
PROVENANCE_SCHEMA = "ordax.control-plane-endpoint-provenance/1"
CANONICAL_REPOSITORY = "ordaxsystems/ordax-platform"
CANONICAL_SOURCE_PATH = "control-plane/cloudflare/production-foundation.json"
ENVIRONMENTS = {"validation-candidate", "production"}
_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
_HOST_LABEL_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")


class ControlPlaneProvenanceError(ValueError):
    """The package endpoint is not backed by its pinned Platform source."""


def _read_object(path: Path, label: str) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as error:
        raise ControlPlaneProvenanceError(f"Cannot read {label}: {error}") from error
    if not isinstance(value, dict):
        raise ControlPlaneProvenanceError(f"{label} must be a JSON object")
    return value


def _require_text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise ControlPlaneProvenanceError(f"{label} must be a non-empty string")
    return value


def _valid_hostname(hostname: str) -> bool:
    if len(hostname) > 253 or hostname.endswith(".") or hostname.lower() != hostname:
        return False
    labels = hostname.split(".")
    return len(labels) >= 2 and all(_HOST_LABEL_RE.fullmatch(label) for label in labels)


def resolve_control_plane_provenance(
    lock_path: str | Path,
    platform_root: str | Path,
    environment: str,
) -> dict:
    """Resolve an installer endpoint from one immutable Platform foundation commit."""
    lock_file = Path(lock_path)
    lock = _read_object(lock_file, "Control Plane source lock")
    if lock.get("schema") != LOCK_SCHEMA or lock.get("authority") != "none":
        raise ControlPlaneProvenanceError("Control Plane source lock schema or authority is invalid")

    source = lock.get("source")
    if not isinstance(source, dict) or set(source) != {"repository", "path", "commit", "version"}:
        raise ControlPlaneProvenanceError("Control Plane source lock source fields are invalid")
    if source.get("repository") != CANONICAL_REPOSITORY:
        raise ControlPlaneProvenanceError("Control Plane source repository is not canonical")
    if source.get("path") != CANONICAL_SOURCE_PATH:
        raise ControlPlaneProvenanceError("Control Plane source path is not canonical")
    commit = source.get("commit")
    if not isinstance(commit, str) or not _COMMIT_RE.fullmatch(commit):
        raise ControlPlaneProvenanceError("Control Plane source commit must be an immutable Git SHA")
    version = source.get("version")
    if isinstance(version, bool) or not isinstance(version, int) or version < 1:
        raise ControlPlaneProvenanceError("Control Plane source version must be a positive integer")

    if not isinstance(environment, str) or environment not in ENVIRONMENTS:
        raise ControlPlaneProvenanceError(f"Unsupported Control Plane environment: {environment}")

    try:
        platform_dir = Path(platform_root).resolve(strict=True)
        source_file = (platform_dir / CANONICAL_SOURCE_PATH).resolve(strict=True)
    except OSError as error:
        raise ControlPlaneProvenanceError(
            f"Cannot resolve pinned Platform foundation: {error}"
        ) from error
    try:
        source_file.relative_to(platform_dir)
    except ValueError as error:
        raise ControlPlaneProvenanceError("Pinned Control Plane source escapes its checkout") from error
    foundation = _read_object(source_file, "pinned Platform foundation")
    foundation_version = foundation.get("version")
    if (
        isinstance(foundation_version, bool)
        or not isinstance(foundation_version, int)
        or foundation_version != version
    ):
        raise ControlPlaneProvenanceError("Pinned Platform foundation version differs from the Runtime lock")

    dns = foundation.get("dns")
    release = foundation.get("mcp_release")
    if not isinstance(dns, dict) or not isinstance(release, dict):
        raise ControlPlaneProvenanceError("Pinned Platform foundation lacks DNS or MCP release metadata")
    zone = _require_text(dns.get("zone"), "Platform DNS zone")
    hostname = _require_text(release.get("hostname"), "Platform MCP hostname")
    endpoint_path = _require_text(release.get("path"), "Platform MCP path")
    if not _valid_hostname(zone) or not _valid_hostname(hostname):
        raise ControlPlaneProvenanceError("Platform DNS zone or MCP hostname is malformed")
    if hostname != zone and not hostname.endswith("." + zone):
        raise ControlPlaneProvenanceError("Platform MCP hostname is outside its canonical DNS zone")
    if hostname.endswith(".workers.dev"):
        raise ControlPlaneProvenanceError("Workers.dev is not an approved package endpoint")
    if (
        not endpoint_path.startswith("/")
        or endpoint_path.startswith("//")
        or any(part in {".", ".."} for part in endpoint_path.split("/"))
        or any(character in endpoint_path for character in "?#\\")
    ):
        raise ControlPlaneProvenanceError("Platform MCP path is malformed")
    if release.get("custom_domain_required") is not True or release.get("workers_dev") is not False:
        raise ControlPlaneProvenanceError("Platform MCP release does not require its canonical custom domain")

    origin = f"https://{hostname}"
    endpoint = origin + endpoint_path
    parsed_endpoint = urlsplit(endpoint)
    if (
        parsed_endpoint.scheme != "https"
        or parsed_endpoint.netloc != hostname
        or parsed_endpoint.path != endpoint_path
        or parsed_endpoint.query
        or parsed_endpoint.fragment
        or parsed_endpoint.username
        or parsed_endpoint.password
    ):
        raise ControlPlaneProvenanceError("Resolved Platform MCP endpoint is not a canonical HTTPS URL")
    configured_default = urlsplit(DEFAULT_PRODUCT_MCP_URL or "")
    if (
        configured_default.scheme != "https"
        or configured_default.netloc != hostname
        or configured_default.path not in ("", "/")
        or configured_default.query
        or configured_default.fragment
        or configured_default.username
        or configured_default.password
    ):
        raise ControlPlaneProvenanceError(
            "Runtime default Product MCP URL differs from the pinned Platform endpoint origin"
        )

    if environment == "validation-candidate":
        if release.get("validation_deployment_ready") is not True:
            raise ControlPlaneProvenanceError("Platform validation endpoint is not deployment-ready")
        connection_modes = foundation.get("connection_modes")
        direct_mcp = (
            connection_modes.get("direct_user_chatgpt_mcp")
            if isinstance(connection_modes, dict)
            else None
        )
        if not isinstance(direct_mcp, dict):
            raise ControlPlaneProvenanceError(
                "Pinned Platform foundation lacks direct MCP validation metadata"
            )
        validation_endpoint = _require_text(
            direct_mcp.get("current_validation_endpoint"),
            "Platform current validation endpoint",
        )
        if validation_endpoint != endpoint:
            raise ControlPlaneProvenanceError(
                "Platform validation endpoint does not match its pinned MCP hostname and path"
            )
    else:
        if foundation.get("deployment_ready") is not True or release.get("deployment_ready") is not True:
            raise ControlPlaneProvenanceError(
                "Production package is blocked: pinned Platform source is not deployment-ready"
            )

    return {
        "schema": PROVENANCE_SCHEMA,
        "environment": environment,
        "endpoint": endpoint,
        "origin": origin,
        "source": {
            "repository": CANONICAL_REPOSITORY,
            "path": CANONICAL_SOURCE_PATH,
            "commit": commit,
            "version": version,
        },
        "worker": {
            "name": _require_text(release.get("worker_name"), "Platform MCP worker name"),
            "hostname": hostname,
            "path": endpoint_path,
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lock", required=True, type=Path)
    parser.add_argument("--platform-root", required=True, type=Path)
    parser.add_argument("--environment", required=True, choices=sorted(ENVIRONMENTS))
    args = parser.parse_args(argv)
    try:
        result = resolve_control_plane_provenance(args.lock, args.platform_root, args.environment)
    except ControlPlaneProvenanceError as error:
        print(f"control_plane_provenance: {error}", file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
