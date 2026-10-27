from __future__ import annotations

import json
import math
import re
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote, urlparse

import httpx


_TERMINAL_STATUSES = frozenset({"succeeded", "failed", "cancelled"})
_ACTION_STATUSES = _TERMINAL_STATUSES | {"queued", "leased", "running"}
_MAX_RESPONSE_BYTES = 4 * 1024 * 1024
_MAX_ACTION_POLL_INTERVAL_SECONDS = 2.0
_REQUEST_ID = re.compile(r"[A-Za-z0-9:_-]{1,128}")
_ERROR_CODE = re.compile(r"[a-z][a-z0-9_]{0,127}")
_UUID = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}",
    re.IGNORECASE,
)
_PROFILE_KEY = re.compile(r"[a-z][a-z0-9._-]{1,79}")
_GITHUB_REPOSITORY = re.compile(
    r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})/[A-Za-z0-9._-]{1,100}"
)


def _reject_duplicate_json_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Product response contains duplicate JSON object keys")
        result[key] = value
    return result


def _parse_finite_json_float(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed):
        raise ValueError("Product response contains a non-finite JSON number")
    return parsed


@dataclass
class ProductRemoteError(RuntimeError):
    error_code: str
    status_code: int
    detail: str
    request_id: str | None = None
    acceptance_unknown: bool = False

    def __str__(self) -> str:
        return f"{self.error_code} (HTTP {self.status_code}): {self.detail}"


class ProductActionWaitTimeout(TimeoutError):
    """The accepted action may still complete; query this id instead of resubmitting."""

    def __init__(self, request_id: str):
        self.request_id = request_id
        super().__init__(f"Product action did not finish: {request_id}")


