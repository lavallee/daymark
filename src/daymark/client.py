"""Small optional synchronous client for the versioned Daymark API."""

from __future__ import annotations

from typing import Any, Self
from urllib.parse import quote, urlencode

from .errors import ClientError, DomainError


class Client:
    def __init__(
        self, base_url: str, token: str, *, timeout: float = 10, transport: Any = None, read_only: bool = False
    ) -> None:
        try:
            import httpx
        except ModuleNotFoundError as exc:
            if exc.name != "httpx":
                raise
            raise DomainError(
                "invalid", "HTTP access requires the optional client dependency",
                ["Install daymark-core[client] in this Python environment."],
            ) from None

        if not token:
            raise DomainError("unauthenticated", "A bearer credential is required",
                              ["For CLI or MCP, set the environment variable named by --token-env (default DAYMARK_TOKEN)."])
        self.read_only = read_only
        self.legacy_search = False
        self._legacy_orientation = False
        self._service_paths = None
        self._known_profile_routes = set()
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
            ("POST", "/v1/search"), ("POST", "/v1/query"), ("POST", "/v1/advice"),
        }:
            raise DomainError("forbidden", "This read-only connection cannot send a write")
        try:
            target = path
            if params := kwargs.pop("params", None):
                # Older HTTPX versions treat a literal %2F in a parameter as
                # pre-escaped. Encode once so imported IDs remain distinct.
                query = urlencode({
                    key: str(value).lower() if isinstance(value, bool) else value
                    for key, value in params.items()
                })
                target = httpx.URL(path).copy_with(query=query.encode("ascii"))
            result = self._http.request(method, target, **kwargs)
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
        body = {
            "operation_id": operation_id,
            "action": action,
            "payload": payload,
            "expected_version": expected_version,
            **({"attribute_capture_note": True} if attribute_capture_note else {}),
        }
        if action == "take":
            return self._selected_request("POST", "/v1/operations", project=payload.get("project"),
                                          context=payload.get("context"), json=body)
        return self._request("POST", "/v1/operations", json=body)

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

    def search(self, text=None, *, kind="task", status=None, scope=None, after=None, limit=50,
               claimed=False, archived=False, now=None, source=None, context=None, projection=None):
        from .search import validate_claim_filters, validate_filters

        validate_filters(text, kind, status, scope, after, limit)
        validate_claim_filters(kind, archived, claimed)
        if projection not in (None, "line", "entity"):
            raise DomainError("invalid", "Search projection must be line or entity")
        if context is not None and (not isinstance(context, str) or not 1 <= len(context) <= 128):
            raise DomainError("invalid", "Context must be a nonempty entity ID of at most 128 characters")
        params = self._params(context=context, projection=projection)
        if params and not self.supports_search_options(*params):
            raise DomainError("invalid", "Upgrade the service for the requested search fields")
        from .models import SourceRef

        ref = SourceRef.parse_ref(source).model_dump() if source is not None else None
        upgrade = ("Upgrade the service to find an exact source with search filters" if ref is not None
                   else "Upgrade the service to search archived records")
        body = self._params(text=text, kind=kind, status=status, scope=scope, after=after, limit=limit,
                            claimed=claimed or None, archived=archived or None, now=now if claimed else None)
        if ref is not None:
            body["source"] = ref
        if not self.legacy_search:
            try:
                result = self._request("POST", "/v1/search", json=body, params=params)
            except ClientError as error:
                if (archived or ref is not None) and error.status_code == 422:
                    raise DomainError("invalid", upgrade) from None
                if error.status_code not in {404, 405}:
                    raise
                self.legacy_search = True
            else:
                if params and (not isinstance(result, dict) or any(result.get(k) != v for k, v in params.items())):
                    raise DomainError("invalid", "The service did not confirm the requested search fields")
                if ref is not None:
                    if not isinstance(result, dict) or result.get("source") != ref:
                        raise DomainError("invalid", upgrade)
                    hits = result.get("items")
                    if not isinstance(hits, list) or len(hits) > 1 or result.get("next") is not None or any(
                        not isinstance(hit, dict) or hit.get("kind") != kind
                        or (status is not None and hit.get("status") != status)
                        or (scope is not None and (hit.get("scope") or "") != scope)
                        for hit in hits
                    ):
                        raise DomainError("invalid", "The service did not return a filtered exact-source result")
                if archived and result.get("archived") is not True:
                    raise DomainError("invalid", upgrade)
                return result
        if params:
            raise DomainError("invalid", "Upgrade the service for the requested search fields")
        if archived or ref is not None:
            raise DomainError("invalid", upgrade)
        if claimed:
            raise DomainError("invalid", "Upgrade the service to search the authenticated worker's claims")
        from .search import legacy_page

        return legacy_page(self, text, kind=kind, status=status, scope=scope, after=after, limit=limit)

    def supports_search_options(self, *fields):
        """Older services can ignore query parameters. Check before sending a constrained read."""
        if self.legacy_search:
            return False
        try:
            operation = self._paths().get("/v1/search", {}).get("post", {})
        except ClientError as error:
            if error.status_code in {404, 405}:
                return False
            raise
        available = {p.get("name") for p in operation.get("parameters", [])}
        return all(field in available for field in fields)

    def query(self, filters: dict[str, Any]) -> Any:
        return self._request("POST", "/v1/query", json={"filters": filters})

    @staticmethod
    def _params(**values: Any) -> dict[str, Any]:
        return {
            key: value.isoformat() if hasattr(value, "isoformat") else value
            for key, value in values.items()
            if value is not None
        }

    def _paths(self):
        if self._service_paths is None:
            # v0.5 ignores the optional flag and returns its full OpenAPI
            # document. Both responses use the same paths/methods structure.
            document = self._request("GET", "/v1/schema", params={"brief": "true"})
            if not isinstance(document, dict) or not isinstance(document.get("paths"), dict):
                raise DomainError("invalid", "The service did not provide its supported routes and parameters")
            self._service_paths = document["paths"]
        return self._service_paths

    def _require_filter(self, path, field, *, recovery=None):
        """Older servers ignore unknown query parameters. Never silently widen a filtered read."""
        parameters = self._paths().get(path, {}).get("get", {}).get("parameters", ())
        if not any(parameter["name"] == field for parameter in parameters):
            raise DomainError("invalid", f"This service does not support {field} on {path}",
                              [recovery or "Upgrade the service or choose a supported filter."])

    def _selected_request(self, method, path, *, project, context, **options):
        if project is None:
            return self._request(method, path, **options)
        self._require_filter(
            "/v1/ready" if method == "POST" else path, "project",
            recovery="Upgrade the service for project selection. The project plan lists its members and their recorded state.",
        )
        try:
            return self._request(method, path, **options)
        except ClientError as exc:
            if exc.status_code != 404:
                raise
            message = "Project or context not found" if context is not None else "Project not found"
            details = ["Find the project ID on the same connection with daymark find --kind project --page."]
            if context is not None:
                details.append("Check the context ID with daymark find --kind context --page.")
            raise DomainError("not_found", message + " in the service workspace", details) from None

    def ready(
        self,
        *,
        now: Any = None,
        limit: int = 50,
        mine: bool = False,
        owner: str | None = None,
        next_move: str | None = None,
        context: str | None = None, scope: str | None = None, project: str | None = None,
        brief: bool = False,
    ) -> Any:
        if context is not None:
            self._require_filter("/v1/ready", "context")
        params = self._params(now=now, limit=limit, owner=owner, mine=mine or None, next_move=next_move,
                              scope=scope, context=context, project=project, brief=brief or None)
        rows = self._selected_request("GET", "/v1/ready", project=project, context=context, params=params)
        self._check_project_rows(rows, project)
        if brief:
            # A 0.5 service ignores the brief query parameter. Keep these
            # rows bounded before returning them to a session or MCP host.
            from .models import normalize_now
            from .views import ready_record

            instant = normalize_now(now)
            return [ready_record(row, instant) if "created_at" in row else row for row in rows]
        return rows

    @staticmethod
    def _check_project_rows(rows, project):
        if project is not None and (not isinstance(rows, list) or any(
            not isinstance(row, dict) or row.get("project_id") != project for row in rows
        )):
            raise DomainError("invalid", "The service returned work outside the requested project")

    def explain_ready(self, *, limit=20, now=None, owner=None, next_move=None, context: str | None = None, scope: str | None = None, project=None, mine=False):
        if context is not None:
            self._require_filter("/v1/ready/explanation", "context")
        params = self._params(limit=limit, now=now, owner=owner, next_move=next_move,
                              scope=scope, context=context, project=project, mine=mine or None)
        result = self._selected_request("GET", "/v1/ready/explanation", project=project, context=context, params=params)
        if project is not None:
            filters = result.get("filters") if isinstance(result, dict) else None
            if not isinstance(filters, dict) or filters.get("project") != project:
                raise DomainError("invalid", "The service did not confirm the requested project")
            self._check_project_rows(result.get("ready"), project)
        return result

    def agenda(
        self, *, now: Any = None, days: int = 7, context: str | None = None,
        scope: str | None = None, deadlines: bool = False,
    ) -> Any:
        if context is not None:
            self._require_filter("/v1/agenda", "context")
        if deadlines:
            self._require_filter("/v1/agenda", "deadlines")
        params = self._params(now=now, days=days, scope=scope, context=context, deadlines=True if deadlines else None)
        return self._request("GET", "/v1/agenda", params=params)

    def explore(self, goal: str | None = None, *, now: Any = None, scope: str | None = None) -> Any:
        if scope is not None:
            self._require_filter("/v1/explore", "scope")
        data = self._request("GET", "/v1/explore", params=self._params(goal=goal, now=now, scope=scope))
        if scope is not None and (not isinstance(data, dict) or data.get("scope") != scope):
            raise DomainError("invalid", "The service did not confirm the requested condition scope",
                              ["Upgrade the service to inspect scoped conditions and related work."])
        return data

    def _optional_profile_read(self, method, path, **kwargs):
        """Distinguish an old API from a missing profile on a service with this route."""
        if self._service_paths is not None and method.lower() not in self._service_paths.get(path, {}):
            return None
        try:
            result = self._request(method, path, **kwargs)
        except ClientError as error:
            if error.status_code not in {404, 405} or path in self._known_profile_routes:
                raise
            if method.lower() in self._paths().get(path, {}):
                raise
            return None
        self._known_profile_routes.add(path)
        return result

    def advice(self, *, profile=None, settings=(), drops=(), scope=None, context=None, now=None, brief=False):
        body = self._params(profile=profile, set=list(settings), drop=list(drops), scope=scope,
                            context=context, now=now, brief=brief or None)
        return self._optional_profile_read("POST", "/v1/advice", json=body)

    def profile_read(self, view="catalog", name=None):
        return self._optional_profile_read("GET", "/v1/profiles", params=self._params(view=view, name=name))

    def orientation(self, *, now=None, owner=None, scope=None):
        """Use the authenticated worker's snapshot, or compatible reads on older services."""
        if scope is not None:
            self._require_filter("/v1/orientation", "scope")
            result = self._request("GET", "/v1/orientation", params=self._params(now=now, scope=scope))
            if not isinstance(result, dict) or result.get("scope") != scope:
                raise DomainError("invalid", "The service did not confirm the requested orientation scope")
            for section in ("ready", "memories"):
                rows = result.get(section)
                if not isinstance(rows, list) or len(rows) > 5 or any(
                    not isinstance(row, dict) or (row.get("scope") or "") != scope for row in rows
                ):
                    raise DomainError("invalid", "The service returned records outside the requested orientation scope")
            return {key: result[key] for key in ("summary", "ready", "memories", "next_memory", "as_of", "scope", "identity")
                    if key in result}
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

    def projects(self, *, now: Any = None, scope: str | None = None) -> Any:
        if scope is not None:
            self._require_filter(
                "/v1/projects", "scope",
                recovery="Upgrade the service for scoped rollups. For names only, find kind=project with the same scope.",
            )
        result = self._request("GET", "/v1/projects", params=self._params(now=now, scope=scope))
        if scope is None:
            return result
        if (
            not isinstance(result, dict) or result.get("scope") != scope
            or result.get("member_scope") != "whole_project"
            or not isinstance(result.get("projects"), list)
            or any(not isinstance(p, dict) or "scope" not in p or (p["scope"] or "") != scope
                   for p in result["projects"])
        ):
            raise DomainError("invalid", "The service did not confirm the requested project scope")
        return {key: result[key] for key in ("projects", "scope", "member_scope")}

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
