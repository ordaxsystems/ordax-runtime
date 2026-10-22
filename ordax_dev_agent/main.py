from __future__ import annotations

import json
import math
import os
from datetime import datetime
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Callable
from collections.abc import Mapping

from . import __version__
from .config import AgentConfig
from .status_server import start_status_server


_VERBOSE_JOB_LIFECYCLE_EVENTS = frozenset(
    {
        "agent.update",
        "agent.resilience_repair",
        "blender.benchmark",
        "blender.live_start",
        "blender.live_export",
        "blender.export_headless",
        "git.sync",
        "unity.hub_install_editor",
        "unity.direct_install_editor",
        "unity.recover_resume",
    }
)


def _startup_log(config: AgentConfig | None, message: str) -> None:
    try:
        if config is not None:
            state_dir = config.state_dir
        else:
            local_app_data = Path(
                os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")
            )
            state_dir = local_app_data / "OrdaX" / "DevAgent"
        state_dir.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        with (state_dir / "agent-startup.log").open("a", encoding="utf-8") as handle:
            handle.write(f"{stamp} {message}\n")
    except Exception:
        pass


def _transport_retry_delay(consecutive_failures: int) -> float:
    """Bounded exponential wait while remote transport is unavailable.

    Successful polls reset the streak; no background polling thread is needed.
    """
    if consecutive_failures < 1:
        raise ValueError("consecutive_failures must be positive")
    return min(30.0, 5.0 * (2 ** min(consecutive_failures - 1, 3)))


def product_idle_claim_delay_seconds(idle_misses: int, configured_ceiling: float) -> float:
    """Bounded idle polling that preserves the daily PostgreSQL query budget.

    A claim against an empty queue still consumes PostgreSQL queries. The
    previous 1s ceiling could exhaust the Hyperdrive Free allowance and
    disable all requests until the UTC reset. Burst promptly after activity,
    then settle; old 1s configs also observe the safe minimum.
    """
    if idle_misses < 1:
        raise ValueError("idle_misses must be positive")
    if not math.isfinite(configured_ceiling):
        raise ValueError("configured_ceiling must be finite")
    ceiling = min(60.0, max(8.0, configured_ceiling))
    return min(ceiling, 0.25 * (1.8 ** min(idle_misses - 1, 11)))


def _mark_transport_delivery_error(runtime: dict, error: Exception) -> None:
    summary = str(error)
    runtime["state"] = "control-plane-error"
    runtime["transport_state"] = "reconnecting"
    runtime["job_phase"] = "delivery-error"
    runtime["last_transport_error"] = {"summary": summary, "at": time.time()}
    runtime["last_result"] = {"ok": False, "summary": summary}


def _mark_transport_healthy(runtime: dict) -> None:
    previous = runtime.get("transport_state")
    runtime["transport_state"] = "connected"
    if previous == "reconnecting":
        runtime["last_transport_recovered_at"] = time.time()
        if runtime.get("job_phase") == "delivery-error":
            runtime["job_phase"] = "idle"
        runtime["last_result"] = {"ok": True, "summary": "control plane connection recovered"}



def _begin_job_lease(runtime: dict, job_id: str) -> None:
    """Expose current job fencing separately from generic connectivity.

    A successful heartbeat does not prove that the active job's lease renewed.
    Never expose lease IDs or credentials through the local status endpoint.
    """
    runtime["job_lease"] = {
        "request_id": job_id,
        "state": "started",
        "renewal_attempts": 0,
        "last_renewed_at": None,
        "lease_expires_at": None,
        "last_error_code": None,
        "last_error_at": None,
    }


def _record_job_lease_renewal(runtime: dict, reply: dict) -> None:
    lease = runtime.get("job_lease")
    if not isinstance(lease, dict):
        return
    lease["renewal_attempts"] += 1
    # Fencing loss is terminal for this execution. A later heartbeat or a
    # malformed/subsequent response must not overwrite that evidence.
    if lease["state"] in {"fencing_lost", "reported"}:
        return
    deadline = reply.get("lease_expires_at") if isinstance(reply, dict) else None
    try:
        date = datetime.fromisoformat(deadline.replace("Z", "+00:00"))
        valid_deadline = date.tzinfo is not None and date.utcoffset() is not None
    except (ValueError, AttributeError, TypeError):
        valid_deadline = False
    if not valid_deadline:
        lease["state"] = "unverified"
        lease["last_error_code"] = "lease_response_invalid"
        lease["last_error_at"] = time.time()
        return
    lease["state"] = "renewed"
    lease["last_renewed_at"] = time.time()
    lease["lease_expires_at"] = deadline
    lease["last_error_code"] = None


