"""Small optional synchronous client for the versioned Daymark API."""

from __future__ import annotations

from typing import Any, Self
from urllib.parse import quote

from .models import DomainError


class ClientError(Exception):
    """Safe failure containing a status code, never server bodies or credentials."""

    def __init__(self, status_code: int | None = None) -> None:
        self.status_code = status_code
        self.code = {403: "forbidden", 404: "not_found", 409: "conflict", 422: "invalid"}.get(
            status_code, "remote"
        )
        super().__init__(f"Daymark request failed ({status_code or 'transport'})")


class Client:
    def __init__(
        self, base_url: str, token: str, *, timeout: float = 10, transport: Any = None, read_only: bool = False
    ) -> None:
        import httpx

        if not token:
            raise ValueError("Bearer credential required")
        self.read_only = read_only
        self.legacy_search = False
        self._legacy_orientation = False
        self._read_params = None
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

        if self.read_only and method != "GET" and (method, path) not in {
            ("POST", "/v1/search"), ("POST", "/v1/query"),
        }:
            raise DomainError("forbidden", "This read-only connection cannot send a write")
        try:
            result = self._http.request(method, path, **kwargs)
            result.raise_for_status()
            return result.json()
        except httpx.HTTPStatusError as exc:
            raise ClientError(exc.response.status_code) from None
        except (httpx.HTTPError, ValueError):
            raise ClientError() from None

    def execute(
        self, operation_id: str, action: str, payload: dict[str, Any], expected_version: int,
        *, attribute_capture_note: bool = False,
    ) -> Any:
        return self._request(
            "POST",
            "/v1/operations",
            json={
                "operation_id": operation_id,
                "action": action,
                "payload": payload,
                "expected_version": expected_version,
                **({"attribute_capture_note": True} if attribute_capture_note else {}),
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

    def _read_id(self, path, query_path, name, value, **params):
        """Use query parameters for IDs that gateways may interpret as path segments."""
        if not isinstance(value, str) or not value:
            raise DomainError("invalid", "An ID is required")
        if "/" in value or "%" in value or value in {".", ".."}:
            return self._request("GET", "/v1/" + query_path, params=self._params(**{name: value}, **params))
        return self._request("GET", "/v1/" + path.format(id=quote(value, safe="")), params=self._params(**params))

    def get(self, entity_id: str) -> Any:
        return self._read_id("entities/{id}", "entity", "entity_id", entity_id)

    def list(self, kind: str | None = None) -> Any:
        return self._request("GET", "/v1/entities", params={"kind": kind} if kind else {})

    def search(self, text=None, *, kind="task", status=None, scope=None, after=None, limit=50, claimed=False, now=None):
        body = self._params(text=text, kind=kind, status=status, scope=scope, after=after, limit=limit,
                            claimed=claimed or None, now=now if claimed else None)
        if not self.legacy_search:
            try:
                return self._request("POST", "/v1/search", json=body)
            except ClientError as error:
                if error.status_code not in {404, 405}:
                    raise
                self.legacy_search = True
        if claimed:
            from .models import DomainError

            raise DomainError("invalid", "Upgrade the service to search the authenticated worker's claims")
        from .search import legacy_page

        return legacy_page(self, text, kind=kind, status=status, scope=scope, after=after, limit=limit)

    def query(self, filters: dict[str, Any]) -> Any:
        return self._request("POST", "/v1/query", json={"filters": filters})

    @staticmethod
    def _params(**values: Any) -> dict[str, Any]:
        return {
            key: value.isoformat() if hasattr(value, "isoformat") else value
            for key, value in values.items()
            if value is not None
        }

    def _require_filter(self, path, field):
        """Older servers ignore unknown query parameters. Never silently widen a filtered read."""
        from .models import DomainError

        if self._read_params is None:
            self._read_params = {
                route: {param["name"] for param in operations.get("get", {}).get("parameters", [])}
                for route, operations in self.schema()["paths"].items()
            }
        if field not in self._read_params.get(path, set()):
            raise DomainError("invalid", f"This service does not support {field} on {path}",
                              ["Upgrade the service or choose a supported filter."])

    def ready(
        self,
        *,
        now: Any = None,
        limit: int = 50,
        mine: bool = False,
        owner: str | None = None,
        next_move: str | None = None,
        context: str | None = None, scope: str | None = None,
        brief: bool = False,
    ) -> Any:
        if context is not None:
            self._require_filter("/v1/ready", "context")
        params = self._params(now=now, limit=limit, owner=owner, mine=mine or None, next_move=next_move, scope=scope, context=context, brief=brief or None)
        rows = self._request("GET", "/v1/ready", params=params)
        if brief:
            # A 0.5 service ignores the brief query parameter. Keep these
            # rows bounded before returning them to a session or MCP host.
            from .models import normalize_now
            from .views import ready_record

            instant = normalize_now(now)
            return [ready_record(row, instant) if "created_at" in row else row for row in rows]
        return rows

    def explain_ready(self, *, limit=20, now=None, owner=None, next_move=None, context: str | None = None, scope: str | None = None, mine=False):
        params = self._params(limit=limit, now=now, owner=owner, next_move=next_move,
                              scope=scope, context=context, mine=mine or None)
        return self._request("GET", "/v1/ready/explanation", params=params)

    def agenda(self, *, now: Any = None, days: int = 7, context: str | None = None, scope: str | None = None) -> Any:
        if context is not None:
            self._require_filter("/v1/agenda", "context")
        params = self._params(now=now, days=days, scope=scope, context=context)
        return self._request("GET", "/v1/agenda", params=params)

    def explore(self, goal: str | None = None, *, now: Any = None, scope: str | None = None) -> Any:
        if scope is not None:
            self._require_filter("/v1/explore", "scope")
        return self._request("GET", "/v1/explore", params=self._params(goal=goal, now=now, scope=scope))

    def orientation(self, *, now=None, owner=None):
        """Use the authenticated worker's snapshot, or compatible reads on older services."""
        if not self._legacy_orientation:
            try:
                return self._request("GET", "/v1/orientation", params=self._params(now=now))
            except ClientError as exc:
                if exc.status_code not in {404, 405}:
                    raise
                self._legacy_orientation = True
        from .models import normalize_now
        from .views import memory_preview

        instant = normalize_now(now)
        facts = self.search(kind="memory", limit=5)
        return {
            "summary": self.summary(now=instant, mine=True, owner=owner),
            "ready": self.ready(now=instant, limit=5, mine=True, owner=owner, brief=True),
            "memories": [memory_preview(m["id"], m["title"], m.get("scope")) for m in facts["items"]],
            "next_memory": facts["next"],
        }

    def summary(
        self, *, now: Any = None, context: str | None = None, scope: str | None = None,
        owner: str | None = None, mine: bool = False,
    ) -> Any:
        if context is not None:
            self._require_filter("/v1/summary", "context")
        params = self._params(now=now, scope=scope, context=context, owner=owner, mine=mine or None)
        return self._request("GET", "/v1/summary", params=params)

    def _source_read(self, entity_id, source, detail, **options):
        from .models import DomainError, SourceRef

        if entity_id is not None:
            raise DomainError("invalid", "Choose an entity ID or a source reference")
        ref = SourceRef.parse_ref(source)
        params = self._params(system=ref.system, kind=ref.kind, ref_id=ref.id, detail=detail, **options)
        return self._request("GET", "/v1/source", params=params)

    def brief(self, entity_id=None, *, now=None, owner=None, mine=False, before_note=None, source=None):
        if source is not None:
            return self._source_read(entity_id, source, "brief", now=now, owner=owner, mine=mine or None, before_note=before_note)
        params = self._params(entity_id=entity_id, now=now, owner=owner, mine=mine or None, before_note=before_note)
        return self._request("GET", "/v1/brief", params=params)

    def changes_since(self, entity_id=None, *, version, source=None):
        if (entity_id is None) == (source is None):
            raise DomainError("invalid", "Choose exactly one entity ID or source reference")
        self._require_filter("/v1/comparison", "since_version")
        if source is not None:
            return self._source_read(entity_id, source, "changes", since_version=version)
        return self._request("GET", "/v1/comparison", params={"entity_id": entity_id, "since_version": version})

    def read_note(self, entity_id, note_id, *, source=None):
        if source is not None:
            return self._source_read(entity_id, source, "note", note_id=note_id)
        return self._request("GET", "/v1/note", params={"entity_id": entity_id, "note_id": note_id})

    def context(
        self, entity_id: str | None = None, *, history: int = 10, now: Any = None,
        owner: str | None = None, mine: bool = False, source=None,
    ) -> Any:
        if source is not None:
            return self._source_read(entity_id, source, "show", history=history, now=now, owner=owner, mine=mine or None)
        if entity_id is None:
            raise DomainError("invalid", "An entity ID or source reference is required")
        params = self._params(history=history, now=now, owner=owner, mine=mine or None)
        return self._read_id("context/{id}", "context", "entity_id", entity_id, **params)

    def receipt_context(self, operation_id: str, *, action: str, entity_id: str | None = None) -> Any:
        try:
            return self._request(
                "GET", "/v1/receipt-context",
                params=self._params(operation_id=operation_id, action=action, entity_id=entity_id),
            )
        except ClientError as exc:
            if exc.status_code != 404:
                raise
            # Older services can accept a new operation. An existing receipt
            # alone cannot prove that the callers requested the same change.
            if self.receipt(operation_id) is None:
                return None
            raise DomainError("conflict", "Upgrade the Daymark service to verify this retry") from None

    def receipt(self, operation_id: str) -> Any:
        """The stored result of a completed operation ID, or None."""
        try:
            return self._read_id("receipts/{id}", "receipt", "operation_id", operation_id)
        except ClientError as exc:
            if exc.status_code == 404:
                return None
            raise

    def activity(self, *, after: int | str | None = None, limit: int = 20) -> Any:
        return self._request("GET", "/v1/activity", params=self._params(after=after, limit=limit))

    def events(self, after: int = 0, limit: int = 100, project: str | None = None) -> Any:
        params = self._params(after=after, limit=limit, project=project)
        return self._request("GET", "/v1/events", params=params)

    def head(self) -> int:
        return self._request("GET", "/v1/events/head")["cursor"]

    def batch(
        self,
        operations: list[dict[str, Any]],
        *,
        atomic: bool = True,
        actor: Any = None,
        request_id: str | None = None,
        request: Any = None,
    ) -> Any:
        """Several operations in one transaction; see Store.batch. The server records
        its authenticated principal as the actor, so ``actor`` is ignored."""
        body: dict[str, Any] = {"operations": operations, "atomic": atomic}
        if request_id is not None:
            body.update(request_id=request_id, request=request)
        return self._request("POST", "/v1/operations/batch", json=body)

    def goals(self, *, now: Any = None) -> Any:
        return self._request("GET", "/v1/goals", params=self._params(now=now))

    def goal_plan(self, goal_id: str, *, now: Any = None) -> Any:
        return self._read_id("goals/{id}/plan", "goal-plan", "goal_id", goal_id, now=now)

    def tree(self, entity_id: str, *, depth: int = 3) -> Any:
        return self._read_id("tree/{id}", "tree", "entity_id", entity_id, depth=depth)

    def projects(self, *, now: Any = None) -> Any:
        return self._request("GET", "/v1/projects", params=self._params(now=now))

    def plan(self, project_id: str, *, now: Any = None) -> Any:
        return self._read_id("projects/{id}/plan", "project-plan", "project_id", project_id, now=now)

    def changes(self, project_id: str, after: int) -> Any:
        return self._read_id("projects/{id}/changes", "project-changes", "project_id", project_id, after=after)

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

    def identity(self) -> dict[str, str] | None:
        """Only this credential's confirmed workspace and actor. A 0.5 service lacks this read."""
        try:
            result = self._request("GET", "/v1/identity")
        except ClientError as exc:
            if exc.status_code == 404:
                return None
            raise
        if not isinstance(result, dict) or not all(
            isinstance(result.get(key), str) and result[key] for key in ("workspace", "actor")
        ):
            raise ClientError()
        return {key: result[key] for key in ("workspace", "actor")}

    def health(self) -> Any:
        return self._request("GET", "/v1/health")

    def schema(self) -> Any:
        return self._request("GET", "/v1/schema")