class ProductRemoteClient:
    """Thin client for the canonical Product MCP HTTP authority.

    This client never stores a Product access token. Owner authorization uses
    only the canonical remote-authority / grant-group endpoints backed by
    PostgreSQL. Retired device-link, pairing and specialized grant routes are
    intentionally absent.
    """

    def __init__(
        self,
        base_url: str,
        *,
        http: httpx.Client | None = None,
        timeout_seconds: float = 20.0,
    ):
        normalized = base_url.rstrip("/")
        parsed = urlparse(normalized)
        if parsed.scheme != "https" or not parsed.netloc:
            raise ValueError("Product remote base_url must use HTTPS")
        if not math.isfinite(timeout_seconds) or timeout_seconds <= 0 or timeout_seconds > 120:
            raise ValueError("timeout_seconds must be between 0 and 120")
        self.base_url = normalized
        self.http = http or httpx.Client(timeout=timeout_seconds)
        self._owns_http = http is None

    def close(self) -> None:
        if self._owns_http:
            self.http.close()

    def __enter__(self) -> "ProductRemoteClient":
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    @staticmethod
    def _headers(access_token: str) -> dict[str, str]:
        if not isinstance(access_token, str) or not access_token.strip():
            raise ValueError("Product access token is required")
        if len(access_token) > 16_000:
            raise ValueError("Product access token is too large")
        return {
            "authorization": f"Bearer {access_token}",
            "accept": "application/json",
        }

    @staticmethod
    def _request_id(value: Any) -> str:
        if not isinstance(value, str) or _REQUEST_ID.fullmatch(value) is None:
            raise ValueError("Product request id must be a bounded path-safe identifier")
        return value

    @staticmethod
    def _uuid(value: Any, label: str) -> str:
        normalized = str(value or "").strip().lower()
        if _UUID.fullmatch(normalized) is None:
            raise ValueError(f"{label} must be a canonical UUID")
        return normalized

    @staticmethod
    def _client_id(value: Any) -> str:
        return ProductRemoteClient._uuid(value, "Product MCP client_id")

    def _request(
        self, method: str, url: str, *, deadline: float | None = None, **kwargs: Any,
    ) -> httpx.Response:
        try:
            with self.http.stream(method, url, follow_redirects=False, **kwargs) as response:
                content = bytearray()
                for chunk in response.iter_bytes():
                    if deadline is not None and time.monotonic() >= deadline:
                        raise ProductRemoteError(
                            "product_action_wait_timeout", 0,
                            "Product action completion was not confirmed before the wait deadline",
                        )
                    if len(content) + len(chunk) > _MAX_RESPONSE_BYTES:
                        raise ProductRemoteError(
                            "product_remote_invalid_response", response.status_code,
                            "Product response exceeds the configured response limit",
                        )
                    content.extend(chunk)
                if deadline is not None and time.monotonic() >= deadline:
                    raise ProductRemoteError(
                        "product_action_wait_timeout", 0,
                        "Product action wait deadline elapsed",
                    )
                return httpx.Response(
                    response.status_code,
                    headers={
                        key: value for key, value in response.headers.items()
                        if key not in {"content-encoding", "content-length"}
                    },
                    content=bytes(content),
                    request=response.request,
                )
        except httpx.HTTPError as error:
            if deadline is not None and time.monotonic() >= deadline:
                raise ProductRemoteError(
                    "product_action_wait_timeout",
                    0,
                    "Product action wait deadline elapsed during transport",
                ) from error
            raise ProductRemoteError(
                "product_remote_transport_error", 0,
                "Product MCP transport failed",
            ) from error

    @staticmethod
    def _body(response: httpx.Response) -> dict[str, Any]:
        def reject_nonfinite(_value: str) -> None:
            raise ValueError("Non-finite JSON number")

        try:
            media_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
            if media_type != "application/json":
                raise ValueError("Product response must use application/json")
            body = json.loads(
                response.content.decode("utf-8"),
                parse_constant=reject_nonfinite,
                parse_float=_parse_finite_json_float,
                object_pairs_hook=_reject_duplicate_json_keys,
            )
        except (ValueError, RecursionError) as error:
            raise ProductRemoteError(
                "product_remote_invalid_response",
                response.status_code,
                "Product MCP returned invalid JSON",
            ) from error
        if not isinstance(body, dict):
            raise ProductRemoteError(
                "product_remote_invalid_response",
                response.status_code,
                "Product MCP returned a non-object JSON body",
            )
        if not response.is_success or body.get("ok") is not True:
            code = body.get("error")
            raise ProductRemoteError(
                code if isinstance(code, str) and _ERROR_CODE.fullmatch(code)
                else "product_remote_error",
                response.status_code,
                "Product MCP rejected the request",
            )
        return body

    def session(self, access_token: str) -> dict[str, Any]:
        response = self._request(
            "GET",
            f"{self.base_url}/v3/product/session",
            headers=self._headers(access_token),
        )
        body = self._body(response)
        session = body.get("session")
        if not isinstance(session, dict):
            raise ProductRemoteError(
                "product_remote_invalid_response",
                response.status_code,
                "Product session payload is missing",
            )
        return session

    def targets(
        self,
        access_token: str,
        *,
        space_id: str | None = None,
    ) -> list[dict[str, Any]]:
        params = {"space_id": space_id} if space_id is not None else None
        response = self._request(
            "GET",
            f"{self.base_url}/v3/product/targets",
            headers=self._headers(access_token),
            params=params,
        )
        body = self._body(response)
        targets = body.get("targets")
        if not isinstance(targets, list) or not all(isinstance(item, dict) for item in targets):
            raise ProductRemoteError(
                "product_remote_invalid_response",
                response.status_code,
                "Product target catalog is invalid",
            )
        return [dict(item) for item in targets]

    def owner_remote_authority(
        self,
        access_token: str,
        *,
        device_id: str,
    ) -> dict[str, Any]:
        normalized_device = self._uuid(device_id, "device_id")
        response = self._request(
            "GET",
            f"{self.base_url}/v3/owner/remote-authority",
            headers=self._headers(access_token),
            params={"device_id": normalized_device},
        )
        body = self._body(response)
        if str(body.get("device_id") or "").lower() != normalized_device:
            raise ProductRemoteError(
                "product_remote_invalid_response",
                response.status_code,
                "Owner remote authority returned another device",
            )
        clients = body.get("clients")
        groups = body.get("grant_groups")
        if (
            not isinstance(clients, list)
            or not all(isinstance(item, dict) for item in clients)
            or not isinstance(groups, list)
            or not all(isinstance(item, dict) for item in groups)
        ):
            raise ProductRemoteError(
                "product_remote_invalid_response",
                response.status_code,
                "Owner remote authority payload is invalid",
            )
        return {
            "device_id": normalized_device,
            "clients": [dict(item) for item in clients],
            "grant_groups": [dict(item) for item in groups],
        }

    def owner_project_scope(
        self,
        access_token: str,
        *,
        device_id: str,
        repository_full_name: str,
    ) -> dict[str, Any]:
        """Resolve owner-authorized canonical project IDs for a registered repo.

        The caller must obtain ``repository_full_name`` from the Runtime's
        registered local Git project. This endpoint remains the authority for
        the corresponding Platform space/project UUIDs.
        """
        normalized_device = self._uuid(device_id, "device_id")
        repository = str(repository_full_name or "").strip()
        if _GITHUB_REPOSITORY.fullmatch(repository) is None:
            raise ValueError("repository_full_name must be owner/repository")
        response = self._request(
            "GET",
            f"{self.base_url}/v3/owner/project-scope",
            headers=self._headers(access_token),
            params={
                "device_id": normalized_device,
                "repository_full_name": repository,
            },
        )
        body = self._body(response)
        returned_repository = str(body.get("repository_full_name") or "").strip()
        if (
            not returned_repository
            or returned_repository.lower() != repository.lower()
            or type(body.get("repository_id")) is not int
            or body["repository_id"] <= 0
        ):
            raise ProductRemoteError(
                "product_remote_invalid_response",
                response.status_code,
                "Owner project scope does not match the requested repository",
            )
        try:
            space_id = self._uuid(body.get("space_id"), "space_id")
            project_id = self._uuid(body.get("project_id"), "project_id")
        except ValueError as error:
            raise ProductRemoteError(
                "product_remote_invalid_response",
                response.status_code,
                "Owner project scope contains invalid canonical identifiers",
            ) from error
        return {
            "space_id": space_id,
            "project_id": project_id,
            "repository_id": body["repository_id"],
            "repository_full_name": returned_repository,
        }

    def approve_owner_workspace_resource(
        self, access_token: str, *, intent_id: str, client_id: str,
        lab_project_id: str, space_id: str, project_id: str, device_id: str,
        actor_id: str, workspace_session_id: str, resource_id: str,
    ) -> bool:
        """Staged Owner approval only; never approval from model or MCP data.

        Call solely from an authenticated interactive host AFTER native
        confirmation, using the original pinned workspace selection. Even a
        true response is NOT a filesystem grant; enrolled Device confirmation
        and live PostgreSQL verification are separately required.
        """
        body = self._workspace_issuance_ids(
            intent_id=intent_id, actor_id=actor_id, client_id=client_id,
            workspace_session_id=workspace_session_id, resource_id=resource_id,
        )
        if (not isinstance(lab_project_id, str)
                or re.fullmatch(r"[A-Za-z0-9_-]{1,180}", lab_project_id) is None):
            raise ValueError("workspace_lab_project_invalid")
        body.update({
            "lab_project_id": lab_project_id,
            "space_id": self._uuid(space_id, "space_id"),
            "project_id": self._uuid(project_id, "project_id"),
            "device_id": self._uuid(device_id, "device_id"),
        })
        response = self._request(
            "POST", f"{self.base_url}/v3/owner/workspace-resources/approvals",
            headers=self._headers(access_token), json=body,
        )
        return self._workspace_stage_result(response, "approved")

    @staticmethod
    def _workspace_issuance_ids(
        *, intent_id: str, actor_id: str, client_id: str,
        workspace_session_id: str, resource_id: str,
    ) -> dict[str, str]:
        if (not isinstance(workspace_session_id, str)
                or re.fullmatch(r"[A-Za-z0-9_-]{1,180}", workspace_session_id) is None
                or not isinstance(resource_id, str)
                or re.fullmatch(r"resource_[A-Za-z0-9_-]{8,160}", resource_id) is None):
            raise ValueError("workspace_issuance_identity_invalid")
        return {
            "intent_id": ProductRemoteClient._uuid(intent_id, "intent_id"),
            "actor_id": ProductRemoteClient._uuid(actor_id, "actor_id"),
            "client_id": ProductRemoteClient._client_id(client_id),
            "workspace_session_id": workspace_session_id,
            "resource_id": resource_id,
        }

    @classmethod
    def _workspace_stage_result(cls, response: httpx.Response, field: str) -> bool:
        body = cls._body(response)
        if set(body) != {"ok", field} or body.get("ok") is not True or body.get(field) is not True:
            raise ProductRemoteError(
                "product_remote_invalid_response", response.status_code,
                "Product workspace stage response invalid",
            )
        return True

    def confirm_device_workspace_resource(
        self, *, device_id: str, device_token: str,
        intent_id: str, actor_id: str, client_id: str,
        workspace_session_id: str, resource_id: str,
    ) -> bool:
        """Second stage via existing enrolled Device transport, no grant cache.

        The trusted Runtime host MUST only call after a real native Owner
        picker confirmation and a successful authenticated Owner approval.
        No GUI attestation can be inferred by this HTTP helper itself.
        """
        canonical_device = self._uuid(device_id, "device_id")
        if (not isinstance(device_token, str)
                or not 32 <= len(device_token) <= 512
                or any(ord(ch) <= 0x20 or ord(ch) == 0x7f for ch in device_token)):
            raise ValueError("device_workspace_confirmation_trusted_token_required")
        body = self._workspace_issuance_ids(
            intent_id=intent_id, actor_id=actor_id, client_id=client_id,
            workspace_session_id=workspace_session_id, resource_id=resource_id,
        )
        response = self._request(
            "POST", f"{self.base_url}/v3/device/transport/workspace-confirm",
            headers={
                "x-ordax-device-id": canonical_device,
                "x-ordax-device-token": device_token,
                "accept": "application/json",
            },
            json=body,
        )
        return self._workspace_stage_result(response, "committed")

    def device_workspace_proof(
        self,
        *,
        device_id: str,
        device_token: str,
        owner_user_id: str,
        actor_id: str,
        client_id: str,
        lab_project_id: str,
        workspace_session_id: str,
        resource_id: str,
    ) -> dict[str, Any]:
        """Read ONE fresh workspace-resource proof via canonical Product Device transport.

        Must only be called in the trusted Runtime Device host, using its
        existing enrolled device credential, never the Owner OAuth token.
        The owner/client/actor/resource fields are lookup keys, not self-
        asserted permission: the canonical SQL verifier checks them all.
        There is no cache, path parameter, grant minting or MCP action.
        """
        canonical_device = self._uuid(device_id, "device_id")
        owner = self._uuid(owner_user_id, "owner_user_id")
        actor = self._uuid(actor_id, "actor_id")
        client = self._client_id(client_id)
        if (not isinstance(device_token, str)
                or not 32 <= len(device_token) <= 512
                or any(ord(ch) <= 0x20 or ord(ch) == 0x7f for ch in device_token)):
            raise ValueError("device_workspace_proof_trusted_device_token_required")
        if (not isinstance(lab_project_id, str)
                or not re.fullmatch(r"[A-Za-z0-9_-]{1,180}", lab_project_id)
                or not isinstance(workspace_session_id, str)
                or not re.fullmatch(r"[A-Za-z0-9_-]{1,180}", workspace_session_id)
                or not isinstance(resource_id, str)
                or not re.fullmatch(r"resource_[A-Za-z0-9_-]{8,160}", resource_id)):
            raise ValueError("device_workspace_proof_identity_invalid")
        response = self._request(
            "POST", f"{self.base_url}/v3/device/transport/workspace-proof",
            headers={
                "x-ordax-device-id": canonical_device,
                "x-ordax-device-token": device_token,
                "accept": "application/json",
            },
            json={
                "owner_user_id": owner,
                "actor_id": actor,
                "client_id": client,
                "lab_project_id": lab_project_id,
                "workspace_session_id": workspace_session_id,
                "resource_id": resource_id,
            },
        )
        return self._body(response)

    def owner_central_session_targets(
        self, access_token: str, *, client_id: str,
    ) -> dict[str, Any]:
        """Read-only canonical eligible devices; never an attestation or grant."""
        oauth_client = self._client_id(client_id)
        response = self._request(
            "GET",
            f"{self.base_url}/v3/owner/central-session-targets",
            headers=self._headers(access_token),
            params={"client_id": oauth_client},
        )
        body = self._body(response)
        if body.get("client_id") != oauth_client:
            raise ProductRemoteError(
                "product_remote_invalid_response", response.status_code,
                "Owner target preflight returned a different OAuth client",
            )
        count = body.get("eligible_device_count")
        targets = body.get("targets")
        if (
            type(count) is not int or count < 0 or count > 25
            or not isinstance(targets, list) or len(targets) != count
            or not all(isinstance(item, dict) and isinstance(item.get("device_id"), str)
                       and _UUID.fullmatch(item["device_id"]) is not None
                       for item in targets)
            or body.get("attested") is not False
            or body.get("dispatch_authorized") is not False
            or body.get("owner_choice_performed") is not False
        ):
            raise ProductRemoteError(
                "product_remote_invalid_response", response.status_code,
                "Owner target preflight is not a read-only eligibility projection",
            )
        return body

    def owner_central_session_selections(
        self, access_token: str, *, client_id: str,
    ) -> dict[str, Any]:
        """Owner-only pending tickets, never exposing provider session hashes."""
        oauth_client = self._client_id(client_id)
        response = self._request(
            "GET",
            f"{self.base_url}/v3/owner/central-session-selections",
            headers=self._headers(access_token),
            params={"client_id": oauth_client},
        )
        body = self._body(response)
        selections = body.get("pending_selections")
        count = body.get("count")
        if (
            not isinstance(selections, list) or type(count) is not int
            or count < 0 or count > 25 or len(selections) != count
            or body.get("attested") is not False
            or body.get("dispatch_authorized") is not False
        ):
            raise ProductRemoteError(
                "product_remote_invalid_response", response.status_code,
                "Owner pending selection response is invalid",
            )
        for item in selections:
            if not isinstance(item, dict) or set(item) != {
                "challenge_id", "created_at", "expires_at",
            }:
                raise ProductRemoteError(
                    "product_remote_invalid_response", response.status_code,
                    "Owner selection ticket has unexpected fields",
                )
            try:
                self._uuid(item["challenge_id"], "challenge_id")
            except ValueError as error:
                raise ProductRemoteError(
                    "product_remote_invalid_response", response.status_code,
                    "Owner selection ticket is invalid",
                ) from error
        return body

    def owner_select_central_device(
        self, access_token: str, *, client_id: str,
        challenge_id: str, device_id: str,
    ) -> dict[str, Any]:
        """Only an explicit Owner action selects the already-granted target.

        Selection does not attest a session; the exact Native Device must
        still confirm its challenge with its credential.
        """
        oauth_client = self._client_id(client_id)
        ticket = self._uuid(challenge_id, "challenge_id")
        target = self._uuid(device_id, "device_id")
        response = self._request(
            "POST",
            f"{self.base_url}/v3/owner/central-session-selections",
            headers={**self._headers(access_token), "content-type": "application/json"},
            json={"client_id": oauth_client, "challenge_id": ticket,
                  "device_id": target},
        )
        body = self._body(response)
        if (
            body.get("status") != "pending"
            or body.get("challenge_id") != ticket
            or body.get("device_id") != target
            or body.get("attested") is not False
            or body.get("dispatch_authorized") is not False
        ):
            raise ProductRemoteError(
                "product_remote_invalid_response", response.status_code,
                "Owner device selection returned inconsistent challenge state",
            )
        return body

    def replace_owner_remote_grant_group(
        self,
        access_token: str,
        *,
        client_id: str,
        device_id: str,
        profile_key: str,
        space_id: str | None = None,
        project_id: str | None = None,
        valid_until: str | None = None,
    ) -> dict[str, Any]:
        normalized_client = self._client_id(client_id)
        normalized_device = self._uuid(device_id, "device_id")
        normalized_profile = str(profile_key or "").strip()
        if _PROFILE_KEY.fullmatch(normalized_profile) is None:
            raise ValueError("profile_key is invalid")
        payload: dict[str, Any] = {
            "client_id": normalized_client,
            "device_id": normalized_device,
            "profile_key": normalized_profile,
            "space_id": self._uuid(space_id, "space_id") if space_id else None,
            "project_id": self._uuid(project_id, "project_id") if project_id else None,
            "valid_until": valid_until,
        }
        response = self._request(
            "POST",
            f"{self.base_url}/v3/owner/remote-grant-groups",
            headers={
                **self._headers(access_token),
                "content-type": "application/json",
            },
            json=payload,
        )
        return self._body(response)

    def revoke_owner_remote_grant_group(
        self,
        access_token: str,
        *,
        client_id: str,
        grant_group_id: str,
    ) -> dict[str, Any]:
        normalized_client = self._client_id(client_id)
        normalized_group = self._uuid(grant_group_id, "grant_group_id")
        response = self._request(
            "DELETE",
            f"{self.base_url}/v3/owner/remote-grant-groups/{quote(normalized_group, safe='')}",
            headers=self._headers(access_token),
            params={"client_id": normalized_client},
        )
        return self._body(response)

    def submit_action(
        self,
        access_token: str,
        *,
        device_id: str,
        action: str,
        arguments: dict[str, Any] | None = None,
        project: str | None = None,
        space_id: str | None = None,
    ) -> str:
        payload: dict[str, Any] = {
            "device_id": device_id,
            "action": action,
            "arguments": dict(arguments or {}),
        }
        if project is not None:
            payload["project"] = project
        if space_id is not None:
            payload["space_id"] = space_id

        try:
            response = self._request(
                "POST",
                f"{self.base_url}/v3/product/actions",
                headers={
                    **self._headers(access_token),
                    "content-type": "application/json",
                },
                json=payload,
            )
            body = self._body(response)
            try:
                return self._request_id(body.get("request_id"))
            except ValueError as error:
                raise ProductRemoteError(
                    "product_remote_invalid_response",
                    response.status_code,
                    "Product action request id is missing or invalid",
                ) from error
        except ProductRemoteError as error:
            raise ProductRemoteError(
                error.error_code,
                error.status_code,
                error.detail,
                acceptance_unknown=(
                    error.status_code in {0, 408}
                    or error.status_code >= 500
                    or error.error_code == "product_remote_invalid_response"
                ),
            ) from error

    def action(
        self,
        access_token: str,
        request_id: str,
        *,
        timeout_seconds: float | None = None,
        _deadline: float | None = None,
    ) -> dict[str, Any]:
        self._request_id(request_id)
        if timeout_seconds is not None and (
            not math.isfinite(timeout_seconds) or timeout_seconds <= 0
        ):
            raise ValueError("Action request timeout must be finite and positive")
        options = {"timeout": timeout_seconds} if timeout_seconds is not None else {}
        try:
            response = self._request(
                "GET",
                f"{self.base_url}/v3/product/actions/{request_id}",
                headers=self._headers(access_token),
                deadline=_deadline,
                **options,
            )
            body = self._body(response)
            action = body.get("action")
            if (
                not isinstance(action, dict)
                or action.get("request_id") != request_id
                or not isinstance(action.get("status"), str)
                or action["status"] not in _ACTION_STATUSES
            ):
                raise ProductRemoteError(
                    "product_remote_invalid_response",
                    response.status_code,
                    "Product action identity or status is invalid",
                )
            if _deadline is not None and time.monotonic() >= _deadline:
                raise ProductRemoteError(
                    "product_action_wait_timeout",
                    0,
                    "Product action status validation exceeded the wait deadline",
                )
            return action
        except ProductRemoteError as error:
            raise ProductRemoteError(
                error.error_code,
                error.status_code,
                error.detail,
                request_id=request_id,
            ) from error

    def wait_action(
        self,
        access_token: str,
        request_id: str,
        *,
        timeout_seconds: float = 30.0,
        poll_interval_seconds: float = 0.5,
    ) -> dict[str, Any]:
        if (
            not math.isfinite(timeout_seconds)
            or timeout_seconds <= 0
            or timeout_seconds > 300
        ):
            raise ValueError("timeout_seconds must be between 0 and 300")
        if (
            not math.isfinite(poll_interval_seconds)
            or poll_interval_seconds < 0.1
            or poll_interval_seconds > 5
        ):
            raise ValueError("poll_interval_seconds must be between 0.1 and 5")

        deadline = time.monotonic() + timeout_seconds
        poll_interval = poll_interval_seconds
        poll_interval_cap = max(
            poll_interval_seconds, _MAX_ACTION_POLL_INTERVAL_SECONDS,
        )
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ProductActionWaitTimeout(request_id)
            try:
                result = self.action(
                    access_token,
                    request_id,
                    timeout_seconds=remaining,
                    _deadline=deadline,
                )
            except ProductRemoteError as error:
                if error.error_code == "product_action_wait_timeout":
                    raise ProductActionWaitTimeout(request_id) from error
                raise
            if result["status"] in _TERMINAL_STATUSES:
                return result
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ProductActionWaitTimeout(request_id)
            time.sleep(min(poll_interval, remaining))
            poll_interval = min(poll_interval * 2, poll_interval_cap)
