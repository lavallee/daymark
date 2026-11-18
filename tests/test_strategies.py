"""The strategy layer: advice over the kernel's facts, never a write. Synthetic records."""

import ast
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from daymark import DomainError, Store, Tasks, strategies
from daymark.cli import main
from daymark.mcp import handle
from daymark.render import advice_text
from daymark.strategies import Advice, Strategy, register
from daymark.strategies.profiles import PROFILES

ZONE = "Europe/London"
NOW = datetime(2030, 4, 3, 15, 0, tzinfo=UTC)


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


def run(tasks, *stages, scope=None):
    profile = {"name": "test", "strategies": [{"use": n, "with": w} for n, w in stages]}
    return strategies.advise(tasks, profile, scope=scope)


def test_the_plain_profile_is_ready_and_nothing_else(tasks):
    tasks.add("Late", id="late", due="2030-04-01")
    tasks.add("Later", id="later")
    advice = strategies.advise(tasks)
    assert [x["id"] for x in advice.picks] == [x["id"] for x in tasks.ready()]
    assert (advice.notes, advice.held_back, advice.tone, advice.suggestions) == ([], 0, "plain", [])
    assert strategies.stored_profile(tasks) == PROFILES["plain"]


def test_strategies_never_write(tasks):
    tasks.add("Late", id="late", due="2030-04-01", move="answer")
    tasks.add("Quiet", id="quiet", waiting_on="a reply")
    before = tasks.engine.events("w", limit=1000)
    for profile in PROFILES.values():
        strategies.advise(tasks, profile)
    assert tasks.engine.events("w", limit=1000) == before


def test_shortlist_holds_the_rest_back_without_hiding_that_it_did(tasks):
    for index in range(5):
        tasks.add(f"Task {index}", id=f"t{index}")
    advice = run(tasks, ("shortlist", {"size": 2}))
    assert ([x["id"] for x in advice.picks], advice.held_back) == (["t0", "t1"], 3)
    assert "(3 more ready" in advice_text(advice, NOW)
    code("invalid", run, tasks, ("shortlist", {"size": 0}))
    code("invalid", run, tasks, ("shortlist", {"size": "two"}))
    code("invalid", run, tasks, ("shortlist", {"length": 2}))


def test_first_step_shows_the_next_unchecked_item_and_flags_its_absence():
    store = Store(":memory:")
    early = Tasks(store, "w", timezone=ZONE)
    steps = [{"id": "a", "text": "Find the form", "completed": True},
             {"id": "b", "text": "Fill in page one", "completed": False}]  # fmt: skip
    early.add("Passport", id="passport", checklist=steps)
    early.add("Vague", id="vague")
    later = Tasks(store, "w", timezone=ZONE, now=datetime.now(UTC) + timedelta(days=5))
    advice = run(later, ("first-step", {}))
    assert advice.steps == {"passport": "Fill in page one"}
    assert [n.ids for n in advice.notes] == [["vague"]]
    assert "    next: Fill in page one" in advice_text(advice)


def test_neutral_aging_keeps_the_date_and_drops_the_verdict(tasks):
    tasks.add("Late", id="late", due="2030-04-01")
    plain, neutral = run(tasks), run(tasks, ("neutral-aging", {}))
    assert "overdue 2d" in advice_text(plain, NOW)
    text = advice_text(neutral, NOW)
    assert "overdue" not in text and "due 2030-04-01" in text


def test_assistant_strategies_notice_what_a_good_assistant_would():
    store = Store(":memory:")
    then = Tasks(store, "w", timezone=ZONE)
    then.add("Choose a venue", id="venue", move="answer")
    then.add("Tax return", id="tax", due="+9d", minutes=300)
    then.add("Planned already", id="planned", due="+9d", plan="+2d")
    then.add("Quote", id="quote", waiting_on="the roofer")
    then.add("Old idea", id="old")
    now = Tasks(store, "w", timezone=ZONE, now=datetime.now(UTC) + timedelta(days=6))
    advice = run(now, ("decisions-first", {}), ("lookahead", {}), ("waiting-check", {}),
                 ("stale", {"days": 5}))  # fmt: skip
    assert advice.picks[0]["id"] == "venue"
    by = {n.strategy: n for n in advice.notes}
    assert by["decisions-first"].ids == ["venue"]
    assert by["lookahead"].ids == ["tax"] and "3 days" in by["lookahead"].text
    assert by["waiting-check"].ids == ["quote"] and "the roofer" in by["waiting-check"].text
    assert set(by["stale"].ids) == {"venue", "tax", "planned", "old"}


