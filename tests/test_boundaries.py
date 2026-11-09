"""Independent adversarial checks, using only synthetic records and public contracts."""

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier

import pytest
from pydantic import ValidationError

from daymark.models import DomainError, Recurrence, Temporal, next_occurrence
from daymark.store import Store
from daymark.worker import RecordingSink, Worker

NOW = datetime(2026, 11, 9, 12, tzinfo=UTC)


def temporal(at, zone="America/New_York"):
    return Temporal(at=datetime.fromisoformat(at), timezone=zone)


def recurring(anchor, mode="fixed", rule="FREQ=DAILY"):
    return Recurrence(rule=rule, mode=mode, timezone=anchor.timezone, anchor=anchor)


def test_dst_gap_moves_forward_preserving_minutes():
    anchor = temporal("2026-03-07T02:30:00-05:00")
    following = next_occurrence(recurring(anchor), anchor, NOW)
    assert following.local().isoformat() == "2026-03-08T03:30:00-04:00"
    # The next scheduled occurrence returns to the anchor wall time.
    assert (
        next_occurrence(recurring(anchor), following, NOW).local().isoformat()
        == "2026-03-09T02:30:00-04:00"
    )


def test_dst_overlap_uses_first_fold_and_completion_uses_local_day():
    anchor = temporal("2026-10-31T01:30:00-04:00")
    following = next_occurrence(recurring(anchor), anchor, NOW)
    assert following.local().isoformat() == "2026-11-01T01:30:00-04:00"
    assert following.local().fold == 0
    completed = datetime.fromisoformat("2026-11-02T02:30:00+00:00")  # Nov 1 21:30 local
    result = next_occurrence(recurring(anchor, "after_completion"), anchor, completed)
    assert result.local().isoformat() == "2026-11-02T21:30:00-05:00"


def test_date_deadline_expires_only_after_local_day():
    due = Temporal(date="2026-11-01", timezone="America/New_York")
    assert not due.is_before(datetime.fromisoformat("2026-11-02T04:59:59+00:00"))
    assert due.is_before(datetime.fromisoformat("2026-11-02T05:00:00+00:00"))
    with pytest.raises(ValidationError):
        Temporal(at="2026-11-01T01:30:00", timezone="America/New_York")


def create(store, workspace, entity_id, kind="task", **fields):
    return store.execute(
        workspace,
        f"create-{entity_id}",
        "create",
        {"id": entity_id, "kind": kind, **fields},
        expected_version=0,
    )


def seed_reminder(store, reminder_id="reminder", **fields):
    create(store, "w", "person", "person")
    create(store, "w", "task")
    return create(
        store,
        "w",
        reminder_id,
        "reminder",
        recipient_id="person",
        task_id="task",
        scheduled_at=(NOW - timedelta(seconds=1)).isoformat(),
        **fields,
    )


def assert_code(code, function, *args, **kwargs):
    with pytest.raises(DomainError) as error:
        function(*args, **kwargs)
    assert error.value.code == code


def test_root_caller_connection_never_commits_and_rollback_removes_operations(tmp_path):
    path = tmp_path / "embedded.sqlite3"
    connection = sqlite3.connect(path)
    store = Store(connection)
    connection.commit()  # Caller commits initial schema before the scenario.
    # A root write must not release an implicit root savepoint and commit.
    connection.execute("BEGIN")
    create(store, "w", "rolled-back")
    connection.rollback()
    assert_code("not_found", store.get, "w", "rolled-back")
    # Operation record must roll back too, so same ID with another payload is fresh.
    result = store.execute(
        "w", "create-rolled-back", "create", {"id": "different", "kind": "task"}, expected_version=0
    )
    assert result["id"] == "different"
    connection.rollback()
    connection.close()


def test_caller_root_write_without_explicit_begin_is_rollbackable(tmp_path):
    connection = sqlite3.connect(tmp_path / "implicit.sqlite3")
    store = Store(connection)
    connection.commit()  # Isolate implicit data transaction from schema migration.
    create(store, "w", "root")
    assert connection.in_transaction
    connection.rollback()
    assert_code("not_found", store.get, "w", "root")
    connection.close()


