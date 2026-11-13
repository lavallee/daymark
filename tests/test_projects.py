"""Managing a project: stages, manual order, milestones, the plan read, the project
change feed and batch writes, through the engine, the plain verbs, the CLI, MCP
and HTTP. All records are synthetic.
"""

import json
import random
import sqlite3
import threading
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from daymark import DomainError, Store, Tasks, plans
from daymark.api import create_app
from daymark.cli import main
from daymark.client import Client
from daymark.mcp import TOOLS, handle
from daymark.plans import rank_between, valid_rank
from daymark.render import compact, line
from daymark.store import canonical

NOW = datetime(2030, 4, 3, 15, 0, tzinfo=UTC)  # A Wednesday.
ZONE = "Europe/London"
BOARD = "todo:To do:open:grey,doing:Doing:open:blue,done:Done:completed:green"
FIXTURE = Path(__file__).parent / "fixtures" / "daymark-0.3.0.sql"
NEW_FIELDS = {
    "stages": [], "stage": None, "rank": None, "milestone": False, "managed_by": None,
    "milestone_id": None, "types": [], "fields": [], "type": None, "values": {}, "measure": None,
}  # fmt: skip


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


def board(tasks, project="site"):
    """{stage: [ids in order]} as the plan reads it."""
    view = tasks.plan(project)
    out = {s["id"]: [] for s in view["stages"]}
    for card in view["tasks"]:
        out[card["plan"]["stage"]].append(card["id"])
    return out


def order(tasks, project="site"):
    return [card["id"] for card in tasks.plan(project)["tasks"]]


def site(tasks, stages=BOARD, *ids):
    tasks.project("Website relaunch", id="site", stages=stages)
    for id in ids:
        tasks.add(id.title(), id=id, project="site")


# -- stages and status


def test_stage_definitions_are_validated(tasks):
    code("invalid", tasks.project, "No done", stages="a:A:open")
    code("invalid", tasks.project, "No open", stages="a:A:completed")
    code("invalid", tasks.project, "Twice", stages="a:A:open,a:B:completed")
    code("invalid", tasks.project, "Bad colour", stages="a:A:open:pink,b:B:completed")
    code("invalid", tasks.project, "Bad status", stages="a:A:proposed,b:B:completed")
    code("invalid", tasks.project, "Bad slug", stages="A b:A:open,b:B:completed")
    code("invalid", tasks.project, "Too long", stages="a:A:open:grey:extra")
    code("invalid", tasks.add, "Not a project", stages=[{"id": "a", "label": "A",
                                                          "status": "open"}])  # fmt: skip
    code("invalid", tasks.add, "No project", stage="todo")
    tasks.add("Sam", kind="person", id="sam")
    code("invalid", tasks.edit, "sam", milestone=True)
    code("invalid", tasks.edit, "sam", managed_by="rev")


def test_a_project_without_stages_reads_as_a_board_derived_from_status(tasks):
    site(tasks, None, "a", "b", "c", "d")
    view = tasks.plan("site")
    assert [s["id"] for s in view["stages"]] == ["todo", "waiting", "done"]
    assert view["default_stages"] is True
    tasks.wait("b", "the printer")
    tasks.done("c")
    tasks.cancel("d")
    assert board(tasks) == {"todo": ["a"], "waiting": ["b"], "done": ["c"]}
    assert tasks.plan("site")["rollup"]["off_board"] == 1
    # Nothing stage-related was stored: the board is derived.
    assert all(tasks.get(x)["stage"] is None for x in "abcd")
    tasks.reopen("c")
    tasks.resume("b")
    assert board(tasks)["todo"] == ["a", "b", "c"]


def test_moving_to_a_stage_sets_the_status_through_the_usual_rules(tasks):
    site(tasks, BOARD, "a")
    item = tasks.move("a", stage="doing")
    assert (item["status"], item["stage"]) == ("open", "doing")
    item = tasks.move("a", stage="done")
    assert (item["status"], item["stage"]) == ("completed", "done") and item["completed_at"]
    item = tasks.move("a", stage="todo")
    assert (item["status"], item["completed_at"]) == ("open", None)
    code("invalid", tasks.move, "a", stage="nowhere")
    code("invalid", tasks.edit, "a", stage="done", status="open")
    code("invalid", tasks.move, "a")
    code("invalid", tasks.move, "a", first=True, last=True)


