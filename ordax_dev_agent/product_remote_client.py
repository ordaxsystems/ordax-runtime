from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import httpx


_TERMINAL_STATUSES = frozenset({"succeeded", "failed", "cancelled"})


@dataclass(frozen=True)
class ProductRemoteError(RuntimeError):
    error_code: str
    status_code: int
    detail: str

    def __str__(self) -> str:
        return f"{self.error_code} (HTTP {self.status_code}): {self.detail}"


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
        if timeout_seconds <= 0 or timeout_seconds > 120:
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
    def _body(response: httpx.Response) -> dict[str, Any]:
        try:
            body = response.json()
        except ValueError as error:
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
        if response.status_code >= 400 or body.get("ok") is not True:
            code = body.get("error")
            raise ProductRemoteError(
                str(code) if isinstance(code, str) and code else "product_remote_error",
                response.status_code,
                "Control Plane rejected the Product request",
            )
        return body

    def session(self, access_token: str) -> dict[str, Any]:
        response = self.http.get(
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
        response = self.http.get(
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
        response = self.http.post(
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
        response = self.http.get(
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
        response = self.http.delete(
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
        response = self.http.get(
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
        response = self.http.post(
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
        response = self.http.delete(
            f"{self.base_url}/v3/product/device-computer-grants/{grant_id}",
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

        response = self.http.post(
            f"{self.base_url}/v3/product/actions",
            headers={
                **self._headers(access_token),
                "content-type": "application/json",
            },
            json=payload,
        )
        body = self._body(response)
        request_id = body.get("request_id")
        if not isinstance(request_id, str) or not request_id:
            raise ProductRemoteError(
                "product_remote_invalid_response",
                response.status_code,
                "Product action request id is missing",
            )
        return request_id

    def action(
        self,
        access_token: str,
        request_id: str,
    ) -> dict[str, Any]:
        response = self.http.get(
            f"{self.base_url}/v3/product/actions/{request_id}",
            headers=self._headers(access_token),
        )
        body = self._body(response)
        action = body.get("action")
        if not isinstance(action, dict):
            raise ProductRemoteError(
                "product_remote_invalid_response",
                response.status_code,
                "Product action payload is missing",
            )
        return action

    def wait_action(
        self,
        access_token: str,
        request_id: str,
        *,
        timeout_seconds: float = 30.0,
        poll_interval_seconds: float = 0.5,
    ) -> dict[str, Any]:
        if timeout_seconds <= 0 or timeout_seconds > 300:
            raise ValueError("timeout_seconds must be between 0 and 300")
        if poll_interval_seconds < 0.1 or poll_interval_seconds > 5:
            raise ValueError("poll_interval_seconds must be between 0.1 and 5")

        deadline = time.monotonic() + timeout_seconds
        while True:
            result = self.action(access_token, request_id)
            status = result.get("status")
            if status in _TERMINAL_STATUSES:
                return result
            if not isinstance(status, str):
                raise ProductRemoteError(
                    "product_remote_invalid_response",
                    200,
                    "Product action status is missing",
                )
            if time.monotonic() >= deadline:
                raise TimeoutError(f"Product action did not finish: {request_id}")
            time.sleep(poll_interval_seconds)