def test_replay_hash_version_and_failed_operation_are_atomic(tmp_path):
    store = Store(tmp_path / "replay.db")
    payload = {"id": "a", "kind": "task", "title": "synthetic"}
    first = store.execute("w", "stable", "create", payload, expected_version=0)
    assert (
        store.execute(
            "w", "stable", "create", dict(reversed(list(payload.items()))), expected_version=0
        )
        == first
    )
    assert_code(
        "conflict",
        store.execute,
        "w",
        "stable",
        "create",
        {**payload, "title": "changed"},
        expected_version=0,
    )
    assert_code(
        "conflict",
        store.execute,
        "w",
        "stale",
        "update",
        {"id": "a", "title": "stale"},
        expected_version=0,
    )
    assert store.get("w", "a")["title"] == "synthetic"
    updated = store.execute(
        "w", "stale", "update", {"id": "a", "title": "correct"}, expected_version=first["version"]
    )
    assert updated["version"] == first["version"] + 1


@pytest.mark.parametrize(
    "field,kind",
    [
        ("parent_id", "task"),
        ("project_id", "project"),
        ("assignee_id", "person"),
        ("task_id", "task"),
        ("goal_id", "goal"),
        ("depends_on", "task"),
        ("beneficiary_ids", "person"),
        ("label_ids", "label"),
        ("context_ids", "context"),
        ("recipient_id", "person"),
    ],
)
def test_foreign_links_cannot_cross_workspace(tmp_path, field, kind):
    store = Store(tmp_path / "foreign.db")
    create(store, "foreign", "hidden", kind)
    fields = {
        field: ["hidden"]
        if field in {"depends_on", "beneficiary_ids", "label_ids", "context_ids"}
        else "hidden"
    }
    if field == "recipient_id":
        fields["scheduled_at"] = NOW.isoformat()
    if field == "task_id":
        create(store, "local", "person", "person")
        fields.update(recipient_id="person", scheduled_at=NOW.isoformat())
    with pytest.raises(DomainError):
        create(
            store,
            "local",
            "attempt",
            "reminder" if field in {"task_id", "recipient_id"} else "task",
            **fields,
        )
    assert store.list("local") == ([] if field != "task_id" else [store.get("local", "person")])


def test_concurrent_claims_lease_expiry_and_old_owner_fencing(tmp_path):
    path = tmp_path / "leases.db"
    store = Store(path)
    seed_reminder(store)
    barrier = Barrier(2)

    def claim(owner):
        engine = Store(path)
        barrier.wait()
        return engine.claim_reminders(owner, now=NOW, lease_seconds=10)

    with ThreadPoolExecutor(max_workers=2) as executor:
        a, b = list(executor.map(claim, ["owner-a", "owner-b"]))
    assert len(a) + len(b) == 1
    original = (a or b)[0]
    assert not store.claim_reminders("owner-c", now=NOW + timedelta(seconds=9))
    new = store.claim_reminders("owner-c", now=NOW + timedelta(seconds=11), lease_seconds=10)
    assert len(new) == 1 and new[0]["delivery_key"] == original["delivery_key"]
    with pytest.raises(DomainError):
        store.finish_delivery(
            "w",
            "reminder",
            original["lease_owner"],
            True,
            lease_token=original["lease_token"],
            now=NOW + timedelta(seconds=12),
        )
    store.finish_delivery(
        "w",
        "reminder",
        "owner-c",
        True,
        lease_token=new[0]["lease_token"],
        now=NOW + timedelta(seconds=12),
    )
    assert not store.claim_reminders("owner-d", now=NOW + timedelta(days=1))


