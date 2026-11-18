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


@pytest.mark.parametrize("ending", ["done", "cancel", "archive"])
@pytest.mark.parametrize("late_action", ["wait", "resume"])
def test_delayed_wait_cannot_restart_closed_work_or_reschedule_reminders(workers, ending, late_action):
    outgoing, incoming = workers
    outgoing.add("Review proof", id="proof")
    outgoing.add("Print edition", id="print", after="proof")
    outgoing.add("Review desk", kind="person", id="desk")
    reminder = outgoing.remind("proof", "2030-05-15 09:00", recipient="desk")
    outgoing.claim("proof")
    parked = outgoing.wait("proof", "approval", op="park")
    ended = getattr(incoming, ending)("proof")
    head = outgoing.engine.head("default")

    args = ("proof", "approval") if late_action == "wait" else ("proof",)
    with pytest.raises(DomainError, match="Unarchive|Reopen") as error:
        getattr(outgoing, late_action)(*args, op="delayed")
    assert error.value.code == "conflict"
    assert outgoing.engine.head("default") == head and not outgoing.engine.receipt("default", "delayed")
    assert outgoing.wait("proof", "approval", op="park") == parked
    assert outgoing.get("proof") == ended
    assert outgoing.get(reminder["id"])["reminder_state"] == "cancelled"
    assert [row["id"] for row in incoming.ready()] == ["print"]
    assert not outgoing.search(claimed=True)["items"]


def test_concurrent_completion_between_resume_read_and_write_is_preserved(tmp_path, monkeypatch):
    first, second = Store(tmp_path / "queue.db"), Store(tmp_path / "queue.db")
    try:
        outgoing, incoming = Tasks(first, actor="writer"), Tasks(second, actor="editor")
        outgoing.add("Review cover", id="cover", waiting_on="approval")
        execute = first.execute

        def interleave(*args, **kwargs):
            incoming.done("cover")
            return execute(*args, **kwargs)

        monkeypatch.setattr(first, "execute", interleave)
        with pytest.raises(DomainError, match="completed"):
            outgoing.resume("cover", op="late")
        assert incoming.get("cover")["status"] == "completed"
        assert first.head("default") == 2
    finally:
        second.close()
        first.close()


def test_legacy_resume_from_closed_state_replays_without_restarting_it_again(workers):
    outgoing, _ = workers
    outgoing.add("Review cover", id="cover")
    cancelled = outgoing.cancel("cover")
    stored = outgoing.engine.execute(
        "default", "historical", "update", {"id": "cover", "status": "open", "waiting_on": None},
        expected_version=cancelled["version"], actor="outgoing",
    )
    current = outgoing.done("cover")
    head = outgoing.engine.head("default")
    assert outgoing.resume("cover", op="historical") == stored
    assert outgoing.get("cover") == current and outgoing.engine.head("default") == head


@pytest.mark.parametrize("guard", ["expected_version", "review_token"])
@pytest.mark.parametrize("requested", [{"action": "resume"}, {"action": "wait", "on": "printer"}])
def test_delayed_printer_reply_preserves_the_new_wait_and_reminder(workers, guard, requested):
    outgoing, incoming = workers
    outgoing.add("Collect proof", id="proof")
    outgoing.add("Review desk", kind="person", id="desk")
    outgoing.wait("proof", "printer")
    review = outgoing.show("proof")
    condition = {guard: review["review_token"] if guard == "review_token" else 2}
    incoming.wait("proof", "editor", until="2099-07-03")
    reminder = incoming.remind("proof", "2099-07-05 09:00", recipient="desk")
    head = outgoing.engine.head("default")
    current = incoming.get("proof")
    with pytest.raises(DomainError) as stale:
        call(outgoing, "daymark_act", {"id": "proof", "op": "reply", **requested, **condition})
    assert stale.value.code == "conflict"
    assert incoming.get("proof") == current and incoming.get(reminder["id"]) == reminder
    assert outgoing.engine.head("default") == head and outgoing.engine.receipt("default", "reply") is None


def test_guarded_dated_wait_and_resume_replay_exact_results_across_clocks(workers):
    first, _ = workers
    earlier = Tasks(first.engine, now="2031-05-03T12:00:00Z", timezone="UTC")
    later = Tasks(first.engine, now="2031-05-10T12:00:00Z", timezone="UTC")
    earlier.add("Collect proof", id="proof")
    wait = {
        "action": "wait", "id": "proof", "op": "park", "on": "printer", "until": "tomorrow",
        "expected_version": 1, "review_token": earlier.brief("proof")["review_token"],
    }
    parked = call(earlier, "daymark_act", wait)
    assert earlier.get("proof")["earliest_start"]["date"] == "2031-05-04"
    resume = {
        "action": "resume", "id": "proof", "op": "printer-replied", "expected_version": 2,
        "review_token": earlier.brief("proof")["review_token"],
    }
    reopened = call(earlier, "daymark_act", resume)
    assert earlier.get("proof")["earliest_start"] is None
    latest = later.wait("proof", "editor")
    assert call(later, "daymark_act", wait) == parked
    assert call(later, "daymark_act", resume) == reopened
    assert later.get("proof") == latest and first.engine.head("default") == 4
    for changes in ({"until": "2031-05-12"}, {"expected_version": 2}, {"review_token": "0" * 32}):
        with pytest.raises(DomainError):
            call(later, "daymark_act", {**wait, **changes})
    assert first.engine.head("default") == 4


def test_cli_uses_original_review_for_wait_and_resume(tmp_path, capsys):
    db = tmp_path / "proof.db"
    base = ["--db", str(db), "--workspace", "lab", "--as", "worker"]
    store = Store(db)
    try:
        work = Tasks(store, "lab")
        work.add("Collect proof", id="proof")
        token = work.brief("proof")["review_token"]
        assert main([*base, "wait", "proof", "--on", "printer", "--if-version", "1", "--if-review", token]) == 0
        capsys.readouterr()
        reviewed = work.brief("proof")["review_token"]
        work.wait("proof", "editor")
        assert main([*base, "resume", "proof", "--if-version", "2", "--if-review", reviewed]) == 4
        assert work.get("proof")["waiting_on"] == "editor"
    finally:
        store.close()