def test_every_lifecycle_verb_keeps_stage_and_status_consistent(tasks):
    site(tasks, BOARD, "a", "b")
    tasks.move("a", stage="doing")
    tasks.wait("a", "the client")  # No waiting stage: a waiting task stays in an open stage.
    assert board(tasks)["doing"] == ["a"] and tasks.get("a")["status"] == "waiting"
    tasks.resume("a")
    assert board(tasks)["doing"] == ["a"]
    tasks.done("a")
    assert board(tasks)["done"] == ["a"]
    tasks.undo("a")  # Back to where it was before it was completed.
    assert board(tasks)["doing"] == ["a"] and tasks.get("a")["status"] == "open"
    tasks.done("a")
    tasks.reopen("a")  # Reopening goes to the first open stage.
    assert board(tasks)["todo"] == ["a", "b"]
    tasks.cancel("b")  # No cancelled stage: off the board.
    assert "b" not in order(tasks)
    tasks.set_stages("site", BOARD + ",dropped:Dropped:cancelled:grey")
    assert board(tasks)["dropped"] == ["b"]
    tasks.reopen("b")
    assert board(tasks)["todo"] == ["a", "b"]
    tasks.set_stages("site", BOARD + ",blocked:Blocked:waiting:red")
    tasks.move("a", stage="doing")
    tasks.wait("a", "the client")  # Now there is a waiting stage, and it goes there.
    assert board(tasks)["blocked"] == ["a"]


def test_proposals_are_not_on_the_board_until_accepted(tasks):
    site(tasks, BOARD)
    tasks.propose("Compare hosts", id="idea", project="site")
    code("invalid", tasks.move, "idea", stage="doing")
    code("invalid", tasks.add, "Proposed in a stage", proposed=True, project="site", stage="todo")
    view = tasks.plan("site")
    assert [x["id"] for x in view["proposed"]] == ["idea"] and view["tasks"] == []
    tasks.accept("idea")
    assert board(tasks)["todo"] == ["idea"]
    tasks.propose("Second idea", id="idea2", project="site")
    tasks.dismiss("idea2")
    assert "idea2" not in order(tasks) and tasks.plan("site")["rollup"]["off_board"] == 1


def test_repeating_rounds_start_in_the_first_open_stage(tasks):
    site(tasks, BOARD)
    tasks.add("Weekly report", id="report", project="site", every="week", plan="2030-04-03")
    tasks.move("report", stage="doing")
    tasks.done("report")
    assert tasks.get("report.1")["stage"] is None
    assert board(tasks)["todo"] == ["report.1"] and board(tasks)["done"] == ["report"]
    tasks.move("report.1", stage="doing")
    tasks.skip("report.1")  # Skipped: cancelled, off the board, and the next round appears.
    assert board(tasks) == {"todo": ["report.2"], "doing": [], "done": ["report"]}


def test_removing_a_stage_moves_its_tasks_home_without_changing_status(tasks):
    site(tasks, BOARD, "a", "b")
    tasks.move("a", stage="doing")
    before = tasks.get("a")["version"]
    tasks.set_stages("site", "todo:To do,done:Done:completed")
    after = tasks.get("a")
    assert (after["stage"], after["status"], after["version"]) == (None, "open", before + 1)
    assert board(tasks) == {"todo": ["a", "b"], "done": []}
    history = [e["action"] for e in tasks.engine.events("w", project="site")]
    assert "stage.reset" in history
    # Undoing the project's change restores its stages; the task stays where it was put.
    tasks.undo("site")
    assert board(tasks)["doing"] == [] and tasks.get("a")["stage"] is None
    tasks.set_stages("site", "")
    assert tasks.plan("site")["default_stages"] is True


def test_a_task_that_changes_project_loses_its_stage_and_goes_last(tasks):
    site(tasks, BOARD, "a", "b", "c")
    tasks.project("Other", id="other", stages="later:Later,fin:Finished:completed")
    tasks.add("Other task", id="o", project="other")
    tasks.move("a", stage="doing", first=True)
    moved = tasks.edit("a", project="other")
    assert moved["stage"] is None and moved["rank"] is not None
    assert order(tasks, "other") == ["o", "a"]
    tasks.edit("c", project="other")
    assert order(tasks, "other") == ["o", "a", "c"]
    moved = tasks.edit("b", project="other", stage="later")
    assert moved["stage"] == "later" and order(tasks, "other")[-1] == "b"
    code("invalid", tasks.edit, "b", project="site", stage="later")
    left = tasks.edit("b", project="")
    assert (left["project_id"], left["stage"], left["rank"]) == (None, None, None)


