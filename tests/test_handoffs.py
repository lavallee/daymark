"""Session-to-session progress survives refused writes, retries and lease turnover."""

from datetime import timedelta

import pytest

from daymark import DomainError, Store, Tasks
from daymark.cli import main
from daymark.mcp import call
from daymark.models import normalize_now
from daymark.render import show_text


@pytest.fixture
def workers():
    store = Store(":memory:")
    try:
        yield Tasks(store, actor="outgoing"), Tasks(store, actor="incoming")
    finally:
        store.close()


def test_release_with_note_is_atomic_and_replays_after_next_claim(workers):
    outgoing, incoming = workers
    outgoing.add("Check draft attachment", id="draft", move="agent")
    held = outgoing.claim("draft")
    assert not incoming.ready()
    with pytest.raises(DomainError, match="claimed by another"):
        incoming.release("draft", note="Could not take over")
    assert incoming.get("draft") == held
    released = outgoing.release("draft", note="Page count checked. Review the index.", op="switch")
    assert [item["id"] for item in incoming.ready()] == ["draft"]
    assert [(n["author"], n["text"]) for n in released["notes"]] == [
        ("outgoing", "Page count checked. Review the index.")
    ]
    taken = incoming.claim("draft")
    assert (
        outgoing.release("draft", note="Page count checked. Review the index.", op="switch")
        == released
    )
    assert incoming.get("draft") == taken
    assert [event["action"] for event in incoming.show("draft")["history"]] == [
        "claim",
        "release",
        "note",
        "claim",
        "create",
    ]


def test_refused_completion_has_no_closing_note(workers):
    outgoing, _ = workers
    before = outgoing.propose("Review synthetic sample", id="sample")
    with pytest.raises(DomainError, match="Accept or dismiss"):
        outgoing.done("sample", note="Reviewed", op="finish")
    assert outgoing.get("sample") == before
    assert [e["action"] for e in outgoing.show("sample")["history"]] == ["create"]
    outgoing.accept("sample")
    completed = outgoing.done("sample", note="Reviewed", op="finish")
    assert outgoing.done("sample", note="Reviewed", op="finish") == completed
    assert [n["text"] for n in completed["notes"]] == ["Reviewed"]


def test_resume_partial_closing_note_from_older_version(workers):
    outgoing, _ = workers
    outgoing.add("Review sample", id="sample")
    outgoing.note("sample", "Reviewed", op="old-finish:note")
    completed = outgoing.done("sample", note="Reviewed", op="old-finish")
    assert [n["text"] for n in completed["notes"]] == ["Reviewed"]
    assert completed["status"] == "completed"


def test_show_explains_active_lease_and_omits_expired_lease(workers):
    outgoing, _ = workers
    outgoing.add("Proof draft", id="draft")
    held = outgoing.claim("draft")
    expiry = normalize_now(held["claim_until"])
    ctx = outgoing.show("draft")
    assert held["claim_until"] in show_text(ctx, now=expiry - timedelta(seconds=1))
    at_expiry = show_text(ctx, now=expiry)
    assert "claim expires" not in at_expiry and "claimed by" not in at_expiry


def test_cli_and_mcp_transfer_the_same_note(tmp_path, capsys):
    db = str(tmp_path / "sample.sqlite")
    for args in (
        ["add", "Proof draft", "--id", "draft"],
        ["claim", "draft"],
        ["release", "draft", "--note", "Checked spelling"],
    ):
        assert main(["--db", db, "--as", "writer", *args]) == 0
    store = Store(db)
    try:
        reviewer = Tasks(store, actor="reviewer")
        call(reviewer, "daymark_act", {"action": "claim", "id": "draft"})
        call(reviewer, "daymark_act", {"action": "release", "id": "draft", "text": "Checked index"})
    finally:
        store.close()
    assert main(["--db", db, "show", "draft"]) == 0
    text = capsys.readouterr().out
    assert "writer: Checked spelling" in text and "reviewer: Checked index" in text


def test_edit_waiting_reason_parks_work_and_omits_it_from_pickup(workers):
    outgoing, incoming = workers
    outgoing.add("Inspect sample", id="sample", move="agent")
    parked = outgoing.edit("sample", waiting_on="new sample", op="park")
    assert parked["status"] == "waiting"
    assert not incoming.ready() and incoming.take() is None
    assert outgoing.edit("sample", waiting_on="new sample", op="park") == parked
    with pytest.raises(DomainError, match="different request"):
        outgoing.edit("sample", waiting_on="another sample", op="park")
    assert outgoing.edit("sample", waiting_on="")["status"] == "waiting"
    assert incoming.take() is None
    outgoing.resume("sample")
    assert incoming.take()["id"] == "sample"


def test_edit_waiting_reason_preserves_proposals_closed_and_archived_states(workers):
    t, _ = workers
    for id in ("complete", "cancel", "archive"):
        t.add("Inspect sample", id=id)
    t.done("complete")
    t.cancel("cancel")
    t.archive("archive")
    t.propose("Inspect a proposed sample", id="proposal")
    for id in ("complete", "cancel", "archive", "proposal"):
        before = t.get(id)
        after = t.edit(id, waiting_on="reply")
        assert (after["status"], after["archived"]) == (before["status"], before["archived"])
    assert t.accept("proposal")["status"] == "waiting"


def test_cli_and_mcp_edit_park_open_work(tmp_path, capsys):
    file = str(tmp_path / "queue.db")
    assert main(["--db", file, "add", "Inspect sample", "--id", "sample", "--move", "agent"]) == 0
    assert main(["--db", file, "edit", "sample", "--waiting-on", "reply"]) == 0
    store = Store(file)
    try:
        t = Tasks(store, actor="worker")
        assert not t.ready() and t.take() is None
        t.resume("sample")
        call(t, "daymark_edit", {"id": "sample", "waiting_on": "another reply"})
        assert t.get("sample")["status"] == "waiting" and t.take() is None
    finally:
        store.close()
