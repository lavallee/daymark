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
