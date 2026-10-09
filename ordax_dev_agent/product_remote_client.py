from __future__ import annotations

import json
import math
import re
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import httpx


_TERMINAL_STATUSES = frozenset({"succeeded", "failed", "cancelled"})
_ACTION_STATUSES = _TERMINAL_STATUSES | {"queued", "leased", "running"}
# Includes the published 2 MiB inline artifact after base64 expansion and metadata.
_MAX_RESPONSE_BYTES = 4 * 1024 * 1024
_REQUEST_ID = re.compile(r"[A-Za-z0-9:_-]{1,128}")
_ERROR_CODE = re.compile(r"[a-z][a-z0-9_]{0,127}")


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
    """Thin Product client over the authenticated Cloudflare v3 API.

    The client intentionally does not store a Product access token. Every call
    receives the current token explicitly so a future MCP/Web host can own token
    refresh and session lifecycle without duplicating authorization rules here.
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

    def _request(
        self, method: str, url: str, *, deadline: float | None = None, **kwargs: Any,
    ) -> httpx.Response:
        # The bound applies while reading, including decompressed response bytes.
        # Never follow a redirect with a Product bearer token, even when the
        # caller injects a Client configured to follow redirects.
        try:
            with self.http.stream(method, url, follow_redirects=False, **kwargs) as response:
                content = bytearray()
                # Do not coalesce chunks: a slow stream must not postpone the
                # wait deadline until a large buffer becomes full.
                for chunk in response.iter_bytes():
                    if deadline is not None and time.monotonic() >= deadline:
                        raise ProductRemoteError(
                            "product_action_wait_timeout", 0,
                            "Product action completion was not confirmed before the wait deadline",
                        )
                    if len(content) + len(chunk) > _MAX_RESPONSE_BYTES:
                        raise ProductRemoteError(
                            "product_remote_invalid_response", response.status_code,
                            "Control Plane response exceeds the Product response limit",
                        )
                    content.extend(chunk)
                if deadline is not None and time.monotonic() >= deadline:
                    raise ProductRemoteError("product_action_wait_timeout", 0,
                                             "Product action wait deadline elapsed")
                return httpx.Response(
                    response.status_code,
                    headers={key: value for key, value in response.headers.items()
                             if key not in {"content-encoding", "content-length"}},
                    content=bytes(content), request=response.request,
                )
        except httpx.HTTPError as error:
            raise ProductRemoteError(
                "product_remote_transport_error", 0,
                "Control Plane transport failed",
            ) from error

    @staticmethod
    def _body(response: httpx.Response) -> dict[str, Any]:
        def reject_nonfinite(_value: str) -> None:
            raise ValueError("Non-finite JSON number")

        try:
            if response.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
                raise ValueError("Product response must use application/json")
            body = json.loads(response.content.decode("utf-8"), parse_constant=reject_nonfinite)
        except (ValueError, RecursionError) as error:
            raise ProductRemoteError(
                "product_remote_invalid_response",
                response.status_code,
                "Control Plane returned non-JSON data",
            ) from error
        if not isinstance(body, dict):
            raise ProductRemoteError(
                "product_remote_invalid_response",
                response.status_code,
                "Control Plane returned a non-object JSON body",
            )
        if not response.is_success or body.get("ok") is not True:
            code = body.get("error")
            raise ProductRemoteError(
                code if isinstance(code, str) and _ERROR_CODE.fullmatch(code) else "product_remote_error",
                response.status_code,
                "Control Plane rejected the Product request",
            )
        return body

    def session(self, access_token: str) -> dict[str, Any]:
        response = self._request("GET",
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
        response = self._request("GET",
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

    def claim_device_pairing(
        self,
        access_token: str,
        *,
        pairing_id: str,
        pairing_secret: str,
        space_id: str | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "pairing_id": pairing_id,
            "pairing_secret": pairing_secret,
        }
        if space_id is not None:
            payload["space_id"] = space_id
        response = self._request("POST",
            f"{self.base_url}/v3/product/device-links",
            headers={
                **self._headers(access_token),
                "content-type": "application/json",
            },
            json=payload,
        )
        body = self._body(response)
        link = body.get("link")
        if not isinstance(link, dict):
            raise ProductRemoteError(
                "product_remote_invalid_response",
                response.status_code,
                "Product device link payload is missing",
            )
        return link

    def device_links(
        self,
        access_token: str,
        *,
        space_id: str | None = None,
    ) -> list[dict[str, Any]]:
        params = {"space_id": space_id} if space_id is not None else None
        response = self._request("GET",
            f"{self.base_url}/v3/product/device-links",
            headers=self._headers(access_token),
            params=params,
        )
        body = self._body(response)
        links = body.get("links")
        if not isinstance(links, list) or not all(isinstance(item, dict) for item in links):
            raise ProductRemoteError(
                "product_remote_invalid_response",
                response.status_code,
                "Product device link catalog is invalid",
            )
        return [dict(item) for item in links]

    def revoke_device_link(
        self,
        access_token: str,
        link_id: str,
    ) -> dict[str, Any]:
        response = self._request("DELETE",
            f"{self.base_url}/v3/product/device-links/{link_id}",
            headers=self._headers(access_token),
        )
        return self._body(response)

    def device_computer_grants(
        self,
        access_token: str,
        *,
        link_id: str | None = None,
    ) -> list[dict[str, Any]]:
        params = {"link_id": link_id} if link_id is not None else None
        response = self._request("GET",
            f"{self.base_url}/v3/product/device-computer-grants",
            headers=self._headers(access_token),
            params=params,
        )
        body = self._body(response)
        grants = body.get("grants")
        if not isinstance(grants, list) or not all(isinstance(item, dict) for item in grants):
            raise ProductRemoteError(
                "product_remote_invalid_response",
                response.status_code,
                "Product device grant catalog is invalid",
            )
        return [dict(item) for item in grants]

    def create_device_computer_grant(
        self,
        access_token: str,
        *,
        link_id: str,
        mode: str,
        expires_at: str | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"link_id": link_id, "mode": mode}
        if expires_at is not None:
            payload["expires_at"] = expires_at
        response = self._request("POST",
            f"{self.base_url}/v3/product/device-computer-grants",
            headers={**self._headers(access_token), "content-type": "application/json"},
            json=payload,
        )
        body = self._body(response)
        grant = body.get("grant")
        if not isinstance(grant, dict):
            raise ProductRemoteError(
                "product_remote_invalid_response",
                response.status_code,
                "Product device grant payload is missing",
            )
        return {"mode": body.get("mode"), "replayed": bool(body.get("replayed")), "grant": dict(grant)}

    def revoke_device_computer_grant(
        self,
        access_token: str,
        grant_id: str,
    ) -> dict[str, Any]:
        response = self._request("DELETE",
            f"{self.base_url}/v3/product/device-computer-grants/{grant_id}",
            headers=self._headers(access_token),
        )
        return self._body(response)

    def device_intelligence_grants(
        self,
        access_token: str,
        *,
        link_id: str | None = None,
    ) -> list[dict[str, Any]]:
        params = {"link_id": link_id} if link_id is not None else None
        response = self._request("GET",
            f"{self.base_url}/v3/product/device-intelligence-grants",
            headers=self._headers(access_token),
            params=params,
        )
        body = self._body(response)
        grants = body.get("grants")
        if not isinstance(grants, list) or not all(isinstance(item, dict) for item in grants):
            raise ProductRemoteError(
                "product_remote_invalid_response",
                response.status_code,
                "Product App Intelligence grant catalog is invalid",
            )
        return [dict(item) for item in grants]

    def create_device_intelligence_grant(
        self,
        access_token: str,
        *,
        link_id: str,
        mode: str = "app-intelligence-read",
        expires_at: str | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"link_id": link_id, "mode": mode}
        if expires_at is not None:
            payload["expires_at"] = expires_at
        response = self._request("POST",
            f"{self.base_url}/v3/product/device-intelligence-grants",
            headers={**self._headers(access_token), "content-type": "application/json"},
            json=payload,
        )
        body = self._body(response)
        grant = body.get("grant")
        if not isinstance(grant, dict):
            raise ProductRemoteError(
                "product_remote_invalid_response",
                response.status_code,
                "Product App Intelligence grant payload is missing",
            )
        return {
            "mode": body.get("mode"),
            "replayed": bool(body.get("replayed")),
            "grant": dict(grant),
        }

    def revoke_device_intelligence_grant(
        self,
        access_token: str,
        grant_id: str,
    ) -> dict[str, Any]:
        response = self._request("DELETE",
            f"{self.base_url}/v3/product/device-intelligence-grants/{grant_id}",
            headers=self._headers(access_token),
        )
        return self._body(response)

    def project_capability_grants(
        self,
        access_token: str,
        *,
        link_id: str | None = None,
    ) -> list[dict[str, Any]]:
        params = {"link_id": link_id} if link_id is not None else None
        response = self._request("GET",
            f"{self.base_url}/v3/product/project-capability-grants",
            headers=self._headers(access_token),
            params=params,
        )
        body = self._body(response)
        grants = body.get("grants")
        if not isinstance(grants, list) or not all(isinstance(item, dict) for item in grants):
            raise ProductRemoteError(
                "product_remote_invalid_response",
                response.status_code,
                "Product project grant catalog is invalid",
            )
        return [dict(item) for item in grants]

    def create_project_capability_grant(
        self,
        access_token: str,
        *,
        link_id: str,
        mode: str,
        projects: list[str],
        expires_at: str | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "link_id": link_id,
            "mode": mode,
            "projects": list(projects),
        }
        if expires_at is not None:
            payload["expires_at"] = expires_at
        response = self._request("POST",
            f"{self.base_url}/v3/product/project-capability-grants",
            headers={**self._headers(access_token), "content-type": "application/json"},
            json=payload,
        )
        body = self._body(response)
        grant = body.get("grant")
        if not isinstance(grant, dict):
            raise ProductRemoteError(
                "product_remote_invalid_response",
                response.status_code,
                "Product project grant payload is missing",
            )
        return {
            "mode": body.get("mode"),
            "replayed": bool(body.get("replayed")),
            "grant": dict(grant),
        }

    def revoke_project_capability_grant(
        self,
        access_token: str,
        grant_id: str,
    ) -> dict[str, Any]:
        response = self._request("DELETE",
            f"{self.base_url}/v3/product/project-capability-grants/{grant_id}",
            headers=self._headers(access_token),
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
            response = self._request("POST",
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
                    "product_remote_invalid_response", response.status_code,
                    "Product action request id is missing or invalid",
                ) from error
        except ProductRemoteError as error:
            # No response / malformed ACK is not evidence that POST had no effect.
            raise ProductRemoteError(
                error.error_code, error.status_code, error.detail,
                acceptance_unknown=(error.status_code in {0, 408}
                                    or error.status_code >= 500
                                    or error.error_code == "product_remote_invalid_response"),
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
        if timeout_seconds is not None and (not math.isfinite(timeout_seconds) or timeout_seconds <= 0):
            raise ValueError("Action request timeout must be finite and positive")
        options = {"timeout": timeout_seconds} if timeout_seconds is not None else {}
        try:
            response = self._request("GET",
                f"{self.base_url}/v3/product/actions/{request_id}",
                headers=self._headers(access_token), deadline=_deadline, **options,
            )
            body = self._body(response)
            action = body.get("action")
            if (not isinstance(action, dict)
                    or action.get("request_id") != request_id
                    or not isinstance(action.get("status"), str)
                    or action["status"] not in _ACTION_STATUSES):
                raise ProductRemoteError(
                    "product_remote_invalid_response", response.status_code,
                    "Product action identity or status is invalid",
                )
            return action
        except ProductRemoteError as error:
            raise ProductRemoteError(
                error.error_code, error.status_code, error.detail, request_id=request_id,
            ) from error

    def wait_action(
        self,
        access_token: str,
        request_id: str,
        *,
        timeout_seconds: float = 30.0,
        poll_interval_seconds: float = 0.5,
    ) -> dict[str, Any]:
        if not math.isfinite(timeout_seconds) or timeout_seconds <= 0 or timeout_seconds > 300:
            raise ValueError("timeout_seconds must be between 0 and 300")
        if not math.isfinite(poll_interval_seconds) or poll_interval_seconds < 0.1 or poll_interval_seconds > 5:
            raise ValueError("poll_interval_seconds must be between 0.1 and 5")

        deadline = time.monotonic() + timeout_seconds
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ProductActionWaitTimeout(request_id)
            try:
                result = self.action(access_token, request_id, timeout_seconds=remaining, _deadline=deadline)
            except ProductRemoteError as error:
                if error.error_code == "product_action_wait_timeout":
                    raise ProductActionWaitTimeout(request_id) from error
                raise
            if result["status"] in _TERMINAL_STATUSES:
                return result
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ProductActionWaitTimeout(request_id)
            time.sleep(min(poll_interval_seconds, remaining))