def test_retry_preserves_delivery_key_and_suppresses_completed_task(tmp_path):
    store = Store(tmp_path / "retry.db")
    seed_reminder(store)
    claim = store.claim_reminders("one", now=NOW)[0]
    store.finish_delivery(
        "w",
        "reminder",
        "one",
        False,
        lease_token=claim["lease_token"],
        error="synthetic_failure",
        now=NOW,
    )
    assert not store.claim_reminders("two", now=NOW)
    retry = store.claim_reminders("two", now=NOW + timedelta(hours=2))[0]
    assert retry["delivery_key"] == claim["delivery_key"]
    store.execute(
        "w",
        "complete",
        "complete",
        {"id": "task"},
        expected_version=store.get("w", "task")["version"],
    )
    # Finishing a stale claim must never mark it successfully delivered.
    try:
        store.finish_delivery(
            "w",
            "reminder",
            "two",
            True,
            lease_token=retry["lease_token"],
            now=NOW + timedelta(hours=2, seconds=1),
        )
    except DomainError:
        pass
    assert store.get("w", "reminder")["reminder_state"] != "delivered"
    assert not store.claim_reminders("three", now=NOW + timedelta(days=1))


def test_worker_recording_default_and_error_redaction(tmp_path, caplog):
    store = Store(tmp_path / "worker.db")
    seed_reminder(store)
    worker = Worker(store)
    assert isinstance(worker.sink, RecordingSink)
    assert worker.run_once(NOW) == {"claimed": 1, "recorded": 1, "failed": 0, "suppressed": 0}
    assert len(worker.sink.delivery_keys) == 1
    assert worker.run_once(NOW + timedelta(seconds=1))["claimed"] == 0
    create(store, "w", "bad", "reminder", recipient_id="person", scheduled_at=NOW.isoformat())

    class BrokenSink:
        def deliver(self, reminder, *, delivery_key):
            raise RuntimeError("private message and secret token")

    assert Worker(store, BrokenSink()).run_once(NOW)["failed"] == 1
    assert "private" not in caplog.text and "secret" not in caplog.text
    assert "private" not in str(store.get("w", "bad"))


def test_import_foreign_reference_rolls_back_entire_snapshot(tmp_path):
    import copy

    source = Store(tmp_path / "source.db")
    create(source, "w", "first")
    create(source, "w", "second")
    snapshot = copy.deepcopy(source.export("w"))
    snapshot["entities"][1]["parent_id"] = "missing-parent"
    destination = Store(tmp_path / "destination.db")
    with pytest.raises(DomainError):
        destination.import_snapshot("w", snapshot, operation_id="bad-import", expected_version=0)
    assert destination.list("w") == []
    assert destination.events("w") == []


def test_lease_finish_after_expiry_without_reclaim_is_rejected(tmp_path):
    store = Store(tmp_path / "expired.db")
    seed_reminder(store)
    claim = store.claim_reminders("owner", now=NOW, lease_seconds=1)[0]
    with pytest.raises(DomainError):
        store.finish_delivery(
            "w",
            "reminder",
            "owner",
            True,
            lease_token=claim["lease_token"],
            now=NOW + timedelta(seconds=2),
        )
    assert store.get("w", "reminder")["reminder_state"] != "delivered"


def test_store_initialization_cannot_commit_unrelated_caller_work(tmp_path):
    connection = sqlite3.connect(tmp_path / "application.db")
    connection.execute("CREATE TABLE application_notes(value TEXT)")
    connection.commit()
    connection.execute("INSERT INTO application_notes VALUES ('synthetic pending')")
    assert connection.in_transaction
    store = Store(connection)
    create(store, "w", "uncommitted")
    connection.rollback()
    assert connection.execute("SELECT count(*) FROM application_notes").fetchone()[0] == 0
    connection.close()


def test_hostile_query_is_bounded_and_never_executes_sql(tmp_path):
    store = Store(tmp_path / "queries.db")
    create(store, "w", "safe")
    attacks = [
        {"field": "title); DROP TABLE daymark_entities;--", "op": "eq", "value": "x"},
        {"op": "raw_sql", "value": "SELECT * FROM daymark_entities"},
    ]
    nested = {"field": "status", "op": "eq", "value": "open"}
    for _ in range(100):
        nested = {"and": [nested]}
    attacks.append(nested)
    for attack in attacks:
        with pytest.raises(DomainError):
            store.query("w", attack)
    assert store.get("w", "safe")["id"] == "safe"


