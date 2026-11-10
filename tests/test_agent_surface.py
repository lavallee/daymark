"""The everyday surface: readiness, claims, notes, proposals, plain verbs, CLI and MCP.

All records are synthetic.
"""

import io
import json
import random
import sqlite3
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from fastapi.testclient import TestClient

from daymark import DomainError, Store, Tasks
from daymark.api import create_app
from daymark.cli import main
from daymark.client import Client
from daymark.mcp import TOOLS, handle, serve
from daymark.render import compact, line
from daymark.store import REFS, canonical
from daymark.tasks import parse_every, parse_when

NOW = datetime(2030, 4, 3, 15, 0, tzinfo=UTC)  # A Wednesday.
ZONE = "Europe/London"


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


def create(store, id, **fields):
    return store.execute(
        "w", "create-" + id, "create", {"id": id, "kind": "task", **fields}, expected_version=0
    )


def day(offset):
    return {"date": (NOW.date() + timedelta(days=offset)).isoformat(), "timezone": ZONE}


# -- indexed validation keeps the whole-workspace contract


def derived_refs(store):
    expected = set()
    for item in store.list("w", include_archived=True):
        for field in REFS:
            value = item[field]
            for ref in value if isinstance(value, list) else ([value] if value else []):
                expected.add(("w", item["id"], field, ref))
    return expected


def test_random_histories_keep_index_and_full_validation_in_agreement():
    for seed in range(6):
        rng, store = random.Random(seed), Store(":memory:")
        ids = [f"t{i}" for i in range(12)]
        create(store, "p", kind="project")
        for step in range(150):
            id, op = rng.choice(ids), f"op-{step}"
            other = rng.choice([x for x in ids if x != id] + ["p", "missing"])
            try:
                current = store.get("w", id)
            except DomainError:
                current = None
            try:
                if current is None:
                    store.execute("w", op, "create", {"id": id, "kind": "task"}, expected_version=0)
                    continue
                choice = rng.randrange(6)
                if choice == 0:
                    payload = {"id": id, "depends_on": sorted({*current["depends_on"], other})}
                elif choice == 1:
                    payload = {"id": id, "parent_id": other}
                elif choice == 2:
                    payload = {"id": id, "depends_on": [], "parent_id": None}
                elif choice == 3:
                    payload = {"id": id, "project_id": other}
                if choice < 4:
                    store.execute("w", op, "update", payload, expected_version=current["version"])
                else:
                    action = "delete" if choice == 4 else "undo"
                    store.execute("w", op, action, {"id": id}, expected_version=current["version"])
            except DomainError as exc:
                assert exc.code in {"invalid", "conflict"}
            store._validate_graph("w")  # The original whole-workspace rules still hold.
            assert set(store.db.execute("SELECT * FROM daymark_refs")) == derived_refs(store)
        store.close()


def test_reference_rules_are_enforced_per_write():
    store = Store(":memory:")
    create(store, "a")
    create(store, "b", depends_on=["a"])
    create(store, "c", depends_on=["b"])
    create(store, "label", kind="label")
    code("invalid", create, store, "d", depends_on=["missing"])
    code("invalid", create, store, "e", depends_on=["label"])
    code("invalid", create, store, "f", depends_on=["f"])
    cycle = {"id": "a", "depends_on": ["c"]}
    code("invalid", store.execute, "w", "cycle", "update", cycle, expected_version=1)
    code("invalid", store.execute, "w", "del", "delete", {"id": "a"}, expected_version=1)
    assert store.get("w", "a")["version"] == 1
    source = [{"system": "mail", "kind": "message", "id": "1"}]
    create(store, "g", source_refs=source)
    assert code("conflict", create, store, "h", source_refs=source).code == "conflict"
    store.execute("w", "del-c", "delete", {"id": "c"}, expected_version=1)
    store.execute("w", "del-b", "delete", {"id": "b"}, expected_version=1)
    store.execute("w", "del-a", "delete", {"id": "a"}, expected_version=1)


def test_invalid_entity_names_fields_without_echoing_input():
    store = Store(":memory:")
    error = code("invalid", create, store, "a", title="secret words", priority=9)
    assert any(detail.startswith("priority:") for detail in error.details)
    assert "secret words" not in " ".join(error.details)


