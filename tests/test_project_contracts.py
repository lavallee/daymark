"""Contracts around projects that an application relies on: the change feed, the
cost of listing projects, batch idempotency, plan-date writes and schema guards.
All records are synthetic.
"""

import json
import sqlite3
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ConfigDict, ValidationError, create_model

from daymark import DomainError, Store, Tasks, plans
from daymark.models import Entity
from daymark.store import SCHEMA

NOW = datetime(2030, 4, 3, 15, 0, tzinfo=UTC)
ZONE = "Europe/London"
FIXTURE = Path(__file__).parent / "fixtures" / "daymark-0.3.0.sql"


def code(expected, call, *args, **kwargs):
    with pytest.raises(DomainError) as error:
        call(*args, **kwargs)
    assert error.value.code == expected
    return error.value


@pytest.fixture
def tasks():
    store = Store(":memory:")
    yield Tasks(store, "w", actor="alice", timezone=ZONE, now=NOW)
    store.close()


# -- the change feed sees what the plan shows


def test_changes_follow_dependencies_outside_the_project_and_its_goal(tasks):
    store = tasks.engine
    tasks.goal("Ship it", id="g")
    tasks.project("Site", id="site", goal="g")
    tasks.add("Elsewhere", id="ext")
    tasks.add("Unrelated", id="other")
    tasks.add("Copy", id="copy", project="site", after="ext")
    cursor = store.head("w")
    tasks.note("other", "Nothing to do with the site")
    assert store.changes("w", "site", cursor)["changed"] is False
    tasks.done("ext")  # The plan's blocked_by for copy changes.
    assert store.changes("w", "site", cursor)["changed"] is True
    assert tasks.plan("site")["tasks"][0]["plan"]["blocked_by"] == []
    cursor = store.head("w")
    tasks.edit("g", title="Ship it well")
    assert store.changes("w", "site", cursor)["changed"] is True
    assert "ext" in {e["entity_id"] for e in store.events("w", project="site")}


# -- listing projects is one pass


def test_projects_reads_a_large_workspace_in_one_pass():
    store = Store(":memory:")
    people = [{"id": f"p{i}", "kind": "person", "title": f"Person {i}"} for i in range(50)]
    projects = [{"id": f"pr{i}", "kind": "project", "title": f"Project {i}"} for i in range(101)]
    work = [
        {"id": f"t{i}", "kind": "task", "title": f"Task {i}", "project_id": f"pr{i % 101}",
         "assignee_id": f"p{i % 50}", "depends_on": [f"t{i - 1}"] if i % 7 else []}
        for i in range(20_000)
    ]  # fmt: skip
    for batch in range(0, len(people + projects + work), 100):
        chunk = (people + projects + work)[batch : batch + 100]
        result = store.batch("w", [
            {"operation_id": f"c-{item['id']}", "action": "create", "payload": item,
             "expected_version": 0}
            for item in chunk
        ])  # fmt: skip
        assert result["committed"]
    started = time.perf_counter()
    listed = store.projects("w", now=NOW)
    elapsed = time.perf_counter() - started
    assert len(listed["projects"]) == 101
    assert sum(p["rollup"]["total"] for p in listed["projects"]) == 20_000
    assert elapsed < 4, elapsed  # Phase-one code took about 6.7 s; this is a generous bound.
    store.close()


# -- batch


def test_batch_refuses_malformed_operation_ids_cleanly(tasks):
    store = tasks.engine
    for bad in ([], {"x": 1}, 7, None):
        code("invalid", store.batch, "w", [
            {"operation_id": bad, "action": "create", "payload": {"kind": "task"},
             "expected_version": 0}])  # fmt: skip


def test_a_batch_request_id_replays_the_request_and_refuses_another(tasks):
    store = tasks.engine
    ops = [{"operation_id": "r:1", "action": "create",
            "payload": {"id": "a", "kind": "task", "title": "A"}, "expected_version": 0}]  # fmt: skip
    request = {"what": "create A"}
    first = store.batch("w", ops, request_id="r", request=request)
    head = store.head("w")
    again = store.batch("w", ops, request_id="r", request=request)
    assert again == {**first, "replayed": True} and store.head("w") == head
    # Operations derived differently on a retry still replay, by the request.
    other_ops = [{**ops[0], "payload": {**ops[0]["payload"], "title": "Changed"}}]
    assert store.batch("w", other_ops, request_id="r", request=request)["replayed"]
    code("conflict", store.batch, "w", ops, request_id="r", request={"what": "else"})
    # A failed atomic batch records nothing, so its retry runs again.
    bad = [{"operation_id": "s:1", "action": "update", "payload": {"id": "a", "title": "x"},
            "expected_version": 9}]  # fmt: skip
    assert store.batch("w", bad, request_id="s")["committed"] is False
    assert store.receipt("w", "s") is None
    code("invalid", store.batch, "w", ops, request_id="r:1")


# -- plan dates


