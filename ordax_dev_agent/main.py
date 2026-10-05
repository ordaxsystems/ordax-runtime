from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

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
    started_monotonic = time.monotonic()
    _startup_log(None, f"MAIN_ENTER pid={os.getpid()} version={__version__}")
    config = AgentConfig.from_env()
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

    status_server = start_status_server(status_payload)
    _startup_log(config, "STATUS_SERVER_READY port=8765")

    from .actions import ActionRegistry
    _startup_log(config, "ACTION_REGISTRY_IMPORT_OK")
    registry = ActionRegistry(config)
    _startup_log(config, f"ACTION_REGISTRY_READY actions={len(registry.names)}")

    watchdog_process = _start_local_watchdog(config)
    runtime["watchdog_pid"] = watchdog_process.pid if watchdog_process else None
    protocol = str(config.control_plane_protocol or "").strip().lower()
    remote_control_configured = bool(
        protocol == "cloudflare-v3"
        and config.control_plane_url
        and config.device_id
    )
    runtime["state"] = "pairing" if remote_control_configured else "local-ready"
    runtime["transport_state"] = "connecting" if remote_control_configured else "not-configured"
    print(json.dumps(status_payload(), indent=2))

    stop = False

    def _stop(*_args) -> None:
        nonlocal stop
        stop = True

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
    control = build_control_plane(config)
    _startup_log(config, "CONTROL_PLANE_READY")


    recover_pending_reports = getattr(control, "recover_pending_reports", None)
    if callable(recover_pending_reports):
        from .remote_protocol import DeviceAuthorizationError

        while not stop:
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
                time.sleep(max(5.0, config.poll_seconds))

    while not stop and not runtime["paired"]:
        try:
            paired_now = control.pair_if_needed(
                registry.names,
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
            time.sleep(max(5.0, config.poll_seconds))

    if stop:
        status_server.shutdown()
        return 0

    next_heartbeat = 0.0
    idle_claim_misses = 0

    try:
        while not stop:
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
                        registry.names,
                        agent_version=__version__,
                        metadata=_agent_metadata(config),
                    )
                    runtime["last_heartbeat_at"] = time.time()
                    _mark_transport_healthy(runtime)
                    runtime.setdefault("startup_seconds", round(time.monotonic() - started_monotonic, 3))
                    next_heartbeat = now + 20.0

                claim_started = time.monotonic()
                job = control.claim_next_job()
                claim_seconds = time.monotonic() - claim_started
                if job is None:
                    idle_claim_misses += 1
                    # Stay very responsive during interactive work, then ease
                    # toward the configured ceiling while the agent is idle.
                    idle_delay = min(
                        config.poll_seconds,
                        0.20 * (1.55 ** min(idle_claim_misses - 1, 5)),
                    )
                    # The WebSocket transport already waits for work; avoid an extra idle gap.
                    time.sleep(0.10 if getattr(control, "uses_long_poll", False) and claim_seconds >= 1.0
                               else max(0.10, idle_delay))
                    continue

                idle_claim_misses = 0
                runtime["state"] = "busy"
                runtime["last_job_id"] = job.id
                runtime["last_job_action"] = job.action
                job_started = time.monotonic()
                runtime["job_started_at"] = time.time()
                runtime["job_phase"] = "executing"
                runtime["job_timings"] = {}
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
                            control.renew(active_job)
                        except Exception as error:
                            print(
                                f"job keepalive error for {active_job.id}: {error}",
                                file=sys.stderr,
                            )
                        try:
                            control.heartbeat(registry.names, agent_version=__version__,
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
                        if job.action in {PRODUCT_REMOTE_CAPABILITY, PRODUCT_REMOTE_LEGACY_CAPABILITY}:
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
                print(f"control-plane error: {error}", file=sys.stderr)
                time.sleep(max(5.0, config.poll_seconds))
                runtime["state"] = "ready"
    finally:
        runtime["state"] = "stopping"
        try:
            control.heartbeat(
                registry.names,
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
