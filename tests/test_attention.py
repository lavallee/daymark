"""Inbox, digest and seen marks: what reaches whom, and what waits."""

import json

import pytest

from daymark import DomainError, Store, Tasks
from daymark.cli import main
from daymark.coordination import mentions, summarise
from daymark.mcp import call


@pytest.fixture
def store():
    db = Store(":memory:")
    try:
        yield db
    finally:
        db.close()


def at(store, actor, minute=0):
    return Tasks(store, "w", actor=actor, now=f"2026-03-02T09:{minute:02}:00+00:00", timezone="UTC")


@pytest.fixture
def board(store):
    lead = at(store, "lead")
    lead.add("Choose the sample venue", id="venue", move="agent")
    lead.ask("venue", "Which venue?", to="pat", options=["Hall", "Park"], recommend=1)
    lead.add("Book the sample caterer", id="cater", move="agent")
    return lead


def test_a_question_is_in_the_inbox_of_exactly_the_actor_it_names(store, board):
    assert [q["id"] for q in at(store, "pat").inbox()["to_answer"]] == ["venue"]
    for other in ("Pat", "pat/phone", "pa", "lead"):
        assert at(store, other).inbox()["to_answer"] == []
    view = at(store, "pat").inbox()
    assert view["to_answer"][0]["question"] == "Which venue?" and view["to_answer"][0]["from"] == "lead"
    assert view["waking"] == 1


def test_an_answer_waits_for_the_asker_until_someone_acts_on_it(store, board):
    at(store, "pat", 5).answer("venue", choice=2)
    answered = at(store, "lead", 6).inbox()["answered"]
    assert answered[0]["id"] == "venue" and answered[0]["answer"]["text"] == "Park"
    assert at(store, "pat", 6).inbox()["answered"] == []  # Not the answerer's to act on.
    at(store, "worker", 7).note("venue", "Booked the park")
    assert at(store, "lead", 8).inbox()["answered"] == []


def test_a_followup_reaches_who_it_is_for_once_it_is_ready(store, board):
    board.followup("cater", "Check the caterer replied", after="10min", for_="sam")
    assert at(store, "sam", 5).inbox()["followups"] == []
    ready = at(store, "sam", 11).inbox()["followups"]
    assert [f["title"] for f in ready] == ["Check the caterer replied"]
    assert at(store, "lead", 11).inbox()["followups"] == []
    mine = board.followup("cater", "Check the deposit cleared", after="10min")
    assert mine["metadata"]["daymark.followup"]["for"] == "lead"
    at(store, "sam", 12).claim(ready[0]["id"])
    assert at(store, "sam", 12).inbox()["followups"] == []  # Claimed work is in hand, not waiting.


def test_mentions_are_exact_and_read_from_the_cursor(store, board):
    cursor = at(store, "lead").inbox()["cursor"]
    pat = at(store, "pat", 3)
    pat.note("cater", "@lead the caterer wants a deposit")
    pat.note("cater", "for @lead/build only, and `@lead` in code, and email lead@example.org")
    board.note("cater", "@lead note to self")
    view = at(store, "lead", 4).inbox(after=cursor)
    assert [m["text"] for m in view["mentions"]] == ["@lead the caterer wants a deposit"]
    assert at(store, "lead", 4).inbox()["mentions"] == []  # Without a cursor, mentions are not scanned.
    assert at(store, "lead", 4).inbox(after=view["cursor"])["mentions"] == []


@pytest.mark.parametrize(
    ("text", "name", "hit"),
    [
        ("@sam please", "sam", True), ("hi @sam.", "sam", True), ("@sam/build go", "sam", False),
        ("@samuel", "sam", False), ("x@sam", "sam", False), ("```\n@sam\n```", "sam", False),
        ("@sam/build go", "sam/build", True), ("@sam-2", "sam", False),
    ],
)
def test_mention_rules(text, name, hit):
    assert mentions(text, name) is hit


def test_digest_reads_without_marking_and_seen_only_moves_forward(store, board):
    pat = at(store, "pat", 5)
    first = pat.digest()
    assert first["since"] == 0 and first["unseen"] == first["through"] and [q["id"] for q in first["to_answer"]] == ["venue"]
    assert pat.digest()["unseen"] == first["unseen"]  # Reading the digest marks nothing.
    assert pat.mark_seen(through=first["through"])["seq"] == first["through"]
    assert pat.digest()["unseen"] == 0
    board.note("cater", "Asked for a quote")
    pat.note("cater", "My own note does not count")
    later = pat.digest()
    assert later["unseen"] == 1 and later["changes"][0]["latest"] == "Asked for a quote"
    assert pat.mark_seen(through=1)["seq"] == first["through"]  # Never back.
    with pytest.raises(DomainError):
        pat.mark_seen(through=later["head"] + 1)
    assert at(store, "sam").digest()["since"] == 0  # Each reader has their own mark.


def test_summary_caps_senders_tasks_and_preview():
    events = [{"seq": n, "id": f"t{n % 10}", "title": f"Task {n % 10}", "actor": f"a{n % 5}",
               "action": "note", "note": {"text": "x" * 300}} for n in range(1, 61)]
    shown, held = summarise(events, "reader")
    assert len(shown) == 8 and held == 2
    assert shown[0]["by"][-1] == "2 more" or len(shown[0]["by"]) <= 3
    assert len(shown[0]["latest"]) == 100 and shown[0]["latest"].endswith("…")