# -- manual order


def test_rank_keys_stay_ordered_and_short():
    rng = random.Random(7)
    keys = []
    for _ in range(2000):
        i = rng.randint(0, len(keys))
        low = keys[i - 1] if i else None
        high = keys[i] if i < len(keys) else None
        key = rank_between(low, high)
        assert (low is None or low < key) and (high is None or key < high)
        assert valid_rank(key)
        keys.insert(i, key)
    assert keys == sorted(keys) and len(set(keys)) == len(keys)
    # Appending and prepending grow keys logarithmically.
    last = first = None
    for _ in range(20_000):
        last = rank_between(last, None)
        first = rank_between(None, first)
    assert len(last) <= 4 and len(first) <= 5 and valid_rank(last) and valid_rank(first)
    # Inserting at one spot lengthens the fraction slowly: about a character per six.
    low, high = "a0", "a1"
    for _ in range(2000):
        high = rank_between(low, high)
    assert low < high and len(high) < 400 and valid_rank(high)
    # Equal or reversed bounds (damaged data) still give a key after the lower one.
    assert rank_between("a5", "a5") > "a5" and rank_between("a7", "a3") > "a7"
    assert not valid_rank("a50") and not valid_rank("0a") and valid_rank("a5V")


def test_place_writes_only_the_task_that_moves(tasks):
    site(tasks, BOARD, "a", "b", "c")
    assert order(tasks) == ["a", "b", "c"]  # New tasks go last, oldest first.
    versions = {x: tasks.get(x)["version"] for x in "abc"}
    head = tasks.engine.head("w")
    tasks.move("c", first=True)
    assert order(tasks) == ["c", "a", "b"]
    assert {x: tasks.get(x)["version"] for x in "abc"} == {**versions, "c": versions["c"] + 1}
    assert [e["entity_id"] for e in tasks.engine.events("w", after=head)] == ["c"]
    tasks.move("a", after="b")
    assert order(tasks) == ["c", "b", "a"]
    tasks.move("c", before="a")
    assert order(tasks) == ["b", "c", "a"]
    tasks.move("b", last=True)
    assert order(tasks) == ["c", "a", "b"]
    tasks.add("D", id="d", project="site")
    assert order(tasks) == ["c", "a", "b", "d"]
    tasks.move("d", stage="doing", after="c")
    assert order(tasks) == ["c", "d", "a", "b"] and board(tasks)["doing"] == ["d"]
    tasks.move("b", after="c")  # Between a placed task and one never placed.
    assert order(tasks) == ["c", "b", "d", "a"]


def test_rank_changes_only_through_place(tasks):
    site(tasks, BOARD, "a", "b")
    error = code("invalid", tasks.edit, "a", rank="a5")
    assert "order" in str(error)
    code("invalid", tasks.add, "Ranked", project="site", rank="a5")
    code("invalid", tasks.add, "Bad", project="site", stage="todo", values={"x": 1})
    tasks.move("b", first=True)
    assert tasks.edit("b", title="B2")["rank"] == tasks.get("b")["rank"]  # Unchanged is fine.


def test_repeating_rounds_and_archived_tasks_never_duplicate_a_rank(tasks):
    site(tasks, BOARD, "a", "b")
    tasks.add("Weekly", id="weekly", project="site", every="week", plan="2030-04-03",
              is_milestone=True, type=None)  # fmt: skip
    tasks.move("weekly", first=True)
    tasks.done("weekly")
    successor = tasks.get("weekly.1")
    assert successor["rank"] > tasks.get("weekly")["rank"] and successor["milestone"] is True
    assert order(tasks)[:2] == ["weekly", "weekly.1"]
    tasks.archive("a")
    tasks.move("b", after="weekly.1")
    tasks.reopen("a")  # Back to its old place; no key collides with it.
    ranks = [plans.effective_rank(tasks.get(x)) for x in order(tasks)]
    assert len(set(ranks)) == len(ranks) and ranks == sorted(ranks)
    # Damaged data with equal ranks does not stop a placement.
    store = tasks.engine
    raw = json.loads(
        store.db.execute("SELECT data FROM daymark_entities WHERE id='b'").fetchone()[0]
    )
    raw["rank"] = tasks.get("weekly.1")["rank"]
    store.db.execute("UPDATE daymark_entities SET data=? WHERE id='b'", (canonical(raw),))
    tasks.add("C", id="c", project="site")
    moved = tasks.move("c", after="weekly.1")
    assert moved["rank"] > raw["rank"]