def test_finite_recurrence_stops_at_rule_count():
    first = Temporal(date="2026-11-09", timezone="America/New_York")
    rule = recurring(first, rule="FREQ=DAILY;COUNT=2")
    second = next_occurrence(rule, first, NOW)
    assert second.date.isoformat() == "2026-11-10"
    assert next_occurrence(rule, second, NOW) is None


def test_recompletion_cannot_duplicate_same_series_occurrence(tmp_path):
    store = Store(tmp_path / "recurrence.db")
    planned = {"date": "2026-11-09", "timezone": "America/New_York"}
    task = create(
        store,
        "w",
        "series",
        planned=planned,
        recurrence={
            "rule": "FREQ=DAILY;COUNT=2",
            "timezone": "America/New_York",
            "anchor": planned,
        },
    )
    payload = {"id": "series"}
    finished = store.execute("w", "done", "complete", payload, expected_version=task["version"])
    assert len(store.list("w", kind="task")) == 2
    assert (
        store.execute("w", "done", "complete", payload, expected_version=task["version"])
        == finished
    )
    reopened = store.execute("w", "again", "reopen", payload, expected_version=finished["version"])
    store.execute("w", "redone", "complete", payload, expected_version=reopened["version"])
    tasks = store.list("w", kind="task")
    assert len(tasks) == 2
    keys = [item["occurrence_key"] for item in tasks if item.get("occurrence_key")]
    assert len(keys) == len(set(keys))


def test_worker_uses_fresh_completion_time_and_fences_late_outcome(tmp_path, monkeypatch):
    import daymark.worker as worker_module

    store = Store(tmp_path / "late-worker.db")
    seed_reminder(store)
    clock = [NOW]

    class Clock:
        @staticmethod
        def now(zone):
            return clock[0]

    class SlowSink:
        def deliver(self, reminder, *, delivery_key):
            clock[0] += timedelta(seconds=120)

    monkeypatch.setattr(worker_module, "datetime", Clock)
    worker = Worker(store, SlowSink(), lease_seconds=10)
    result = worker.run_once()
    assert result == {"claimed": 1, "recorded": 0, "failed": 1, "suppressed": 0}
    assert store.get("w", "reminder")["reminder_state"] != "delivered"
    # A new pass reclaims with a different owner, while keeping the delivery key.
    worker.sink = RecordingSink()
    assert worker.run_once()["recorded"] == 1


@pytest.mark.parametrize(
    "action,target", [("reminder.cancel", "reminder"), ("cancel", "task"), ("complete", "task")]
)
def test_worker_suppresses_cancellation_between_claim_and_send(tmp_path, action, target):
    store = Store(tmp_path / "cancel-before-send.db")
    seed_reminder(store)

    class CancelAfterClaim:
        def __getattr__(self, name):
            return getattr(store, name)

        def claim_reminders(self, *args, **kwargs):
            result = store.claim_reminders(*args, **kwargs)
            store.execute(
                "w",
                "cancel-during-dispatch",
                action,
                {"id": target},
                expected_version=store.get("w", target)["version"],
            )
            return result

    sink = RecordingSink()
    result = Worker(CancelAfterClaim(), sink).run_once(NOW)
    assert result == {"claimed": 1, "recorded": 0, "failed": 0, "suppressed": 1}
    assert not sink.delivery_keys


