"""Synthetic Store invariants beyond the independent consumer acceptance suite."""

import copy
import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from daymark import DomainError, Store

NOW = datetime(2030, 1, 1, 12, tzinfo=UTC)


def create(store, id, kind="task", **kwargs):
    return store.execute(
        "w", "create-" + id, "create", {"id": id, "kind": kind, **kwargs}, expected_version=0
    )


def change(store, item, action="update", **fields):
    return store.execute(
        "w",
        f"{action}-{item['id']}-{item['version']}",
        action,
        {"id": item["id"], **fields},
        expected_version=item["version"],
    )


def reminder(store):
    create(store, "person", "person")
    create(store, "task")
    return create(
        store,
        "reminder",
        "reminder",
        recipient_id="person",
        task_id="task",
        scheduled_at=NOW.isoformat(),
    )


def test_schema_and_root_rollback_leave_connection_reusable(tmp_path):
    connection = sqlite3.connect(tmp_path / "caller.db")
    store = Store(connection)
    create(store, "a")
    connection.rollback()  # Roll back the schema and the first operation together.
    assert store.list("w") == [] and store.events("w") == []
    with pytest.raises(DomainError, match="Entity not found"):
        store.get("w", "a")
    create(store, "b")
    connection.commit()
    store.close()
    assert connection.execute("SELECT count(*) FROM daymark_entities").fetchone()[0] == 1
    connection.close()


@pytest.mark.parametrize(
    "field,kind",
    [
        ("project_id", "task"),
        ("goal_id", "person"),
        ("label_ids", "context"),
        ("assignee_id", "label"),
        ("depends_on", "project"),
    ],
)
def test_existing_wrong_kind_reference_is_rejected(tmp_path, field, kind):
    store = Store(tmp_path / "kinds.db")
    create(store, "ref", kind)
    baseline = store.export("w")
    value = ["ref"] if field in {"depends_on", "label_ids"} else "ref"
    with pytest.raises(DomainError):
        create(store, "bad", **{field: value})
    assert store.export("w") == baseline


def test_source_links_append_without_identity_reassignment(tmp_path):
    store = Store(tmp_path / "sources.db")
    source = {"system": "synthetic", "kind": "task", "id": "one"}
    second = {"system": "synthetic", "kind": "recipe", "id": "two"}
    item = create(store, "a", source_refs=[source])
    item = change(store, item, source_refs=[source, second])
    baseline = store.export("w")
    with pytest.raises(DomainError):
        change(store, item, source_refs=[second])
    with pytest.raises(DomainError):
        create(store, "alias", source_refs=[source])
    assert store.export("w") == baseline


def test_delete_replay_exact_restore_and_undo_versions(tmp_path):
    store = Store(tmp_path / "undo.db")
    original = create(store, "a", metadata={"synthetic": {"keep": [1, 2]}})
    deleted = change(store, original, "delete")
    with pytest.raises(DomainError):
        store.get("w", "a")
    assert change(store, original, "delete") == deleted
    restored = store.execute(
        "w", "undo-deletion", "undo", {"id": "a"}, expected_version=deleted["version"]
    )
    assert restored["metadata"] == original["metadata"]
    assert restored["version"] == deleted["version"] + 1
    change(store, restored, "delete")
    store.import_snapshot(
        "w",
        {"schema_version": 1, "entities": [original], "events": []},
        operation_id="exact-host-undo",
        expected_version=0,
    )
    assert store.get("w", "a") == original
    assert len([x for x in store.events("w") if x["action"] == "delete"]) == 2


@pytest.mark.parametrize("action", ["complete", "cancel", "archive", "update", "skip"])
def test_task_lifecycle_fences_existing_claim(tmp_path, action):
    store = Store(tmp_path / "lifecycle.db")
    reminder(store)
    claimed = store.claim_reminders("owner", now=NOW)[0]
    task = store.get("w", "task")
    change(
        store, task, action, **({"planned": {"date": "2030-01-02"}} if action == "update" else {})
    )
    current = store.get("w", "reminder")
    assert current["reminder_state"] == "cancelled"
    assert current["lease_token"] is None and current["lease_until"] is None
    with pytest.raises(DomainError):
        store.finish_delivery(
            "w", "reminder", "owner", True, now=NOW, lease_token=claimed["lease_token"]
        )


def test_missing_token_and_forged_scheduler_update_are_atomic(tmp_path):
    store = Store(tmp_path / "fence.db")
    reminder(store)
    claimed = store.claim_reminders("owner", now=NOW)[0]
    baseline = store.export("w")
    with pytest.raises(DomainError):
        change(store, claimed, lease_token="forged")
    with pytest.raises(DomainError):
        store.finish_delivery("w", "reminder", "owner", True, now=NOW)
    assert store.export("w") == baseline


