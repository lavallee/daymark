"""Optional authenticated HTTP boundary around the embedded Store engine."""

from __future__ import annotations

import hashlib
import hmac
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from fastapi import APIRouter, FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

LOG = logging.getLogger("daymark.api")
MAX_BODY_BYTES = 1_048_576


@dataclass(frozen=True)
class Identity:
    workspace: str
    principal: str


class Operation(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    operation_id: str = Field(min_length=1, max_length=200)
    action: str = Field(min_length=1, max_length=80)
    payload: dict[str, Any]
    expected_version: int = Field(ge=0)


class BatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    operations: list[Operation] = Field(min_length=1, max_length=100)
    atomic: bool = True
    request_id: str | None = Field(default=None, min_length=1, max_length=200)
    request: Any = None


class ImportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    operation_id: str = Field(min_length=1, max_length=200)
    expected_version: int = Field(ge=0)
    snapshot: dict[str, Any]


class QueryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    filters: dict[str, Any] = Field(default_factory=dict)


def _audit(event: str, identity: Identity | None = None) -> None:
    # Hash identifiers too: server configuration may itself contain personal labels.
    actor = (
        hashlib.sha256(identity.principal.encode()).hexdigest()[:16] if identity else "anonymous"
    )
    LOG.info("daymark_boundary", extra={"event_type": event, "actor": actor})


def _invoke(call: Any, *args: Any, **kwargs: Any) -> Any:
    try:
        return call(*args, **kwargs)
    except Exception as exc:  # noqa: BLE001 - redact arbitrary engine errors at boundary
        # Exception messages and validation inputs may contain entire private entities.
        name = type(exc).__name__.lower()
        code = str(getattr(exc, "code", "")).lower()
        if code == "forbidden":
            raise HTTPException(403, "Forbidden") from None
        if "notfound" in name or code in {"not_found", "missing"}:
            raise HTTPException(404, "Not found") from None
        if "conflict" in name or "conflict" in code or "version" in code:
            raise HTTPException(409, "Operation conflict") from None
        if isinstance(exc, (ValueError, TypeError)) or "domain" in name or "validation" in name:
            raise HTTPException(422, "Invalid operation") from None
        _audit("engine_failure")
        raise HTTPException(500, "Engine failure") from None


def create_router(store: Any) -> APIRouter:
    """Create v1 routes; mount via create_app so authentication/body limits apply.

    Embedded consumers can include this router behind their own middleware providing
    ``request.state.daymark_identity`` with an Identity from trusted authentication.
    """
    router = APIRouter(prefix="/v1")

    def identity(request: Request) -> Identity:
        result = getattr(request.state, "daymark_identity", None)
        if not isinstance(result, Identity):
            raise HTTPException(
                401, "Bearer authentication required", headers={"WWW-Authenticate": "Bearer"}
            )
        return result

    @router.get("/health", operation_id="daymark_v1_health")
    def health(request: Request) -> dict[str, Any]:
        actor = identity(request)
        raw = _invoke(store.health, workspace=actor.workspace)
        # Explicit allowlist: never expose workspace names, paths, errors or entities.
        allowed = (
            "scheduler",
            "outbox",
            "last_tick",
            "last_heartbeat",
            "pending",
            "leased",
            "failed",
        )

        def sanitize(value: Any) -> Any:
            if isinstance(value, dict):
                return {
                    k: sanitize(v)
                    for k, v in value.items()
                    if k
                    in (
                        *allowed,
                        "count",
                        "status",
                        "running",
                        "due",
                        "delivered",
                        "retrying",
                        "stale",
                        "heartbeat",
                        "tick",
                    )
                }
            if isinstance(value, (int, float, bool)) or value is None:
                return value
            # Time/status strings are permitted only after checking bounded syntax.
            if (
                isinstance(value, str)
                and len(value) <= 40
                and all(c in "0123456789-:.+TZ " for c in value)
            ):
                return value
            return None

        return {"status": "ok", **sanitize({k: v for k, v in raw.items() if k in allowed})}

    def principal_payload(body: Operation, actor: Identity) -> dict[str, Any]:
        payload = dict(body.payload)
        # The authenticated principal, not the request body, says who is acting.
        if body.action in {"claim", "release"}:
            payload["owner"] = actor.principal
        elif body.action == "note":
            payload["author"] = actor.principal
        return payload

    @router.post("/operations", operation_id="daymark_v1_execute")
    def execute(body: Operation, request: Request) -> Any:
        actor = identity(request)
        _audit("write_attempt", actor)
        return _invoke(
            store.execute,
            actor.workspace,
            body.operation_id,
            body.action,
            principal_payload(body, actor),
            expected_version=body.expected_version,
            actor=actor.principal,
        )

    @router.post("/operations/batch", operation_id="daymark_v1_batch")
    def batch(body: BatchRequest, request: Request) -> Any:
        """Per-operation results in one transaction; see Store.batch. Refusals are
        results (200), not HTTP errors; a malformed batch is 422."""
        actor = identity(request)
        _audit("write_attempt", actor)
        operations = [
            {
                "operation_id": op.operation_id,
                "action": op.action,
                "payload": principal_payload(op, actor),
                "expected_version": op.expected_version,
            }
            for op in body.operations
        ]
        result = _invoke(
            store.batch,
            actor.workspace,
            operations,
            atomic=body.atomic,
            actor=actor.principal,
            request_id=body.request_id,
            request=body.request,
        )
        # Domain messages are fixed rule text; field details are dropped at the boundary.
        for item in result["results"]:
            if not item["ok"]:
                item["error"] = {**item["error"], "details": []}
        return result

    @router.get("/projects", operation_id="daymark_v1_projects")
    def projects(request: Request, now: str | None = Query(None, max_length=40)) -> Any:
        return _invoke(store.projects, identity(request).workspace, now=now)

    @router.get("/projects/{project_id}/plan", operation_id="daymark_v1_plan")
    def plan(
        project_id: str, request: Request, now: str | None = Query(None, max_length=40)
    ) -> Any:
        return _invoke(store.plan, identity(request).workspace, project_id, now=now)

    @router.get("/projects/{project_id}/changes", operation_id="daymark_v1_changes")
    def changes(project_id: str, request: Request, after: int = Query(..., ge=0)) -> Any:
        return _invoke(store.changes, identity(request).workspace, project_id, after)

    @router.get("/goals", operation_id="daymark_v1_goals")
    def goals(request: Request, now: str | None = Query(None, max_length=40)) -> Any:
        return _invoke(store.goals, identity(request).workspace, now=now)

    @router.get("/goals/{goal_id}/plan", operation_id="daymark_v1_goal_plan")
    def goal_plan(
        goal_id: str, request: Request, now: str | None = Query(None, max_length=40)
    ) -> Any:
        return _invoke(store.goal_plan, identity(request).workspace, goal_id, now=now)

    @router.get("/tree/{entity_id}", operation_id="daymark_v1_tree")
    def tree(entity_id: str, request: Request, depth: int = Query(3, ge=1, le=10)) -> Any:
        return _invoke(store.tree, identity(request).workspace, entity_id, depth=depth)

    @router.get("/entities/{entity_id}", operation_id="daymark_v1_get")
    def get(entity_id: str, request: Request) -> Any:
        return _invoke(store.get, identity(request).workspace, entity_id)

    @router.get("/entities", operation_id="daymark_v1_list")
    def list_entities(request: Request, kind: str | None = None) -> Any:
        return _invoke(store.list, identity(request).workspace, kind=kind)

    @router.post("/query", operation_id="daymark_v1_query")
    def query(body: QueryRequest, request: Request) -> Any:
        return _invoke(store.query, identity(request).workspace, body.filters)

    @router.get("/ready", operation_id="daymark_v1_ready")
    def ready(
        request: Request,
        limit: int = Query(50, ge=1, le=1000),
        owner: str | None = Query(None, max_length=200),
        next_move: str | None = Query(None, max_length=20),
        scope: str | None = Query(None, max_length=100),
        now: str | None = Query(None, max_length=40),
    ) -> Any:
        return _invoke(
            store.ready,
            identity(request).workspace,
            now=now,
            limit=limit,
            owner=owner,
            next_move=next_move,
            scope=scope,
        )

    @router.get("/agenda", operation_id="daymark_v1_agenda")
    def agenda(
        request: Request,
        days: int = Query(7, ge=0, le=366),
        scope: str | None = Query(None, max_length=100),
        now: str | None = Query(None, max_length=40),
    ) -> Any:
        return _invoke(store.agenda, identity(request).workspace, now=now, days=days, scope=scope)

    @router.get("/summary", operation_id="daymark_v1_summary")
    def summary(
        request: Request,
        scope: str | None = Query(None, max_length=100),
        now: str | None = Query(None, max_length=40),
    ) -> Any:
        return _invoke(store.summary, identity(request).workspace, now=now, scope=scope)

    @router.get("/context/{entity_id}", operation_id="daymark_v1_context")
    def context(entity_id: str, request: Request, history: int = Query(10, ge=0, le=100)) -> Any:
        return _invoke(store.context, identity(request).workspace, entity_id, history=history)

    @router.get("/receipts/{operation_id}", operation_id="daymark_v1_receipt")
    def receipt(operation_id: str, request: Request) -> Any:
        result = _invoke(store.receipt, identity(request).workspace, operation_id)
        if result is None:
            raise HTTPException(404, "Not found")
        return result

    @router.get("/events/head", operation_id="daymark_v1_events_head")
    def events_head(request: Request) -> Any:
        return {"cursor": _invoke(store.head, identity(request).workspace)}

    @router.get("/events", operation_id="daymark_v1_events")
    def events(
        request: Request,
        after: int = Query(0, ge=0),
        limit: int = Query(100, ge=1, le=1000),
        project: str | None = Query(None, min_length=1, max_length=128),
    ) -> Any:
        return _invoke(
            store.events, identity(request).workspace, after=after, limit=limit, project=project
        )

    @router.get("/export", operation_id="daymark_v1_export")
    def export(request: Request) -> Any:
        return _invoke(store.export, identity(request).workspace)

    @router.post("/import", operation_id="daymark_v1_import")
    def import_snapshot(body: ImportRequest, request: Request) -> Any:
        actor = identity(request)
        _audit("import_attempt", actor)
        return _invoke(
            store.import_snapshot,
            actor.workspace,
            body.snapshot,
            operation_id=body.operation_id,
            expected_version=body.expected_version,
        )

    return router


def create_app(
    store: Any,
    tokens: Mapping[str, Identity | Mapping[str, str]],
    *,
    max_body_bytes: int = MAX_BODY_BYTES,
) -> FastAPI:
    """Wrap an existing Store; token-to-workspace/principal mapping stays server-side.

    No CORS middleware is enabled. API documentation and schema require the same
    bearer authentication as data routes. Caller owns Store lifetime.
    """
    if max_body_bytes < 1:
        raise ValueError("Invalid request body limit")
    credentials = []
    for token, value in tokens.items():
        actor = value if isinstance(value, Identity) else Identity(**value)
        if (
            not isinstance(token, str)
            or not token
            or not token.isascii()
            or any(character.isspace() for character in token)
            or not isinstance(actor.workspace, str)
            or not actor.workspace
            or not isinstance(actor.principal, str)
            or not actor.principal
        ):
            raise ValueError("Invalid authentication configuration")
        credentials.append((hashlib.sha256(token.encode()).digest(), actor))
    if not credentials:
        raise ValueError("At least one bearer credential is required")
    app = FastAPI(title="Daymark", version="1.0", openapi_url=None, docs_url=None, redoc_url=None)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse({"detail": "Invalid request"}, status_code=422)

    # Pure ASGI middleware bounds streamed bodies before JSON parsing and strips
    # untrusted path/query values from all logging decisions.
    class Boundary:
        def __init__(self, app: Any) -> None:
            self.inner = app

        async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
            if scope["type"] != "http":
                await self.inner(scope, receive, send)
                return
            headers = [(k.lower(), v) for k, v in scope.get("headers", [])]
            auth = [v for k, v in headers if k == b"authorization"]
            actor = None
            if len(auth) == 1:
                scheme, _, credential = auth[0].partition(b" ")
                if scheme.lower() == b"bearer" and credential:
                    digest = hashlib.sha256(credential).digest()
                    for expected, candidate in credentials:
                        if hmac.compare_digest(digest, expected):
                            actor = candidate
            _audit("authentication_allowed" if actor else "authentication_denied", actor)
            if actor is None:
                await JSONResponse(
                    {"detail": "Bearer authentication required"},
                    401,
                    headers={"WWW-Authenticate": "Bearer"},
                )(scope, receive, send)
                return
            scope.setdefault("state", {})["daymark_identity"] = actor
            chunks, size = [], 0
            while True:
                message = await receive()
                if message["type"] == "http.disconnect":
                    return
                size += len(message.get("body", b""))
                if size > max_body_bytes:
                    await JSONResponse({"detail": "Request body too large"}, 413)(
                        scope, receive, send
                    )
                    return
                chunks.append(message.get("body", b""))
                if not message.get("more_body", False):
                    break

            async def replay() -> Any:
                return {"type": "http.request", "body": b"".join(chunks), "more_body": False}

            started = False

            async def guarded_send(message: Any) -> None:
                nonlocal started
                if message["type"] == "http.response.start":
                    started = True
                await send(message)

            try:
                await self.inner(scope, replay, guarded_send)
            except Exception as exc:  # noqa: BLE001 - redact parser/framework exceptions as well
                _audit("boundary_failure", actor)
                if not started:
                    malformed = isinstance(exc, (RecursionError, UnicodeDecodeError))
                    await JSONResponse(
                        {"detail": "Invalid request" if malformed else "Engine failure"},
                        400 if malformed else 500,
                    )(scope, replay, send)

    app.add_middleware(Boundary)
    app.include_router(create_router(store))

    @app.get("/v1/schema", operation_id="daymark_v1_schema", include_in_schema=False)
    def schema() -> Any:
        document = app.openapi()
        document.setdefault("components", {}).setdefault("securitySchemes", {})["BearerAuth"] = {
            "type": "http",
            "scheme": "bearer",
        }
        document["security"] = [{"BearerAuth": []}]
        return document

    return app
