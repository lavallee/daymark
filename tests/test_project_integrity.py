"""Regressions from the second review of projects: blockers, ties and keys,
number bounds, value merging, undo of configuration, schema refusals and
upgrade notices, milestone rules, and the README's worked examples. Synthetic
records only.
"""

import json
import re
import shlex
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from daymark import DomainError, Store, Tasks, plans, ranks
from daymark.api import create_app
from daymark.cli import main
from daymark.client import Client
from daymark.store import STALE, canonical

NOW = datetime(2030, 4, 3, 15, 0, tzinfo=UTC)
ZONE = "Europe/London"
ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "daymark-0.3.0.sql"


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


def order(tasks, project="p"):
    return [c["id"] for c in tasks.plan(project)["tasks"]]


# -- 1. one blocker rule everywhere


def test_an_archived_dependency_in_the_project_releases_its_dependent(tasks):
    tasks.project("P", id="p")
    tasks.add("Blocker", id="blk", project="p")
    tasks.add("Work", id="work", project="p", after="blk")
    tasks.add("Epic", id="epic", project="p")
    tasks.add("Child", id="child", project="p", parent="epic")
    tasks.archive("blk")
    tasks.archive("epic")
    view = tasks.plan("p")
    work = next(c for c in view["tasks"] if c["id"] == "work")
    assert work["plan"]["blocked_by"] == [] and view["health"]["blocked"] == 0
    assert view["external_dependencies"] == []
    assert view["warnings"] == []  # An archived parent in the project is not "elsewhere".
    listed = next(p for p in tasks.projects()["projects"] if p["id"] == "p")
    assert listed["health"] == view["health"]
    assert "work" in [x["id"] for x in tasks.ready()]


# -- 2 and 3. order keys: total order, ties, malformed keys, long runs


def test_undo_of_a_place_does_not_tie_with_the_task_that_took_its_key(tasks):
    tasks.project("P", id="p")
    for id in "axb":
        tasks.add(id, id=id, project="p")
    tasks.move("a", first=True)
    tasks.move("x", after="a")
    tasks.move("b", after="x")
    tasks.move("x", last=True)
    tasks.add("y", id="y", project="p")
    tasks.move("y", after="a")  # Takes the key x used to have.
    tasks.undo("x")
    keys = [plans.effective_rank(tasks.get(i)) for i in order(tasks)]
    assert len(set(keys)) == len(keys) and keys == sorted(keys)
    assert order(tasks) == ["a", "y", "x", "b"]
    tasks.add("z", id="z", project="p")
    tasks.move("z", after="x")
    assert order(tasks) == ["a", "y", "x", "z", "b"]


def test_placing_between_tasks_that_share_a_key_lands_between_them(tasks):
    store = tasks.engine
    tasks.project("P", id="p")
    for id in ("m", "n", "o"):
        tasks.add(id, id=id, project="p")
    # Two tasks created in the same microsecond share their creation-time key.
    for id in ("m", "n"):
        raw = store.get("w", id)
        raw["created_at"] = "2020-01-01T00:00:00Z"
        store.db.execute("UPDATE daymark_entities SET data=? WHERE id=?", (canonical(raw), id))
    assert order(tasks) == ["m", "n", "o"]  # Ties break by ID: total and stable.
    tasks.add("q", id="q", project="p")
    tasks.move("q", after="m")
    assert order(tasks) == ["m", "q", "n", "o"]
    keys = [plans.effective_rank(tasks.get(i)) for i in order(tasks)]
    assert len(set(keys)) == len(keys)
    assert tasks.get("o")["version"] == 1  # Only the tied neighbour was re-keyed.


def test_created_at_is_set_by_daymark(tasks):
    tasks.project("P", id="p")
    code("invalid", tasks.engine.execute, "w", "c", "create",
         {"id": "back", "kind": "task", "project_id": "p", "created_at": "2001-01-01T00:00:00Z"},
         expected_version=0)  # fmt: skip


@pytest.mark.parametrize("bad", ["zz", "A" + "0" * 26, "a50", "a", "a5!"])
def test_malformed_keys_are_refused_wherever_they_enter(tasks, bad):
    tasks.project("P", id="p")
    tasks.add("a", id="a", project="p")
    snapshot = tasks.engine.export("w")
    for entity in snapshot["entities"]:
        if entity["id"] == "a":
            entity["rank"] = bad
    copy = Store(":memory:")
    code("invalid", copy.import_snapshot, "w", snapshot, operation_id="imp", expected_version=0)
    assert not ranks.valid_rank(bad)


