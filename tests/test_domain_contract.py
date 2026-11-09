"""Independent product acceptance checks with synthetic work records."""

from datetime import UTC, datetime

import pytest

from daymark import DomainError, Store


def create(store, entity_id, kind="task", **fields):
    return store.execute(
        "work",
        f"create-{entity_id}",
        "create",
        {"id": entity_id, "kind": kind, **fields},
        expected_version=0,
    )


def test_assignee_and_beneficiary_remain_different_after_export_import(tmp_path):
    source = Store(tmp_path / "source.db")
    create(source, "owner", "person")
    create(source, "beneficiary", "person")
    task = create(
        source,
        "prepare",
        assignee_id="owner",
        beneficiary_ids=["beneficiary"],
        planned={"date": "2030-04-03", "timezone": "Europe/London"},
        latest_target={"date": "2030-04-05", "timezone": "Europe/London"},
        source_refs=[{"system": "example", "kind": "intention", "id": "intent-12"}],
    )
    target = Store(tmp_path / "target.db")
    target.import_snapshot(
        "work", source.export("work"), operation_id="transfer", expected_version=0
    )
    assert target.get("work", "prepare") == task
    assert task["deadline"] is None
    assert task["status"] == "open"
    assert task["assignee_id"] != task["beneficiary_ids"][0]


def test_waiting_cancelled_and_archived_are_distinct_lifecycle_states(tmp_path):
    store = Store(tmp_path / "life.db")
    task = create(store, "a", status="waiting")
    cancelled = store.execute(
        "work", "cancel-a", "cancel", {"id": "a"}, expected_version=task["version"]
    )
    assert cancelled["status"] == "cancelled"
    assert not cancelled["archived"]
    assert cancelled["completed_at"] is None
    archived = store.execute(
        "work", "archive-a", "archive", {"id": "a"}, expected_version=cancelled["version"]
    )
    assert archived["archived"] and archived["status"] == "cancelled"
    assert store.list("work") == []
    assert store.list("work", include_archived=True) == [archived]


@pytest.mark.parametrize("field", ["parent_id", "depends_on"])
def test_long_cycle_rejected_without_mutation(tmp_path, field):
    store = Store(tmp_path / "cycles.db")
    first = create(store, "a")
    create(store, "b", **{field: ["a"] if field == "depends_on" else "a"})
    create(store, "c", **{field: ["b"] if field == "depends_on" else "b"})
    with pytest.raises(DomainError):
        store.execute(
            "work",
            "cycle",
            "update",
            {
                "id": "a",
                field: ["c"] if field == "depends_on" else "c",
            },
            expected_version=first["version"],
        )
    assert store.get("work", "a") == first
    assert len(store.events("work")) == 3


def test_omitted_expected_version_does_not_allow_lost_update(tmp_path):
    store = Store(tmp_path / "versions.db")
    first = create(store, "a", title="Original")
    with pytest.raises(DomainError):
        store.execute("work", "unversioned", "update", {"id": "a", "title": "Lost"})
    assert store.get("work", "a") == first


@pytest.mark.parametrize(
    "payload",
    [
        {"deadline": {"date": "2030-02-30"}},
        {"planned": {"at": "2030-04-03T18:00:00"}},
        {"deadline": {"date": "2030-04-03", "timezone": "Unknown/Zone"}},
        {"pretend_schema": {"deadline": "someday"}},
        {"checklist": [{"id": "a", "text": "One"}, {"id": "a", "text": "Two"}]},
    ],
)
def test_invalid_captures_do_not_leave_entities_or_events(tmp_path, payload):
    store = Store(tmp_path / "invalid.db")
    with pytest.raises((DomainError, ValueError)):
        create(store, "invalid", **payload)
    assert store.list("work") == []
    assert store.events("work") == []


def test_update_completion_suppresses_claimed_reminder(tmp_path):
    store = Store(tmp_path / "stale.db")
    create(store, "person", "person")
    task = create(store, "task")
    create(
        store,
        "reminder",
        "reminder",
        task_id="task",
        recipient_id="person",
        scheduled_at="2030-04-03T12:00:00Z",
    )
    now = datetime(2030, 4, 3, 12, tzinfo=UTC)
    claimed = store.claim_reminders("worker", now=now)
    assert len(claimed) == 1
    store.execute(
        "work",
        "patch-complete",
        "update",
        {"id": "task", "status": "completed"},
        expected_version=task["version"],
    )
    assert store.get("work", "reminder")["reminder_state"] == "cancelled"
    assert store.claim_reminders("other", now=now) == []