def test_earlier_schema_upgrades_in_place(tmp_path):
    path = tmp_path / "old.db"
    old = sqlite3.connect(path)
    old.executescript(
        "CREATE TABLE daymark_entities (workspace TEXT NOT NULL,id TEXT NOT NULL,kind TEXT NOT NULL,version INTEGER NOT NULL,data TEXT NOT NULL,PRIMARY KEY(workspace,id));"
        "CREATE TABLE daymark_events (seq INTEGER PRIMARY KEY AUTOINCREMENT,workspace TEXT NOT NULL,operation_id TEXT,entity_id TEXT,action TEXT NOT NULL,before_data TEXT,after_data TEXT,created_at TEXT NOT NULL);"
        "CREATE TABLE daymark_event_cursors (workspace TEXT NOT NULL,seq INTEGER NOT NULL,event_seq INTEGER NOT NULL UNIQUE,PRIMARY KEY(workspace,seq));"
        "CREATE TABLE daymark_operations (workspace TEXT NOT NULL,operation_id TEXT NOT NULL,payload_hash TEXT NOT NULL,result TEXT NOT NULL,PRIMARY KEY(workspace,operation_id));"
        "CREATE TABLE daymark_scheduler (key TEXT PRIMARY KEY,value TEXT NOT NULL);"
    )
    stamp = "2030-01-01T00:00:00Z"
    base = {
        "workspace": "w", "kind": "task", "version": 1, "title": "", "status": "open",
        "archived": False, "priority": 0, "description": "", "assignee_id": None,
        "beneficiary_ids": [], "parent_id": None, "project_id": None, "goal_id": None,
        "label_ids": [], "context_ids": [], "depends_on": [], "checklist": [],
        "deadline": None, "planned": None, "earliest_start": None, "latest_target": None,
        "recurrence": None, "series_id": None, "occurrence_key": None, "source_refs": [],
        "metadata": {}, "filter": None, "created_at": stamp, "updated_at": stamp,
        "completed_at": None, "task_id": None, "scheduled_at": None, "channel": "recording",
        "recipient_id": None, "reminder_state": "pending", "attempts": 0, "retry_at": None,
        "lease_owner": None, "lease_token": None, "lease_until": None, "delivery_key": None,
        "error": None,
    }  # fmt: skip
    for item in ({**base, "id": "a"}, {**base, "id": "b", "depends_on": ["a"]}):
        old.execute(
            "INSERT INTO daymark_entities VALUES(?,?,?,?,?)",
            ("w", item["id"], "task", 1, canonical(item)),
        )
    old.commit()
    old.close()
    store = Store(path)
    assert [x["id"] for x in store.ready("w")] == ["a"]
    assert store.get("w", "b")["notes"] == []
    code("invalid", store.execute, "w", "del", "delete", {"id": "a"}, expected_version=1)
    store.execute("w", "done", "complete", {"id": "a"}, expected_version=1)
    assert [x["id"] for x in store.ready("w")] == ["b"]
    store.close()


# -- engine actions


def test_notes_append_and_cannot_be_rewritten():
    store = Store(":memory:")
    create(store, "a")
    noted = store.execute(
        "w", "n1", "note", {"id": "a", "text": "Left a voicemail", "author": "alice"}, 1
    )
    assert [n["text"] for n in noted["notes"]] == ["Left a voicemail"]
    assert store.execute("w", "n1", "note", {"id": "a", "text": "Left a voicemail", "author": "alice"}, 1) == noted  # fmt: skip
    code("invalid", store.execute, "w", "u", "update", {"id": "a", "notes": []}, 2)
    code("invalid", store.execute, "w", "n2", "note", {"id": "a", "text": ""}, 2)


def test_claims_exclude_other_owners_until_they_lapse_or_finish():
    store = Store(":memory:")
    create(store, "a")
    held = store.execute("w", "c1", "claim", {"id": "a", "owner": "alice"}, 1)
    assert held["claimed_by"] == "alice"
    code("conflict", store.execute, "w", "c2", "claim", {"id": "a", "owner": "bob"}, 2)
    code("conflict", store.execute, "w", "r0", "release", {"id": "a", "owner": "bob"}, 2)
    assert store.ready("w", owner="bob") == []
    assert [x["id"] for x in store.ready("w", owner="alice")] == ["a"]
    later = datetime.now(UTC) + timedelta(hours=2)
    assert [x["id"] for x in store.ready("w", owner="bob", now=later)] == ["a"]
    renewed = store.execute(
        "w", "c3", "claim", {"id": "a", "owner": "alice", "lease_seconds": 60}, 2
    )
    code("invalid", store.execute, "w", "u", "update", {"id": "a", "claimed_by": "bob"}, 3)
    done = store.execute("w", "d", "complete", {"id": "a"}, renewed["version"])
    assert done["claimed_by"] is None and done["claim_until"] is None
    code("invalid", store.execute, "w", "c4", "claim", {"id": "a", "owner": "bob"}, 4)