def test_many_inserts_at_one_spot_rebalance_instead_of_failing(tasks):
    tasks.project("P", id="p")
    tasks.add("a", id="a", project="p")
    tasks.add("b", id="b", project="p")
    tasks.move("a", first=True)
    tasks.move("b", last=True)
    previous = "a"
    for n in range(900):
        id = f"x{n}"
        tasks.engine.execute("w", f"c{n}", "create", {"id": id, "kind": "task", "project_id": "p"},
                             expected_version=0)  # fmt: skip
        tasks.move(id, after=previous)
        previous = id
    found = order(tasks)
    assert found == ["a", *[f"x{n}" for n in range(900)], "b"]
    longest = max(len(tasks.get(i)["rank"]) for i in found)
    assert longest <= ranks.REBALANCE_AT
    # The key generator itself is iterative: thousands of inserts at one spot work.
    low, high, count = "a0", "a1", 0
    while len(high) < ranks.MAX_LENGTH - 2:
        high, count = ranks.rank_between(low, high), count + 1
    assert count > 5000 and ranks.valid_rank(high)


# -- 5. custom numbers


def test_custom_numbers_are_finite_and_integers_exact_within_a_bound(tasks):
    tasks.project("P", id="p", fields="pts:Pts:number")
    tasks.add("A", id="a", project="p")
    for text in ("9" * 30, "1e400", "nan", "inf", "-inf", "12abc"):
        error = code("invalid", tasks.parse_values, "p", [f"pts={text}"])
        assert "finite number" in str(error)
    assert tasks.parse_values("p", ["pts=9007199254740991"]) == {"pts": 9007199254740991}
    assert tasks.parse_values("p", ["pts=2.5"]) == {"pts": 2.5}
    assert tasks.parse_values("p", ["pts=3"]) == {"pts": 3}
    for bad in (10**400, 2**53, float("inf"), float("nan")):
        code("invalid", tasks.edit, "a", values={"pts": bad})
    assert tasks.edit("a", values={"pts": -(2**53 - 1)})["values"]["pts"] == -(2**53 - 1)


# -- 6. values merge per key on every path


def test_values_merge_per_key_through_the_engine_batch_and_http(tmp_path):
    store = Store(tmp_path / "v.db")
    tasks = Tasks(store, "w", timezone=ZONE, now=NOW)
    tasks.project("P", id="p", fields="pts:Pts:number,note:Note:text,risk:Risk:option:lo|hi")
    tasks.add("A", id="a", project="p", values={"pts": 3, "note": "keep", "risk": "hi"})
    item = store.execute("w", "e1", "update", {"id": "a", "values": {"pts": 5}},
                         expected_version=1)  # fmt: skip
    assert item["values"] == {"pts": 5, "note": "keep", "risk": "hi"}
    result = store.batch("w", [{"operation_id": "b1", "action": "update",
                                "payload": {"id": "a", "values": {"risk": None}},
                                "expected_version": 2}])  # fmt: skip
    assert result["results"][0]["result"]["values"] == {"pts": 5, "note": "keep"}
    app = create_app(store, {"token": {"workspace": "w", "principal": "alice"}})
    with TestClient(app) as http:

        def forward(request):
            reply = http.request(request.method, request.url.raw_path.decode(),
                                 content=request.content, headers=dict(request.headers))  # fmt: skip
            return httpx.Response(reply.status_code, content=reply.content)

        with Client("http://x.invalid", "token", transport=httpx.MockTransport(forward)) as client:
            client.update("h1", {"id": "a", "values": {"note": "changed"}}, 3)
            assert client.get("a")["values"] == {"pts": 5, "note": "changed"}
    assert tasks.edit("a", values={"pts": None})["values"] == {"note": "changed"}
    store.close()


# -- 7. undoing a configuration change is one unit


def test_undoing_a_configuration_change_restores_the_task_stages_it_cleared(tasks):
    tasks.project("P", id="p", preset="kanban")
    tasks.add("A", id="a", project="p", stage="ready")
    tasks.add("B", id="b", project="p", stage="review")
    tasks.add("C", id="c", project="p", stage="review")
    tasks.configure("p", preset="simple")
    assert [tasks.get(x)["stage"] for x in "abc"] == [None, None, None]
    tasks.edit("c", title="C2")  # Written since: undo leaves it as it is.
    tasks.undo("p")
    assert [tasks.get(x)["stage"] for x in "abc"] == ["ready", "review", None]


# -- 8. one refusal for a schema the caller has not upgraded


def test_every_read_refuses_a_stale_schema_with_one_message(tmp_path):
    connection = sqlite3.connect(tmp_path / "old.db")
    connection.executescript(FIXTURE.read_text())
    connection.commit()
    store = Store(connection)
    connection.rollback()  # The caller discards the upgrade.
    messages = set()
    for read in (
        lambda: store.get("home", "seeds"),
        lambda: store.list("home"),
        lambda: store.query("home", {}),
        lambda: store.ready("home"),
        lambda: store.plan("home", "garden"),
        lambda: store.changes("home", "garden", 0),
    ):
        messages.add(str(code("conflict", read)))
    assert messages == {STALE}
    connection.close()