def test_sink_success_then_persistence_crash_replays_same_key(tmp_path):
    import os
    import subprocess
    import sys
    import textwrap
    from pathlib import Path

    path, receiver = tmp_path / "after-delivery.db", tmp_path / "receiver.db"
    store = Store(path)
    seed_reminder(store)
    script = textwrap.dedent("""
        import os
        import sqlite3
        import sys
        from datetime import datetime
        from daymark.store import Store
        from daymark.worker import Worker

        engine = Store(sys.argv[1])
        class Receiver:
            def deliver(self, reminder, *, delivery_key):
                with sqlite3.connect(sys.argv[2]) as conn:
                    conn.execute('CREATE TABLE IF NOT EXISTS effects(key TEXT PRIMARY KEY)')
                    conn.execute('CREATE TABLE IF NOT EXISTS calls(key TEXT)')
                    conn.execute('INSERT INTO calls VALUES (?)', (delivery_key,))
                    conn.execute('INSERT OR IGNORE INTO effects VALUES (?)', (delivery_key,))
        class CrashAfterSink:
            def __getattr__(self, name):
                return getattr(engine, name)
            def finish_delivery(self, *args, **kwargs):
                os._exit(23)
        backend = CrashAfterSink() if sys.argv[4] == 'crash' else engine
        Worker(backend, Receiver(), lease_seconds=10).run_once(datetime.fromisoformat(sys.argv[3]))
    """)
    environment = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src")}
    args = [sys.executable, "-c", script, str(path), str(receiver)]
    crashed = subprocess.run(
        [*args, NOW.isoformat(), "crash"],
        env=environment,
        capture_output=True,
        timeout=20,
        check=False,
    )
    assert crashed.returncode == 23
    ambiguous = store.get("w", "reminder")
    assert ambiguous["reminder_state"] == "leased" and ambiguous["attempts"] == 1
    recovered = subprocess.run(
        [*args, (NOW + timedelta(seconds=11)).isoformat(), "recover"],
        env=environment,
        capture_output=True,
        timeout=20,
        check=False,
    )
    assert recovered.returncode == 0, recovered.stderr.decode()
    with sqlite3.connect(receiver) as conn:
        keys = [row[0] for row in conn.execute("SELECT key FROM calls")]
        assert len(keys) == 2 and keys[0] == keys[1]
        assert conn.execute("SELECT count(*) FROM effects").fetchone()[0] == 1
    outcome = Store(path).get("w", "reminder")
    assert outcome["reminder_state"] == "delivered" and outcome["attempts"] == 2


def test_cancel_after_final_check_cannot_unsend_but_cannot_record_success(tmp_path):
    from threading import Event

    store = Store(tmp_path / "cancel-after-check.db")
    seed_reminder(store)
    checked, cancelled = Event(), Event()
    sink = RecordingSink()

    class PausedAfterCheck(Worker):
        def _ready(self, claim, now):
            result = super()._ready(claim, now)
            checked.set()
            assert cancelled.wait(timeout=10)
            return result

    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(PausedAfterCheck(store, sink).run_once, NOW)
        assert checked.wait(timeout=10)
        try:
            store.execute(
                "w",
                "cancel-at-boundary",
                "reminder.cancel",
                {"id": "reminder"},
                expected_version=store.get("w", "reminder")["version"],
            )
        finally:
            cancelled.set()
        result = future.result(timeout=10)
    # The sink may already have been authorized by the preflight. Be explicit
    # about this residual race rather than claiming cancellation can unsend.
    assert len(sink.delivery_keys) == 1
    assert result["recorded"] == 0 and result["failed"] == 1
    assert store.get("w", "reminder")["reminder_state"] == "cancelled"


def test_snapshot_restart_preserves_event_cursor_and_original_operation_results(tmp_path):
    source = Store(tmp_path / "snapshot-source.db")
    original = create(source, "w", "restored", title="first")
    payload = {"id": "restored", "title": "second"}
    second = source.execute(
        "w", "edit-second", "update", payload, expected_version=original["version"]
    )
    latest = source.execute(
        "w",
        "edit-third",
        "update",
        {"id": "restored", "title": "third"},
        expected_version=second["version"],
    )
    source_events = source.events("w", after=0, limit=1000)
    snapshot = source.export("w")
    target_path = tmp_path / "snapshot-target.db"
    target = Store(target_path)
    target.import_snapshot("w", snapshot, operation_id="restore-once", expected_version=0)
    close = getattr(target, "close", None)
    if close:
        close()
    target = Store(target_path)
    baseline_events = target.events("w", after=0, limit=1000)
    # Prior operation results can differ from current state; replay returns the
    # original result and must neither rewind the entity nor append an event.
    assert (
        target.execute("w", "edit-second", "update", payload, expected_version=original["version"])
        == second
    )
    assert target.get("w", "restored") == latest
    assert target.events("w", after=0, limit=1000) == baseline_events
    assert_code(
        "conflict",
        target.execute,
        "w",
        "edit-second",
        "update",
        {"id": "restored", "title": "different"},
        expected_version=original["version"],
    )
    for event in source_events:
        assert event in baseline_events
    assert (
        target.events("w", after=source_events[0]["seq"], limit=1000)[: len(source_events) - 1]
        == source_events[1:]
    )
    # Import itself is an idempotent write, including after process restart.
    state_before = target.export("w")
    target.import_snapshot("w", snapshot, operation_id="restore-once", expected_version=0)
    assert target.export("w") == state_before