def test_proposals_wait_for_a_decision():
    store = Store(":memory:")
    create(store, "a", status="proposed")
    create(store, "b", status="proposed")
    create(store, "c")
    assert [x["id"] for x in store.ready("w")] == ["c"]
    assert [x["id"] for x in store.agenda("w")["proposed"]] == ["a", "b"]
    assert store.execute("w", "ok", "accept", {"id": "a"}, 1)["status"] == "open"
    assert store.execute("w", "no", "dismiss", {"id": "b"}, 1)["status"] == "cancelled"
    code("invalid", store.execute, "w", "again", "accept", {"id": "c"}, 1)
    assert [x["id"] for x in store.ready("w")] == ["a", "c"]


# -- readiness and agenda


def test_ready_rules_and_order():
    store = Store(":memory:")
    create(store, "undated")
    create(store, "soon", deadline=day(3))
    create(store, "late", deadline=day(-2))
    create(store, "today-plan", planned=day(0))
    create(store, "due-today", deadline=day(0))
    create(store, "important", priority=1)
    create(store, "hidden", earliest_start=day(1))
    create(store, "blocked", depends_on=["undated"])
    create(store, "after-cancelled", depends_on=["late"], priority=4)
    create(store, "parent")
    create(store, "child", parent_id="parent", next_move="agent")
    create(store, "waiting", status="waiting", waiting_on="a reply")
    order = [x["id"] for x in store.ready("w", now=NOW)]
    assert order == ["late", "due-today", "today-plan", "soon", "important", "undated", "child"]
    assert [x["id"] for x in store.ready("w", now=NOW, next_move="agent")] == ["child"]
    assert [x["id"] for x in store.ready("w", now=NOW, limit=2)] == ["late", "due-today"]
    store.execute("w", "x", "cancel", {"id": "late"}, 1)
    store.execute("w", "y", "complete", {"id": "child"}, 1)
    order = [x["id"] for x in store.ready("w", now=NOW + timedelta(days=1))]
    assert {"after-cancelled", "parent", "hidden"} <= set(order) and "blocked" not in order
    code("invalid", store.ready, "w", limit=0)
    code("invalid", store.ready, "w", next_move="robot")


def test_agenda_places_each_task_once():
    store = Store(":memory:")
    create(store, "late", deadline=day(-1))
    create(store, "late-waiting", deadline=day(-1), status="waiting")
    create(store, "today", planned=day(0))
    create(store, "friday", deadline=day(2), planned=day(5))
    create(store, "far", deadline=day(60))
    create(store, "waiting", status="waiting", waiting_on="a quote")
    create(store, "idea", status="proposed")
    view = store.agenda("w", now=NOW, days=7)
    assert [x["id"] for x in view["overdue"]] == ["late", "late-waiting"]
    assert [x["id"] for x in view["today"]] == ["today"]
    assert {k: [x["id"] for x in v] for k, v in view["upcoming"].items()} == {
        day(2)["date"]: ["friday"]
    }
    assert [x["id"] for x in view["waiting"]] == ["waiting"]
    assert [x["id"] for x in view["proposed"]] == ["idea"]
    assert view["later_or_undated"] == 1
    assert store.summary("w", now=NOW) == {
        "ready": 4, "overdue": 2, "today": 1, "next_7_days": 1, "waiting": 1, "proposed": 1,
    }  # fmt: skip


def test_query_narrowing_matches_the_unindexed_result():
    store = Store(":memory:")
    create(store, "a")
    create(store, "b", status="waiting")
    create(store, "p", kind="project")
    for ast in (
        {},
        {"field": "status", "op": "eq", "value": "waiting"},
        {"and": [{"field": "kind", "op": "eq", "value": "task"}, {"field": "id", "op": "ne", "value": "a"}]},
        {"or": [{"field": "kind", "op": "eq", "value": "project"}, {"field": "id", "op": "eq", "value": "a"}]},
        {"not": {"field": "kind", "op": "eq", "value": "task"}},
    ):  # fmt: skip
        test = store._compile_filter(ast)
        assert store.query("w", ast) == [x for x in store.list("w") if test(x)]


