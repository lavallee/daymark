"""Small optional synchronous client for the versioned Daymark API."""

from __future__ import annotations

from typing import Any, Self
from urllib.parse import quote


class ClientError(Exception):
    """Safe failure containing a status code, never server bodies or credentials."""

    def __init__(self, status_code: int | None = None) -> None:
        self.status_code = status_code
        super().__init__(f"Daymark request failed ({status_code or 'transport'})")


class Client:
    def __init__(
        self, base_url: str, token: str, *, timeout: float = 10, transport: Any = None
    ) -> None:
        import httpx

        if not token:
            raise ValueError("Bearer credential required")
        self._http = httpx.Client(
            base_url=base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {token}"},
            timeout=timeout,
            transport=transport,
            follow_redirects=False,
        )

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def close(self) -> None:
        self._http.close()

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        import httpx

        try:
            result = self._http.request(method, path, **kwargs)
            result.raise_for_status()
            return result.json()
        except httpx.HTTPStatusError as exc:
            raise ClientError(exc.response.status_code) from None
        except (httpx.HTTPError, ValueError):
            raise ClientError() from None

    def execute(
        self, operation_id: str, action: str, payload: dict[str, Any], expected_version: int
    ) -> Any:
        return self._request(
            "POST",
            "/v1/operations",
            json={
                "operation_id": operation_id,
                "action": action,
                "payload": payload,
                "expected_version": expected_version,
            },
        )

    def create(self, operation_id: str, entity: dict[str, Any]) -> Any:
        return self.execute(operation_id, "create", entity, 0)

    def update(self, operation_id: str, payload: dict[str, Any], expected_version: int) -> Any:
        return self.execute(operation_id, "update", payload, expected_version)

    def action(
        self, operation_id: str, action: str, entity_id: str, expected_version: int, **fields: Any
    ) -> Any:
        return self.execute(operation_id, action, {"id": entity_id, **fields}, expected_version)

    def get(self, entity_id: str) -> Any:
        return self._request("GET", f"/v1/entities/{quote(entity_id, safe='')}")

    def list(self, kind: str | None = None) -> Any:
        return self._request("GET", "/v1/entities", params={"kind": kind} if kind else {})

    def query(self, filters: dict[str, Any]) -> Any:
        return self._request("POST", "/v1/query", json={"filters": filters})

    def events(self, after: int = 0, limit: int = 100) -> Any:
        return self._request("GET", "/v1/events", params={"after": after, "limit": limit})

    def export(self) -> Any:
        return self._request("GET", "/v1/export")

    def import_snapshot(
        self, snapshot: dict[str, Any], operation_id: str, expected_version: int
    ) -> Any:
        return self._request(
            "POST",
            "/v1/import",
            json={
                "snapshot": snapshot,
                "operation_id": operation_id,
                "expected_version": expected_version,
            },
        )

    def health(self) -> Any:
        return self._request("GET", "/v1/health")

    def schema(self) -> Any:
        return self._request("GET", "/v1/schema")