def test_root_and_nested_rollback_remove_intent_events_and_replay_ledger(tmp_path):
    path = tmp_path / "nested.db"
    connection = sqlite3.connect(path)
    store = Store(connection)
    create(store, "w", "person", "person")
    create(store, "w", "task")
    connection.commit()  # Only the application owns the root commit.
    baseline = store.export("w")
    connection.commit()  # Export's caller-owned read transaction is caller-owned too.
    observer = Store(path)
    connection.execute("BEGIN")
    create(store, "w", "keep-in-root")
    connection.execute("SAVEPOINT application_nested")
    create(
        store,
        "w",
        "nested-reminder",
        "reminder",
        recipient_id="person",
        task_id="task",
        scheduled_at=NOW.isoformat(),
    )
    assert observer.list("w") == baseline["entities"]
    assert observer.events("w", limit=1000) == baseline["events"]
    connection.execute("ROLLBACK TO application_nested")
    connection.execute("RELEASE application_nested")
    assert store.get("w", "keep-in-root")["id"] == "keep-in-root"
    assert_code("not_found", store.get, "w", "nested-reminder")
    # The nested operation hash/result rolled back, not just its current row.
    store.execute(
        "w",
        "create-nested-reminder",
        "create",
        {"id": "fresh-after-nested", "kind": "task"},
        expected_version=0,
    )
    connection.rollback()
    assert observer.export("w") == baseline
    assert observer.list("w", kind="reminder") == []
    connection.close()


@pytest.mark.parametrize("stale_success", [True, False])
def test_same_owner_aba_rejects_stale_success_and_failure_without_mutation(tmp_path, stale_success):
    store = Store(tmp_path / "same-owner-aba.db")
    seed_reminder(store)
    first = store.claim_reminders("same-owner", now=NOW, lease_seconds=10)[0]
    second = store.claim_reminders("same-owner", now=NOW + timedelta(seconds=11), lease_seconds=10)[
        0
    ]
    assert first["lease_owner"] == second["lease_owner"] == "same-owner"
    assert first["lease_token"] != second["lease_token"]
    assert first["delivery_key"] == second["delivery_key"]
    current, events = store.get("w", "reminder"), store.events("w", limit=1000)
    assert_code(
        "conflict",
        store.finish_delivery,
        "w",
        "reminder",
        "same-owner",
        stale_success,
        lease_token=first["lease_token"],
        error=None if stale_success else "old_failure",
        now=NOW + timedelta(seconds=12),
    )
    assert store.get("w", "reminder") == current
    assert store.events("w", limit=1000) == events
    store.finish_delivery(
        "w",
        "reminder",
        "same-owner",
        True,
        lease_token=second["lease_token"],
        now=NOW + timedelta(seconds=12),
    )
    assert store.get("w", "reminder")["reminder_state"] == "delivered"


def test_default_recording_sink_does_not_succeed_for_unsupported_channel(tmp_path):
    store = Store(tmp_path / "unsupported-channel.db")
    seed_reminder(store, channel="unsupported-synthetic")
    sink = RecordingSink()
    counts = Worker(store, sink).run_once(NOW)
    assert counts["failed"] == 1 and counts["recorded"] == 0
    assert not sink.delivery_keys
    assert store.get("w", "reminder")["reminder_state"] != "delivered"


