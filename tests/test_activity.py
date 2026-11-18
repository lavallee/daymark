"""Compact event pages let a new session recover observed changes."""

import json

import pytest
from fastapi.testclient import TestClient

from daymark import DomainError, Store, Tasks
from daymark.api import create_app
from daymark.cli import main
from daymark.mcp import call
from daymark.render import activity_text


@pytest.fixture
def store():
    db = Store(":memory:")
    try:
        yield db
    finally:
        db.close()


def test_tail_and_cursor_pages_are_chronological_and_workspace_local(store):
    work = Tasks(store, "sample", actor="writer")
    other = Tasks(store, "separate")
    assert work.activity() == {"events": [], "cursor": 0, "head": 0}
    work.add("Check proofs", id="proofs")
    other.add("Other task", id="elsewhere")
    work.note("proofs", "Checked tables")
    work.edit("proofs", title="Check index")
    work.done("proofs", note="Index confirmed")
    all_events = work.activity(after=0)["events"]
    assert [e["action"] for e in all_events] == ["create", "note", "update", "note", "complete"]
    assert all(e["actor"] == "writer" and e["id"] == "proofs" for e in all_events)
    assert all_events[2]["changed"] == ["title"]
    assert all_events[3]["note"]["text"] == "Index confirmed"
    assert work.activity(limit=2) == {"events": all_events[-2:], "cursor": 5, "head": 5}
    page = work.activity(after=1, limit=2)
    assert page == {"events": all_events[1:3], "cursor": 3, "head": 5}
    assert work.activity(after=page["cursor"], limit=2)["events"] == all_events[-2:]
    assert work.activity(after=5) == {"events": [], "cursor": 5, "head": 5}
    with pytest.raises(DomainError, match="ahead"):
        work.activity(after=6)


def test_tail_respects_sparse_event_cursors_and_survives_deleted_entity(store):
    admin = Tasks(store, "sample", actor="operator")
    task = admin.add("Old sample", id="old")
    store.execute("sample", "delete-old", "delete", {"id": "old"}, expected_version=task["version"])
    admin.add("New sample", id="new")
    snapshot = store.export("sample")
    for event, seq in zip(snapshot["events"], (7, 50, 99), strict=True):
        event["seq"] = seq
    restored = Store(":memory:")
    try:
        restored.import_snapshot("sample", snapshot, operation_id="restore", expected_version=0)
        page = restored.activity("sample", limit=2)
        assert [e["seq"] for e in page["events"]] == [50, 99]
        assert page["events"][0]["title"] == "Old sample"
        assert [e["seq"] for e in restored.activity("sample", after=7)["events"]] == [50, 99]
    finally:
        restored.close()


def test_activity_previews_notes_without_repeating_history(store):
    work = Tasks(store, "sample")
    work.add("Proof attachment", id="proofs", note="Original note")
    note = "Checked with\nattached sample. " * 100
    work.note("proofs", note)
    page = work.activity(after=1)
    assert page["events"][0]["note"]["text"] == note
    text = activity_text(page)
    assert "Original note" not in text and "Checked with attached" in text
    assert text.endswith("…\ncursor 2") and len(text) < 400
    assert len(json.dumps(page)) < len(json.dumps(store.events("sample", after=1)))


@pytest.mark.parametrize("options", [{"after": -1}, {"after": True}, {"limit": 0}, {"limit": 101}])
def test_invalid_activity_parameters_are_refused(store, options):
    with pytest.raises(DomainError) as error:
        store.activity("sample", **options)
    assert error.value.code == "invalid"


def test_http_mcp_cli_expose_the_same_page(store, tmp_path, capsys):
    tasks = Tasks(store, "sample")
    tasks.add("Proof attachment", id="proofs")
    tasks.note("proofs", "Index checked")
    with TestClient(
        create_app(store, {"test-only": {"workspace": "sample", "principal": "reader"}})
    ) as http:
        response = http.get("/v1/activity?after=1", headers={"Authorization": "Bearer test-only"})
        assert response.status_code == 200 and response.json() == tasks.activity(after=1)
        assert http.get("/v1/activity").status_code == 401
    assert call(tasks, "daymark_view", {"view": "activity", "after": 1}) == activity_text(
        response.json()
    )
    db = str(tmp_path / "sample.sqlite")
    assert main(["--db", db, "add", "Proof attachment", "--id", "proofs"]) == 0
    capsys.readouterr()
    assert main(["--db", db, "activity", "--limit", "1", "--json"]) == 0
    page = json.loads(capsys.readouterr().out)
    assert page["events"][0]["id"] == "proofs" and page["cursor"] == page["head"] == 1


def test_sql_event_projection_matches_legacy_reader_and_keeps_real_changed_fields():
    import sqlite3

    class CompatibilityConnection(sqlite3.Connection):
        without_json = False

        def execute(self, sql, *args):
            if self.without_json and "json_extract(" in sql:
                raise sqlite3.OperationalError("no such function: json_extract")
            return super().execute(sql, *args)

    conn = sqlite3.connect(":memory:", factory=CompatibilityConnection)
    try:
        store = Store(conn)
        tasks = Tasks(store, actor="writer")
        tasks.add("Proof document", id="x", description="same" * 10000)
        tasks.edit("x", title="Second title", description="amended", minutes=15)
        tasks.note("x", "Preview\x00 detail")
        tasks.claim("x")
        tasks.done("x", note="Checked")
        expected = tasks.activity(after=0)
        assert set(expected["events"][1]["changed"]) == {"title", "description", "estimate_minutes"}
        assert expected["events"][2]["note"]["text"] == "Preview\x00 detail"
        conn.without_json = True
        assert tasks.activity(after=0) == expected
    finally:
        conn.close()
