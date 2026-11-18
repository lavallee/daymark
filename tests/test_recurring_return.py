"""A calendar app returns after missed work and settles one real occurrence at a time."""

from contextlib import closing
from datetime import date, timedelta

import httpx
import pytest

from daymark import DomainError, Store, Tasks
from daymark.client import Client
from daymark.mcp import call


@pytest.mark.parametrize("closed", ["done", "cancel", "archive"])
@pytest.mark.parametrize("action", ["done", "skip"])
def test_delayed_advance_keeps_original_evidence_and_never_restarts_stopped_work(closed, action):
    with closing(Store(":memory:")) as store:
        work = Tasks(store, actor="calendar", timezone="UTC")
        work.add(
            "Inspect press", id="weekly", every="week", plan="2030-01-01", note="Original reading"
        )
        old = getattr(work, closed)("weekly", op="settle")
        head = store.head("default")
        with pytest.raises(DomainError, match="Reopen|Unarchive") as error:
            if action == "done":
                work.done("weekly", note="Late report", op="delayed")
            else:
                work.skip("weekly", op="delayed")
        assert error.value.code == "conflict"
        assert work.get("weekly") == old and store.head("default") == head
        assert getattr(work, closed)("weekly", op="settle") == old
        assert not store.receipt("default", "delayed")
        assert [i["id"] for i in store.list("default", kind="task") if i["id"] != "weekly"] == (
            ["weekly.1"] if closed == "done" else []
        )


def test_one_off_skip_refuses_with_cancellation_path_and_no_fresh_receipt():
    with closing(Store(":memory:")) as store:
        tasks = Tasks(store)
        tasks.add("Send one proof", id="proof")
        with pytest.raises(DomainError, match="cancel to retire a one-off"):
            call(tasks, "daymark_act", {"action": "skip", "id": "proof", "op": "late"})
        assert tasks.get("proof")["status"] == "open" and store.receipt("default", "late") is None
        assert tasks.cancel("proof")["status"] == "cancelled"


@pytest.mark.parametrize("settle_past_first", [True, False])
def test_correcting_old_round_retains_independent_work_and_one_slot_per_round(
    tmp_path, settle_past_first
):
    with (
        closing(Store(tmp_path / "old.db")) as original,
        closing(Store(tmp_path / "copy.db")) as copied,
    ):
        first = Tasks(original, "print", actor="calendar", timezone="UTC")
        anchor = date(2030, 1, 1)
        first.add("Inspect press", id="weekly", every="week", plan=anchor.isoformat())
        first.skip("weekly", op="pass")
        first.note("weekly.1", "First reading for the second round")
        first.claim("weekly.1")
        first.undo("weekly", op="correction")
        copied.import_snapshot(
            "print", original.export("print"), operation_id="copy", expected_version=0
        )
        for store in (original, copied):
            worker = Tasks(store, "print", actor="calendar")
            rounds = ("weekly", "weekly.1") if settle_past_first else ("weekly.1", "weekly")
            for id in rounds:
                result = worker.done(id, op="finish-" + id)
                assert worker.done(id, op="finish-" + id) == result
            items = store.list("print", kind="task", include_archived=True)
            assert [(i["id"], i["occurrence_key"], i["status"]) for i in items] == [
                ("weekly", "weekly:0", "completed"),
                ("weekly.1", "weekly:1", "completed"),
                ("weekly.2", "weekly:2", "open"),
            ]
            assert [item["planned"]["date"] for item in items] == [
                (anchor + timedelta(weeks=n)).isoformat() for n in range(3)
            ]
            assert items[1]["notes"][0]["text"] == "First reading for the second round"
            assert items[2]["notes"] == [] and not items[2]["claimed_by"]
            assert [
                e["entity_id"] for e in store.events("print") if e["action"] == "recurrence.create"
            ] == ["weekly.1", "weekly.2"]


@pytest.mark.parametrize("note", [None, "Read after return"])
def test_new_remote_app_refuses_stale_occurrence_on_old_service_before_posting(note):
    with closing(Store(":memory:")) as store:
        work = Tasks(store)
        work.add("Check", id="weekly", every="week", plan="2030-01-01")
        closed = work.cancel("weekly")
    requests = []

    def old(request):
        requests.append((request.method, request.url.path))
        return httpx.Response(200, json=closed)

    with Client("https://sample.invalid", "sample", transport=httpx.MockTransport(old)) as client:
        remote = Tasks(client)
        with pytest.raises(DomainError, match="cancelled"):
            remote.done("weekly", note=note)
        with pytest.raises(DomainError, match="cancelled"):
            remote.skip("weekly")
    assert requests == [("GET", "/v1/entities/weekly")] * 2