def test_snapshot_rejects_duplicate_event_identity_with_changed_content_atomically(tmp_path):
    import copy

    source = Store(tmp_path / "events-source.db")
    create(source, "w", "portable")
    snapshot = source.export("w")
    forged = copy.deepcopy(snapshot["events"][0])
    forged["action"] = "forged-action"
    snapshot["events"].append(forged)
    target = Store(tmp_path / "events-target.db")
    with pytest.raises(DomainError):
        target.import_snapshot("w", snapshot, operation_id="bad-event-import", expected_version=0)
    assert not target.list("w") and not target.events("w")
    # Receipt rollback too: correcting the input can reuse the failed import ID.
    target.import_snapshot(
        "w", source.export("w"), operation_id="bad-event-import", expected_version=0
    )
    assert target.get("w", "portable")["id"] == "portable"


def test_snapshot_rejects_conflicting_duplicate_operation_receipt_atomically(tmp_path):
    import copy

    source = Store(tmp_path / "receipt-source.db")
    create(source, "w", "portable")
    snapshot = source.export("w")
    assert snapshot["operations"], "Full-fidelity export must preserve replay receipts"
    forged = copy.deepcopy(snapshot["operations"][0])
    forged["payload_hash"] = "0" * 64
    snapshot["operations"].append(forged)
    target = Store(tmp_path / "receipt-target.db")
    with pytest.raises(DomainError):
        target.import_snapshot("w", snapshot, operation_id="bad-receipt-import", expected_version=0)
    assert not target.list("w") and not target.events("w")


def test_import_cannot_rewind_newer_state_or_partially_merge_on_conflict(tmp_path):
    source = Store(tmp_path / "old-snapshot.db")
    first = create(source, "w", "original", title="first")
    target = Store(tmp_path / "newer-target.db")
    target.import_snapshot(
        "w", source.export("w"), operation_id="initial-merge", expected_version=0
    )
    target.execute(
        "w",
        "newer-edit",
        "update",
        {"id": "original", "title": "newer"},
        expected_version=first["version"],
    )
    create(source, "w", "aaa-new-before-conflict")
    baseline = target.export("w")
    with pytest.raises(DomainError):
        target.import_snapshot(
            "w", source.export("w"), operation_id="stale-merge", expected_version=0
        )
    assert target.export("w") == baseline


def test_event_cursors_do_not_reveal_foreign_workspace_activity(tmp_path):
    store = Store(tmp_path / "private-cursors.db")
    first = create(store, "one", "first")
    before = store.events("one")[-1]["seq"]
    for index in range(3):
        create(store, "two", f"foreign-{index}")
    store.execute(
        "one",
        "local-edit",
        "update",
        {"id": "first", "title": "changed"},
        expected_version=first["version"],
    )
    after = store.events("one", after=before)
    assert len(after) == 1 and after[0]["seq"] == before + 1


def test_owned_commit_failure_rolls_back_and_preserves_original_error(tmp_path):
    path = tmp_path / "commit-failure.db"
    store = Store(path)
    create(store, "w", "baseline")
    store.db.execute("PRAGMA busy_timeout=20")  # Deterministic lock fault, not a timing test.
    reader = sqlite3.connect(path)
    reader.execute("BEGIN")
    reader.execute("SELECT data FROM daymark_entities").fetchall()
    try:
        # An older reader can permit writes but prevent their COMMIT in SQLite's
        # rollback-journal mode. RELEASE succeeded; cleanup must roll back root,
        # rather than address the now-released library savepoint.
        with pytest.raises(sqlite3.OperationalError, match="locked"):
            create(store, "w", "blocked")
    finally:
        reader.rollback()
        reader.close()
    assert_code("not_found", store.get, "w", "blocked")
    store.execute(
        "w",
        "create-blocked",
        "create",
        {"id": "new-after-rollback", "kind": "task"},
        expected_version=0,
    )
    assert store.get("w", "new-after-rollback")["id"] == "new-after-rollback"


@pytest.mark.parametrize("field", ["title", "description"])
@pytest.mark.parametrize("claimed", [False, True])
def test_harmless_task_text_edit_preserves_explicit_reminder(tmp_path, field, claimed):
    store = Store(tmp_path / "harmless-edit.db")
    seed_reminder(store)
    if claimed:
        store.claim_reminders("owner", now=NOW)
    before = store.get("w", "reminder")
    task = store.get("w", "task")
    store.execute(
        "w",
        "harmless-edit",
        "update",
        {"id": "task", field: "Revised synthetic text"},
        expected_version=task["version"],
    )
    assert store.get("w", "reminder") == before
    if claimed:
        store.finish_delivery(
            "w", "reminder", "owner", True, lease_token=before["lease_token"], now=NOW
        )
    else:
        assert len(store.claim_reminders("owner", now=NOW)) == 1