def _record_job_lease_error(runtime: dict, error: Exception) -> None:
    lease = runtime.get("job_lease")
    if not isinstance(lease, dict):
        return
    lease["renewal_attempts"] += 1
    from .remote_protocol import DeviceAuthorizationError, TransientDeliveryError
    if isinstance(error, DeviceAuthorizationError):
        state, code = "fencing_lost", "device_authorization_rejected"
    elif "lease_not_active" in str(error):
        state, code = "fencing_lost", "lease_not_active"
    elif isinstance(error, TransientDeliveryError):
        state, code = "unverified", "lease_transport_unavailable"
    else:
        state, code = "unverified", "lease_renewal_unexpected"
    if lease["state"] not in {"fencing_lost", "reported"}:
        lease["state"] = state
        lease["last_error_code"] = code
        lease["last_error_at"] = time.time()


def _end_job_lease(runtime: dict, *, report_committed: bool) -> None:
    lease = runtime.get("job_lease")
    if isinstance(lease, dict) and report_committed:
        lease["state"] = "reported"
        lease["lease_expires_at"] = None


def _agent_metadata(config: AgentConfig) -> dict:
    from .projects import load_projects

    return {
        "hordax_path": str(config.hordax_path),
        "bridge_path": str(config.bridge_path),
        "platform": sys.platform,
        "projects": [project.public() for project in load_projects(config).values()],
    }


def _start_local_watchdog(config: AgentConfig) -> subprocess.Popen | None:
    if sys.platform != "win32":
        return None

    repo_root = Path(__file__).resolve().parents[1]
    script = (
        repo_root
        / "scripts"
        / "windows"
        / "ordax-agent-watchdog.ps1"
    )
    if not script.is_file():
        print(f"local watchdog script missing: {script}", file=sys.stderr)
        return None

    command = [
        "powershell.exe",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(script),
        "-AgentPid",
        str(os.getpid()),
    ]
    try:
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        process = subprocess.Popen(
            command,
            cwd=str(repo_root),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            shell=False,
            creationflags=creationflags,
        )
        return process
    except Exception as error:
        print(f"could not start local watchdog: {error}", file=sys.stderr)
        return None


def _upload_result_artifacts(
    control,
    job,
    result,
    cache: dict | None = None,
) -> list[dict]:
    uploaded: list[dict] = []
    if cache is None:
        cache = {}

    candidates: list[tuple[str, str]] = []
    artifact = result.data.get("artifact")
    if isinstance(artifact, str) and artifact:
        suffix = Path(artifact).suffix.lower()
        if job.action == "blender.live_export" or suffix in {".glb", ".gltf", ".fbx"}:
            artifact_kind = "model-export"
        elif suffix == ".blend":
            artifact_kind = "blender-scene"
        elif suffix in {".png", ".jpg", ".jpeg", ".webp"}:
            artifact_kind = "visual-image"
        else:
            artifact_kind = "artifact"
        candidates.append((artifact, artifact_kind))

    snapshot_path = result.data.get("snapshot_path")
    if isinstance(snapshot_path, str) and snapshot_path:
        candidates.append((snapshot_path, "scene-snapshot"))

    for artifact in result.data.get("artifacts", []):
        if not isinstance(artifact, dict):
            continue
        raw_path = artifact.get("path") or artifact.get("artifact")
        if isinstance(raw_path, str) and raw_path:
            candidates.append((raw_path, artifact.get("kind", "artifact")))

    for key in ("log_file", "upm_log_file"):
        value = result.data.get(key)
        if isinstance(value, str) and value:
            candidates.append((value, "unity-log"))

    seen: set[str] = set()
    for raw_path, kind in candidates:
        path = Path(raw_path)
        key = str(path).lower()
        if key in seen or not path.is_file():
            continue
        seen.add(key)
        if key not in cache:
            cache[key] = control.upload_artifact(
                job,
                path,
                kind=kind,
                metadata={"action": job.action},
            )
        uploaded.append(cache[key])

    return uploaded