@pytest.mark.parametrize("without_json", [False, True])
def test_revisiting_old_round_does_not_recreate_its_deleted_or_reused_successor(without_json):
    import sqlite3

    class Connection(sqlite3.Connection):
        def execute(self, sql, parameters=(), /):
            if without_json and "json_extract(" in sql:
                raise sqlite3.OperationalError("no such function: json_extract")
            return super().execute(sql, parameters)

    with closing(sqlite3.connect(":memory:", factory=Connection)) as connection:
        store = Store(connection)
        t = Tasks(store, "press")
        t.add("Inspect ink", id="sample", every="week", plan="2030-01-01")
        t.done("sample")
        t.note("sample.1", "A later reading")
        original = t.get("sample.1")
        store.execute("press", "remove", "delete", {"id": original["id"]}, original["version"])
        replacement = t.add("Unrelated one-off errand", id=original["id"])
        t.reopen("sample")
        t.skip("sample")
        assert t.get(replacement["id"]) == replacement
        assert [
            e["entity_id"] for e in store.events("press") if e["action"] == "recurrence.create"
        ] == ["sample.1"]


@pytest.mark.parametrize("deliver_before_completion", [False, True])
def test_overdue_and_future_notices_belong_to_one_occurrence(deliver_before_completion):
    from daymark.models import normalize_now
    from daymark.worker import RecordingSink, Worker

    with closing(Store(":memory:")) as store:
        t = Tasks(store, "press", timezone="UTC")
        t.add("Desk", kind="person", id="desk")
        t.add("Weekly reading", id="weekly", every="week", plan="2030-01-02")
        old = t.remind("weekly", "2030-01-02 09:00", recipient="desk")
        future = t.remind("weekly", "2030-02-01 09:00", recipient="desk")
        sink = RecordingSink()
        runner = Worker(store, sink)
        now = normalize_now("2030-01-30T12:00:00Z")
        if deliver_before_completion:
            assert runner.run_once(now=now)["recorded"] == 1
        completed = store.execute(
            "press",
            "done",
            "complete",
            {"id": "weekly", "completed_at": now.isoformat()},
            t.get("weekly")["version"],
        )
        assert completed["status"] == "completed"
        assert t.get("weekly.1")["planned"]["date"] == "2030-01-09"
        assert t.show("weekly.1")["reminders"] == []
        assert runner.run_once(now=normalize_now("2030-02-05T12:00:00Z"))["recorded"] == 0
        assert t.get(old["id"])["reminder_state"] == (
            "delivered" if deliver_before_completion else "cancelled"
        )
        assert t.get(future["id"])["reminder_state"] == "cancelled"
        assert len(sink.delivery_keys) == int(deliver_before_completion)


@pytest.mark.parametrize("freeze", ["archive", "cancel"])
@pytest.mark.parametrize("route", ["edit", "stage"])
def test_indirect_completion_cannot_restart_shelved_series(freeze, route):
    with closing(Store(":memory:")) as store:
        t = Tasks(store)
        t.project("Edition", id="edition")
        t.add("Check ink", id="ink", plan="2030-01-01", every="week", project="edition")
        closed = getattr(t, freeze)("ink")
        head = store.head("default")
        with pytest.raises(DomainError, match="Unarchive|Reopen"):
            if route == "edit":
                t.edit("ink", status="completed", op="late")
            else:
                t.move("ink", stage="done", op="late")
        assert t.get("ink") == closed and store.head("default") == head
        assert not store.receipt("default", "late")
        assert [
            item["id"] for item in store.list("default", kind="task", include_archived=True)
        ] == ["ink"]


@pytest.mark.parametrize("count,dates", [(1, ["2030-01-01"]), (2, ["2030-01-01", "2030-01-04"])])
def test_explicit_off_pattern_first_round_counts_toward_fixed_budget(count, dates):
    from daymark.models import Recurrence, Temporal, next_occurrence, normalize_now

    rule = f"FREQ=WEEKLY;BYDAY=FR;COUNT={count}"
    first = Temporal(date="2030-01-01", timezone="UTC")
    state = Recurrence(rule=rule, anchor=first)
    found = [first]
    while following := next_occurrence(state, found[-1], normalize_now("2030-02-01T00:00:00Z")):
        found.append(following)
        assert len(found) <= count
    assert [str(item.date) for item in found] == dates
    with closing(Store(":memory:")) as source, closing(Store(":memory:")) as restored:
        calendar = Tasks(source, "edition", timezone="UTC")
        calendar.add("Read on Tuesday, then Fridays", id="weekly", every=rule, plan=dates[0])
        restored.import_snapshot(
            "edition", source.export("edition"), operation_id="copy", expected_version=0
        )
        for store in (source, restored):
            t = Tasks(store, "edition", timezone="UTC")
            for n in range(len(dates)):
                t.skip("weekly" if n == 0 else f"weekly.{n}")
            rows = store.list("edition")
            assert [r["planned"]["date"] for r in rows] == dates
            assert all(r["status"] == "cancelled" for r in rows)