def test_cli_inbox_count_digest_and_seen(tmp_path, capsys):
    db = str(tmp_path / "t.sqlite3")

    def run(*args, actor="lead"):
        code = main(["--db", db, "--as", actor, *args])
        return code, capsys.readouterr()

    run("init")
    run("add", "Choose the sample venue", "--id", "venue")
    run("ask", "venue", "Which venue?", "--to", "pat", "--option", "Hall", "--option", "Park")
    code, counted = run("inbox", "--for", "pat", "--count")
    signal = json.loads(counted.out)
    assert code == 0 and signal["to_answer"] == 1 and signal["waking"] == 1 and "question" not in counted.out
    code, shown = run("digest", actor="pat")
    assert code == 0 and "Waiting on your answer (1)" in shown.out and "daymark seen --for pat --through" in shown.out
    through = shown.out.rsplit("--through ", 1)[1].split()[0]
    assert run("seen", "--through", through, actor="pat")[0] == 0
    assert "Nothing new" not in run("digest", actor="pat")[1].out  # The question still waits.
    code, text = run("inbox", actor="pat")
    assert "answer  venue  Which venue?  (from lead)" in text.out and "Next read: daymark inbox --for pat --after" in text.out


def test_mcp_inbox_digest_and_seen(store, board):
    pat = at(store, "pat", 5)
    assert "answer  venue" in call(pat, "daymark_view", {"view": "inbox"})
    shown = call(pat, "daymark_view", {"view": "digest"})
    assert "action=seen" in shown
    through = int(shown.rsplit("expected_version=", 1)[1].split()[0])
    assert "has seen through" in call(pat, "daymark_act", {"action": "seen", "expected_version": through})
    assert call(pat, "daymark_view", {"view": "digest", "json": True}) and pat.digest()["unseen"] == 0
    assert "answer  venue" in call(board, "daymark_view", {"view": "inbox", "by": "pat"})


def test_inbox_keys_name_one_item_and_hold_while_it_waits(store, board):
    pat = at(store, "pat", 5)
    key = pat.inbox()["to_answer"][0]["key"]
    assert key.startswith("q:w:venue:") and len(key) > len("q:w:venue:")
    pat.note("venue", "Looking at both")  # The task changes; the question is the same.
    assert pat.inbox()["to_answer"][0]["key"] == key
    assert pat.inbox()["to_answer"][0]["options"] == ["Hall", "Park"]
    pat.answer("venue", choice=1)
    answered = at(store, "lead", 6).inbox()["answered"][0]
    assert answered["key"] == "a" + key[1:] and answered["question"] == "Which venue?"
    board.ask("venue", "And which date?", to="pat")
    assert pat.inbox()["to_answer"][0]["key"] != key  # A new question is a new item.
    cursor = board.activity()["head"]
    pat.note("cater", "@lead deposit due")
    mention = at(store, "lead", 7).inbox(after=cursor)["mentions"][0]
    assert mention["key"] == f"m:w:{mention['seq']}"
    board.followup("cater", "Check the deposit", after="10min")
    assert at(store, "lead", 20).inbox()["followups"][0]["key"].startswith("f:w:")


def test_delayed_answer_matches_question_across_edits_retries_and_replacement(store, board):
    pat = at(store, "pat")
    key = pat.inbox()["to_answer"][0]["key"]
    board.note("venue", "Ordinary progress")
    answered = pat.answer("venue", "Park", question_key=key, op="answer-first")
    board.ask("venue", "Change the time?", to="pat")
    next_key = pat.inbox()["to_answer"][0]["key"]
    assert next_key != key
    assert pat.answer("venue", "Park", question_key=key, op="answer-first") == answered
    with pytest.raises(DomainError, match="open question changed"):
        pat.answer("venue", "Delayed", question_key=key, op="answer-late")
    assert pat.inbox()["to_answer"][0]["key"] == next_key
    pat.answer("venue", "Yes", question_key=next_key)


def test_cli_and_mcp_answers_guard_the_same_inbox_key(tmp_path, capsys):
    from contextlib import closing

    from daymark.mcp import handle

    path = str(tmp_path / "tasks.db")
    with closing(Store(path)) as db:
        lead, pat = at(db, "lead"), at(db, "pat")
        lead.add("Select venue", id="venue")
        lead.ask("venue", "First question?", to="pat")
        first = pat.inbox()["to_answer"][0]["key"]
        lead.note("venue", "Progress")

        def cli(key, op):
            code = main(["--db", path, "--workspace", "w", "--as", "pat", "answer", "venue", "Hall",
                         "--if-question", key, "--op", op, "--json"])
            return code, capsys.readouterr()

        def mcp(key, op):
            return handle(pat, {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {
                "name": "daymark_act", "arguments": {"action": "answer", "id": "venue",
                    "text": "Hall", "question_key": key, "op": op}}})["result"]

        code, original = cli(first, "first")
        assert code == 0
        lead.ask("venue", "Second question?", to="pat")
        second = pat.inbox()["to_answer"][0]["key"]
        head = lead.activity()["head"]
        code, refused = cli(first, "late-cli")
        assert code != 0 and "conflict" in refused.err
        assert mcp(first, "late-mcp")["structuredContent"]["error"]["code"] == "conflict"
        code, replay = cli(first, "first")
        assert code == 0 and replay.out == original.out
        assert lead.activity()["head"] == head and pat.inbox()["to_answer"][0]["key"] == second
        receipt = mcp(second, "second")
        assert not receipt.get("isError") and pat.inbox()["to_answer"] == []
        assert mcp(second, "second") == receipt