# -- the date and recurrence grammar


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("2030-05-01", {"date": "2030-05-01"}),
        ("today", {"date": "2030-04-03"}),
        ("tomorrow", {"date": "2030-04-04"}),
        ("fri", {"date": "2030-04-05"}),
        ("Wednesday", {"date": "2030-04-10"}),
        ("+3d", {"date": "2030-04-06"}),
        ("+2w", {"date": "2030-04-17"}),
        ("+1m", {"date": "2030-05-03"}),
        ("tomorrow 09:30", {"at": "2030-04-04T09:30:00+01:00"}),
        ("2030-05-01T18:00", {"at": "2030-05-01T18:00:00+01:00"}),
        ("2030-05-01T18:00+02:00", {"at": "2030-05-01T18:00:00+02:00"}),
    ],
)
def test_dates_resolve_only_from_the_documented_forms(text, expected):
    result = parse_when(text, ZONE, NOW)
    assert result["timezone"] == ZONE
    for key, value in expected.items():
        assert datetime.fromisoformat(result[key]) == datetime.fromisoformat(value)
    relative = not text[0].isdigit()
    assert result["expression"] == (text if relative else None)


@pytest.mark.parametrize(
    "text", ["", "soon", "next blue moon", "+3", "t", "tomorrow 9am", "2030-13-45"]
)
def test_unrecognised_dates_are_refused_not_guessed(text):
    assert "Accepted dates" in code("invalid", parse_when, text, ZONE, NOW).details[0]


def test_local_day_follows_the_callers_zone():
    late = datetime(2030, 4, 3, 23, 30, tzinfo=UTC)
    assert parse_when("today", "Europe/London", late)["date"] == "2030-04-04"
    assert parse_when("today", "America/New_York", late)["date"] == "2030-04-03"


@pytest.mark.parametrize(
    ("text", "rule"),
    [
        ("day", "FREQ=DAILY"),
        ("every week", "FREQ=WEEKLY"),
        ("2 weeks", "FREQ=WEEKLY;INTERVAL=2"),
        ("3 months", "FREQ=MONTHLY;INTERVAL=3"),
        ("weekdays", "FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR"),
        ("thu, mon", "FREQ=WEEKLY;BYDAY=MO,TH"),
        ("FREQ=MONTHLY;BYMONTHDAY=1", "FREQ=MONTHLY;BYMONTHDAY=1"),
    ],
)
def test_recurrence_shorthand(text, rule):
    assert parse_every(text) == rule


def test_unrecognised_recurrence_is_refused():
    for text in ("fortnightly", "0 days", "", "mon,xyz"):
        code("invalid", parse_every, text)


# -- plain verbs


def test_capture_work_and_finish_it(tasks):
    passport = tasks.add("Renew passport", due="+30d", priority=1, note="Photos in the drawer")
    assert passport["id"].startswith("dm-") and len(passport["id"]) == 7
    assert passport["deadline"]["date"] == "2030-05-03" and passport["planned"] is None
    assert passport["deadline"]["expression"] == "+30d"
    assert [n["author"] for n in passport["notes"]] == ["alice"]
    tasks.add("Book flights", plan="fri", id="flights")
    hotel = tasks.add("Book hotel", after="flights", id="hotel")
    assert [x["id"] for x in tasks.ready()] == ["flights", passport["id"]]
    tasks.claim("flights")
    bob = Tasks(tasks.engine, "w", actor="bob", timezone=ZONE)
    code("conflict", bob.claim, "flights")
    assert "flights" not in [x["id"] for x in bob.ready()]
    tasks.done("flights", note="Booked")
    assert hotel["id"] in [x["id"] for x in bob.ready()]
    shown = tasks.show("flights")
    assert shown["dependents"] == ["hotel"]
    assert [e["action"] for e in shown["history"]] == ["complete", "note", "claim", "create"]
    assert tasks.undo("flights")["status"] == "open"


def test_writes_follow_concurrent_changes_and_replay_by_operation(tasks):
    tasks.add("Call the school", id="call")
    first = tasks.note("call", "Line busy", op="note-1")
    tasks.edit("call", title="Call the school office")
    assert tasks.note("call", "Line busy", op="note-1") == first
    assert len(tasks.get("call")["notes"]) == 1
    created = tasks.add("Pay invoice", op="add-2")
    assert tasks.add("Pay invoice", op="add-2") == created