@pytest.mark.parametrize("mode", [False, True])
@pytest.mark.parametrize(
    "rule,first",
    [
        ("fri", "2030-01-04"),
        ("mon,wed", "2030-01-02"),
        ("weekdays", "2030-01-01"),
        ("FREQ=MONTHLY;BYMONTHDAY=31;COUNT=1", "2030-01-31"),
        ("FREQ=YEARLY;BYMONTH=2;BYMONTHDAY=29;COUNT=2", "2032-02-29"),
        ("FREQ=WEEKLY;BYDAY=FR;UNTIL=20300104", "2030-01-04"),
        ("week", "2030-01-01"),
    ],
)
def test_implicit_capture_and_conversion_begin_on_first_matching_local_day(mode, rule, first):
    with closing(Store(":memory:")) as store:
        # UTC is still December 31. The calendar assistant uses the person's local day.
        t = Tasks(store, "edition", timezone="Pacific/Kiritimati", now="2029-12-31T12:00:00Z")
        t.add("Existing work", id="converted")
        captured = t.add("Future work", id="new", every=rule, after_completion=mode, op="new")
        converted = t.edit("converted", every=rule, after_completion=mode, op="convert")
        for row in (captured, converted):
            assert row["planned"]["date"] == first and row["recurrence"]["anchor"] == row["planned"]
            assert row["series_id"] == row["id"] and row["occurrence_key"] == row["id"] + ":0"
            assert t.brief(row["id"])["readiness"]["ready"] == (first == "2030-01-01")
        future = Tasks(store, "edition", timezone="Pacific/Kiritimati", now="2031-04-03T12:00:00Z")
        assert (
            future.add("Future work", id="new", every=rule, after_completion=mode, op="new")
            == captured
        )
        assert (
            future.edit("converted", every=rule, after_completion=mode, op="convert") == converted
        )


@pytest.mark.parametrize(
    "rule,code",
    [
        ("FREQ=WEEKLY;BYDAY=FR;UNTIL=20300102", "invalid"),
        ("FREQ=WEEKLY;BYDAY=FR;COUNT=2;UNTIL=20300105", "invalid"),
        ("FREQ=MONTHLY;BYMONTH=2;BYMONTHDAY=30", "recurrence_limit"),
    ],
)
def test_implicit_first_day_refuses_empty_or_invalid_rule_atomically(rule, code):
    with closing(Store(":memory:")) as store:
        t = Tasks(store, "press", now="2030-01-01T12:00:00Z", timezone="UTC")
        original = t.add("Original task", id="original")
        with pytest.raises(DomainError) as error:
            t.edit("original", every=rule, op="start")
        assert error.value.code == code
        assert t.get("original") == original and not store.receipt("press", "start")
        with pytest.raises(DomainError) as error:
            t.add("New task", every=rule, op="capture")
        assert error.value.code == code and store.head("press") == 1


def test_undo_enrollment_then_transfer_and_reschedule_first_round():
    with closing(Store(":memory:")) as source, closing(Store(":memory:")) as copy:
        t = Tasks(source, "press", now="2030-01-01T12:00:00Z", timezone="UTC")
        t.add("Weekly proof", id="weekly", source="press:reading:one")
        initial = t.edit("weekly", every="fri", op="first")
        previous = t.undo("weekly")
        assert (
            previous["recurrence"]
            is previous["planned"]
            is previous["series_id"]
            is previous["occurrence_key"]
            is None
        )
        assert t.edit("weekly", every="fri", op="first") == initial
        t.edit("weekly", every="fri", op="start-again")
        t.edit("weekly", plan="2030-01-25")
        copy.import_snapshot(
            "press", source.export("press"), operation_id="copy", expected_version=0
        )
        for engine in (source, copy):
            work = Tasks(engine, "press")
            work.done("weekly", op="measure")
            following = work.get("weekly.1")
            assert following["planned"]["date"] == "2030-01-11"
            assert (following["series_id"], following["occurrence_key"]) == ("weekly", "weekly:1")
            assert work.show(source="press:reading:one")["entity"]["id"] == "weekly"