@pytest.mark.parametrize("action", ["cancel", "complete", "archive", "delete"])
def test_terminal_archive_delete_invalidates_explicit_reminder(tmp_path, action):
    store = Store(tmp_path / "terminal-edit.db")
    seed_reminder(store)
    store.execute(
        "w", "terminal", action, {"id": "task"}, expected_version=store.get("w", "task")["version"]
    )
    assert store.get("w", "reminder")["reminder_state"] == "cancelled"
    assert store.claim_reminders("owner", now=NOW) == []
    if action == "delete":
        assert_code("not_found", store.get, "w", "task")


@pytest.mark.parametrize("action", ["create", "update"])
@pytest.mark.parametrize(
    "field,value",
    [
        ("attempts", 99),
        ("lease_owner", "forged-owner"),
        ("lease_token", "forged-token"),
        ("lease_until", "2026-11-10T12:00:00Z"),
        ("retry_at", "2026-11-10T12:00:00Z"),
        ("delivery_key", "forged-delivery"),
        ("reminder_state", "delivered"),
        ("error", "forged-outcome"),
    ],
)
def test_commands_cannot_forge_scheduler_owned_state(tmp_path, action, field, value):
    store = Store(tmp_path / "scheduler-forgery.db")
    seed_reminder(store)
    baseline = store.export("w")
    if action == "create":
        payload = {
            "id": "forged",
            "kind": "reminder",
            "recipient_id": "person",
            "scheduled_at": NOW.isoformat(),
            field: value,
        }
        version = 0
    else:
        payload = {"id": "reminder", field: value}
        version = store.get("w", "reminder")["version"]
    assert_code("invalid", store.execute, "w", "forgery", action, payload, expected_version=version)
    assert store.export("w") == baseline


def test_native_skip_advances_series_without_recording_actual_completion(tmp_path):
    store = Store(tmp_path / "skip-disposition.db")
    planned = {"date": "2026-11-09", "timezone": "America/New_York"}
    task = create(
        store,
        "w",
        "skipped-task",
        planned=planned,
        recurrence={
            "rule": "FREQ=DAILY;COUNT=2",
            "timezone": "America/New_York",
            "anchor": planned,
        },
    )
    create(store, "w", "person", "person")
    create(
        store,
        "w",
        "skip-reminder",
        "reminder",
        recipient_id="person",
        task_id="skipped-task",
        scheduled_at=(NOW - timedelta(seconds=1)).isoformat(),
    )
    payload = {"id": "skipped-task"}
    skipped = store.execute(
        "w", "skip-occurrence", "skip", payload, expected_version=task["version"]
    )
    assert skipped["status"] == "cancelled" and skipped["completed_at"] is None
    tasks = store.list("w", kind="task")
    successor = next(item for item in tasks if item["id"] != "skipped-task")
    assert len(tasks) == 2
    assert successor["status"] == "open" and successor["completed_at"] is None
    assert successor["planned"]["date"] == "2026-11-10"
    assert successor["series_id"] == skipped["series_id"]
    assert successor["occurrence_key"] != skipped["occurrence_key"]
    assert store.get("w", "skip-reminder")["reminder_state"] == "cancelled"
    assert not store.claim_reminders("worker", now=NOW)
    history = store.events("w", limit=1000)
    skip_events = [
        event
        for event in history
        if event["entity_id"] == "skipped-task" and event["action"] == "skip"
    ]
    assert len(skip_events) == 1
    assert skip_events[0]["after_data"]["completed_at"] is None
    assert not any(event["action"] == "complete" for event in history)
    assert (
        store.execute("w", "skip-occurrence", "skip", payload, expected_version=task["version"])
        == skipped
    )
    assert store.events("w", limit=1000) == history
    assert len(store.list("w", kind="task")) == 2