def test_waiting_deferring_and_blocking(tasks):
    tasks.add("Fix the leak", id="leak", due="+5d")
    parked = tasks.wait("leak", "the plumber's quote", until="+2d")
    assert parked["status"] == "waiting" and parked["waiting_on"] == "the plumber's quote"
    assert tasks.ready() == [] and tasks.agenda()["waiting"][0]["id"] == "leak"
    assert tasks.resume("leak")["waiting_on"] is None
    assert [x["id"] for x in tasks.ready()] == ["leak"]  # Resuming lifts the future start.
    assert tasks.get("leak")["earliest_start"] is None
    moved = tasks.defer("leak", "tomorrow")
    assert moved["deadline"]["date"] == "2030-04-08"  # The deadline did not move.
    tasks.add("Buy sealant", id="sealant")
    assert tasks.block("leak", "sealant")["depends_on"] == ["sealant"]
    code("invalid", tasks.block, "sealant", "leak")
    assert tasks.unblock("leak", "sealant")["depends_on"] == []
    waiting = tasks.add("Hear back from the bank", waiting_on="the bank")
    assert waiting["status"] == "waiting"


def test_repeating_tasks_spawn_a_readable_successor_that_waits_its_turn():
    tasks = Tasks(Store(":memory:"), "w", timezone=ZONE)  # Completion uses the real clock.
    tasks.add("Water the plants", id="plants", every="3 days", after_completion=True)
    done = tasks.done("plants", note="Fern looks dry")
    assert done["status"] == "completed"
    successor = tasks.get("plants.1")
    assert successor["notes"] == [] and successor["occurrence_key"] == "plants:1"
    assert successor["planned"]["date"] > done["planned"]["date"]
    assert tasks.ready() == []
    assert tasks.agenda()["upcoming"] == {successor["planned"]["date"]: [successor]}
    weekly = tasks.add("Put the bins out", id="bins", every="thu", due="thu")
    assert weekly["recurrence"]["rule"] == "FREQ=WEEKLY;BYDAY=TH"
    first = datetime.fromisoformat(weekly["deadline"]["date"])
    assert (
        first.weekday() == 3 and weekly["recurrence"]["anchor"]["date"] == first.date().isoformat()
    )
    tasks.skip("bins")
    following = datetime.fromisoformat(tasks.get("bins.1")["deadline"]["date"])
    assert following - first == timedelta(days=7)
    assert tasks.get("bins")["completed_at"] is None


def test_readable_successor_yields_to_an_unrelated_entity(tasks):
    tasks.add("Unrelated", id="chore.1")
    tasks.add("Chore", id="chore", every="day")
    tasks.done("chore")
    series = [x for x in tasks.find(kind="task") if x["series_id"] == "chore"]
    assert len(series) == 2 and tasks.get("chore.1")["title"] == "Unrelated"
    tasks.reopen("chore")
    tasks.done("chore")
    assert len([x for x in tasks.find(kind="task") if x["series_id"] == "chore"]) == 2


def test_proposals_sources_memories_and_reminders(tasks):
    idea = tasks.propose("Check the smoke alarms", plan="+2w", source="mail:message:42")
    assert idea["status"] == "proposed" and tasks.ready() == []
    code("conflict", tasks.propose, "Check the alarms again", source="mail:message:42")
    code("invalid", tasks.add, "Bad source", source="nope")
    assert tasks.accept(idea["id"])["status"] == "open"
    fact = tasks.remember("Prefers errands on Saturday mornings")
    assert [m["title"] for m in tasks.memories()] == ["Prefers errands on Saturday mornings"]
    tasks.forget(fact["id"])
    assert tasks.memories() == []
    person = tasks.add("Sam", kind="person", id="sam")
    reminder = tasks.remind(idea["id"], "tomorrow 08:00", recipient=person["id"])
    assert reminder["scheduled_at"] == "2030-04-04T07:00:00Z"
    code("invalid", tasks.remind, idea["id"], "tomorrow", recipient="sam")
    assert tasks.show(idea["id"])["reminders"][0]["id"] == reminder["id"]


