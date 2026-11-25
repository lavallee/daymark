"""Ask and answer, follow-ups and watching: work that passes between actors."""

import json

import pytest
from fastapi.testclient import TestClient

from daymark import DomainError, Store, Tasks
from daymark.api import create_app
from daymark.cli import main
from daymark.client import Client
from daymark.coordination import QUESTION, interval
from daymark.mcp import call


@pytest.fixture
def store():
    db = Store(":memory:")
    try:
        yield db
    finally:
        db.close()


@pytest.fixture
def pair(store):
    worker, person = Tasks(store, "w", actor="worker-a"), Tasks(store, "w", actor="pat")
    worker.add("Send the sample report", id="report", move="agent")
    return worker, person


def test_a_question_leaves_the_agent_queue_and_joins_the_decision_queue(pair):
    worker, person = pair
    worker.claim("report")
    asked = worker.ask("report", "Which format?", to="pat", options=["PDF", "HTML"], recommend=2)
    assert asked["next_move"] == "answer" and asked["claimed_by"] is None
    assert asked["status"] == "open"
    assert asked["notes"][-1]["text"] == "Question for pat: Which format?\n1. PDF\n2. HTML (recommended)"
    assert [t["id"] for t in person.ready(move="answer")] == ["report"]
    assert worker.ready(move="agent") == []
    assert worker.take() is None  # No agent picks it up while the decision is open.


def test_an_answer_returns_the_task_to_any_agent_not_only_the_asker(pair):
    worker, person = pair
    worker.ask("report", "Which format?", to="pat", options=["PDF", "HTML"])
    answered = person.answer("report", choice=2)
    assert answered["next_move"] == "agent"
    assert answered["metadata"][QUESTION]["answer"] == {"text": "HTML", "choice": 2, "by": "pat"}
    assert answered["notes"][-1]["text"] == "Answer from pat: option 2, HTML"
    # The session that asked may be gone. Another worker finds the work and the thread.
    other = Tasks(worker.engine, "w", actor="worker-b")
    assert other.take()["id"] == "report"
    assert [n["author"] for n in other.get("report")["notes"]] == ["worker-a", "pat"]


def test_free_text_answers_and_text_with_a_choice(pair):
    worker, person = pair
    worker.ask("report", "Who should receive it?", to="pat")
    assert person.answer("report", "The sample board")["notes"][-1]["text"] == "Answer from pat: The sample board"
    worker.ask("report", "Which format?", to="pat", options=["PDF", "HTML"])
    note = person.answer("report", "With the appendix", choice=1)["notes"][-1]["text"]
    assert note == "Answer from pat: With the appendix (option 1: PDF)"


def test_the_asker_cannot_answer_itself_but_can_relay_a_named_answer(pair):
    worker, _ = pair
    worker.ask("report", "Which format?", to="pat", options=["PDF", "HTML"])
    with pytest.raises(DomainError) as refused:
        worker.answer("report", choice=1)
    assert refused.value.code == "forbidden" and "--heard-from" in refused.value.details[1]
    relayed = worker.answer("report", choice=1, heard_from="pat")
    assert relayed["metadata"][QUESTION]["answer"] == {
        "text": "PDF", "choice": 1, "by": "pat", "relayed_by": "worker-a",
    }
    assert relayed["notes"][-1]["text"] == "Answer from pat: option 1, PDF (relayed by worker-a)"
    assert relayed["notes"][-1]["author"] == "worker-a"  # The record shows who wrote it.


def test_one_open_question_at_a_time_and_only_on_open_tasks(pair):
    worker, person = pair
    worker.ask("report", "Which format?", to="pat")
    with pytest.raises(DomainError, match="already has an unanswered question"):
        worker.ask("report", "And which paper?", to="pat")
    person.answer("report", "PDF")
    with pytest.raises(DomainError, match="no unanswered question"):
        person.answer("report", "PDF again")
    worker.done("report")
    with pytest.raises(DomainError, match="open task"):
        worker.ask("report", "Too late?", to="pat")


def test_asking_about_work_another_actor_holds_is_refused(pair, store):
    worker, _ = pair
    Tasks(store, "w", actor="worker-b").claim("report")
    with pytest.raises(DomainError, match="claimed by another owner"):
        worker.ask("report", "Which format?", to="pat")
    assert worker.get("report")["notes"] == []


