"""The startup page, counts, memories and event cursor describe one snapshot."""

import json
import sqlite3
from contextlib import closing
from datetime import UTC, datetime

import httpx
import pytest
from fastapi.testclient import TestClient

from daymark import Store, Tasks, views
from daymark.api import create_app
from daymark.client import Client, ClientError
from daymark.render import brief_session


def test_snapshot_keeps_work_memories_and_activity_together_across_a_writer(tmp_path, monkeypatch):
    path = tmp_path / "work.db"
    with closing(Store(path)) as writer, closing(Store(path, read_only=True)) as reader:
        sender, returning = Tasks(writer, "proofs"), Tasks(reader, "proofs")
        sender.add("Collect proof", id="collect")
        first = sender.remember("Use a short legend", scope="print")
        head = writer.head("proofs")
        before = writer.db.total_changes
        assert (
            returning.orientation()["activity_cursor"] == head and writer.db.total_changes == before
        )
        memory_read = views._orientation_memories

        def concurrent_write(store, workspace, scope=None):
            sender.done("collect")
            sender.edit(first["id"], title="Use a complete legend")
            return memory_read(store, workspace, scope)

        with monkeypatch.context() as patch:
            patch.setattr(views, "_orientation_memories", concurrent_write)
            old = returning.orientation()
        new = returning.orientation()
        assert old["summary"]["ready"] == len(old["ready"]) == 1
        assert old["memories"][0]["title"] == "Use a short legend"
        assert old["activity_cursor"] == head
        assert [event["action"] for event in returning.activity(after=head)["events"]] == [
            "complete",
            "update",
        ]
        assert new["summary"]["ready"] == len(new["ready"]) == 0
        assert new["memories"][0]["title"] == "Use a complete legend"
        assert new["activity_cursor"] > head


@pytest.mark.parametrize("json_sql", [True, False])
def test_orientation_agrees_with_dated_views_and_keeps_empty_store_read_only(json_sql):
    class Connection(sqlite3.Connection):
        def execute(self, statement, *args):
            if not json_sql and "json_extract(" in statement:
                raise sqlite3.OperationalError("no such function: json_extract")
            return super().execute(statement, *args)

    instant = datetime(2030, 4, 7, 18, tzinfo=UTC)
    with closing(sqlite3.connect(":memory:", factory=Connection)) as connection:
        store = Store(connection)
        worker = Tasks(store, "sample", actor="reader", now=instant, timezone="UTC")
        for i in range(12):
            worker.add(
                "Review sample", id=f"item-{i}", due=f"+{i}d", plan="today" if i % 2 else None
            )
            worker.add("📖" * 159 + "\x00" + "t" * 15, kind="memory", id=f"m-{i:02}", scope="print")
        worker.wait("item-0", "a reply")
        worker.focus("item-10")
        worker.propose("Check another sample")
        snapshot = worker.orientation()
        assert snapshot["ready"] == worker.ready_summary(limit=5)
        assert snapshot["summary"] == worker.summary()
        assert snapshot["next_memory"] == "m-04"
        assert snapshot["memories"][0]["title"] == "📖" * 159 + "\x00…"
        assert all(item["scope"] == "print" for item in snapshot["memories"])
        connection.rollback()  # The caller's rollback includes creating all schema tables.
        empty = store.orientation("sample", now=instant)
        stamp = empty.pop("activity_checkpoint")
        assert store.activity("sample", after=stamp)["checkpoint"] == stamp
        assert empty == {
            "summary": dict.fromkeys(
                ("ready", "overdue", "today", "next_7_days", "waiting", "proposed"), 0
            ),
            "ready": [],
            "memories": [],
            "next_memory": None,
            "activity_cursor": 0,
            "as_of": instant.isoformat(),
        }
        assert not connection.in_transaction
        assert not connection.execute("SELECT name FROM sqlite_master").fetchall()


def test_authenticated_wire_orientation_is_one_bounded_read_with_known_identity():
    with closing(Store(":memory:")) as store:
        worker = Tasks(store, "proofs", actor="real-worker")
        other_worker = Tasks(store, "proofs", actor="claimed-elsewhere")
        worker.add("Proof", id="mine", description="large" * 10000, note="n" * 8000)
        worker.claim("mine")
        other_worker.add("Second", id="theirs")
        other_worker.claim("theirs")
        for i in range(8):
            worker.add("📖" * 2000, kind="memory", id=f"fact-{i}", scope="print", note="n" * 8000)
        other = Tasks(store, "other")
        other.add("Hidden", id="private")
        service = create_app(store, {"sample": {"workspace": "proofs", "principal": "real-worker"}})
        with TestClient(service) as app:
            paths = []

            def forward(request):
                paths.append(request.url.path)
                response = app.request(
                    request.method, request.url.raw_path.decode(), headers=request.headers
                )
                assert len(response.content) < 7000 and b"large" not in response.content
                return httpx.Response(response.status_code, content=response.content)

            with Client(
                "https://sample.invalid",
                "sample",
                transport=httpx.MockTransport(forward),
                read_only=True,
            ) as client:
                tasks = Tasks(client, actor="wrong-local-name")
                output = brief_session(tasks, {"endpoint": "https://sample.invalid"})
                assert (
                    output["context"]["actor"] == "real-worker"
                    and output["context"]["workspace"] == "proofs"
                )
                assert [row["id"] for row in output["ready"]] == ["mine"]
                assert output["summary"]["ready"] == 1
                assert output["memories"][0]["title"] == "📖" * 160 + "…"
                assert output["next_memory"] == "fact-4" and paths == ["/v1/orientation"]
                assert output["activity_cursor"] == store.head("proofs")


def test_older_service_retains_preview_shape_and_fails_closed_on_denied_orientation():
    instant = "2030-04-01T18:00:00+00:00"
    calls = []
    sample = {"id": "fact", "title": "x" * 2000, "scope": "print"}

    def legacy(request):
        calls.append(request.url.path)
        if request.url.path == "/v1/orientation":
            return httpx.Response(404)
        if request.url.path == "/v1/search":
            assert json.loads(request.content) == {"kind": "memory", "limit": 5}
            return httpx.Response(200, json={"items": [sample], "next": "fact"})
        assert request.url.params["now"] == instant
        if request.url.path == "/v1/summary":
            return httpx.Response(200, json={"ready": 0})
        assert request.url.path == "/v1/ready" and request.url.params["mine"] == "true"
        return httpx.Response(200, json=[])

    with Client(
        "https://sample.invalid", "sample", transport=httpx.MockTransport(legacy)
    ) as client:
        for _ in range(2):
            data = Tasks(client, now=instant).orientation()
            assert data == {
                "summary": {"ready": 0},
                "ready": [],
                "memories": [{"id": "fact", "title": "x" * 160 + "…", "scope": "print"}],
                "next_memory": "fact",
            }
        assert calls.count("/v1/orientation") == 1
    with Client(
        "https://sample.invalid",
        "sample",
        transport=httpx.MockTransport(lambda _: httpx.Response(403)),
    ) as client:
        with pytest.raises(ClientError) as exc:
            client.orientation()
        assert exc.value.status_code == 403