def test_renderings_are_lean(tasks):
    item = tasks.add("Renew passport", due="2030-04-01", priority=1, id="passport")
    assert line(item, NOW) == "passport  P1  Renew passport  [overdue 2d; due 2030-04-01]"
    lean = compact(item)
    assert lean == {
        "id": "passport", "kind": "task", "title": "Renew passport", "status": "open",
        "version": 1, "priority": 1, "deadline": "2030-04-01",
    }  # fmt: skip
    assert len(json.dumps(lean)) * 4 < len(json.dumps(item))


# -- command line


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

    return run


def test_cli_needs_init_then_finds_its_database(cli, tmp_path, monkeypatch):
    status, _, err = cli("ready")
    assert status == 6 and "daymark init" in err
    assert cli("init")[0] == 0 and (tmp_path / "xdg/daymark/daymark.sqlite3").is_file()
    assert cli("add", "Global task", "--id", "g")[0] == 0
    project = tmp_path / "project" / "src"
    project.mkdir(parents=True)
    monkeypatch.chdir(tmp_path / "project")
    assert cli("init", "--here")[0] == 0
    monkeypatch.chdir(project)
    assert cli("ready")[1].startswith("Nothing is ready")
    assert cli("add", "Local task", "--id", "l", "--due", "tomorrow", "-p", "2")[0] == 0
    status, out, _ = cli("ready", "--json")
    assert [x["id"] for x in json.loads(out)] == ["l"]
    monkeypatch.chdir(tmp_path)
    assert "Global task" in cli("ready")[1]


def test_cli_everyday_flow_and_coded_failures(cli):
    cli("init")
    assert cli("add", "Book flights", "--id", "flights", "--plan", "fri", "--minutes", "30")[0] == 0
    assert cli("add", "Book hotel", "--id", "hotel", "--after", "flights")[0] == 0
    assert cli("--as", "alice", "claim", "flights")[0] == 0
    status, _, err = cli("--as", "bob", "claim", "flights")
    assert status == 4 and err.startswith("error: conflict:")
    status, _, err = cli("done", "missing", "--json")
    assert status == 3 and json.loads(err)["error"]["code"] == "not_found"
    status, _, err = cli("add", "Vague", "--due", "sometime")
    assert status == 5 and "Accepted dates" in err
    assert cli("--as", "alice", "done", "flights", "--note", "Booked")[0] == 0
    assert cli("ready")[1].startswith("hotel  Book hotel")
    out = cli("show", "flights")[1]
    assert "unblocks: hotel" in out and "alice: Booked" in out
    assert cli("wait", "hotel", "--on", "dates", "--until", "+2d")[0] == 0
    assert "WAITING (1)" in cli("agenda")[1]
    assert cli("propose", "Compare insurance", "--id", "idea")[0] == 0
    assert cli("accept", "idea")[0] == 0
    assert cli("remember", "Window seat preferred")[0] == 0
    out = cli("prime")[1]
    assert "Window seat preferred" in out and "daymark ready" in out and "1 ready" in out
    assert json.loads(cli("prime", "--json")[1])["summary"]["waiting"] == 1
    assert "Compare insurance" in cli("find", "insurance")[1]


def test_onboard_snippet_is_inserted_once(cli, tmp_path):
    target = tmp_path / "AGENTS.md"
    target.write_text("# House rules\n\nBe kind.\n")
    assert "daymark prime" in cli("onboard")[1]
    cli("onboard", "--write", str(target))
    cli("onboard", "--write", str(target))
    text = target.read_text()
    assert text.startswith("# House rules") and text.count("daymark:begin") == 1


# -- other surfaces