def test_schedule_maps_plan_dates_and_keeps_precision():
    timed = {"at": "2030-05-01T17:30:00+01:00", "date": None, "timezone": ZONE, "expression": None}
    task = {"planned": None, "earliest_start": {"date": "2030-04-15", "at": None,
            "timezone": ZONE, "expression": None}, "latest_target": None, "deadline": timed}  # fmt: skip
    assert plans.schedule(task, deadline="2030-05-01") == {}  # Same day: the time stays.
    moved = plans.schedule(task, deadline="2030-05-03")["deadline"]
    assert moved["at"].startswith("2030-05-03T17:30") and moved["timezone"] == ZONE
    assert plans.schedule(task, end="2030-05-20") == {
        "latest_target": {"date": "2030-05-20", "at": None, "timezone": ZONE, "expression": None}
    }
    assert (
        plans.schedule(task, start="2030-04-20", end="2030-04-25")["planned"]["date"]
        == "2030-04-20"
    )
    error = code("invalid", plans.schedule, task, end="2030-04-10")
    assert "start is after the end" in str(error)
    # A title-only edit of such a task never reaches a date check.
    late = {**task, "planned": {"date": "2030-06-01", "at": None, "timezone": ZONE,
                                "expression": None}}  # fmt: skip
    assert plans.schedule(late) == {}
    code("invalid", plans.schedule, task, start="05/01/2030")
    code("invalid", plans.schedule, task, finish="2030-05-01")
    assert plans.schedule(None, start="2030-04-01", timezone="UTC")["planned"]["timezone"] == "UTC"
    assert plans.schedule(task, deadline=None) == {"deadline": None}


def test_schedule_verb_never_moves_a_deadline_by_moving_a_plan(tasks):
    tasks.project("Site", id="site")
    tasks.add("Copy", id="copy", project="site", due="2030-05-01 17:30", plan="2030-04-20")
    item = tasks.schedule("copy", start="2030-04-22", end="2030-04-28")
    assert item["deadline"]["at"].startswith("2030-05-01T17:30")
    assert plans.plan_dates(item) == {"start": "2030-04-22", "end": "2030-04-28",
                                      "deadline": "2030-05-01"}  # fmt: skip


# -- schema guards: no silent upgrade from a read, and never a downgrade


def old_database(path):
    db = sqlite3.connect(path)
    db.executescript(FIXTURE.read_text())
    db.commit()
    return db


def test_a_read_only_connection_on_an_old_schema_gets_a_clear_refusal(tmp_path):
    path = tmp_path / "old.db"
    old_database(path).close()
    reader = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    error = code("conflict", Store, reader)
    assert "read-only" in str(error)
    reader.close()
    error = code("conflict", Store, str(path), upgrade=False)
    assert "0.4.0" in str(error)
    check = sqlite3.connect(path)
    assert check.execute("SELECT value FROM daymark_scheduler WHERE key='schema'").fetchone() == (
        "4",
    )
    check.close()


def test_reads_refuse_a_schema_a_caller_has_not_upgraded(tmp_path):
    connection = old_database(tmp_path / "old.db")
    store = Store(connection)
    connection.rollback()  # The caller discards the upgrade made at open.
    for read in (
        lambda: store.plan("home", "garden"),
        lambda: store.projects("home"),
        lambda: store.changes("home", "garden", 0),
        lambda: store.goals("home"),
        lambda: store.tree("home", "garden"),
        lambda: store.export("home"),
    ):
        code("conflict", read)
    assert connection.execute(
        "SELECT value FROM daymark_scheduler WHERE key='schema'"
    ).fetchone() == ("4",)  # No read upgraded it.
    connection.close()


def test_a_newer_schema_is_never_downgraded(tmp_path):
    path = tmp_path / "new.db"
    Store(path).close()
    db = sqlite3.connect(path)
    db.execute("UPDATE daymark_scheduler SET value=? WHERE key='schema'", (str(int(SCHEMA) + 1),))
    db.commit()
    db.close()
    error = code("conflict", Store, path)
    assert "newer Daymark" in str(error)


def test_release_0_3_0_cannot_read_an_upgraded_file(tmp_path):
    """The upgrade is one-way. 0.3.0's entity model forbids unknown fields; rebuild
    that model from this one minus the fields 0.4.0 added and show it refuses."""
    path = tmp_path / "old.db"
    old_database(path).close()
    Store(path).close()
    added = {"stages", "stage", "rank", "milestone", "managed_by", "milestone_id", "types",
             "fields", "type", "values", "measure"}  # fmt: skip
    old_model = create_model(
        "Entity030",
        __config__=ConfigDict(extra="forbid"),
        **{
            name: (info.annotation, info)
            for name, info in Entity.model_fields.items()
            if name not in added
        },
    )
    db = sqlite3.connect(path)
    stored = json.loads(db.execute("SELECT data FROM daymark_entities LIMIT 1").fetchone()[0])
    db.close()
    with pytest.raises(ValidationError) as error:
        old_model.model_validate(stored)
    assert {e["type"] for e in error.value.errors()} == {"extra_forbidden"}
