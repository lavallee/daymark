"""A reminder's terminal outcome survives its own archive and transfer."""

from contextlib import closing
from datetime import datetime

import pytest

from daymark import Store, Tasks


@pytest.mark.parametrize("outcome", ["delivered", "acknowledged", "dismissed", "failed"])
def test_archive_preserves_dispatch_evidence_and_fences_retry_after_transfer(tmp_path, outcome):
    instant = "2030-05-02T09:00:00Z"
    work = "print"
    with closing(Store(tmp_path / "source.db")) as store:
        tasks = Tasks(store, work, actor="proof-reader")
        tasks.add("Reader", id="desk", kind="person")
        tasks.add("Proof", id="proof")
        reminder = tasks.remind("proof", instant, recipient="desk")
        ident = reminder["id"]
        held = store.claim_reminders("worker", now=instant)[0]
        success = outcome != "failed"
        delivery = {
            "success": success,
            "error": None if success else "Temporary recording failure",
            "now": instant,
            "lease_token": held["lease_token"],
        }
        reported = store.finish_delivery(work, ident, "worker", **delivery)
        if outcome in {"acknowledged", "dismissed"}:
            action = "reminder.ack" if outcome == "acknowledged" else "reminder.dismiss"
            store.execute(
                work, "outcome", action, {"id": ident}, expected_version=reported["version"]
            )
        closed = tasks.archive(ident, op="retire")
        assert closed["reminder_state"] == ("cancelled" if outcome == "failed" else outcome)
        assert closed["attempts"] == 1 and closed["delivery_key"] == reported["delivery_key"]
        assert closed["error"] == reported["error"]
        if outcome == "failed":
            assert datetime.fromisoformat(closed["retry_at"]) == datetime.fromisoformat(
                reported["retry_at"]
            )
        else:
            assert closed["retry_at"] is None
        native = store.export(work)
    for path in ("source.db", "restored.db"):
        with closing(Store(tmp_path / path)) as store:
            tasks = Tasks(store, work, actor="proof-reader")
            if path == "restored.db":
                store.import_snapshot(work, native, operation_id="transfer", expected_version=0)
            restored = tasks.unarchive(ident)
            assert restored["reminder_state"] == closed["reminder_state"]
            assert not store.claim_reminders("next-worker", now="2030-05-03T09:00:00Z")
            assert tasks.archive(ident, op="retire") == closed
            assert store.finish_delivery(work, ident, "worker", **delivery) == reported
            assert store.get(work, ident) == restored


@pytest.mark.parametrize("outcome", ["leased", "failed", "delivered"])
def test_native_scheduler_snapshot_merges_back_without_replacing_or_reviving_bytes(
    tmp_path, outcome
):
    from daymark import DomainError

    when = "2030-05-02T09:00:00Z"
    with closing(Store(tmp_path / "source.db")) as store:
        tasks = Tasks(store, "print", timezone="UTC")
        tasks.add("Proof", id="proof")
        tasks.add("Desk", kind="person", id="desk")
        reminder = tasks.remind("proof", when, recipient="desk")
        held = store.claim_reminders("recorder", now=when)[0]
        if outcome != "leased":
            store.finish_delivery(
                "print",
                reminder["id"],
                "recorder",
                outcome == "delivered",
                now=when,
                lease_token=held["lease_token"],
            )
        before = tasks.get(reminder["id"])
        snap = store.export("print")
        original_head = store.head("print")
        transfer = store.import_snapshot("print", snap, operation_id="return", expected_version=0)
        assert (
            store.import_snapshot("print", snap, operation_id="return", expected_version=0)
            == transfer
        )
        assert tasks.get(reminder["id"]) == before and store.head("print") == original_head

        changed = {
            **snap,
            "entities": [
                {"kind": "task", "title": "New", "workspace": "print", "id": "new"},
                {**before, "attempts": before["attempts"] + 1},
            ],
        }
        with pytest.raises(DomainError, match="Entity collision"):
            store.import_snapshot("print", changed, operation_id="different", expected_version=0)
        assert tasks.get(reminder["id"]) == before and not store.receipt("print", "different")
        with pytest.raises(DomainError) as missing:
            tasks.get("new")
        assert missing.value.code == "not_found"
