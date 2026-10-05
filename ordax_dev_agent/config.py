from __future__ import annotations

import json
import os
import socket
from dataclasses import dataclass
from pathlib import Path


DEFAULT_CONTROL_PLANE_URL = (
    "https://ordax-control-plane-v3.ordax-ac1ca1b50d09.workers.dev"
)


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
    control_plane_url: str | None = DEFAULT_CONTROL_PLANE_URL
    workspace_root: Path | None = None

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
            device_id=os.environ.get(
                "ORDAX_DEVICE_ID",
                settings.get("device_id"),
            ),
            control_plane_url=os.environ.get(
                "ORDAX_CONTROL_PLANE_URL",
                settings.get("control_plane_url") or DEFAULT_CONTROL_PLANE_URL,
            ),
            agent_name=os.environ.get(
                "ORDAX_AGENT_NAME",
                settings.get("agent_name") or socket.gethostname(),
            ),
            poll_seconds=min(
                1.0,
                max(
                    0.25,
                    float(
                        os.environ.get(
                            "ORDAX_AGENT_POLL_SECONDS",
                            settings.get("poll_seconds", 1),
                        )
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
            "control_plane_url_configured": bool(self.control_plane_url),
            "control_plane_url": self.control_plane_url,
            "control_plane_protocol": self.control_plane_protocol,
            "device_id_configured": bool(self.device_id),
            "device_id": self.device_id,
        }

    def write_public_status(self) -> None:
        target = self.state_dir / "agent-config.json"
        target.write_text(
            json.dumps(self.public_status(), indent=2),
            encoding="utf-8",
        )