def test_place_refuses_neighbours_outside_the_project_or_workspace(tasks):
    site(tasks, BOARD, "a", "b")
    tasks.add("Loose", id="loose")
    code("invalid", tasks.move, "a", after="loose")
    code("invalid", tasks.move, "loose", first=True)
    code("invalid", tasks.move, "a", after="a")
    tasks.add("Shelved", id="shelved", project="site")
    tasks.archive("shelved")
    code("invalid", tasks.move, "a", after="shelved")
    other = Tasks(tasks.engine, "elsewhere", actor="bob", timezone=ZONE, now=NOW)
    other.project("Same name", id="site")
    other.add("Foreign", id="foreign", project="site")
    code("invalid", tasks.move, "a", after="foreign")
    code("invalid", tasks.engine.execute, "w", "x", "place", {"id": "a"}, expected_version=1)
    code("invalid", tasks.engine.execute, "w", "x", "place",
         {"id": "a", "after": "b", "before": "b"}, expected_version=1)  # fmt: skip
    assert order(other) == ["foreign"]


def test_concurrent_places_from_two_writers_both_land(tmp_path):
    path = tmp_path / "plan.db"
    setup = Tasks(Store(path), "w", actor="setup", timezone=ZONE, now=NOW)
    site(setup, BOARD, "anchor", "x", "y", "tail")
    setup.move("anchor", first=True)
    stores = [Store(path), Store(path)]
    barrier, errors = threading.Barrier(2), []

    def place(store, id):
        try:
            barrier.wait()
            Tasks(store, "w", actor=id, timezone=ZONE, now=NOW).move(id, after="anchor")
        except DomainError as exc:  # pragma: no cover - reported below
            errors.append(exc)

    threads = [threading.Thread(target=place, args=(s, i)) for s, i in zip(stores, "xy")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []
    result = order(setup)
    assert result[0] == "anchor" and set(result[1:3]) == {"x", "y"} and result[3] == "tail"
    ranks = [plans.effective_rank(setup.get(x)) for x in result]
    assert ranks == sorted(ranks) and len(set(ranks)) == 4
    # A stale version is refused, not merged.
    current = setup.get("x")["version"]
    code("conflict", setup.engine.execute, "w", "stale", "place",
         {"id": "x", "before": None}, expected_version=current - 1)  # fmt: skip
    for store in [*stores, setup.engine]:
        store.close()


# -- milestones, dates and the plan read


def test_plan_read_resolves_dates_dependencies_people_and_rollups(tasks):
    site(tasks, BOARD)
    tasks.add("Ada", kind="person", id="ada")
    tasks.add("Elsewhere", id="ext")
    tasks.add("Sitemap", id="map", project="site", plan="2030-04-10", target="2030-04-20",
              assignee="Ada", after="ext")  # fmt: skip
    tasks.add("Copy", id="copy", project="site", start="2030-04-15", due="2030-04-01",
              after="map")  # fmt: skip
    tasks.add("Launch", id="launch", project="site", due="2030-05-10", milestone=True,
              after="copy", stage="doing")  # fmt: skip
    tasks.add("Old", id="old", project="site")
    tasks.archive("old")
    view = tasks.plan("site")
    cards = {c["id"]: c for c in view["tasks"]}
    assert list(cards) == ["map", "copy", "launch"]
    assert cards["map"]["plan"] == {
        "stage": "todo", "start": "2030-04-10", "end": "2030-04-20", "deadline": None,
        "done": False, "overdue": False, "blocked_by": ["ext"],
    }  # fmt: skip
    assert cards["map"]["assignee_id"] == "ada"
    assert cards["copy"]["plan"]["start"] == "2030-04-15"  # earliest_start when no plan
    assert cards["copy"]["plan"]["end"] == cards["copy"]["plan"]["deadline"] == "2030-04-01"
    assert cards["copy"]["plan"]["overdue"] is True
    assert cards["launch"]["milestone"] is True
    assert view["dependencies"] == [{"from": "copy", "to": "map"},
                                    {"from": "launch", "to": "copy"}]  # fmt: skip
    assert view["external_dependencies"] == [
        {"id": "ext", "title": "Elsewhere", "project_id": None, "status": "open",
         "dependents": ["map"]}
    ]  # fmt: skip
    assert view["people"] == [{"id": "ada", "kind": "person", "title": "Ada"}]
    assert view["rollup"] == {
        "stages": {"todo": 2, "doing": 1, "done": 0}, "total": 3, "done": 0,
        "percent_done": 0, "earliest_start": "2030-04-10", "latest_end": "2030-05-10",
        "overdue": 1, "milestones": 1, "proposed": 0, "off_board": 0, "estimate_minutes": None,
        "sums": {},
    }  # fmt: skip
    assert view["cursor"] == tasks.engine.head("w")
    tasks.done("map")
    assert tasks.plan("site")["rollup"]["percent_done"] == 33
    listed = tasks.projects()
    assert [p["id"] for p in listed["projects"]] == ["site"]
    assert listed["projects"][0]["rollup"]["done"] == 1
    code("not_found", tasks.plan, "missing")
    code("not_found", tasks.plan, "map")
    # Another workspace sees none of it.
    other = Tasks(tasks.engine, "elsewhere", actor="bob", timezone=ZONE, now=NOW)
    code("not_found", other.plan, "site")
    assert other.projects()["projects"] == []


def test_assigning_by_name_finds_the_person_and_never_invents_one(tasks):
    site(tasks, BOARD)
    tasks.add("Ada Lovelace", kind="person", id="ada")
    tasks.add("Grace", kind="principal", id="grace")
    assert tasks.add("One", project="site", assignee="ada lovelace")["assignee_id"] == "ada"
    assert tasks.add("Two", project="site", assignee="ada")["assignee_id"] == "ada"
    made = tasks.add("Three", project="site", assignee=" GRACE ")
    assert made["assignee_id"] == "grace"
    error = code("invalid", tasks.add, "Four", assignee="Linus")
    assert "--kind person" in error.details[0]
    assert len(tasks.engine.list("w", kind="person")) == 1
    tasks.add("Grace", kind="person", id="grace2")
    assert code("invalid", tasks.add, "Five", assignee="Grace").details == ["grace", "grace2"]
    edited = tasks.edit(made["id"], assignee="Ada Lovelace")
    assert (
        edited["assignee_id"] == "ada"
        and tasks.edit(made["id"], assignee="")["assignee_id"] is None
    )


# -- the change feed


def test_project_changes_and_events_follow_only_that_project(tasks):
    site(tasks, BOARD, "a")
    tasks.project("Other", id="other")
    tasks.add("Elsewhere", id="loose")
    tasks.add("Sam", kind="person", id="sam")
    store = tasks.engine
    cursor = tasks.plan("site")["cursor"]
    assert store.changes("w", "site", cursor) == {"cursor": cursor, "changed": False}
    tasks.note("loose", "Unrelated")
    tasks.add("Other task", id="o", project="other")
    assert store.changes("w", "site", cursor) == {"cursor": cursor + 2, "changed": False}
    for change in (
        lambda: tasks.edit("loose", project="site"),  # enters
        lambda: tasks.edit("loose", project="other"),  # leaves
        lambda: tasks.edit("site", title="Relaunch"),  # the project itself
        lambda: tasks.edit("sam", title="Samuel"),  # a person it can name
        lambda: tasks.move("a", stage="doing"),
    ):
        cursor = store.head("w")
        change()
        assert store.changes("w", "site", cursor)["changed"] is True
    assert store.changes("w", "site", 10**6)["changed"] is True  # A cursor from the future.
    events = store.events("w", project="site")
    assert {e["entity_id"] for e in events} >= {"site", "a", "loose", "sam"}
    assert "o" not in {e["entity_id"] for e in events}
    assert store.events("w", project="site", limit=2) == events[:2]
    code("invalid", store.changes, "w", "site", -1)
    code("invalid", store.events, "w", project="")
    assert store.head("nobody") == 0


# -- batch writes


def batch_ops(*pairs):
    return [
        {"operation_id": op, "action": action, "payload": payload, "expected_version": version}
        for op, action, payload, version in pairs
    ]


def test_atomic_batch_rolls_back_everything_on_one_refusal(tasks):
    store = tasks.engine
    tasks.add("A", id="a")
    head = store.head("w")
    result = store.batch("w", batch_ops(
        ("b1", "update", {"id": "a", "title": "A2"}, 1),
        ("b2", "create", {"id": "new", "kind": "task", "title": "N"}, 0),
        ("b3", "update", {"id": "a", "title": "stale"}, 1),
        ("b4", "complete", {"id": "a"}, 2),
    ), actor="alice")  # fmt: skip
    assert result["committed"] is False
    assert [r["ok"] for r in result["results"]] == [False] * 4
    assert [r["error"]["code"] for r in result["results"]] == [
        "aborted", "aborted", "conflict", "aborted"]  # fmt: skip
    assert store.get("w", "a")["title"] == "A" and store.head("w") == head
    code("not_found", store.get, "w", "new")
    assert store.receipt("w", "b1") is None


def test_partial_batch_keeps_what_succeeded_and_replays_by_operation(tasks):
    store = tasks.engine
    tasks.add("A", id="a")
    ops = batch_ops(
        ("p1", "update", {"id": "a", "title": "A2"}, 1),
        ("p2", "update", {"id": "missing", "title": "x"}, 1),
        ("p3", "complete", {"id": "a"}, 2),
    )
    first = store.batch("w", ops, atomic=False, actor="alice")
    assert first["committed"] is True
    assert [r["ok"] for r in first["results"]] == [True, False, True]
    assert first["results"][1]["error"]["code"] == "not_found"
    head = store.head("w")
    again = store.batch("w", ops, atomic=False, actor="alice")
    assert again["results"][0] == first["results"][0] and again["results"][2] == first["results"][2]
    assert store.head("w") == head  # Replayed, not written again.
    assert [e["actor"] for e in store.events("w")][-2:] == ["alice", "alice"]
    changed = batch_ops(("p1", "update", {"id": "a", "title": "Other"}, 1))
    assert store.batch("w", changed)["results"][0]["error"]["code"] == "conflict"
    code("invalid", store.batch, "w", [])
    code("invalid", store.batch, "w", ops * 2)
    code("invalid", store.batch, "w", [{"operation_id": "x"}])
    code("invalid", store.batch, "w", ops, atomic="yes")


def test_batch_from_two_writers_applies_one_and_refuses_the_other_whole(tmp_path):
    path = tmp_path / "batch.db"
    Tasks(Store(path), "w", timezone=ZONE, now=NOW).add("Shared", id="s")
    stores, barrier, results = [Store(path), Store(path)], threading.Barrier(2), {}

    def run(store, name):
        barrier.wait()
        results[name] = store.batch("w", batch_ops(
            (name + "1", "update", {"id": "s", "title": name}, 1),
            (name + "2", "update", {"id": "s", "description": name}, 2),
        ))  # fmt: skip

    threads = [threading.Thread(target=run, args=(s, n)) for s, n in zip(stores, ("x", "y"))]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    committed = [n for n, r in results.items() if r["committed"]]
    assert len(committed) == 1
    final = stores[0].get("w", "s")
    assert final["title"] == final["description"] == committed[0] and final["version"] == 3
    for store in stores:
        store.close()


def test_batch_respects_a_caller_owned_transaction():
    connection = sqlite3.connect(":memory:")
    store = Store(connection)
    connection.commit()
    connection.execute("BEGIN")
    result = store.batch("w", batch_ops(("c1", "create", {"id": "a", "kind": "task"}, 0)))
    assert result["committed"] is True and connection.in_transaction
    connection.rollback()
    code("not_found", store.get, "w", "a")
    with connection:
        store.batch("w", batch_ops(("c1", "create", {"id": "a", "kind": "task"}, 0)))
    assert store.get("w", "a")["version"] == 1
    failed = store.batch("w", batch_ops(
        ("c2", "update", {"id": "a", "title": "T"}, 1),
        ("c3", "update", {"id": "a", "title": "U"}, 9),
    ))  # fmt: skip
    assert failed["committed"] is False and store.get("w", "a")["title"] == ""
    connection.close()


# -- the schema upgrade from 0.3.0


def old_database(target):
    db = sqlite3.connect(target)
    db.executescript(FIXTURE.read_text())
    db.commit()
    snapshot = {
        "entities": {(r[0], r[1]): (r[2], json.loads(r[3])) for r in db.execute(
            "SELECT workspace,id,version,data FROM daymark_entities")},
        "events": db.execute("SELECT * FROM daymark_events ORDER BY seq").fetchall(),
        "cursors": db.execute("SELECT * FROM daymark_event_cursors ORDER BY seq").fetchall(),
        "operations": db.execute("SELECT * FROM daymark_operations").fetchall(),
    }  # fmt: skip
    assert db.execute("SELECT value FROM daymark_scheduler WHERE key='schema'").fetchone() == ("4",)
    return db, snapshot


def assert_upgraded(store, db, snapshot):
    assert db.execute("SELECT value FROM daymark_scheduler WHERE key='schema'").fetchone() == ("5",)
    for (workspace, id), (version, data) in snapshot["entities"].items():
        now = store.get(workspace, id)
        assert now == {**data, **NEW_FIELDS} and now["version"] == version
    assert db.execute("SELECT * FROM daymark_events ORDER BY seq").fetchall() == snapshot["events"]
    assert (
        db.execute("SELECT * FROM daymark_event_cursors ORDER BY seq").fetchall()
        == snapshot["cursors"]
    )
    assert db.execute("SELECT * FROM daymark_operations").fetchall() == snapshot["operations"]
    # Behaviour for records that use none of the new fields is unchanged.
    tasks = Tasks(store, "home", actor="alice", timezone=ZONE, now=NOW)
    assert [x["id"] for x in tasks.ready()] == ["water.1", "seeds"]  # plant waits on seeds
    assert compact(store.get("home", "seeds")) == {
        "id": "seeds", "kind": "task", "title": "Buy seeds", "status": "open", "version": 1,
        "priority": 2, "assignee_id": "sam", "project_id": "garden", "deadline": "2030-04-10",
    }  # fmt: skip
    assert line(store.get("home", "seeds"), NOW) == "seeds  P2  Buy seeds  [due 2030-04-10]"
    # And the old records work as a plan.
    assert board(tasks, "garden") == {"todo": ["seeds", "plant", "water.1"], "waiting": [],
                                      "done": ["water"]}  # fmt: skip
    tasks.undo("passport")  # Undo reads a 0.3.0 event body.
    assert store.get("home", "passport")["status"] == "open"


def test_a_0_3_0_database_upgrades_in_place_through_a_path(tmp_path):
    path = tmp_path / "old.db"
    db, snapshot = old_database(path)
    db.close()
    store = Store(path)
    db = sqlite3.connect(path)
    assert_upgraded(store, db, snapshot)
    db.close()
    store.close()


def test_a_0_3_0_database_upgrades_inside_a_caller_owned_connection(tmp_path):
    connection, snapshot = old_database(tmp_path / "old.db")
    store = Store(connection)
    connection.rollback()  # The caller can still discard the upgrade.
    assert (
        connection.execute("SELECT value FROM daymark_scheduler WHERE key='schema'").fetchone()[0]
        == "4"
    )
    store = Store(connection)
    connection.commit()
    assert_upgraded(store, connection, snapshot)
    connection.close()


# -- plain verbs on the command line, MCP and HTTP


@pytest.fixture
def cli(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    monkeypatch.setenv("DAYMARK_TZ", ZONE)
    for name in ("DAYMARK_DB", "DAYMARK_WORKSPACE", "DAYMARK_ACTOR"):
        monkeypatch.delenv(name, raising=False)

    def run(*argv):
        status = main(list(argv))
        captured = capsys.readouterr()
        return status, captured.out, captured.err

    run("init")
    return run


def test_cli_manages_a_project(cli):
    assert cli("project", "add", "Website relaunch", "--id", "site", "--stages", BOARD)[0] == 0
    assert cli("add", "Ada", "--kind", "person", "--id", "ada")[0] == 0
    status, _, err = cli("add", "Orphan", "--project", "site", "--assignee", "Nobody")
    assert status == 5 and "No person" in err
    out = cli("add", "Sitemap", "--id", "map", "--project", "site", "--stage", "doing",
              "--assignee", "Ada", "--is-milestone")[1]  # fmt: skip
    assert out.startswith("map  Sitemap  [") and "stage:doing" in out and "milestone" in out
    assert cli("add", "Copy", "--id", "copy", "--project", "site")[0] == 0
    assert "stage" not in cli("add", "Plain", "--id", "plain")[1]
    assert cli("move", "copy", "--first")[0] == 0
    assert cli("move", "map", "--stage", "done", "--after", "copy")[0] == 0
    assert cli("edit", "map", "--no-is-milestone", "--json")[1].count("milestone") == 0
    text = cli("plan", "site")[1]
    assert text.splitlines()[0].startswith("site  Website relaunch  [1/2 done (50%)")
    assert "TO DO (1)" in text and "DONE (1)" in text and "owner Ada" in text
    view = json.loads(cli("plan", "site", "--json")[1])
    assert [t["id"] for t in view["tasks"]] == ["copy", "map"]
    assert "workspace" not in view["tasks"][0] and view["tasks"][1]["plan"]["stage"] == "done"
    assert "1/2 done" in cli("projects")[1]
    cursor = view["cursor"]
    assert cli("changes", "site", "--after", str(cursor))[1].strip().endswith("unchanged")
    cli("project", "stages", "site", "todo:To do,done:Done:completed")
    assert json.loads(cli("changes", "site", "--after", str(cursor), "--json")[1])["changed"]
    status, _, err = cli("move", "copy", "--stage", "nowhere")
    assert status == 5 and "Unknown stage" in err
    status, out, _ = cli("events", "--head")
    assert json.loads(out)["cursor"] > cursor
    status, out, _ = cli("events", "--project", "site", "--after", str(cursor))
    assert {e["entity_id"] for e in json.loads(out)} == {"site"}
    assert cli("project", "set", "site", "--managed-by", "rev")[0] == 0
    assert "managed by rev: read-only" in cli("plan", "site")[1]
    assert "managed by rev" in cli("projects")[1]
    assert (
        json.loads(cli("edit", "site", "--managed-by", "", "--json")[1]).get("managed_by") is None
    )


def test_mcp_reads_plans_and_moves_cards(tasks):
    site(tasks, BOARD, "a", "b")

    def tool(name, **arguments):
        message = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                   "params": {"name": name, "arguments": arguments}}  # fmt: skip
        result = handle(tasks, message)["result"]
        return result["isError"], result["content"][0]["text"]

    assert len(json.dumps(TOOLS)) < 6000
    assert tool("daymark_act", action="move", id="b", stage="doing", place="first")[0] is False
    assert order(tasks) == ["b", "a"] and board(tasks)["doing"] == ["b"]
    assert tool("daymark_act", action="move", id="a", place="sideways")[0] is True
    assert "DOING (1)" in tool("daymark_view", view="plan", id="site")[1]
    assert "Website relaunch" in tool("daymark_view", view="projects")[1]
    assert tool("daymark_add", title="C", project="site", stage="done", milestone=True)[0] is False
    assert board(tasks)["done"]


def test_projects_plans_batches_and_feeds_over_http(tmp_path):
    store = Store(tmp_path / "http.db")
    app = create_app(store, {"token": {"workspace": "w", "principal": "alice"}})
    with TestClient(app) as http:
        auth = {"Authorization": "Bearer token"}

        def forward(request):
            reply = http.request(request.method, request.url.raw_path.decode(),
                                 content=request.content, headers=dict(request.headers))  # fmt: skip
            return httpx.Response(reply.status_code, content=reply.content)

        transport = httpx.MockTransport(forward)
        with Client("http://daymark.invalid", "token", transport=transport) as client:
            remote = Tasks(client, actor="alice", timezone=ZONE, now=NOW)
            site(remote, BOARD, "a", "b")
            remote.move("b", stage="doing", first=True, op="mv")
            assert remote.move("b", stage="doing", first=True, op="mv")["rank"]
            assert order(remote) == ["b", "a"]
            assert client.projects()["projects"][0]["id"] == "site"
            cursor, version = client.head(), client.get("a")["version"]
            assert client.changes("site", cursor) == {"cursor": cursor, "changed": False}
            result = client.batch(batch_ops(
                ("h1", "claim", {"id": "a", "owner": "mallory"}, version),
                ("h2", "note", {"id": "a", "text": "Started", "author": "mallory"}, version + 1),
            ))  # fmt: skip
            assert result["committed"] is True
            assert client.get("a")["claimed_by"] == "alice"  # The principal, not the body.
            assert client.get("a")["notes"][0]["author"] == "alice"
            assert client.changes("site", cursor)["changed"] is True
            assert {e["entity_id"] for e in client.events(after=cursor, project="site")} == {"a"}
            refused = client.batch(batch_ops(("h3", "update", {"id": "a", "title": "x"}, 1)))
            assert refused["results"][0]["error"] == {
                "code": "conflict", "message": "Version mismatch", "details": []}  # fmt: skip
        assert http.get("/v1/projects/missing/plan", headers=auth).status_code == 404
        assert http.get("/v1/projects/site/changes", headers=auth).status_code == 422
        too_many = {"operations": batch_ops(*[(f"o{i}", "create", {"kind": "task"}, 0)
                                              for i in range(101)])}  # fmt: skip
        assert http.post("/v1/operations/batch", json=too_many, headers=auth).status_code == 422
        assert http.get("/v1/events/head").status_code == 401
    store.close()
