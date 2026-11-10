"""HTTP/CLI boundaries are tested independently of storage implementation details."""

import json
import logging

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from daymark.api import Identity, create_app, create_router
from daymark.cli import main
from daymark.client import Client, ClientError
from daymark.models import DomainError


class Engine:
    def __init__(self):
        self.calls = []

    def execute(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return {"id": "synthetic", "version": 1}

    def get(self, workspace, entity_id):
        if entity_id == "private":
            raise DomainError("not_found", "private title token-secret")
        return {"id": entity_id, "workspace": workspace}

    def list(self, workspace, kind=None):
        return [{"workspace": workspace, "kind": kind}]

    def query(self, workspace, filters):
        return [workspace, filters]

    def events(self, workspace, after=0, limit=100):
        return []

    def export(self, workspace):
        return {"schema_version": 1, "entities": [], "events": []}

    def import_snapshot(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return {"imported": 0}

    def health(self, workspace=None):
        return {
            "scheduler": {"last_tick": "2026-11-09T12:00:00Z", "owner": "private"},
            "outbox": {"pending": 2},
            "database": "/secret/path",
            "entities": ["private"],
        }


@pytest.fixture
def boundary():
    engine = Engine()
    app = create_app(
        engine,
        {
            "secret-token": Identity("one", "actor\nprivate"),
            "other-token": {"workspace": "two", "principal": "other"},
        },
    )
    with TestClient(app) as client:
        yield engine, client


def auth():
    return {"Authorization": "Bearer secret-token"}


def operation(**changes):
    return {
        "operation_id": "op-1",
        "action": "create",
        "payload": {"title": "synthetic"},
        "expected_version": 0,
        **changes,
    }


@pytest.mark.parametrize(
    "path", ["/v1/health", "/v1/schema", "/openapi.json", "/docs", "/v1/entities"]
)
def test_every_route_and_schema_require_bearer(boundary, path):
    _, client = boundary
    assert client.get(path).status_code == 401
    assert client.get(path, headers={"Authorization": "Basic secret-token"}).status_code == 401


def test_workspace_is_resolved_only_from_server_mapping(boundary):
    engine, client = boundary
    response = client.post("/v1/operations", headers=auth(), json=operation())
    assert response.status_code == 200
    assert engine.calls == [
        (
            ("one", "op-1", "create", {"title": "synthetic"}),
            {"expected_version": 0, "actor": "actor\nprivate"},
        )
    ]
    assert (
        client.get("/v1/entities/x", headers={"Authorization": "Bearer other-token"}).json()[
            "workspace"
        ]
        == "two"
    )
    assert (
        client.post("/v1/operations", headers=auth(), json=operation(workspace="two")).status_code
        == 422
    )


@pytest.mark.parametrize(
    "change",
    [
        {"expected_version": None},
        {"expected_version": "1"},
        {"expected_version": -1},
        {"operation_id": ""},
        {"unknown": "token-secret"},
    ],
)
def test_strict_write_envelope_and_safe_validation(boundary, change):
    engine, client = boundary
    response = client.post("/v1/operations", headers=auth(), json=operation(**change))
    assert response.status_code == 422
    assert "token-secret" not in response.text
    assert not engine.calls


def test_missing_expected_version_rejected(boundary):
    _, client = boundary
    body = operation()
    del body["expected_version"]
    assert client.post("/v1/operations", headers=auth(), json=body).status_code == 422


def test_streamed_body_limit_precedes_json_parsing():
    app = create_app(Engine(), {"secret-token": Identity("one", "actor")}, max_body_bytes=32)
    with TestClient(app) as client:
        response = client.post(
            "/v1/operations", headers=auth(), content=iter([b"x" * 20, b"x" * 20])
        )
        assert response.status_code == 413
        assert client.post("/v1/operations", content=b"x" * 100).status_code == 401


def test_schema_ids_health_and_cors(boundary):
    _, client = boundary
    response = client.get("/v1/schema", headers=auth())
    ids = [
        spec["operationId"]
        for methods in response.json()["paths"].values()
        for spec in methods.values()
    ]
    assert len(ids) == len(set(ids))
    assert "daymark_v1_execute" in ids
    health = client.get("/v1/health", headers={**auth(), "Origin": "https://untrusted.invalid"})
    assert health.json()["outbox"]["pending"] == 2
    assert "private" not in health.text and "secret" not in health.text
    assert "access-control-allow-origin" not in health.headers


def test_error_and_logs_never_echo_private_content(boundary, caplog):
    _, client = boundary
    with caplog.at_level(logging.INFO, logger="daymark.api"):
        response = client.get("/v1/entities/private", headers=auth())
        client.post(
            "/v1/operations", headers=auth(), json=operation(payload={"title": "private title"})
        )
    assert response.status_code == 404
    assert response.json() == {"detail": "Not found"}
    records = [record for record in caplog.records if record.name == "daymark.api"]
    assert records
    assert all(
        "private" not in str(record.__dict__) and "secret-token" not in str(record.__dict__)
        for record in records
    )


def test_router_requires_trusted_identity():
    app = FastAPI()
    app.include_router(create_router(Engine()))
    with TestClient(app) as client:
        assert client.get("/v1/entities").status_code == 401


def test_client_uses_auth_version_and_redacts_failures():
    captured = []

    def handler(request):
        captured.append(request)
        if request.url.path.endswith("private"):
            return httpx.Response(500, text="private body")
        return httpx.Response(200, json={"version": 1})

    with Client(
        "https://synthetic.invalid", "secret-token", transport=httpx.MockTransport(handler)
    ) as client:
        assert client.create("stable", {"title": "synthetic"}) == {"version": 1}
        assert captured[0].headers["authorization"] == "Bearer secret-token"
        assert json.loads(captured[0].content)["expected_version"] == 0
        with pytest.raises(ClientError) as error:
            client.get("private")
        assert str(error.value) == "Daymark request failed (500)"


def test_cli_help_and_backup_restore(tmp_path, capsys):
    import sqlite3

    from daymark.cli import parser

    assert "serve" in parser().format_help()
    source, backup, restored = (
        tmp_path / name for name in ("source.db", "backup.db", "restored.db")
    )
    with sqlite3.connect(source) as connection:
        connection.execute("CREATE TABLE synthetic (value TEXT)")
        connection.execute("INSERT INTO synthetic VALUES ('test')")
    assert main(["--db", str(source), "backup", "--file", str(backup)]) == 0
    assert main(["--db", str(restored), "restore", "--file", str(backup)]) == 0
    with sqlite3.connect(restored) as connection:
        assert connection.execute("SELECT value FROM synthetic").fetchone() == ("test",)
    assert main(["--db", str(restored), "restore", "--file", str(backup)]) == 1
    assert "test" not in capsys.readouterr().err


def test_deep_or_malformed_json_redacted(boundary):
    _, client = boundary
    response = client.post(
        "/v1/operations",
        headers={**auth(), "content-type": "application/json"},
        content=b'{"private":' + b"[" * 2000 + b"0" + b"]" * 2000 + b"}",
    )
    assert response.status_code in {400, 422}
    assert "private" not in response.text
    response = client.post(
        "/v1/operations",
        headers={**auth(), "content-type": "application/json"},
        content=b'{"private": invalid}',
    )
    assert response.status_code == 422
    assert "private" not in response.text


def test_import_has_write_identity_and_is_workspace_bound(boundary):
    engine, client = boundary
    snapshot = {"schema_version": 1, "entities": [], "events": []}
    response = client.post(
        "/v1/import",
        headers=auth(),
        json={"snapshot": snapshot, "operation_id": "import-1", "expected_version": 0},
    )
    assert response.status_code == 200
    assert engine.calls == [
        (("one", snapshot), {"operation_id": "import-1", "expected_version": 0})
    ]


def test_client_does_not_forward_bearer_on_redirect():
    called = []

    def handler(request):
        called.append(request)
        return httpx.Response(302, headers={"location": "https://foreign.invalid/secret"})

    with Client(
        "https://synthetic.invalid", "secret", transport=httpx.MockTransport(handler)
    ) as client:
        with pytest.raises(ClientError) as error:
            client.health()
        assert error.value.status_code == 302
        assert len(called) == 1


def test_real_engine_replay_conflict_unknown_fields_and_workspace(tmp_path):
    from daymark.store import Store

    store = Store(tmp_path / "api.db")
    with TestClient(create_app(store, {"secret-token": Identity("one", "actor")})) as client:
        body = operation(payload={"id": "task", "kind": "task", "title": "synthetic"})
        first = client.post("/v1/operations", headers=auth(), json=body)
        assert first.status_code == 200
        assert client.post("/v1/operations", headers=auth(), json=body).json() == first.json()
        conflict = client.post(
            "/v1/operations",
            headers=auth(),
            json={**body, "payload": {**body["payload"], "title": "private title"}},
        )
        assert conflict.status_code == 409 and "private title" not in conflict.text
        bad = client.post(
            "/v1/operations",
            headers=auth(),
            json=operation(
                operation_id="bad",
                payload={"id": "bad", "kind": "task", "surprise": "private title"},
            ),
        )
        assert bad.status_code == 422 and "private title" not in bad.text
        foreign = client.post(
            "/v1/operations",
            headers=auth(),
            json=operation(
                operation_id="foreign", payload={"id": "bad", "kind": "task", "workspace": "two"}
            ),
        )
        assert foreign.status_code in {403, 422}
        assert len(store.list("one")) == 1


def test_real_http_reads_exports_and_errors_do_not_cross_workspace(tmp_path):
    from daymark.store import Store

    store = Store(tmp_path / "isolation.db")
    store.execute(
        "one",
        "create-visible",
        "create",
        {"id": "visible", "kind": "task", "title": "visible"},
        expected_version=0,
    )
    store.execute(
        "two",
        "create-hidden",
        "create",
        {"id": "hidden", "kind": "task", "title": "foreign-private-canary"},
        expected_version=0,
    )
    with TestClient(create_app(store, {"secret-token": Identity("one", "actor")})) as client:
        for path in ("/v1/entities", "/v1/events", "/v1/export", "/v1/health"):
            response = client.get(path, headers=auth())
            assert response.status_code == 200
            assert "foreign-private-canary" not in response.text
        assert client.get("/v1/entities/hidden", headers=auth()).status_code == 404
        invalid = client.post(
            "/v1/query",
            headers=auth(),
            json={"filters": {"op": "raw_sql", "value": "foreign-private-canary"}},
        )
        assert invalid.status_code == 422 and "foreign-private-canary" not in invalid.text
        query = client.post(
            "/v1/query",
            headers=auth(),
            json={"filters": {"field": "title", "op": "eq", "value": "visible"}},
        )
        assert query.status_code == 200
        assert [item["id"] for item in query.json()] == ["visible"]


def test_python_client_against_real_router_and_store(tmp_path):
    from daymark.store import Store

    store = Store(tmp_path / "client.db")
    with TestClient(create_app(store, {"secret-token": Identity("one", "actor")})) as app_client:

        def send(request):
            response = app_client.request(
                request.method,
                request.url.raw_path.decode(),
                headers=dict(request.headers),
                content=request.content,
            )
            return httpx.Response(
                response.status_code, content=response.content, headers=dict(response.headers)
            )

        with Client(
            "https://synthetic.invalid", "secret-token", transport=httpx.MockTransport(send)
        ) as client:
            created = client.create("sdk-create", {"id": "sdk", "kind": "task", "title": "first"})
            updated = client.update(
                "sdk-update", {"id": "sdk", "title": "second"}, created["version"]
            )
            done = client.action("sdk-complete", "complete", "sdk", updated["version"])
            assert client.get("sdk") == done
            assert done["status"] == "completed"
            assert client.export()["entities"][0]["id"] == "sdk"
            assert client.events()


def test_cli_real_create_update_action_export_import(tmp_path, capsys):
    source, restored, payload, snapshot = (
        tmp_path / name for name in ("cli.db", "restored.db", "payload.json", "snapshot.json")
    )
    base = ["--db", str(source), "--workspace", "synthetic"]
    payload.write_text(json.dumps({"id": "cli-task", "kind": "task", "title": "first"}))
    assert main([*base, "create", "--operation-id", "cli-create", "--file", str(payload)]) == 0
    created = json.loads(capsys.readouterr().out)
    payload.write_text(json.dumps({"id": "cli-task", "title": "second"}))
    assert (
        main(
            [
                *base,
                "update",
                "--operation-id",
                "cli-update",
                "--expected-version",
                str(created["version"]),
                "--file",
                str(payload),
            ]
        )
        == 0
    )
    updated = json.loads(capsys.readouterr().out)
    payload.write_text(json.dumps({"id": "cli-task"}))
    assert (
        main(
            [
                *base,
                "action",
                "complete",
                "--operation-id",
                "cli-complete",
                "--expected-version",
                str(updated["version"]),
                "--file",
                str(payload),
            ]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out)["status"] == "completed"
    assert main([*base, "export", "--output", str(snapshot)]) == 0
    assert (
        main(
            [
                "--db",
                str(restored),
                "--workspace",
                "synthetic",
                "import",
                "--file",
                str(snapshot),
                "--operation-id",
                "cli-restore",
                "--expected-version",
                "0",
            ]
        )
        == 0
    )
    capsys.readouterr()
    assert main(["--db", str(restored), "--workspace", "synthetic", "get", "cli-task"]) == 0
    assert json.loads(capsys.readouterr().out)["title"] == "second"


def test_http_health_outbox_counts_are_workspace_scoped(tmp_path):
    from datetime import UTC, datetime

    from daymark.store import Store

    store = Store(tmp_path / "health-isolation.db")
    with TestClient(create_app(store, {"secret-token": Identity("one", "actor")})) as client:
        baseline = client.get("/v1/health", headers=auth())
        assert baseline.status_code == 200
        store.execute(
            "two",
            "foreign-person",
            "create",
            {"id": "foreign-person", "kind": "person"},
            expected_version=0,
        )
        store.execute(
            "two",
            "foreign-reminder",
            "create",
            {
                "id": "foreign-reminder",
                "kind": "reminder",
                "recipient_id": "foreign-person",
                "scheduled_at": datetime.now(UTC).isoformat(),
            },
            expected_version=0,
        )
        later = client.get("/v1/health", headers=auth())
        assert later.status_code == 200
        assert later.json()["outbox"] == baseline.json()["outbox"]


def test_unexpected_engine_failure_is_redacted_server_error():
    with TestClient(create_app(object(), {"secret-token": Identity("one", "actor")})) as client:
        response = client.get("/v1/health", headers=auth())
        assert response.status_code == 500
        assert response.json() == {"detail": "Engine failure"}


def test_duplicate_authorization_headers_are_rejected(boundary):
    _, client = boundary
    response = client.get(
        "/v1/health",
        headers=[("Authorization", "Bearer secret-token"), ("Authorization", "Bearer other-token")],
    )
    assert response.status_code == 401


def test_cli_serve_and_client_live_loopback(tmp_path):
    import os
    import queue
    import re
    import subprocess
    import sys
    import threading
    import time
    from pathlib import Path

    credentials = tmp_path / "auth.json"
    credentials.write_text(
        json.dumps({"synthetic-live-token": {"workspace": "synthetic", "principal": "operator"}})
    )
    environment = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src")}
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "daymark.cli",
            "--db",
            str(tmp_path / "live.db"),
            "serve",
            "--auth-file",
            str(credentials),
            "--port",
            "0",
        ],
        env=environment,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    messages, logs = queue.Queue(), []

    def read_logs():
        for line in process.stderr:
            logs.append(line)
            messages.put(line)

    reader = threading.Thread(target=read_logs, daemon=True)
    reader.start()
    try:
        deadline, url = time.monotonic() + 15, None
        while url is None:
            remaining = deadline - time.monotonic()
            assert remaining > 0, "Loopback server did not become ready"
            line = messages.get(timeout=remaining)
            match = re.search(r"http://127\.0\.0\.1:(\d+)", line)
            if match:
                url = match.group(0)
        assert httpx.get(f"{url}/v1/schema").status_code == 401
        with Client(url, "synthetic-live-token") as client:
            created = client.create(
                "live-create",
                {"id": "live-canary-id", "kind": "task", "title": "live-private-canary"},
            )
            assert client.get("live-canary-id") == created
            assert client.schema()["security"] == [{"BearerAuth": []}]
            assert client.health()["status"] == "ok"
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        reader.join(timeout=2)
        if process.stderr:
            process.stderr.close()
    assert not any(
        "synthetic-live-token" in line or "live-private-canary" in line or "live-canary-id" in line
        for line in logs
    )