@pytest.mark.parametrize(
    "kwargs",
    [
        {"question": " ", "to": "pat"},
        {"question": "Which?", "to": ""},
        {"question": "Which?", "to": "pat", "options": ["a"] * 2},
        {"question": "Which?", "to": "pat", "options": [str(n) for n in range(7)]},
        {"question": "Which?", "to": "pat", "options": ["a", "b"], "recommend": 3},
    ],
)
def test_a_malformed_question_writes_nothing(pair, kwargs):
    worker, _ = pair
    with pytest.raises(DomainError) as refused:
        worker.ask("report", kwargs.pop("question"), **kwargs)
    assert refused.value.code == "invalid"
    assert worker.get("report")["version"] == 1


def test_answer_validation(pair):
    worker, person = pair
    worker.ask("report", "Which format?", to="pat", options=["PDF", "HTML"])
    for bad in ({"choice": 3}, {}, {"text": " "}):
        with pytest.raises(DomainError) as refused:
            person.answer("report", **bad)
        assert refused.value.code == "invalid"
    assert "answer" not in person.get("report")["metadata"][QUESTION]


def test_ask_and_answer_replay_by_operation_id(pair):
    worker, person = pair
    first = worker.ask("report", "Which format?", to="pat", options=["PDF", "HTML"], op="ask-1")
    again = worker.ask("report", "Which format?", to="pat", options=["PDF", "HTML"], op="ask-1")
    assert again == first and len(worker.get("report")["notes"]) == 1
    with pytest.raises(DomainError, match="reused with a different request"):
        worker.ask("report", "Another question?", to="pat", op="ask-1")
    done = person.answer("report", choice=1, op="answer-1")
    assert person.answer("report", choice=1, op="answer-1") == done
    assert len(person.get("report")["notes"]) == 2


def test_a_followup_becomes_ready_work_for_an_agent_after_the_interval(store):
    start = "2026-03-02T09:00:00+00:00"
    worker = Tasks(store, "w", actor="worker-a", now=start, timezone="UTC")
    worker.add("Open the sample pull request", id="pr", move="agent")
    item = worker.followup("pr", "Check the sample build finished", after="10min", op="f-1")
    assert item["earliest_start"]["at"] == "2026-03-02T09:10:00Z"
    assert item["next_move"] == "agent"
    assert item["description"] == "Follow-up on pr: Open the sample pull request"
    assert item["metadata"]["daymark.followup"] == {"of": "pr", "by": "worker-a", "for": "worker-a", "after": "10min"}
    assert item["id"] not in [t["id"] for t in worker.ready(move="agent")]
    later = Tasks(store, "w", actor="worker-b", now="2026-03-02T09:11:00+00:00", timezone="UTC")
    assert item["id"] in [t["id"] for t in later.ready(move="agent")]
    # A retry after the clock moved keeps the first instant and creates nothing new.
    retry = Tasks(store, "w", actor="worker-a", now="2026-03-02T09:30:00+00:00", timezone="UTC")
    assert retry.followup("pr", "Check the sample build finished", after="10min", op="f-1")["id"] == item["id"]


@pytest.mark.parametrize("text", ["5m", "+10min", "10 minutes", "0min", "400d", "soon", ""])
def test_an_unrecognised_interval_lists_the_accepted_forms(text):
    with pytest.raises(DomainError) as refused:
        interval(text)
    assert "10min, 2h or 1d" in refused.value.details[0]


def test_intervals():
    assert interval("10min").total_seconds() == 600
    assert interval(" 2H ").total_seconds() == 7200
    assert interval("1d").total_seconds() == 86400


class Clock:
    """A clock that only moves when the watcher sleeps."""

    def __init__(self, during=None):
        self.now, self.sleeps, self.during = 0.0, 0, during or {}

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds
        self.sleeps += 1
        if act := self.during.get(self.sleeps):
            act()


def test_watch_returns_another_actors_change_and_ignores_its_own(pair):
    worker, person = pair
    clock = Clock({1: lambda: worker.note("report", "Still drafting"), 2: lambda: person.note("report", "Use the new logo")})
    result = worker.watch(["report"], timeout=60, sleep=clock.sleep, clock=clock)
    assert result["status"] == "event" and result["event"]["actor"] == "pat"
    assert result["event"]["note"]["text"] == "Use the new logo"
    assert clock.sleeps == 2
    assert worker.watch(["report"], after=result["cursor"], timeout=3, sleep=clock.sleep, clock=clock) == {
        "status": "timeout", "cursor": result["cursor"], "head": result["cursor"],
    }