def test_overbooked_counts_what_today_already_holds(tasks):
    tasks.add("Big", id="big", plan="today", minutes=200)
    tasks.add("Unestimated", id="guess", due="today")
    tasks.add("Not today", id="later", plan="+3d", minutes=500)
    assert run(tasks, ("overbooked", {})).notes == []
    advice = run(tasks, ("overbooked", {"capacity_minutes": 210}))
    assert advice.notes[0].ids == ["big", "guess"] or set(advice.notes[0].ids) == {"big", "guess"}
    assert "about 220 min" in advice.notes[0].text and "1 counted at 20 min" in advice.notes[0].text


def test_scope_narrows_what_strategies_see(tasks):
    tasks.add("Home", id="home", scope="household", move="answer")
    tasks.add("Work", id="work", scope="studio", move="answer")
    advice = run(tasks, ("decisions-first", {}), scope="studio")
    assert [x["id"] for x in advice.picks] == ["work"] and advice.notes[0].ids == ["work"]


def test_profiles_are_stored_per_workspace_and_editable(tasks):
    adopted = strategies.store_profile(tasks, PROFILES["gentle"])
    assert strategies.stored_profile(tasks) == adopted
    other = Tasks(tasks.engine, "elsewhere", timezone=ZONE)
    assert strategies.stored_profile(other)["name"] == "plain"
    edited = strategies.edit_profile(adopted, ["shortlist.size=5", "stale.days=10"], ["first-step"])
    assert edited["name"] == "gentle (edited)"
    assert [s["use"] for s in edited["strategies"]] == [
        "neutral-aging",
        "shortlist",
        "overbooked",
        "stale",
    ]
    saved = strategies.store_profile(tasks, edited)
    assert saved["strategies"][1]["with"] == {"size": 5}
    code("not_found", strategies.edit_profile, adopted, [], ["nope"])
    code("not_found", strategies.edit_profile, adopted, ["nope.x=1"], [])
    code("invalid", strategies.validate_profile, {"name": "x", "strategies": [{"with": {}}]})
    assert tasks.engine.setting("w", "nothing") is None
    code("invalid", tasks.engine.set_setting, "w", "Bad Name", 1)
    tasks.engine.set_setting("w", "profile", None)
    assert strategies.stored_profile(tasks)["name"] == "plain"


def test_a_plugin_strategy_obeys_the_same_contract(tasks):
    def celebrate(view, advice: Advice, params):
        advice.notes.append(strategies.Note("celebrate", f"{len(view.open)} open", []))

    register(Strategy("celebrate", "tone", "Counts open tasks.", "folk", celebrate))
    tasks.add("One", id="one")
    assert run(tasks, ("celebrate", {})).notes[0].text == "1 open"
    with pytest.raises(ValueError):
        Strategy("bad", "nonsense", "x", "folk", celebrate)
    code("not_found", strategies.get, "not-installed-anywhere")


def test_the_strategy_layer_reads_only_the_public_surface():
    # The boundary is structural: nothing under strategies/ may import the engine.
    root = Path(strategies.__file__).parent
    for path in root.glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            names = []
            if isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            elif isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            for name in names:
                assert not name.endswith(("store", "views", "sqlite3")), (path.name, name)
    source = "".join(p.read_text() for p in root.glob("*.py"))
    assert ".db." not in source and "_rows" not in source and "engine.execute" not in source


