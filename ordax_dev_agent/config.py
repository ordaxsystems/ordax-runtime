from __future__ import annotations

import json
import os
import socket
from dataclasses import dataclass
from pathlib import Path


DEFAULT_PRODUCT_MCP_URL = "https://mcp.ordax.com.br"


@dataclass(frozen=True, slots=True)
class AgentConfig:
    agent_name: str
    poll_seconds: float
    state_dir: Path
    agent_repo_path: Path
    hordax_path: Path
    bridge_path: Path
    projects: dict | None = None
    default_project: str = "hordax"
    adapters: tuple[str, ...] = ()
    control_plane_protocol: str = "cloudflare-v3"
    device_id: str | None = None
    product_mcp_url: str | None = DEFAULT_PRODUCT_MCP_URL
    workspace_root: Path | None = None
    # Trusted host-owned authority paths; never loaded from remote payloads.
    protected_roots: tuple[Path, ...] = ()
    # Off until the canonical MCP Worker wake DO rollout is validated.
    product_wake_enabled: bool = False

    @classmethod
    def from_env(cls) -> "AgentConfig":
        local_app_data = Path(
            os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")
        )
        state_dir = Path(
            os.environ.get(
                "ORDAX_AGENT_STATE_DIR",
                local_app_data / "OrdaX" / "DevAgent",
            )
        )
        state_dir.mkdir(parents=True, exist_ok=True)

        settings_path = state_dir / "agent-settings.json"
        settings: dict = {}
        if settings_path.is_file():
            try:
                settings = json.loads(settings_path.read_text(encoding="utf-8-sig"))
            except Exception as error:
                raise ValueError(
                    f"Cannot read agent settings: {settings_path}: {error}"
                ) from error
        if not isinstance(settings, dict):
            raise ValueError("Agent settings must be a JSON object")
        if not isinstance(settings.get("adapters", []), list):
            raise ValueError("adapters must be a list of locally installed adapter names")

        wake_setting = settings.get("product_wake_enabled", False)
        if not isinstance(wake_setting, bool):
            raise ValueError("product_wake_enabled must be a boolean")
        wake_override = os.environ.get("ORDAX_PRODUCT_WAKE_MODE")
        if wake_override is not None and wake_override not in {"enabled", "disabled"}:
            raise ValueError("ORDAX_PRODUCT_WAKE_MODE must be 'enabled' or 'disabled'")
        wake_enabled = (
            wake_setting if wake_override is None else wake_override == "enabled"
        )

        projects = settings.get("projects")
        default_project = settings.get("default_project", "hordax")
        if (
            isinstance(projects, dict)
            and len(projects) == 1
            and default_project not in projects
        ):
            default_project = next(iter(projects))

        protocol = os.environ.get(
            "ORDAX_CONTROL_PLANE_PROTOCOL",
            settings.get("control_plane_protocol", "cloudflare-v3"),
        )
        if str(protocol).strip().lower() != "cloudflare-v3":
            raise ValueError(f"Unsupported control-plane protocol: {protocol}")

        return cls(
            projects=projects,
            default_project=default_project,
            adapters=tuple(settings.get("adapters", [])),
            control_plane_protocol="cloudflare-v3",
            product_wake_enabled=wake_enabled,
            device_id=os.environ.get(
                "ORDAX_DEVICE_ID",
                settings.get("device_id"),
            ),
            product_mcp_url=os.environ.get(
                "ORDAX_PRODUCT_MCP_URL",
                settings.get("product_mcp_url") or DEFAULT_PRODUCT_MCP_URL,
            ),
            agent_name=os.environ.get(
                "ORDAX_AGENT_NAME",
                settings.get("agent_name") or socket.gethostname(),
            ),
            # Product MCP: the free Hyperdrive query budget must not be
            # exhausted by empty-queue polls. Keep the idle dispatch cadence
            # configurable, but never silently restore the legacy 1s ceiling.
            poll_seconds=min(
                60.0,
                max(
                    8.0,
                    float(
                        os.environ.get("ORDAX_AGENT_POLL_SECONDS")
                        or settings.get("poll_seconds", 12)
                    ),
                ),
            ),
            state_dir=state_dir,
            agent_repo_path=Path(
                os.environ.get(
                    "ORDAX_AGENT_REPO_PATH",
                    settings.get("agent_repo_path", state_dir / "src"),
                )
            ),
            hordax_path=Path(
                os.environ.get(
                    "ORDAX_HORDAX_PATH",
                    settings.get(
                        "hordax_path",
                        str(Path.home() / "Documents" / "github" / "HORDAX-game"),
                    ),
                )
            ),
            bridge_path=Path(
                os.environ.get(
                    "ORDAX_BRIDGE_PATH",
                    settings.get(
                        "bridge_path",
                        str(Path(__file__).resolve().parents[1]),
                    ),
                )
            ),
            workspace_root=Path(
                os.environ.get(
                    "ORDAX_WORKSPACE_ROOT",
                    settings.get(
                        "workspace_root",
                        str(
                            Path(
                                os.environ.get(
                                    "ORDAX_HORDAX_PATH",
                                    settings.get(
                                        "hordax_path",
                                        str(Path.home() / "Documents" / "github" / "HORDAX-game"),
                                    ),
                                )
                            ).expanduser().resolve().parent
                        ),
                    ),
                )
            ).expanduser().resolve(),
        )

    def public_status(self) -> dict:
        return {
            "agent_name": self.agent_name,
            "poll_seconds": self.poll_seconds,
            "state_dir": str(self.state_dir),
            "agent_repo_path": str(self.agent_repo_path),
            "hordax_path": str(self.hordax_path),
            "bridge_path": str(self.bridge_path),
            "workspace_root": str((self.workspace_root or self.hordax_path.parent).resolve()),
            "product_mcp_url_configured": bool(self.product_mcp_url),
            "product_mcp_url": self.product_mcp_url,
            "control_plane_protocol": self.control_plane_protocol,
            "product_wake_enabled": self.product_wake_enabled,
            "device_id_configured": bool(self.device_id),
            "device_id": self.device_id,
        }

    def write_public_status(self) -> None:
        target = self.state_dir / "agent-config.json"
        target.write_text(
            json.dumps(self.public_status(), indent=2),
            encoding="utf-8",
        )