def test_watch_sees_a_change_made_before_it_started_when_given_the_cursor(pair):
    worker, person = pair
    cursor = worker.ask("report", "Which format?", to="pat") and worker.activity()["cursor"]
    person.answer("report", "PDF")  # Lands before the asker starts watching.
    clock = Clock()
    result = worker.watch(["report"], after=cursor, timeout=5, sleep=clock.sleep, clock=clock)
    assert result["event"]["note"]["text"] == "Answer from pat: PDF" and clock.sleeps == 0
    # Without the cursor it starts at the newest event, so that answer is behind it.
    assert worker.watch(["report"], timeout=2, sleep=clock.sleep, clock=clock)["status"] == "timeout"


def test_watch_filters_by_task_and_actor_and_skips_nothing(pair):
    worker, person = pair
    person.add("Unrelated sample", id="other")
    head = worker.activity()["cursor"]
    person.note("other", "Noise")
    Tasks(worker.engine, "w", actor="sam").note("report", "From sam")
    person.note("report", "From pat")
    clock = Clock()
    watch = lambda **kw: worker.watch(timeout=1, sleep=clock.sleep, clock=clock, **kw)
    assert watch(ids=["report"], after=head)["event"]["actor"] == "sam"
    assert watch(ids=["report"], after=head, by="pat")["event"]["note"]["text"] == "From pat"
    assert watch(after=head)["event"]["id"] == "other"
    first = watch(ids=["report"], after=head)
    assert watch(ids=["report"], after=first["cursor"])["event"]["actor"] == "pat"
    own = worker.note("report", "Mine") and watch(ids=["report"], after=first["cursor"] + 1)
    assert own["status"] == "timeout"
    assert watch(ids=["report"], after=first["cursor"] + 1, include_own=True)["event"]["actor"] == "worker-a"


def test_watch_limits(pair):
    worker, _ = pair
    for bad in ({"timeout": -1}, {"timeout": 86401}, {"poll": 0}):
        with pytest.raises(DomainError):
            worker.watch(**bad)


def test_cli_round_trip_and_timeout_exit_code(tmp_path, capsys):
    db = str(tmp_path / "t.sqlite3")

    def run(*args, actor="worker-a"):
        code = main(["--db", db, "--as", actor, *args])
        return code, capsys.readouterr()

    assert run("init")[0] == 0
    run("add", "Send the sample report", "--move", "agent", "--json")
    id = json.loads(run("find", "sample", "--json")[1].out)[0]["id"]
    code, shown = run("ask", id, "Which format?", "--to", "pat", "--option", "PDF", "--option", "HTML", "--recommend", "2")
    assert code == 0 and "question for pat" in shown.out
    assert f"daymark answer --if-question q:default:{id}:" in run("show", id, actor="pat")[1].out
    code, refused = run("answer", id, "--choice", "1")
    assert code == 7 and "--heard-from" in refused.err
    assert run("answer", id, "--choice", "2", actor="pat")[0] == 0
    code, timed = run("watch", id, "--timeout", "0", "--by", "nobody", "--json")
    assert code == 8 and json.loads(timed.out)["status"] == "timeout"  # A timeout is not an answer.
    code, seen = run("watch", id, "--timeout", "0", "--after", "0", "--by", "pat")
    assert code == 0 and f"Keep watching: daymark watch {id} --after" in seen.out
    assert run("watch", id, "--timeout", "soon")[0] == 5
    code, bad = run("followup", id, "Check it", "--in", "5m")
    assert code == 5 and "10min, 2h or 1d" in bad.err
    assert run("followup", id, "Check the sample build", "--in", "2h")[0] == 0