def main() -> int:
    _startup_log(None, f"MAIN_ENTER pid={os.getpid()} version={__version__}")
    return run(AgentConfig.from_env())


def _native_local_access_granted(provider: Callable[[], bool] | None) -> bool:
    """Recheck the OS-owned local MCP switch; unknown/error is always denial."""
    if provider is None:
        return True  # Windows uses its existing local Computer Access owner.
    try:
        return provider() is True
    except Exception:
        return False


def _wait_for_native_access(
    delay: float,
    stop_requested: threading.Event,
    native_access_enabled: Callable[[], bool] | None,
) -> bool:
    """Wait on the existing loop without delaying local revocation.

    Only the Native host polls its OS-owned local policy during idle/retry.
    Remote claim cadence is unchanged and no independent poller is created.
    """
    if native_access_enabled is None:
        return not stop_requested.wait(delay)
    deadline = time.monotonic() + max(0.0, delay)
    while not stop_requested.is_set():
        if not _native_local_access_granted(native_access_enabled):
            return False
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return True
        stop_requested.wait(min(1.0, remaining))
    return False


def run(
    config: AgentConfig,
    *,
    device_credential_provider: Callable[[], dict[str, str] | None] | None = None,
    computer_adapter: Mapping | None = None,
    status_port: int = 8765,
    orchestrator_installation_provider: Callable | None = None,
    native_access_enabled: Callable[[], bool] | None = None,
) -> int:
    """Run the canonical lifecycle with host-owned configuration and identity.

    Native hosts pass their existing identity-store reader directly. The CLI
    retains its Windows configuration path. Neither path creates another
    scheduler, grant authority, credential file or command transport.
    Call from the owning process's main thread (signal handlers are installed).
    """
    if type(status_port) is not int or not 0 <= status_port <= 65535:
        raise ValueError("Runtime status port is invalid")
    if orchestrator_installation_provider is not None and not callable(orchestrator_installation_provider):
        raise TypeError("Orchestrator installation provider must be host-owned and callable")
    if device_credential_provider is not None:
        if not callable(device_credential_provider):
            raise TypeError("Device credential provider must be callable")
        if not config.device_id or not config.product_mcp_url:
            raise ValueError("Native Runtime requires an enrolled Product device and origin")
        if str(config.control_plane_protocol).strip().lower() != "cloudflare-v3":
            raise ValueError("Native Runtime requires the canonical Product transport")
        # A native host must not advertise Windows-only handlers. An absent
        # adapter means no graphical capability, not a fallback to Win32.
        if computer_adapter is None:
            computer_adapter = {}
        if not callable(native_access_enabled):
            raise TypeError("Native Runtime requires the OS-owned local Connector policy")
        if not _native_local_access_granted(native_access_enabled):
            raise RuntimeError("native_local_connector_disabled")
    elif native_access_enabled is not None:
        raise TypeError("Native Connector policy requires a native device identity")
    started_monotonic = time.monotonic()
    _startup_log(config, f"RUN_ENTER pid={os.getpid()} version={__version__}")
    _startup_log(config, "CONFIG_READY")
    config.write_public_status()

    runtime = {
        "pid": os.getpid(),
        "supervisor_pid": int(os.environ.get("ORDAX_SUPERVISOR_PID", "0")),
        "state": "initializing",
        "paired": False,
        "last_job_id": None,
        "last_job_action": None,
        "last_result": None,
        "started_at": time.time(),
        "job_phase": "idle",
        "jobs_completed": 0,
        "transport_state": "not-configured",
        "last_transport_error": None,
        "last_transport_recovered_at": None,
    }
    registry = None

    def status_payload() -> dict:
        runtime["uptime_seconds"] = round(time.monotonic() - started_monotonic, 2)
        if registry is None:
            return {
                **config.public_status(),
                "agent_version": __version__,
                "actions": [],
                "projects": [],
                "runtime": dict(runtime),
            }
        base = registry.agent_status({}).data
        base["agent_version"] = __version__
        base["runtime"] = dict(runtime)
        return base

    def health_payload() -> dict:
        # A watchdog probe must not enumerate projects, integrations or actions.
        return {
            "agent_version": __version__,
            "runtime": {
                "state": runtime["state"],
                "last_job_id": runtime["last_job_id"],
            },
        }

    status_server = start_status_server(
        status_payload, port=status_port, health_provider=health_payload,
    )
    _startup_log(config, f"STATUS_SERVER_READY port={status_server.server_port}")

    from .actions import ActionRegistry
    _startup_log(config, "ACTION_REGISTRY_IMPORT_OK")
    registry = ActionRegistry(config, computer_adapter=computer_adapter)
    # Host composition owns App installation provenance; never advertise a
    # capability that this host cannot connect to an installed App.
    host_actions = sorted({*registry.names, *({"orchestrator.app_read"}
        if callable(orchestrator_installation_provider) else set())})
    _startup_log(config, f"ACTION_REGISTRY_READY actions={len(registry.names)}")

    watchdog_process = _start_local_watchdog(config)
    runtime["watchdog_pid"] = watchdog_process.pid if watchdog_process else None
    protocol = str(config.control_plane_protocol or "").strip().lower()
    remote_control_configured = bool(
        protocol == "cloudflare-v3"
        and config.product_mcp_url
        and config.device_id
    )
    runtime["state"] = "pairing" if remote_control_configured else "local-ready"
    runtime["transport_state"] = "connecting" if remote_control_configured else "not-configured"
    print(json.dumps(status_payload(), indent=2))

    stop = False
    stop_requested = threading.Event()

    def _stop(*_args) -> None:
        nonlocal stop
        stop = True
        stop_requested.set()

    signal.signal(signal.SIGINT, _stop)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _stop)

    if not remote_control_configured:
        print(
            "Remote control plane is not configured yet. Local action registry is ready; "
            "configure agent-settings.json/environment to enable the selected transport."
        )
        try:
            while not stop:
                time.sleep(1)
        finally:
            runtime["state"] = "stopping"
            status_server.shutdown()
        return 0

    from .control_plane import build_control_plane
    from .models import ActionResult
    _startup_log(config, "CONTROL_PLANE_IMPORT_OK")
    try:
        control = build_control_plane(
            config, device_credential_provider=device_credential_provider,
        )
    except Exception:
        status_server.shutdown()
        raise
    _startup_log(config, "CONTROL_PLANE_READY")


    recover_pending_reports = getattr(control, "recover_pending_reports", None)
    if callable(recover_pending_reports):
        from .remote_protocol import DeviceAuthorizationError

        while not stop:
            if not _native_local_access_granted(native_access_enabled):
                runtime["state"] = "local-disabled"
                status_server.shutdown()
                return 0
            try:
                runtime["state"] = "recovering-terminal-reports"
                recovered = int(recover_pending_reports())
                runtime["terminal_reports_recovered"] = recovered
                if recovered:
                    _startup_log(
                        config,
                        f"TERMINAL_REPORTS_RECOVERED count={recovered}",
                    )
                break
            except DeviceAuthorizationError:
                runtime["state"] = "credential-recovery-required"
                status_server.shutdown()
                return 43
            except Exception as error:
                runtime["state"] = "terminal-recovery-error"
                runtime["last_result"] = {"ok": False, "summary": str(error)}
                print(f"terminal report recovery error: {error}", file=sys.stderr)
                if not _wait_for_native_access(
                    max(5.0, config.poll_seconds), stop_requested, native_access_enabled,
                ):
                    continue

    while not stop and not runtime["paired"]:
        if not _native_local_access_granted(native_access_enabled):
            runtime["state"] = "local-disabled"
            status_server.shutdown()
            return 0
        try:
            paired_now = control.pair_if_needed(
                host_actions,
                agent_version=__version__,
                metadata=_agent_metadata(config),
            )
            runtime["paired"] = True
            runtime["state"] = "ready"
            runtime["transport_state"] = "connected"
            runtime["last_result"] = {
                "ok": True,
                "summary": "agent paired" if paired_now else "agent token loaded",
            }
            if paired_now:
                print("OrdaX Dev Agent paired with remote control plane.")
        except Exception as error:
            runtime["state"] = "pairing-error"
            runtime["last_result"] = {"ok": False, "summary": str(error)}
            print(f"pairing error: {error}", file=sys.stderr)
            if not _wait_for_native_access(
                max(5.0, config.poll_seconds), stop_requested, native_access_enabled,
            ):
                continue

    if stop:
        status_server.shutdown()
        return 0

    next_heartbeat = 0.0
    idle_claim_misses = 0
    transport_failures = 0

    try:
        while not stop:
            if not _native_local_access_granted(native_access_enabled):
                runtime["state"] = "local-disabled"
                break
            now = time.monotonic()
            try:
                if callable(recover_pending_reports):
                    recovered = int(recover_pending_reports())
                    if recovered:
                        runtime["terminal_reports_recovered"] = (
                            int(runtime.get("terminal_reports_recovered", 0)) + recovered
                        )
                        runtime["last_result"] = {
                            "ok": True,
                            "summary": f"recovered {recovered} pending terminal report(s)",
                        }

                if now >= next_heartbeat:
                    control.heartbeat(
                        host_actions,
                        agent_version=__version__,
                        metadata=_agent_metadata(config),
                    )
                    runtime["last_heartbeat_at"] = time.time()
                    _mark_transport_healthy(runtime)
                    runtime.setdefault("startup_seconds", round(time.monotonic() - started_monotonic, 3))
                    next_heartbeat = now + 20.0

                claim_started = time.monotonic()
                job = control.claim_next_job()
                transport_failures = 0
                runtime.pop("transport_retry_seconds", None)
                claim_seconds = time.monotonic() - claim_started
                if job is None:
                    idle_claim_misses += 1
                    # Stay very responsive during interactive work, then ease
                    # toward the configured ceiling while the agent is idle.
                    idle_delay = product_idle_claim_delay_seconds(
                        idle_claim_misses, config.poll_seconds,
                    )
                    # The WebSocket transport already waits for work; avoid an extra idle gap.
                    idle_wait = (0.10 if getattr(control, "uses_long_poll", False)
                                 and claim_seconds >= 1.0 else max(0.10, idle_delay))
                    _wait_for_native_access(idle_wait, stop_requested, native_access_enabled)
                    continue

                idle_claim_misses = 0
                runtime["state"] = "busy"
                runtime["last_job_id"] = job.id
                runtime["last_job_action"] = job.action
                job_started = time.monotonic()
                runtime["job_started_at"] = time.time()
                runtime["job_phase"] = "executing"
                runtime["job_timings"] = {}
                _begin_job_lease(runtime, job.id)
                runtime["progress"] = None
                verbose_lifecycle = job.action in _VERBOSE_JOB_LIFECYCLE_EVENTS
                if verbose_lifecycle:
                    control.append_event(job.id, "info", f"starting {job.action}")
                artifact_cache = {}

                def publish_observation(observation: dict) -> None:
                    progress = {"index": observation["index"], "ok": observation["ok"],
                                "observed_at": observation["observed_at"],
                                "duration_seconds": observation["duration_seconds"]}
                    runtime["progress"] = progress
                    try:
                        progress["artifacts"] = _upload_result_artifacts(
                            control, job, ActionResult(observation["ok"], observation["summary"], observation),
                            artifact_cache,
                        )
                        control.append_event(job.id, "info", "visual observation available", progress)
                    except Exception as error:
                        progress["delivery_error"] = str(error)
                        # Final upload retries missing artifacts without repeating app actions.
                    runtime["progress"] = progress

                registry.on_observation = publish_observation

                keepalive_stop = threading.Event()

                def _keep_job_alive(active_job=job, stop_event=keepalive_stop) -> None:
                    while not stop_event.wait(20.0):
                        try:
                            lease_response = control.renew(active_job)
                            _record_job_lease_renewal(runtime, lease_response)
                        except Exception as error:
                            _record_job_lease_error(runtime, error)
                            # The command may already have produced side effects.
                            # Never redispatch or pretend that a good heartbeat
                            # means the active job still owns its fence.
                            print(
                                f"job keepalive error for {active_job.id}: "
                                f"{runtime.get('job_lease', {}).get('last_error_code')}",
                                file=sys.stderr,
                            )
                        try:
                            control.heartbeat(host_actions, agent_version=__version__,
                                              status="busy")
                            runtime["last_heartbeat_at"] = time.time()
                        except Exception as error:
                            print(f"busy heartbeat error: {error}", file=sys.stderr)

                keepalive_thread = threading.Thread(
                    target=_keep_job_alive,
                    name=f"ordax-job-{job.id[:8]}-keepalive",
                    daemon=True,
                )
                keepalive_thread.start()

                try:
                    try:
                        from .product_remote import (
                            PRODUCT_REMOTE_CAPABILITY,
                            PRODUCT_REMOTE_LEGACY_CAPABILITY,
                        )
                        if not _native_local_access_granted(native_access_enabled):
                            # The local switch can be turned off after claim/start.
                            # Report denial, never execute under a stale grant.
                            result = ActionResult(False, "native_local_connector_disabled")
                        elif job.action == "orchestrator.app_read":
                            from .orchestrator_product_execution import execute_orchestrator_app_status
                            result = execute_orchestrator_app_status(control, job,
                                installation_provider=orchestrator_installation_provider)
                        elif job.action in {PRODUCT_REMOTE_CAPABILITY, PRODUCT_REMOTE_LEGACY_CAPABILITY}:
                            from .product_remote import execute_product_invocation
                            result = execute_product_invocation(
                                registry,
                                control,
                                job.action_payload(),
                            )
                        else:
                            result = registry.execute(job.action, job.action_payload())
                    except Exception as error:
                        result = ActionResult(False, f"{type(error).__name__}: {error}")
                    runtime["job_timings"]["execution_seconds"] = round(time.monotonic() - job_started, 3)
                    runtime["job_phase"] = "uploading"
                    upload_started = time.monotonic()
                    try:
                        uploaded = _upload_result_artifacts(control, job, result, artifact_cache)
                        if uploaded:
                            local_only = [item for item in uploaded if str(item.get("delivery", "")).startswith("local-only")]
                            delivered = [item for item in uploaded if item not in local_only]
                            if local_only:
                                result.data["local_artifacts"] = local_only
                                result.data["artifact_delivery"] = "local-only"
                            if delivered:
                                result.data["uploaded_artifacts"] = delivered
                    except Exception as error:
                        result.data["upload_error"] = str(error)
                        result.ok = False
                        result.summary += "; artifact upload failed"
                    runtime["job_timings"]["upload_seconds"] = round(time.monotonic() - upload_started, 3)
                    runtime["job_phase"] = "reporting"
                    report_started = time.monotonic()
                    result.data["agent_timings"] = dict(runtime["job_timings"])
                    if verbose_lifecycle or not result.ok:
                        try:
                            control.append_event(
                                job.id, "info" if result.ok else "error", result.summary,
                            )
                        except Exception as error:
                            # An optional progress event must not block the terminal result.
                            print(f"terminal progress event failed: {error}", file=sys.stderr)
                    control.complete(job, result)
                    _end_job_lease(runtime, report_committed=True)
                    runtime["job_timings"]["report_seconds"] = round(time.monotonic() - report_started, 3)
                finally:
                    registry.on_observation = None
                    keepalive_stop.set()
                    keepalive_thread.join(timeout=2.0)

                runtime["job_timings"]["total_seconds"] = round(time.monotonic() - job_started, 3)
                runtime["job_phase"] = "completed" if result.ok else "failed"
                runtime["jobs_completed"] += 1
                runtime["last_result"] = {
                    "ok": result.ok,
                    "summary": result.summary,
                }

                if result.ok and result.data.get("restart_required"):
                    runtime["state"] = "restarting"
                    return 42

                runtime["state"] = "ready"
            except Exception as error:
                from .remote_protocol import DeviceAuthorizationError
                if isinstance(error, DeviceAuthorizationError):
                    runtime["state"] = "credential-recovery-required"
                    return 43
                _mark_transport_delivery_error(runtime, error)
                transport_failures += 1
                retry_delay = _transport_retry_delay(transport_failures)
                runtime["transport_retry_seconds"] = retry_delay
                # Report the first failures and powers of two, not every
                # identical error through a prolonged backend outage.
                if transport_failures <= 2 or (transport_failures & (transport_failures - 1)) == 0:
                    print(f"control-plane error (retry in {retry_delay:.0f}s): {error}", file=sys.stderr)
                _wait_for_native_access(retry_delay, stop_requested, native_access_enabled)
                runtime["state"] = "stopping" if stop else "ready"
    finally:
        runtime["state"] = "stopping"
        try:
            control.heartbeat(
                host_actions,
                agent_version=__version__,
                metadata=_agent_metadata(config),
                status="offline",
            )
        except Exception:
            pass
        status_server.shutdown()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