# -- 4. the command line announces the one-way upgrade, once


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


def test_the_command_line_announces_an_upgrade_once(cli, tmp_path):
    old = tmp_path / "old.sqlite3"
    db = sqlite3.connect(old)
    db.executescript(FIXTURE.read_text())
    db.commit()
    db.close()
    status, out, err = cli("--db", str(old), "--workspace", "home", "ready")
    assert status == 0 and "Buy seeds" in out
    assert err == (f"daymark: upgraded {old} from schema 4 to 5 (Daymark 0.4.0); "
                   "older versions can no longer open it\n")  # fmt: skip
    assert cli("--db", str(old), "--workspace", "home", "ready")[2] == ""
    fresh = tmp_path / "fresh.sqlite3"
    assert cli("--db", str(fresh), "init")[2] == ""
    status, out, err = cli("--db", str(fresh), "prime")
    assert "0 ready" in out and err == ""
    status, out, err = cli("--db", str(fresh), "add", "Probe", "--id", "probe")
    assert out == "probe  Probe\n" and err == ""
    backup = tmp_path / "before.sqlite3"
    second = tmp_path / "second.sqlite3"
    db = sqlite3.connect(second)
    db.executescript(FIXTURE.read_text())
    db.commit()
    db.close()
    assert cli("--db", str(second), "backup", "--file", str(backup))[2] == ""  # Never upgrades.
    check = sqlite3.connect(backup)
    assert check.execute("SELECT value FROM daymark_scheduler WHERE key='schema'").fetchone() == (
        "4",
    )
    check.close()


# -- 9. milestones, conflicts and parents


def test_milestones_conflicts_and_parents_report_honestly(tasks):
    tasks.project("P", id="p")
    tasks.project("Q", id="q")
    tasks.add("M1", id="m1", project="p", is_milestone=True, due="2030-04-10")
    tasks.add("M2", id="m2", project="p", is_milestone=True, due="2030-04-20")
    error = code("invalid", tasks.edit, "m1", milestone="m2")
    assert "milestone does not belong to a milestone" in str(error)
    tasks.add("Member", id="member", project="p", milestone="m1")
    code("invalid", tasks.edit, "member", is_milestone=True)
    tasks.cancel("m1")  # Off the default board, but still listed with its status.
    listed = {m["id"]: m for m in tasks.plan("p")["milestones"]}
    assert listed["m1"]["status"] == "cancelled" and listed["m1"]["total"] == 1
    tasks.reopen("m1")
    moved = tasks.edit("m1", project="q")
    assert moved["project_id"] == "q"
    member = tasks.get("member")
    assert member["milestone_id"] is None
    assert "milestone.detach" in [e["action"] for e in tasks.engine.events("w")]
    # Date conflicts are facts about live work only.
    tasks.add("Blocker", id="blk", project="p", target="2030-04-10")
    tasks.add("Early", id="early", project="p", after="blk", plan="2030-04-09")
    assert [c["item"] for c in tasks.plan("p")["dependency_conflicts"]] == ["early"]
    tasks.done("blk")
    tasks.done("early")
    assert tasks.plan("p")["dependency_conflicts"] == []
    # A cancelled parent still rolls up its children, and says it is cancelled.
    tasks.add("Epic", id="epic", project="p")
    tasks.add("Story", id="story", project="p", parent="epic")
    tasks.cancel("epic")
    assert tasks.plan("p")["parents"]["epic"]["status"] == "cancelled"
    assert tasks.plan("p")["parents"]["epic"]["children"] == ["story"]


# -- 10. the README's worked examples run as printed


def readme_examples():
    text = (ROOT / "README.md").read_text()
    section = text.split("## Projects:", 1)[1].split("\n## ", 1)[0]
    return re.findall(r"```console\n(.*?)```", section, re.DOTALL)


@pytest.mark.parametrize("block", readme_examples())
def test_the_readme_examples_run_as_printed(cli, tmp_path, block):
    database = tmp_path / "example.sqlite3"
    assert cli("--db", str(database), "init")[0] == 0
    command, expected = None, []

    def check():
        status, out, err = cli("--db", str(database), *shlex.split(command)[1:])
        assert (status, err) == (0, ""), command
        assert out == "".join(expected), command

    for line in block.splitlines(keepends=True):
        if line.startswith("$ daymark "):
            if command:
                check()
            command, expected = line[2:].strip(), []
        else:
            expected.append(line)
    check()


def test_the_readme_has_its_examples():
    assert len(readme_examples()) == 2
    assert json.dumps(readme_examples()).count("cursor ") == 2
