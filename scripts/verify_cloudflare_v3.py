from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

from ordax_dev_agent.cloudflare_control_plane import CloudflareControlPlane
from ordax_dev_agent.config import AgentConfig
from ordax_dev_agent.models import ActionResult


def _operator_headers(token: str) -> dict[str, str]:
    return {"authorization": f"Bearer {token}"}


def run(base_url: str, operator_token: str) -> None:
    base_url = base_url.rstrip("/")
    operator = httpx.Client(
        base_url=base_url,
        timeout=httpx.Timeout(20.0),
        headers=_operator_headers(operator_token),
    )
    control: CloudflareControlPlane | None = None
    restarted_control: CloudflareControlPlane | None = None
    device_id: str | None = None
    try:
        health = operator.get("/health")
        health.raise_for_status()
        health_payload = health.json()
        if health_payload.get("service") != "ordax-control-plane-v3":
            raise RuntimeError("unexpected control-plane health response")
        capabilities = health_payload.get("capabilities")
        if (
            not isinstance(capabilities, list)
            or "artifact_multipart_v1" not in capabilities
            or "product_grant_store_v1" not in capabilities
            or "product_grant_resolution_v1" not in capabilities
        ):
            raise RuntimeError("control-plane required capabilities are missing")

        protected_resource = operator.get("/.well-known/oauth-protected-resource")
        protected_resource.raise_for_status()
        resource_metadata = protected_resource.json()
        scopes = resource_metadata.get("scopes_supported")
        if not isinstance(scopes, list) or not {"openid", "email", "offline_access"}.issubset(scopes):
            raise RuntimeError("MCP protected-resource metadata is missing openid/email scopes")
        authorization_servers = resource_metadata.get("authorization_servers")
        if not isinstance(authorization_servers, list):
            raise RuntimeError("MCP protected-resource metadata has invalid authorization_servers")
        if health_payload.get("product_auth_configured") is True:
            if len(authorization_servers) != 1 or not isinstance(authorization_servers[0], str):
                raise RuntimeError("MCP protected-resource metadata has invalid authorization server")
            issuer = authorization_servers[0].rstrip("/")
            discovery = httpx.get(
                f"{issuer}/.well-known/openid-configuration",
                timeout=20.0,
                follow_redirects=False,
            )
            discovery.raise_for_status()
            oidc = discovery.json()
            if oidc.get("issuer") != issuer:
                raise RuntimeError("OIDC discovery issuer does not match protected resource")
            oidc_scopes = oidc.get("scopes_supported")
            if not isinstance(oidc_scopes, list) or not {"openid", "email"}.issubset(oidc_scopes):
                raise RuntimeError("OIDC provider is missing openid/email scopes")
            userinfo_endpoint = oidc.get("userinfo_endpoint")
            if not isinstance(userinfo_endpoint, str) or not userinfo_endpoint.startswith("https://"):
                raise RuntimeError("OIDC provider is missing HTTPS userinfo_endpoint")
            pkce_methods = oidc.get("code_challenge_methods_supported")
            if not isinstance(pkce_methods, list) or "S256" not in pkce_methods:
                raise RuntimeError("OIDC provider is missing PKCE S256 support")
        elif authorization_servers:
            raise RuntimeError("unconfigured local Product auth advertised an authorization server")

        provision = operator.post("/v3/devices", json={"name": "ci-e2e-device"})
        provision.raise_for_status()
        provisioned = provision.json()
        device_id = str(provisioned["device_id"])
        device_token = str(provisioned["device_token"])

        unauthorized_grant = httpx.post(
            f"{base_url}/v3/product-grants",
            json={
                "subject_id": "ci:user",
                "device_id": device_id,
                "actions": ["projects.list"],
                "projects": [],
            },
            timeout=20.0,
        )
        if unauthorized_grant.status_code != 401:
            raise RuntimeError("Product grant admin API accepted missing operator auth")

        grant_expiry = (
            datetime.now(timezone.utc) + timedelta(hours=1)
        ).isoformat()
        created_grant = operator.post(
            "/v3/product-grants",
            json={
                "subject_id": "ci:user",
                "space_id": "ci:space",
                "device_id": device_id,
                "actions": ["git.status", "projects.list", "workspace.repository_catalog"],
                "projects": ["scene"],
                "expires_at": grant_expiry,
            },
        )
        created_grant.raise_for_status()
        created_grant_payload = created_grant.json()
        grant = created_grant_payload.get("grant") or {}
        grant_id = str(grant.get("id") or "")
        if not grant_id or grant.get("subject_id") != "ci:user":
            raise RuntimeError("Product grant creation returned invalid provenance")
        if grant.get("actions") != [
            "git.status",
            "projects.list",
            "workspace.repository_catalog",
        ]:
            raise RuntimeError("Product grant actions were not canonicalized")
        if grant.get("projects") != ["scene"]:
            raise RuntimeError("Product grant projects were not persisted")

        unauthorized_resolution = httpx.post(
            f"{base_url}/v3/product-grants/resolve",
            json={
                "subject_id": "ci:user",
                "space_id": "ci:space",
                "device_id": device_id,
                "action": "git.status",
                "project": "scene",
            },
            timeout=20.0,
        )
        if unauthorized_resolution.status_code != 401:
            raise RuntimeError(
                "Product grant resolver accepted missing operator auth"
            )

        resolved_grant = operator.post(
            "/v3/product-grants/resolve",
            json={
                "subject_id": "ci:user",
                "space_id": "ci:space",
                "device_id": device_id,
                "action": "git.status",
                "project": "scene",
            },
        )
        resolved_grant.raise_for_status()
        if (resolved_grant.json().get("grant") or {}).get("id") != grant_id:
            raise RuntimeError("Product grant resolver returned the wrong grant")

        for discovery_action in (
            "projects.list",
            "workspace.repository_catalog",
        ):
            resolved_discovery = operator.post(
                "/v3/product-grants/resolve",
                json={
                    "subject_id": "ci:user",
                    "space_id": "ci:space",
                    "device_id": device_id,
                    "action": discovery_action,
                    "project": None,
                },
            )
            resolved_discovery.raise_for_status()
            discovery_grant = resolved_discovery.json().get("grant") or {}
            if discovery_grant.get("id") != grant_id:
                raise RuntimeError(
                    f"{discovery_action} did not resolve the project-bounded discovery grant"
                )
            if discovery_grant.get("projects") != ["scene"]:
                raise RuntimeError(
                    f"{discovery_action} lost the discovery project filter"
                )

        wrong_project = operator.post(
            "/v3/product-grants/resolve",
            json={
                "subject_id": "ci:user",
                "space_id": "ci:space",
                "device_id": device_id,
                "action": "git.status",
                "project": "other",
            },
        )
        if (
            wrong_project.status_code != 404
            or wrong_project.json().get("error") != "product_grant_not_resolved"
        ):
            raise RuntimeError("Product grant resolver crossed project scope")

        wrong_space = operator.post(
            "/v3/product-grants/resolve",
            json={
                "subject_id": "ci:user",
                "space_id": "ci:other-space",
                "device_id": device_id,
                "action": "git.status",
                "project": "scene",
            },
        )
        if (
            wrong_space.status_code != 404
            or wrong_space.json().get("error") != "product_grant_not_resolved"
        ):
            raise RuntimeError("Product grant resolver crossed Space scope")

        invalid_resolution_action = operator.post(
            "/v3/product-grants/resolve",
            json={
                "subject_id": "ci:user",
                "space_id": "ci:space",
                "device_id": device_id,
                "action": "git.sync",
                "project": "scene",
            },
        )
        if invalid_resolution_action.status_code != 400:
            raise RuntimeError(
                "Product grant resolver accepted a mutating action"
            )

        mutation_grant = operator.post(
            "/v3/product-grants",
            json={
                "subject_id": "ci:user",
                "device_id": device_id,
                "actions": ["git.sync"],
                "projects": ["scene"],
            },
        )
        if mutation_grant.status_code != 400:
            raise RuntimeError("Product grant store accepted mutating action")

        listed_grants = operator.get(
            "/v3/product-grants",
            params={"subject_id": "ci:user", "device_id": device_id},
        )
        listed_grants.raise_for_status()
        listed = listed_grants.json().get("grants") or []
        if not any(item.get("id") == grant_id for item in listed):
            raise RuntimeError("Product grant was not returned by filtered listing")

        revoked_grant = operator.delete(f"/v3/product-grants/{grant_id}")
        revoked_grant.raise_for_status()
        revoked_payload = revoked_grant.json()
        if revoked_payload.get("revoked") is not True:
            raise RuntimeError("Product grant revocation was not confirmed")
        if not (revoked_payload.get("grant") or {}).get("revoked_at"):
            raise RuntimeError("Product grant revocation timestamp was not persisted")

        revoked_resolution = operator.post(
            "/v3/product-grants/resolve",
            json={
                "subject_id": "ci:user",
                "space_id": "ci:space",
                "device_id": device_id,
                "action": "git.status",
                "project": "scene",
            },
        )
        if (
            revoked_resolution.status_code != 404
            or revoked_resolution.json().get("error")
            != "product_grant_not_resolved"
        ):
            raise RuntimeError("Revoked Product grant still resolved")

        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            state = root / "state"
            state.mkdir(parents=True)
            (state / "device-token.cloudflare-v3.txt").write_text(
                device_token + "\n",
                encoding="utf-8",
            )

            config = AgentConfig(
                agent_name="cloudflare-v3-ci",
                poll_seconds=1.0,
                state_dir=state,
                agent_repo_path=root / "agent",
                hordax_path=root / "hordax",
                bridge_path=root / "bridge",
                control_plane_protocol="cloudflare-v3",
                device_id=device_id,
                control_plane_url=base_url,
            )
            control = CloudflareControlPlane(config)

            control.heartbeat(
                ["agent.status"],
                agent_version="ci-e2e",
                metadata={"test": "cloudflare-v3"},
            )

            queued = operator.post(
                "/v3/jobs",
                json={
                    "device_id": device_id,
                    "action": "agent.status",
                    "payload": {"probe": True},
                },
            )
            queued.raise_for_status()
            job_id = str(queued.json()["job_id"])

            job = control.claim_next_job()
            if job is None:
                raise RuntimeError("device did not receive queued job")
            if job.id != job_id or job.action != "agent.status":
                raise RuntimeError("received job does not match queued job")
            if job.payload != {"probe": True}:
                raise RuntimeError("job payload changed in transport")

            control.append_event(
                job.id,
                "info",
                "cloudflare-v3 e2e progress",
                {"progress_percent": 50},
            )
            control.renew(job)

            blocked = operator.post(
                "/v3/jobs",
                json={
                    "device_id": device_id,
                    "action": "agent.status",
                    "payload": {"after_recovery": True},
                },
            )
            blocked.raise_for_status()
            blocked_job_id = str(blocked.json()["job_id"])

            artifact_path = root / "probe.txt"
            artifact_bytes = b"ordax-cloudflare-v3-e2e\n"
            artifact_path.write_bytes(artifact_bytes)
            artifact = control.upload_artifact(
                job,
                artifact_path,
                kind="ci-probe",
                metadata={"source": "verify_cloudflare_v3"},
            )
            signed_url = str(artifact.get("signed_url") or "")
            if not signed_url:
                raise RuntimeError("artifact upload did not return signed URL")

            downloaded = httpx.get(signed_url, timeout=20.0)
            downloaded.raise_for_status()
            if downloaded.content != artifact_bytes:
                raise RuntimeError("artifact round-trip changed bytes")

            bad_artifact_id = str(uuid.uuid4())
            bad_upload = httpx.put(
                f"{base_url}/v3/artifacts/{job.id}/{bad_artifact_id}",
                headers={
                    "X-Ordax-Device-Id": device_id,
                    "X-Ordax-Device-Token": device_token,
                    "X-Ordax-Artifact-Name": "bad-probe.txt",
                    "X-Ordax-Artifact-Kind": "ci-negative-integrity-probe",
                    "X-Ordax-Artifact-Sha256": "0" * 64,
                    "X-Ordax-Artifact-Size": str(len(artifact_bytes)),
                    "X-Ordax-Artifact-Metadata": "{}",
                    "content-type": "text/plain",
                },
                content=artifact_bytes,
                timeout=20.0,
            )
            if bad_upload.status_code != 422:
                raise RuntimeError(
                    "artifact checksum mismatch was not rejected: "
                    f"{bad_upload.status_code} {bad_upload.text}"
                )

            multipart_size = 6 * 1024 * 1024 + 123
            multipart_seed = b"ordax-cloudflare-multipart-e2e\n"
            multipart_bytes = (
                multipart_seed
                * ((multipart_size + len(multipart_seed) - 1) // len(multipart_seed))
            )[:multipart_size]
            multipart_path = root / "probe-multipart.bin"
            multipart_path.write_bytes(multipart_bytes)
            multipart_artifact_id = str(uuid.uuid4())
            multipart_digest = hashlib.sha256(multipart_bytes).hexdigest()
            multipart_artifact = control._upload_artifact_multipart(
                job,
                multipart_path,
                artifact_id=multipart_artifact_id,
                kind="ci-multipart-probe",
                metadata={"source": "verify_cloudflare_v3", "multipart": True},
                digest=multipart_digest,
                size=len(multipart_bytes),
                content_type="application/octet-stream",
            )
            if multipart_artifact.get("delivery") != "cloudflare-v3-multipart":
                raise RuntimeError("multipart artifact did not use multipart transport")
            multipart_signed_url = str(multipart_artifact.get("signed_url") or "")
            if not multipart_signed_url:
                raise RuntimeError("multipart artifact upload did not return signed URL")
            multipart_download = httpx.get(multipart_signed_url, timeout=30.0)
            multipart_download.raise_for_status()
            if multipart_download.content != multipart_bytes:
                raise RuntimeError("multipart artifact round-trip changed bytes")
            if hashlib.sha256(multipart_download.content).hexdigest() != multipart_digest:
                raise RuntimeError("multipart artifact round-trip changed digest")

            terminal_result = ActionResult(
                True,
                "cloudflare-v3 e2e complete",
                {"transport": "cloudflare-v3"},
            )
            result_body, result_digest = control._canonical_result(terminal_result)
            report = {
                **control._execution_context(job),
                "report_id": str(uuid.uuid4()),
                "status": "succeeded",
                "exit_code": 0,
                "result": result_body,
                "result_sha256": result_digest,
                "error_code": None,
            }

            outbox_path = control._terminal_outbox.persist(report)
            if not outbox_path.is_file():
                raise RuntimeError("terminal report was not persisted locally")

            # Simulate the Agent process disappearing after local execution but
            # before a terminal WebSocket report is accepted.
            control._drop_socket()
            restarted_control = CloudflareControlPlane(config)
            if restarted_control._socket is not None:
                raise RuntimeError("restart recovery unexpectedly opened a websocket")

            recovered_count = restarted_control.recover_pending_reports()
            if recovered_count != 1:
                raise RuntimeError(
                    f"expected one recovered terminal report, got {recovered_count}"
                )
            if restarted_control._terminal_outbox.pending():
                raise RuntimeError("terminal outbox was not cleared after recovery")
            if restarted_control._socket is not None:
                raise RuntimeError("terminal recovery must happen before websocket intake")

            next_job = restarted_control.claim_next_job()
            if next_job is None or next_job.id != blocked_job_id:
                raise RuntimeError(
                    "device queue did not resume with the job blocked behind running work"
                )
            if next_job.payload != {"after_recovery": True}:
                raise RuntimeError("post-recovery queued job payload changed")
            third = operator.post(
                "/v3/jobs",
                json={
                    "device_id": device_id,
                    "action": "agent.status",
                    "payload": {"after_live_recovery": True},
                },
            )
            third.raise_for_status()
            third_job_id = str(third.json()["job_id"])

            second_result = ActionResult(True, "live recovery completed", {})
            second_body, second_digest = restarted_control._canonical_result(second_result)
            second_report = {
                **restarted_control._execution_context(next_job),
                "report_id": str(uuid.uuid4()),
                "status": "succeeded",
                "exit_code": 0,
                "result": second_body,
                "result_sha256": second_digest,
                "error_code": None,
            }
            restarted_control._terminal_outbox.persist(second_report)
            if restarted_control.recover_pending_reports() != 1:
                raise RuntimeError("live terminal recovery did not clear one report")
            if next_job.id in restarted_control._jobs:
                raise RuntimeError("live terminal recovery left stale local job state")

            third_job = restarted_control.claim_next_job()
            if third_job is None or third_job.id != third_job_id:
                raise RuntimeError(
                    "terminal recovery did not wake the existing device websocket"
                )
            if third_job.payload != {"after_live_recovery": True}:
                raise RuntimeError("live-recovery queued job payload changed")
            restarted_control.complete(
                third_job,
                ActionResult(True, "queue wake verified", {}),
            )

            replay_report = restarted_control._recover_terminal_report(report)
            if replay_report.get("replayed") is not True:
                raise RuntimeError("identical recovered report replay was not acknowledged")

            conflict = dict(report)
            conflict["result_sha256"] = "0" * 64
            try:
                restarted_control._recover_terminal_report(conflict)
            except RuntimeError as error:
                if "terminal_report_conflict" not in str(error):
                    raise
            else:
                raise RuntimeError("conflicting recovered terminal report was accepted")

            control._jobs.pop(job.id, None)

            status = operator.get(f"/v3/jobs/{job_id}")
            status.raise_for_status()
            body = status.json()
            row = body.get("job") or {}
            if row.get("status") != "succeeded":
                raise RuntimeError(f"job did not finish successfully: {json.dumps(body)}")
            if row.get("report_id") != report["report_id"]:
                raise RuntimeError("terminal report id was not persisted")
            result = row.get("result") or {}
            if result.get("data", {}).get("transport") != "cloudflare-v3":
                raise RuntimeError("terminal result was not persisted")
            events = body.get("events") or []
            if not any(event.get("progress_percent") == 50 for event in events):
                raise RuntimeError("progress event was not persisted")
            artifacts = body.get("artifacts") or []
            if not any(item.get("id") == artifact.get("artifact_id") for item in artifacts):
                raise RuntimeError("artifact metadata was not persisted")
            if not any(
                item.get("id") == multipart_artifact.get("artifact_id")
                for item in artifacts
            ):
                raise RuntimeError("multipart artifact metadata was not persisted")
            if any(item.get("id") == bad_artifact_id for item in artifacts):
                raise RuntimeError("rejected artifact checksum was persisted")
    finally:
        active_error = sys.exc_info()[0] is not None
        if control is not None:
            control._drop_socket()
            control.http.close()
        if restarted_control is not None:
            restarted_control._drop_socket()
            restarted_control.http.close()

        cleanup_error: Exception | None = None
        if device_id:
            try:
                cleanup = operator.delete(f"/v3/devices/{device_id}")
                cleanup.raise_for_status()
                payload = cleanup.json()
                if payload.get("ok") is not True or payload.get("deleted") is not True:
                    raise RuntimeError("remote e2e cleanup was not confirmed")
            except Exception as error:
                cleanup_error = error
        operator.close()

        if cleanup_error is not None:
            message = (
                f"cloudflare-v3 e2e cleanup failed for device {device_id}: "
                f"{cleanup_error}"
            )
            if active_error:
                print(message, file=sys.stderr)
            else:
                raise RuntimeError(message) from cleanup_error


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--base-url",
        default=os.environ.get("ORDAX_E2E_CONTROL_PLANE_URL", "http://127.0.0.1:8787"),
    )
    parser.add_argument(
        "--operator-token",
        default=os.environ.get("ORDAX_E2E_OPERATOR_TOKEN"),
    )
    args = parser.parse_args()
    if not args.operator_token:
        parser.error("--operator-token or ORDAX_E2E_OPERATOR_TOKEN is required")

    run(args.base_url, args.operator_token)
    print("cloudflare-v3 end-to-end OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