def test_mcp_tools_cover_the_flow():
    tasks = Tasks(Store(":memory:"), "w", actor="alice", timezone=ZONE)

    def rpc(method, **params):
        return handle(tasks, {"jsonrpc": "2.0", "id": 1, "method": method, "params": params})

    def tool(name, **arguments):
        result = rpc("tools/call", name=name, arguments=arguments)["result"]
        return result["isError"], result["content"][0]["text"]

    assert (
        rpc("initialize", protocolVersion="2025-06-18")["result"]["serverInfo"]["name"] == "daymark"
    )
    assert handle(tasks, {"jsonrpc": "2.0", "method": "notifications/initialized"}) is None
    assert [t["name"] for t in rpc("tools/list")["result"]["tools"]] == [t["name"] for t in TOOLS]
    assert len(json.dumps(TOOLS)) < 6000  # Tool definitions stay cheap to carry.
    failed, text = tool("daymark_add", title="Pay the rent", due="tomorrow")
    assert not failed and "Pay the rent" in text
    id = text.split()[0]
    assert id in tool("daymark_view", view="ready")[1]
    assert tool("daymark_act", action="note", id=id) == (
        True,
        "error: invalid: missing argument text",
    )
    assert tool("daymark_act", action="claim", id=id)[0] is False
    assert "claimed by alice" in tool("daymark_view", view="show", id=id)[1]
    assert tool("daymark_edit", id=id, due="someday")[1].startswith("error: invalid: Unrecognized")
    assert "Pay the rent" in tool("daymark_view", view="agenda", days=60)[1]
    assert tool("daymark_act", action="done", id=id, text="Paid")[0] is False
    assert tool("daymark_view", view="ready")[1] == "Nothing is ready."
    assert "Commands" in tool("daymark_prime")[1]
    assert rpc("nope")["error"]["code"] == -32601
    stream = io.StringIO()
    serve(tasks, io.StringIO('not json\n\n{"jsonrpc":"2.0","id":7,"method":"ping"}\n'), stream)
    replies = [json.loads(x) for x in stream.getvalue().splitlines()]
    assert replies[0]["error"]["code"] == -32700 and replies[1] == {"jsonrpc": "2.0", "id": 7, "result": {}}  # fmt: skip


def test_plain_verbs_work_over_http(tmp_path):
    store = Store(tmp_path / "http.db")
    app = create_app(store, {"token": {"workspace": "w", "principal": "alice"}})
    with TestClient(app) as http:

        def forward(request):
            reply = http.request(
                request.method,
                request.url.raw_path.decode(),
                content=request.content,
                headers=dict(request.headers),
            )
            return httpx.Response(reply.status_code, content=reply.content)

        with Client("http://daymark.invalid", "token", transport=httpx.MockTransport(forward)) as client:  # fmt: skip
            remote = Tasks(client, actor="alice", timezone=ZONE, now=NOW)
            first = remote.add("Send the form", due="tomorrow")
            assert first["id"].startswith("dm-") and first["workspace"] == "w"
            remote.add("Then file it", id="file", after=first["id"])
            assert [x["id"] for x in remote.ready()] == [first["id"]]
            remote.done(first["id"], note="Sent")
            assert [x["id"] for x in remote.ready()] == ["file"]
            assert remote.show("file")["blocked_by"] == []
            assert remote.agenda()["later_or_undated"] == 1
            assert remote.summary()["ready"] == 1
            noted = remote.note("file", "Filed under tax", op="note-1")
            assert remote.note("file", "Filed under tax", op="note-1") == noted
            assert remote.add("Once", op="add-1") == remote.add("Once", op="add-1")
    store.close()


# -- regressions from independent review


def test_changing_a_repeat_rule_keeps_the_series_going():
    tasks = Tasks(Store(":memory:"), "w", timezone=ZONE)
    tasks.add("Chore", id="a", every="day")
    tasks.done("a")
    tasks.done("a.1")
    assert tasks.edit("a.2", every="week")["recurrence"]["occurrence_index"] == 2
    tasks.done("a.2")
    assert tasks.get("a.3")["recurrence"]["rule"] == "FREQ=WEEKLY"


def test_a_round_is_neither_lost_nor_repeated_when_its_name_was_used_elsewhere():
    tasks = Tasks(Store(":memory:"), "w", timezone=ZONE)
    tasks.add("Unrelated", id="a.1")
    tasks.engine.execute("w", "del", "delete", {"id": "a.1"}, expected_version=1)
    tasks.add("Chore", id="a", every="day")
    tasks.done("a")
    rounds = [x for x in tasks.find(kind="task") if x["occurrence_key"] == "a:1"]
    assert len(rounds) == 1 and rounds[0]["id"] != "a.1"

    tasks.add("Other", id="b", every="day")
    tasks.done("b")
    tasks.engine.execute("w", "del-b1", "delete", {"id": "b.1"}, expected_version=1)
    tasks.add("Impostor", id="b.1")
    tasks.undo("b")
    tasks.done("b")
    assert [x["id"] for x in tasks.find(kind="task") if x["occurrence_key"] == "b:1"] == []


def test_a_reused_identifier_can_repeat():
    tasks = Tasks(Store(":memory:"), "w", timezone=ZONE)
    tasks.add("Plain", id="r")
    tasks.engine.execute("w", "del", "delete", {"id": "r"}, expected_version=1)
    tasks.add("Repeating", id="r", every="day")
    tasks.done("r")
    assert tasks.get("r.1")["status"] == "open"