def test_delivery_outcome_retry_is_idempotent_and_changed_outcome_conflicts(tmp_path):
    store = Store(tmp_path / "outcome.db")
    reminder(store)
    claim = store.claim_reminders("owner", now=NOW)[0]
    result = store.finish_delivery(
        "w", "reminder", "owner", True, now=NOW, lease_token=claim["lease_token"]
    )
    baseline = store.export("w")
    assert (
        store.finish_delivery(
            "w",
            "reminder",
            "owner",
            True,
            now=NOW + timedelta(days=1),
            lease_token=claim["lease_token"],
        )
        == result
    )
    with pytest.raises(DomainError):
        store.finish_delivery(
            "w",
            "reminder",
            "owner",
            False,
            error="changed",
            now=NOW,
            lease_token=claim["lease_token"],
        )
    assert store.export("w") == baseline


@pytest.mark.parametrize("initial_count", [0, 1, 3])
def test_snapshot_restart_retains_actual_recurrence_budget(tmp_path, initial_count):
    store = Store(tmp_path / "recurrence.db")
    planned = {"date": "2029-12-01", "timezone": "UTC"}
    task = create(
        store,
        "series",
        planned=planned,
        recurrence={"rule": "FREQ=DAILY;COUNT=4", "mode": "after_completion", "anchor": planned},
    )
    for _ in range(initial_count):
        change(store, task, "complete", completed_at=NOW.isoformat())
        task = next((x for x in store.list("w") if x["status"] == "open"), None)
    snapshot = store.export("w")
    restored = Store(tmp_path / "restored.db")
    restored.import_snapshot("w", snapshot, operation_id="transfer", expected_version=0)
    restored.close()
    restored = Store(tmp_path / "restored.db")
    while True:
        pending = [x for x in restored.list("w") if x["status"] == "open"]
        if not pending:
            break
        change(restored, pending[0], "complete", completed_at=NOW.isoformat())
    tasks = restored.list("w")
    assert len(tasks) == 4
    assert sorted(x["recurrence"]["occurrence_index"] for x in tasks) == [0, 1, 2, 3]
    assert len({x["occurrence_key"] for x in tasks}) == 4


def test_moved_occurrence_uses_original_slot_after_snapshot_restart(tmp_path):
    store = Store(tmp_path / "move.db")
    planned = {"date": "2030-01-01", "timezone": "UTC"}
    task = create(
        store,
        "series",
        planned=planned,
        recurrence={"rule": "FREQ=DAILY;COUNT=3", "anchor": planned},
    )
    moved = change(store, task, planned={"date": "2030-01-20", "timezone": "UTC"})
    assert moved["occurrence_key"] == task["occurrence_key"]
    target = Store(tmp_path / "move-target.db")
    target.import_snapshot("w", store.export("w"), operation_id="transfer", expected_version=0)
    done = change(target, moved, "complete", completed_at=NOW.isoformat())
    following = [x for x in target.list("w") if x["status"] == "open"]
    assert len(following) == 1
    assert following[0]["planned"]["date"] == "2030-01-02"
    reopened = change(target, done, "reopen")
    change(target, reopened, "complete", completed_at=NOW.isoformat())
    assert len(target.list("w")) == 2


def test_cross_workspace_cursors_merge_without_global_collisions(tmp_path):
    source, target = Store(tmp_path / "source.db"), Store(tmp_path / "target.db")
    create(source, "portable")
    target.execute(
        "other", "create", "create", {"id": "foreign", "kind": "task"}, expected_version=0
    )
    target.import_snapshot("w", source.export("w"), operation_id="transfer", expected_version=0)
    assert target.events("w") == source.events("w")
    assert target.events("other")[0]["seq"] == target.events("w")[0]["seq"] == 1


def test_import_cycle_rolls_back_every_collection(tmp_path):
    source, target = Store(tmp_path / "source.db"), Store(tmp_path / "target.db")
    create(source, "a")
    create(source, "b", parent_id="a")
    snapshot = copy.deepcopy(source.export("w"))
    snapshot["entities"][0]["parent_id"] = "b"
    baseline = target.export("w")
    with pytest.raises(DomainError):
        target.import_snapshot("w", snapshot, operation_id="bad", expected_version=0)
    assert target.export("w") == baseline


def test_overdue_only_hard_deadline(tmp_path, monkeypatch):
    import daymark.store as module

    monkeypatch.setattr(module, "normalize_now", lambda now=None: now or NOW)
    store = Store(tmp_path / "query.db")
    create(store, "hard", deadline={"date": "2029-12-31"})
    create(store, "plan", planned={"date": "2029-12-31"})
    create(store, "same-day", deadline={"date": "2030-01-01"})
    create(store, "done", deadline={"date": "2029-12-31"}, status="completed")
    assert [x["id"] for x in store.query("w", {"field": "overdue", "op": "eq", "value": True})] == [
        "hard"
    ]