def test_mcp_ask_answer_followup_and_watch(pair):
    worker, person = pair
    call(worker, "daymark_act", {"action": "ask", "id": "report", "text": "Which format?", "recipient": "pat", "set": ["PDF", "HTML"], "choice": 2})
    assert worker.get("report")["metadata"][QUESTION]["recommend"] == 2
    cursor = worker.activity()["cursor"]
    call(person, "daymark_act", {"action": "answer", "id": "report", "choice": 2})
    seen = call(worker, "daymark_view", {"view": "watch", "id": "report", "after": cursor})
    assert "Answer from pat: option 2, HTML" in seen and "view=watch after=" in seen
    made = call(worker, "daymark_act", {"action": "followup", "id": "report", "text": "Check the sample build", "when": "10min"})
    assert "Check the sample build" in made
    with pytest.raises(DomainError):
        call(worker, "daymark_act", {"action": "followup", "id": "report", "text": "x"})


def test_the_verbs_work_through_the_authenticated_service(store):
    app = create_app(store, {"agent": {"workspace": "w", "principal": "helper"}, "owner": {"workspace": "w", "principal": "owner"}})
    with TestClient(app) as http:
        def tasks(token):
            return Tasks(Client("http://testserver", token, transport=http._transport), "w")

        helper, owner = tasks("agent"), tasks("owner")
        helper.add("Send the sample report", id="report", move="agent")
        helper.claim("report")
        asked = helper.ask("report", "Which format?", to="owner", options=["PDF", "HTML"], op="ask-1")
        assert asked["next_move"] == "answer" and asked["claimed_by"] is None
        assert helper.ask("report", "Which format?", to="owner", options=["PDF", "HTML"], op="ask-1") == asked
        cursor = helper.activity()["cursor"]
        answered = owner.answer("report", choice=1)
        assert answered["next_move"] == "agent"
        # The service records its authenticated principal as the author.
        assert [n["author"] for n in answered["notes"]] == ["helper", "owner"]
        assert answered["metadata"][QUESTION]["by"] == "helper"
        seen = helper.watch(["report"], after=cursor, timeout=1, sleep=lambda _s: None)
        assert seen["event"]["actor"] == "owner"
        # The service's principal, not a local label, decides who the asker is.
        helper.ask("report", "And which paper?", to="owner")
        with pytest.raises(DomainError) as refused:
            helper.answer("report", "A4")
        assert refused.value.code == "forbidden"
        assert owner.watch(["report"], after=seen["cursor"], timeout=1, sleep=lambda _s: None)["event"]["actor"] == "helper"


def test_explore_names_conditions_to_reassess_after_advancing_work_finishes(store):
    def at(hour, actor):
        return Tasks(store, "w", actor=actor, now=f"2026-03-02T{hour:02}:00:00+00:00", timezone="UTC")

    lead = at(9, "lead")
    lead.add("Move the sample office", kind="goal", id="move")
    lead.condition("The sample lease is agreed", goal="move", id="lease")
    lead.add("Draft the sample lease", id="draft", advances=["lease"], move="agent")
    assert lead.explore("move")["to_reassess"] == []
    at(10, "worker").done("draft", note="Draft sent")
    view = at(10, "worker").explore("move")
    assert view["to_reassess"] == ["lease"]
    condition = view["conditions"][0]
    assert condition["finished_since_assessed"] == ["draft"]
    assert condition["state"] == "unknown"  # Finishing the work did not judge the condition.
    at(11, "lead").assess("lease", "partly_met", evidence="Draft sent, not signed")
    view = at(11, "lead").explore("move")
    assert view["to_reassess"] == [] and "finished_since_assessed" not in view["conditions"][0]
    at(12, "lead").assess("lease", "met", evidence="Countersigned")
    assert at(12, "lead").explore("move")["to_reassess"] == []


def test_done_names_what_to_reassess(tmp_path, capsys):
    db = str(tmp_path / "t.sqlite3")
    for args in (
        ["init"], ["goal", "add", "Move the sample office", "--id", "move"],
        ["condition", "The sample lease is agreed", "--goal", "move", "--id", "lease"],
        ["add", "Draft the sample lease", "--id", "draft", "--advances", "lease"],
    ):
        assert main(["--db", db, *args]) == 0
    capsys.readouterr()
    assert main(["--db", db, "done", "draft"]) == 0
    assert "daymark assess lease STATE --evidence TEXT" in capsys.readouterr().out
    assert main(["--db", db, "explore", "move"]) == 0
    shown = capsys.readouterr().out
    assert "reassess: draft finished since" in shown and "1 to reassess after finished work: lease" in shown