def test_undo_cannot_change_kind_drop_notes_or_steal_a_claim(tasks):
    store = tasks.engine
    create(store, "x")
    create(store, "t", depends_on=["x"])
    snapshot = store.export("w")
    event = dict(snapshot["events"][0])
    forged = {**event["after_data"], "kind": "label", "version": 1}
    forged_event = {**event, "seq": 99, "operation_id": "forged", "action": "update",
                    "before_data": forged}  # fmt: skip
    store.import_snapshot(
        "w", {"schema_version": 1, "workspace": "w", "events": [forged_event]},
        operation_id="import", expected_version=0,
    )  # fmt: skip
    code("invalid", store.execute, "w", "undo-x", "undo", {"id": "x"}, expected_version=1)
    assert store.get("w", "x")["kind"] == "task"

    tasks.add("Call", id="call")
    tasks.edit("call", title="Call back")
    tasks.claim("call")
    tasks.note("call", "Left a message")
    bob = Tasks(store, "w", actor="bob", timezone=ZONE)
    undone = bob.undo("call")
    assert [n["text"] for n in undone["notes"]] == ["Left a message"]
    assert undone["claimed_by"] == "alice"
    code("conflict", bob.claim, "call")


def test_proposals_stay_inert_until_decided(tasks):
    store = tasks.engine
    parent = tasks.add("Trip", id="trip")
    idea = tasks.propose("Buy a guidebook", id="idea", parent="trip", every="year")
    sam = tasks.add("Sam", kind="person", id="sam")
    reminder = tasks.remind("idea", "tomorrow 08:00", recipient=sam["id"])
    assert [x["id"] for x in tasks.ready()] == [parent["id"]]  # A proposed child blocks nothing.
    for refuse in (tasks.resume, tasks.done, tasks.skip, tasks.reopen):
        code("invalid", refuse, "idea")
    code("invalid", tasks.edit, "idea", status="open")
    code("invalid", tasks.edit, "trip", status="proposed")
    assert store.tick() == {"cancelled": 0}
    due = NOW + timedelta(days=3)
    assert store.claim_reminders("worker", now=due) == []
    tasks.accept(idea["id"])
    assert [x["id"] for x in store.claim_reminders("worker", now=due)] == [reminder["id"]]
    failed = handle(tasks, {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {
        "name": "daymark_add", "arguments": {"title": "Sneaky", "status": "completed"}}})  # fmt: skip
    assert (
        failed["result"]["isError"] and "Unknown argument" in failed["result"]["content"][0]["text"]
    )


def test_first_note_is_part_of_the_capture(tasks):
    first = tasks.add("Order the part", note="Model number is on the label", op="capture-1")
    assert tasks.add("Order the part", note="Model number is on the label", op="capture-1") == first
    assert [n["text"] for n in first["notes"]] == ["Model number is on the label"]


def test_archived_prerequisite_releases_its_dependents(tasks):
    tasks.add("Shelved", id="shelved")
    tasks.add("Next", id="next", after="shelved")
    assert [x["id"] for x in tasks.ready()] == ["shelved"]
    tasks.archive("shelved")
    assert [x["id"] for x in tasks.ready()] == ["next"]
    assert tasks.show("next")["blocked_by"] == []


def test_mcp_survives_malformed_requests(tasks):
    for params in ("x", [1], 3):
        reply = handle(tasks, {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": params})
        assert reply["error"]["code"] == -32602
    reply = handle(tasks, {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {
        "name": "daymark_view", "arguments": "ready"}})  # fmt: skip
    assert reply["result"]["isError"]


def test_views_refuse_a_schema_they_cannot_read(tmp_path):
    connection = sqlite3.connect(tmp_path / "old.db")
    connection.executescript(
        "CREATE TABLE daymark_entities (workspace TEXT NOT NULL,id TEXT NOT NULL,kind TEXT NOT NULL,version INTEGER NOT NULL,data TEXT NOT NULL,PRIMARY KEY(workspace,id));"
    )
    connection.commit()
    store = Store(connection)
    connection.rollback()  # The caller discards the upgrade.
    assert store.list("w") == []
    for view in (store.ready, store.agenda, store.summary):
        code("conflict", view, "w")
    connection.close()