def test_command_line_and_mcp(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("DAYMARK_DB", str(tmp_path / "s.sqlite3"))
    monkeypatch.setenv("DAYMARK_TZ", ZONE)

    def cli(*argv):
        status = main(list(argv))
        captured = capsys.readouterr()
        return status, captured.out, captured.err

    cli("init")
    for index in range(4):
        cli("add", f"Task {index}", "--id", f"t{index}")
    assert cli("advise")[1].count("\n") == 4
    assert "shortlist" in cli("strategies")[1]
    assert cli("profile", "gentle")[1].startswith("profile: gentle")
    assert "(1 more ready" in cli("advise")[1]
    assert json.loads(cli("advise", "--profile", "plain", "--json")[1])["held_back"] == 0
    assert "size=2" in cli("profile", "--set", "shortlist.size=2")[1]
    shown = json.loads(cli("advise", "--json")[1])
    assert (shown["profile"], shown["held_back"], len(shown["picks"])) == ("gentle (edited)", 2, 2)
    assert cli("profile", "nope")[0] == 3
    assert cli("profile", "--set", "shortlist.size=0")[0] == 5
    tasks = Tasks(Store(str(tmp_path / "s.sqlite3")), timezone=ZONE)
    reply = handle(tasks, {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {
        "name": "daymark_view", "arguments": {"view": "advise"}}})  # fmt: skip
    assert "(2 more ready" in reply["result"]["content"][0]["text"]


# -- conditions: what must be true for a goal, and the work that moves them


def test_conditions_form_the_path_to_a_goal(tasks):
    tasks.goal("Move house", id="move")
    tasks.condition("A buyer has signed", goal="move", id="buyer")
    tasks.condition("The new lease is agreed", goal="move", id="lease", state="partly_met")
    tasks.condition("Keys are in hand", goal="move", id="keys", requires=["buyer", "lease"])
    tasks.add("Chase the solicitor", id="chase", advances=["buyer"])
    tasks.add("Book the movers", id="movers", after="keys")
    view = tasks.explore("move")
    assert view["goal"]["id"] == "move"
    assert (view["frontier"], view["gaps"]) == (["buyer", "lease"], ["lease"])
    assert view["unassessed"] == ["buyer", "keys"]
    by = {c["id"]: c for c in view["conditions"]}
    assert by["keys"]["waiting_on"] == ["buyer", "lease"] and not by["keys"]["frontier"]
    assert by["buyer"]["advancing"] == [
        {"id": "chase", "title": "Chase the solicitor", "status": "open"}
    ]
    # Work that waits on a condition is not ready until the condition is met.
    assert [x["id"] for x in tasks.ready()] == ["chase"]

    met = tasks.assess("buyer", "met", evidence="Contract countersigned")
    assert (met["status"], met["condition"]) == ("completed", "met")
    assert met["notes"][-1]["text"] == "Contract countersigned"
    tasks.assess("lease", "met")
    view = tasks.explore("move")
    assert view["frontier"] == ["keys"] and view["gaps"] == ["keys"]
    tasks.assess("keys", "met")
    assert {x["id"] for x in tasks.ready()} == {"chase", "movers"}
    # A judgement can be revised; what depended on it waits again.
    back = tasks.assess("keys", "unmet", evidence="The agent has not released them")
    assert (back["status"], back["completed_at"]) == ("open", None)
    assert [x["id"] for x in tasks.ready()] == ["chase"]


def test_condition_rules(tasks):
    tasks.goal("Goal", id="g")
    made = tasks.condition("Something holds", goal="g", id="c")
    assert (made["condition"], made["status"]) == ("unknown", "open")
    tasks.add("A task", id="t")
    code("invalid", tasks.assess, "t", "met")
    code("invalid", tasks.assess, "c", "certainly")
    code("invalid", tasks.add, "Bad link", advances=["t"])  # Only conditions are advanced.
    code("invalid", tasks.edit, "c", advances=["c"])
    code("invalid", tasks.edit, "t", condition="met")
    code("invalid", tasks.explore, "t")
    code("invalid", tasks.condition, "Loop", goal="g", id="loop", requires=["loop"])
    # Status cannot be set past the state; completing a condition means it is met.
    code("invalid", tasks.edit, "c", status="completed")
    code("invalid", tasks.wait, "c", "someone")
    code(
        "invalid",
        tasks.propose,
        "Proposed condition",
        kind="condition",
        goal="g",
        condition="unknown",
    )
    code("invalid", tasks.add, "No goal", kind="condition", condition="unmet")
    code(
        "invalid", tasks.add, "Repeats", kind="condition", goal="g", condition="unmet", every="day"
    )
    assert tasks.done("c")["condition"] == "met"
    assert tasks.reopen("c")["condition"] == "unmet"
    dropped = tasks.cancel("c")
    assert dropped["status"] == "cancelled"
    assert tasks.explore("g")["conditions"][0]["state"] == "dropped"
    assert tasks.explore()["frontier"] == []
    scoped = {"field": "condition", "op": "eq", "value": "unmet"}
    assert [x["id"] for x in tasks.engine.query("w", scoped)] == ["c"]


def test_frontier_first_leads_with_work_that_moves_what_is_next(tasks):
    tasks.goal("Launch", id="launch")
    tasks.condition("Pricing is decided", goal="launch", id="pricing")
    tasks.condition("The page is live", goal="launch", id="page", requires=["pricing"])
    tasks.condition("Support is staffed", goal="launch", id="support")
    tasks.add("Tidy the backlog", id="tidy", due="2030-04-01")
    tasks.add("Draft the page", id="draft", advances=["page"])
    tasks.add("Compare three price points", id="compare", advances=["pricing"])
    advice = run(tasks, ("frontier-first", {}))
    assert [x["id"] for x in advice.picks] == ["compare", "tidy", "draft"]
    gap = next(n for n in advice.notes if n.ids == ["support"])
    assert "Nothing is moving" in gap.text
    assert advice.suggestions == [
        {"title": "Find a first move for: Support is staffed", "advances": ["support"]}
    ]
    assert any("never been assessed" in n.text for n in advice.notes)
    assert tasks.engine.events("w", limit=1000)[-1]["action"] == "create"  # Nothing was written.


def test_explore_on_the_command_line(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("DAYMARK_DB", str(tmp_path / "e.sqlite3"))
    monkeypatch.setenv("DAYMARK_TZ", ZONE)

    def cli(*argv):
        status = main(list(argv))
        captured = capsys.readouterr()
        return status, captured.out, captured.err

    cli("init")
    cli("goal", "add", "Move house", "--id", "move")
    assert cli("condition", "A buyer has signed", "--goal", "move", "--id", "buyer")[0] == 0
    cli("condition", "Keys are in hand", "--goal", "move", "--id", "keys", "--requires", "buyer")
    cli("add", "Chase the solicitor", "--id", "chase", "--advances", "buyer")
    out = cli("explore", "move")[1]
    assert "[?] buyer  A buyer has signed  [next]" in out
    assert "chase  Chase the solicitor  [open]" in out and "needs buyer" in out
    assert cli("assess", "buyer", "met", "--evidence", "Countersigned")[0] == 0
    out = cli("explore", "move")[1]
    assert "[x] buyer" in out and "evidence: Countersigned" in out
    assert "keys  Keys are in hand  [next; nothing moving it]" in out
    assert json.loads(cli("explore", "--json")[1])["gaps"] == ["keys"]


# -- regressions from independent review


def test_a_stale_stored_profile_never_locks_a_workspace_out(tasks):
    tasks.add("One", id="one")
    broken = {"name": "old", "strategies": [
        {"use": "uninstalled-plugin"},
        {"use": "shortlist", "with": {"size": 1, "renamed_away": 9}},
    ]}  # fmt: skip
    tasks.engine.set_setting("w", "profile", broken)
    profile = strategies.stored_profile(tasks)
    assert profile["skipped"] == ["uninstalled-plugin"]
    assert profile["strategies"] == [{"use": "shortlist", "with": {"size": 1}}]
    advice = strategies.advise(tasks, profile)
    assert "could not be used: uninstalled-plugin" in advice.notes[0].text
    assert "! uninstalled-plugin" in __import__("daymark.render", fromlist=["x"]).profile_text(
        profile
    )
    fixed = strategies.store_profile(tasks, profile)
    assert "skipped" not in fixed and strategies.stored_profile(tasks) == fixed
    tasks.engine.set_setting("w", "profile", "nonsense")
    assert strategies.stored_profile(tasks)["name"] == "plain"
    code("invalid", tasks.engine.set_setting, "w", "profile", {1, 2})
    code("invalid", strategies.edit_profile, PROFILES["gentle"], ["stale=3"], [])
    code("invalid", strategies.edit_profile, PROFILES["gentle"], ["stale.days="], [])


def test_a_condition_keeps_status_and_state_together_on_every_path(tasks):
    tasks.goal("Goal", id="g")
    tasks.condition("Holds", goal="g", id="c", state="met")
    tasks.archive("c")
    back = tasks.reopen("c")  # Un-archiving keeps the assessment.
    assert (back["condition"], back["status"], back["archived"]) == ("met", "completed", False)
    tasks.cancel("c")
    again = tasks.reopen("c")
    assert (again["condition"], again["status"]) == ("met", "completed")
    assert tasks.reopen("c")["condition"] == "unmet"  # Reopening a met one says it no longer holds.
    snapshot = tasks.engine.export("w")
    forged = [dict(e, status="completed") if e["id"] == "c" else e for e in snapshot["entities"]]
    other = Store(":memory:")
    code("invalid", other.import_snapshot, "w", {**snapshot, "entities": forged},
         operation_id="import", expected_version=0)  # fmt: skip


def test_explore_ignores_finished_goals_and_counts_a_proposed_move(tasks):
    tasks.goal("Done goal", id="old")
    tasks.condition("Left over", goal="old", id="left")
    tasks.goal("Live goal", id="live")
    tasks.condition("Needs a move", goal="live", id="need")
    assert tasks.explore()["gaps"] == ["left", "need"]
    tasks.done("old")
    assert tasks.explore()["gaps"] == ["need"]
    tasks.propose("A suggested move", advances=["need"])
    assert tasks.explore()["gaps"] == [] and tasks.explore()["frontier"] == ["need"]
    assert run(tasks, ("frontier-first", {})).suggestions == []
    task = tasks.add("Real work", id="work", advances=["need"])
    assert tasks.edit("work", advances="")["advances"] == [] and task["advances"] == ["need"]


def test_lookahead_stays_bounded(tasks):
    for index in range(30):
        tasks.add(f"Due soon {index}", id=f"d{index:02d}", due="+3d")
    notes = run(tasks, ("lookahead", {"limit": 4})).notes
    assert len(notes) == 5 and "26 more due soon" in notes[-1].text


def test_a_0_4_0_database_upgrades_in_place(tmp_path):
    import sqlite3

    from daymark.store import SCHEMA

    path = tmp_path / "from-0.4.0.sqlite3"
    db = sqlite3.connect(path)
    db.executescript((Path(__file__).parent / "fixtures" / "daymark-0.4.0.sql").read_text())
    before = {
        row[0]: json.loads(row[1]) for row in db.execute("SELECT id, data FROM daymark_entities")
    }
    events = db.execute("SELECT count(*) FROM daymark_events").fetchone()[0]
    db.commit()
    db.close()
    store = Store(str(path))
    for id, old in before.items():
        new = store.get(old["workspace"], id)
        assert new == {**old, "condition": None, "advances": []}
    check = sqlite3.connect(path)
    assert check.execute("SELECT count(*) FROM daymark_events").fetchone()[0] == events
    assert check.execute("SELECT value FROM daymark_scheduler WHERE key='schema'").fetchone() == (
        SCHEMA,
    )
    check.close()
    upgraded = Tasks(store, "fixture", timezone=ZONE)
    upgraded.condition("Works after upgrade", goal="fixture-goal", id="works")
    assert upgraded.explore("fixture-goal")["frontier"] == ["works"]
    store.close()


def test_temporary_advice_uses_the_same_adjustments_as_adoption(tasks):
    for i in range(4):
        tasks.add(f"Studio item {i}", id=f"s{i}", scope="studio")
    tasks.add("Other item", id="other")
    saved = strategies.store_profile(tasks, PROFILES["assistant"])
    before = tasks.engine.events("w", limit=100)
    options = {"settings": ["shortlist.size=2"], "drops": ["first-step"]}
    preview = tasks.advise(profile="gentle", scope="studio", **options)
    assert preview.profile == "gentle (edited)"
    assert ([x["id"] for x in preview.picks], preview.held_back) == (["s0", "s1"], 2)
    assert strategies.stored_profile(tasks) == saved
    assert tasks.engine.events("w", limit=100) == before
    assert tasks.advise().profile == "assistant"
    for invalid in ("shortlist.", "shortlist=2", "shortlist.size=0", "shortlist.=2"):
        code("invalid", tasks.advise, settings=[invalid])
    assert strategies.stored_profile(tasks) == saved
    tasks.profile("gentle", **options)
    active = tasks.advise(scope="studio").to_dict()
    trial = preview.to_dict()
    assert active.pop("profile_source")["kind"] == "workspace"
    assert trial.pop("profile_source") == {"kind": "built-in", "name": "gentle", "adjusted": True}
    assert active == trial


def test_preview_from_cli_and_mcp(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("DAYMARK_DB", str(tmp_path / "tasks.sqlite3"))
    monkeypatch.setenv("DAYMARK_TZ", "UTC")
    assert main(["init"]) == 0
    for i in range(3):
        assert main(["add", f"Studio item {i}", "--id", f"item{i}"]) == 0
    capsys.readouterr()
    assert (
        main(
            [
                "advise",
                "--profile",
                "gentle",
                "--drop",
                "first-step",
                "--set",
                "shortlist.size=1",
                "--json",
            ]
        )
        == 0
    )
    preview = json.loads(capsys.readouterr().out)
    assert (preview["profile"], len(preview["picks"]), preview["held_back"]) == (
        "gentle (edited)",
        1,
        2,
    )
    store = Store(str(tmp_path / "tasks.sqlite3"))
    try:
        tasks = Tasks(store, timezone="UTC")
        args = {
            "view": "advise",
            "profile": "gentle",
            "set": ["shortlist.size=1"],
            "drop": ["first-step"],
        }
        reply = handle(
            tasks,
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "daymark_view", "arguments": args},
            },
        )
        assert not reply["result"]["isError"]
        assert "(2 more ready" in reply["result"]["content"][0]["text"]
        assert strategies.stored_profile(tasks) == PROFILES["plain"]
    finally:
        store.close()


def test_time_fit_uses_only_individual_recorded_estimates(tasks):
    tasks.add("Draft outline", id="short", minutes=15)
    tasks.add("Check minutes", id="edge", minutes=20, priority=1)
    tasks.add("Review packet", id="long", minutes=21, priority=1)
    tasks.add("Ask for an estimate", id="unknown")
    tasks.add("Later", id="later", start="+1d", minutes=10)
    tasks.add("Work elsewhere", id="other", scope="studio", minutes=2)
    advice = tasks.advise(profile="plain", settings=["time-fit.minutes=20"], scope="")
    assert [x["id"] for x in advice.picks] == ["edge", "short"]
    assert advice.held_back == 2
    assert (
        advice.notes[0].text
        == "2 individually estimated at 20 min or less. 1 estimated longer. 1 without an estimate."
    )
    assert len(tasks.ready(scope="")) == 4
    empty = tasks.advise(profile="plain", settings=["time-fit.minutes=5"], scope="")
    assert not empty.picks and empty.held_back == 4
    assert "No picks for this view." in advice_text(empty)
    assert "Nothing is ready" not in advice_text(empty)
    code("invalid", tasks.advise, settings=["time-fit.minutes=0"])


def test_scoped_frontier_filters_which_conditions_to_mention_not_what_moves_them(tasks):
    tasks.goal("Prepare publication", id="pub")
    tasks.goal("Arrange a delivery", id="delivery")
    tasks.add(
        "Signed proof",
        kind="condition",
        goal="pub",
        condition="unknown",
        id="proof",
        scope="studio",
    )
    tasks.add(
        "Print slot confirmed",
        kind="condition",
        goal="pub",
        condition="unmet",
        id="slot",
        scope="studio",
    )
    tasks.add(
        "Delivery window known",
        kind="condition",
        goal="delivery",
        condition="unknown",
        id="window",
        scope="home",
    )
    tasks.add("Check the proof", id="check", scope="home", advances=["proof"])
    view = tasks.explore(scope="studio")
    assert set(view["frontier"]) == {"proof", "slot"} and view["gaps"] == ["slot"]
    advice = tasks.advise(profile="assistant", scope="studio").to_dict()
    assert "window" not in json.dumps(advice)
    assert advice["suggestions"][0]["advances"] == ["slot"]
    assert tasks.explore(scope="")["conditions"] == []


def test_readiness_limit_message_requires_actual_truncation(tasks, monkeypatch):
    monkeypatch.setattr(strategies, "READY_LIMIT", 2)
    tasks.add("One", id="a")
    tasks.add("Two", id="b")
    tasks.add("Other scope", id="c", scope="studio")
    assert not tasks.advise(scope="").notes
    assert "first 2 of 3" in tasks.advise().notes[0].text


def test_shortlist_reads_only_the_ready_view(tasks, monkeypatch):
    def unneeded(*args, **kwargs):
        pytest.fail("A preview using only ready picks fetched unrelated workspace data")

    tasks.add("Task", id="sample")
    for method in ("find", "explore", "agenda"):
        monkeypatch.setattr(tasks, method, unneeded)
    assert [x["id"] for x in tasks.advise(settings=["shortlist"]).picks] == ["sample"]
    assert [x["id"] for x in tasks.advise(profile="gentle").picks] == ["sample"]
