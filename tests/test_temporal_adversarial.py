"""Pure temporal adversarial checks; no package init, Store, network or real records.

Policy checks are named explicitly. Calendar invalid-day skipping follows the
current RRULE contract; gap advance and first fold are explicit Daymark policies.
The subprocess budget is a test safety limit, not a product latency promise.
"""

import importlib.util
import json
import subprocess
import sys
import textwrap
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

MODEL_PATH = Path(__file__).resolve().parents[1] / "src/daymark/models.py"
MODULE_NAME = "_daymark_temporal_adversarial_models"
spec = importlib.util.spec_from_file_location(MODULE_NAME, MODEL_PATH)
models = importlib.util.module_from_spec(spec)
sys.modules[MODULE_NAME] = models
spec.loader.exec_module(models)
Temporal = models.Temporal
Recurrence = models.Recurrence
Entity = models.Entity
next_occurrence = models.next_occurrence
NOW = datetime(2026, 11, 9, 18, tzinfo=UTC)


def civil(value="2026-11-09", zone="America/New_York"):
    return Temporal(date=value, timezone=zone)


def instant(value="2026-11-09T09:00:00-05:00", zone="America/New_York"):
    return Temporal(at=value, timezone=zone)


def recurrence(anchor, rule="FREQ=DAILY", mode="fixed"):
    return Recurrence(rule=rule, mode=mode, timezone=anchor.timezone, anchor=anchor)


def walk(rule, first, limit=20):
    """A bounded preview, deliberately never materializing an unbounded RRULE."""
    result = [first]
    for _ in range(limit):
        following = next_occurrence(rule, result[-1], NOW)
        if following is None:
            return result
        result.append(following)
    pytest.fail(f"finite rule did not exhaust within {limit} successors: {rule.rule}")


@pytest.mark.parametrize("zone", ["Pacific/Kiritimati", "Pacific/Pago_Pago", "Asia/Kathmandu"])
def test_civil_due_day_uses_its_zone_across_international_date_line(zone):
    due = civil(zone=zone)
    midnight_after = datetime(2026, 11, 10, tzinfo=ZoneInfo(zone)).astimezone(UTC)
    assert not due.is_before(midnight_after - timedelta(microseconds=1))
    assert due.is_before(midnight_after)
    assert due.date == date(2026, 11, 9) and due.at is None
    assert due.local().date() == due.date


@pytest.mark.parametrize("zone", ["UTC", "America/New_York", "Asia/Kathmandu"])
def test_equivalent_instants_compare_identically_in_any_display_zone(zone):
    a = instant("2026-11-09T09:00:00-05:00", zone)
    b = instant("2026-11-09T14:00:00Z", zone)
    cutoff = datetime(2026, 11, 9, 14, tzinfo=UTC)
    assert a.at == b.at
    assert not a.is_before(cutoff) and not b.is_before(cutoff)
    assert a.is_before(cutoff + timedelta(microseconds=1))
    assert a.local() == b.local()


def test_aware_instant_order_uses_elapsed_time_across_same_zone_fold():
    zone = ZoneInfo("America/New_York")
    due = Temporal(at=datetime(2026, 11, 1, 1, 30, tzinfo=zone, fold=0), timezone=zone.key)
    now = datetime(2026, 11, 1, 1, 15, tzinfo=zone, fold=1)
    assert due.at.astimezone(UTC) < now.astimezone(UTC)
    assert due.is_before(now)


def test_reminder_temporal_window_rejects_reversed_instants_across_fold():
    zone = ZoneInfo("America/New_York")
    earliest = Temporal(at=datetime(2026, 11, 1, 1, 15, tzinfo=zone, fold=1), timezone=zone.key)
    latest = Temporal(at=datetime(2026, 11, 1, 1, 30, tzinfo=zone, fold=0), timezone=zone.key)
    assert earliest.at.astimezone(UTC) > latest.at.astimezone(UTC)
    with pytest.raises(ValidationError):
        Entity(
            id="synthetic-reminder",
            workspace="synthetic",
            kind="reminder",
            scheduled_at=NOW,
            recipient_id="synthetic-person",
            earliest_start=earliest,
            latest_target=latest,
        )


def test_policy_latest_civil_target_includes_same_day_london_evening():
    task = Entity(
        id="synthetic-window",
        workspace="synthetic",
        kind="task",
        earliest_start={"at": "2030-04-03T18:00:00+01:00", "timezone": "Europe/London"},
        latest_target={"date": "2030-04-03", "timezone": "Europe/London"},
    )
    assert task.latest_target.date == date(2030, 4, 3)
    assert task.latest_target.at is None
    assert task.deadline is None


@pytest.mark.parametrize(
    "day,zone,end_utc",
    [
        ("2030-04-03", "Europe/London", "2030-04-03T23:00:00Z"),
        ("2026-03-08", "America/New_York", "2026-03-09T04:00:00Z"),
        ("2026-11-01", "America/New_York", "2026-11-02T05:00:00Z"),
        ("2026-10-04", "Australia/Lord_Howe", "2026-10-04T13:00:00Z"),
        ("2026-11-09", "Asia/Kathmandu", "2026-11-09T18:15:00Z"),
        ("2026-11-09", "Pacific/Kiritimati", "2026-11-09T10:00:00Z"),
    ],
)
def test_policy_civil_latest_target_ends_at_actual_next_local_midnight(day, zone, end_utc):
    end = datetime.fromisoformat(end_utc)
    latest = civil(day, zone)
    before_end = instant(end - timedelta(microseconds=1), zone)
    task = Entity(
        id="synthetic-window",
        workspace="synthetic",
        kind="task",
        earliest_start=before_end,
        latest_target=latest,
    )
    assert task.latest_target == latest and task.latest_target.at is None
    for outside in [end, end + timedelta(hours=9)]:
        with pytest.raises(ValidationError):
            Entity(
                id="synthetic-window",
                workspace="synthetic",
                kind="task",
                earliest_start=instant(outside, zone),
                latest_target=latest,
            )
    assert not latest.is_before(before_end.at)
    assert latest.is_before(end)


@pytest.mark.parametrize("zone", ["Europe/London", "America/New_York"])
@pytest.mark.parametrize(
    "start,end,valid",
    [
        ("2026-11-01", "2026-11-01", True),
        ("2026-11-01", "2026-11-02", True),
        ("2026-11-02", "2026-11-01", False),
    ],
)
def test_policy_date_date_window_preserves_civil_order(start, end, valid, zone):
    values = {
        "id": "synthetic-window",
        "workspace": "synthetic",
        "kind": "task",
        "earliest_start": civil(start, zone),
        "latest_target": civil(end, zone),
    }
    if valid:
        task = Entity(**values)
        assert task.earliest_start.date == date.fromisoformat(start)
        assert task.latest_target.date == date.fromisoformat(end)
    else:
        with pytest.raises(ValidationError):
            Entity(**values)


@pytest.mark.parametrize("offset,valid", [(0, True), (-1, False), (1, True)])
def test_policy_civil_earliest_starts_at_local_day_beginning(offset, valid):
    earliest = civil("2026-11-01")
    target = instant(datetime(2026, 11, 1, 4, tzinfo=UTC) + timedelta(microseconds=offset))
    values = {
        "id": "synthetic-window",
        "workspace": "synthetic",
        "kind": "task",
        "earliest_start": earliest,
        "latest_target": target,
    }
    if valid:
        assert Entity(**values).earliest_start.at is None
    else:
        with pytest.raises(ValidationError):
            Entity(**values)


@pytest.mark.parametrize(
    "start_date,start_zone,target_date,target_zone,valid",
    [
        ("2030-04-04", "Pacific/Kiritimati", "2030-04-03", "Europe/London", True),
        ("2030-04-03", "Pacific/Honolulu", "2030-04-03", "Pacific/Kiritimati", False),
    ],
)
def test_policy_date_windows_in_different_zones_require_actual_day_overlap(
    start_date, start_zone, target_date, target_zone, valid
):
    values = {
        "id": "synthetic-window",
        "workspace": "synthetic",
        "kind": "task",
        "earliest_start": civil(start_date, start_zone),
        "latest_target": civil(target_date, target_zone),
    }
    if valid:
        assert Entity(**values).latest_target.date == date.fromisoformat(target_date)
    else:
        with pytest.raises(ValidationError):
            Entity(**values)


def test_policy_maximum_civil_day_window_needs_no_invented_tomorrow_timestamp():
    last_day = civil(date.max, "UTC")
    task = Entity(
        id="synthetic-window",
        workspace="synthetic",
        kind="task",
        earliest_start=last_day,
        latest_target=last_day,
    )
    assert task.latest_target.date == date.max and task.latest_target.at is None


@pytest.mark.parametrize("fields", [{}, {"date": "2026-11-09", "at": NOW}])
def test_temporal_requires_exactly_one_representation(fields):
    with pytest.raises(ValidationError):
        Temporal(**fields)


@pytest.mark.parametrize("zone", ["Not/AZone", "../UTC", "/etc/localtime", "", "+05:00"])
def test_temporal_rejects_non_iana_zone(zone):
    with pytest.raises(ValidationError):
        civil(zone=zone)


def test_recurrence_zone_must_match_anchor_zone():
    with pytest.raises(ValidationError):
        Recurrence(rule="FREQ=DAILY", timezone="UTC", anchor=civil())


@pytest.mark.parametrize("value", ["2026-11-09T09:00:00", datetime(2026, 11, 9, 9)])
def test_timed_temporal_rejects_floating_instants(value):
    with pytest.raises(ValidationError):
        instant(value)


@pytest.mark.parametrize("zone", ["UTC", "America/New_York", "Asia/Kathmandu"])
@pytest.mark.parametrize("as_date", [False, True])
def test_temporal_json_roundtrip_preserves_precision_and_zone(zone, as_date):
    original = civil(zone=zone) if as_date else instant(zone=zone)
    restored = Temporal.model_validate_json(original.model_dump_json())
    assert restored == original
    assert (restored.at is None) == as_date
    assert restored.timezone == zone


@pytest.mark.parametrize("field", ["planned", "earliest_start", "latest_target"])
def test_soft_temporal_fields_do_not_create_a_hard_deadline(field):
    task = Entity(
        id="synthetic", workspace="synthetic", kind="task", **{field: civil("2000-01-01")}
    )
    assert task.deadline is None
    assert getattr(task, field).date == date(2000, 1, 1)
    assert "overdue" not in task.model_dump()


@pytest.mark.parametrize(
    "zone,start,expected,elapsed_hours",
    [
        ("America/New_York", "2026-03-07T09:00:00-05:00", "2026-03-08T09:00:00-04:00", 23),
        ("America/New_York", "2026-10-31T09:00:00-04:00", "2026-11-01T09:00:00-05:00", 25),
        ("Australia/Lord_Howe", "2026-10-03T09:00:00+10:30", "2026-10-04T09:00:00+11:00", 23.5),
    ],
)
def test_policy_daily_means_local_calendar_day_not_24_elapsed_hours(
    zone, start, expected, elapsed_hours
):
    anchor = instant(start, zone)
    following = next_occurrence(recurrence(anchor), anchor, NOW)
    assert following.local().isoformat() == expected
    assert (
        following.at.astimezone(UTC) - anchor.at.astimezone(UTC)
    ).total_seconds() == elapsed_hours * 3600


@pytest.mark.parametrize(
    "zone,start,expected",
    [
        ("Australia/Lord_Howe", "2026-10-03T02:15:00+10:30", "2026-10-04T02:45:00+11:00"),
        ("Pacific/Apia", "2011-12-29T12:00:00-10:00", "2011-12-31T12:00:00+14:00"),
        ("Pacific/Kiritimati", "1994-12-30T12:00:00-10:00", "1995-01-01T12:00:00+14:00"),
        ("Africa/Monrovia", "1972-01-06T00:15:00-00:44:30", "1972-01-07T00:59:30+00:00"),
    ],
)
def test_policy_gap_advance_uses_actual_transition_size(zone, start, expected):
    anchor = instant(datetime.fromisoformat(start), zone)
    following = next_occurrence(recurrence(anchor), anchor, NOW)
    assert following.local().isoformat() == expected
    assert following.at.astimezone(UTC) > anchor.at.astimezone(UTC)


def test_gap_crossing_midnight_does_not_drift_next_nominal_wall_clock_slot():
    anchor = instant("1981-12-30T23:45:00+07:30", "Asia/Singapore")
    state = recurrence(anchor, "FREQ=DAILY;COUNT=4")
    shifted = next_occurrence(state, anchor, NOW)
    assert shifted.local().isoformat() == "1982-01-01T00:15:00+08:00"
    following = next_occurrence(state, shifted, NOW)
    assert following.local().isoformat() == "1982-01-01T23:45:00+08:00"
    last = next_occurrence(state, following, NOW)
    assert last.local().isoformat() == "1982-01-02T23:45:00+08:00"
    assert next_occurrence(state, last, NOW) is None
    assert shifted.at.astimezone(UTC) < following.at.astimezone(UTC) < last.at.astimezone(UTC)


def test_policy_first_fold_remains_first_even_when_completion_is_in_second_fold():
    anchor = instant("2026-10-31T01:30:00-04:00")
    second_fold_completion = datetime.fromisoformat("2026-11-01T01:45:00-05:00")
    following = next_occurrence(recurrence(anchor), anchor, second_fold_completion)
    assert following.at.astimezone(UTC) == datetime(2026, 11, 1, 5, 30, tzinfo=UTC)
    assert following.local().fold == 0


def test_next_occurrence_cannot_return_an_earlier_instant_inside_second_fold():
    anchor = instant("2026-10-31T01:30:00-04:00")
    zone = ZoneInfo(anchor.timezone)
    current = Temporal(
        at=datetime(2026, 11, 1, 1, 15, tzinfo=zone, fold=1), timezone=anchor.timezone
    )
    following = next_occurrence(recurrence(anchor), current, NOW)
    assert following.at.astimezone(UTC) > current.at.astimezone(UTC)
    assert following.local().isoformat() == "2026-11-02T01:30:00-05:00"


def test_policy_fixed_recurrence_keeps_scheduled_anchor_after_late_completion():
    anchor = instant("2026-11-09T09:00:00-05:00")
    completed = datetime.fromisoformat("2026-11-12T18:45:00Z")
    fixed = next_occurrence(recurrence(anchor, "FREQ=DAILY;INTERVAL=2"), anchor, completed)
    relative = next_occurrence(
        recurrence(anchor, "FREQ=DAILY;INTERVAL=2", "after_completion"), anchor, completed
    )
    assert fixed.local().isoformat() == "2026-11-11T09:00:00-05:00"
    assert relative.local().isoformat() == "2026-11-14T13:45:00-05:00"


def test_policy_after_completion_date_uses_completion_local_day_and_keeps_date_precision():
    anchor = civil()
    completed = datetime.fromisoformat("2026-11-13T02:30:00Z")  # Nov 12 locally.
    following = next_occurrence(recurrence(anchor, mode="after_completion"), anchor, completed)
    assert following.date == date(2026, 11, 13) and following.at is None
    assert following.timezone == anchor.timezone


def test_after_completion_rejects_naive_completion_instant():
    anchor = civil()
    with pytest.raises(ValueError):
        next_occurrence(recurrence(anchor, mode="after_completion"), anchor, datetime(2026, 11, 9))


@pytest.mark.parametrize(
    "start,rule,expected",
    [
        (
            "2026-01-31",
            "FREQ=MONTHLY;COUNT=4",
            ["2026-01-31", "2026-03-31", "2026-05-31", "2026-07-31"],
        ),
        ("2024-02-29", "FREQ=YEARLY;COUNT=3", ["2024-02-29", "2028-02-29", "2032-02-29"]),
        ("2096-02-29", "FREQ=YEARLY;COUNT=3", ["2096-02-29", "2104-02-29", "2108-02-29"]),
        (
            "2026-01-31",
            "FREQ=MONTHLY;BYMONTHDAY=-1;COUNT=4",
            ["2026-01-31", "2026-02-28", "2026-03-31", "2026-04-30"],
        ),
        (
            "2026-11-09",
            "FREQ=WEEKLY;INTERVAL=2;COUNT=3",
            ["2026-11-09", "2026-11-23", "2026-12-07"],
        ),
        (
            "2026-12-31",
            "FREQ=MONTHLY;INTERVAL=2;COUNT=3",
            ["2026-12-31", "2027-08-31", "2027-10-31"],
        ),
    ],
)
def test_policy_rrule_calendar_edges_skip_invalid_dates(start, rule, expected):
    anchor = civil(start)
    actual = walk(recurrence(anchor, rule), anchor)
    assert [item.date.isoformat() for item in actual] == expected
    assert all(item.at is None and item.timezone == anchor.timezone for item in actual)


@pytest.mark.parametrize("as_date", [True, False])
def test_live_finite_weekly_naive_until_exhausts_at_last_december_slot(as_date):
    anchor = civil() if as_date else instant()
    rule = recurrence(anchor, "FREQ=WEEKLY;UNTIL=20261231T235959")
    values = walk(rule, anchor)
    assert [item.local().date().isoformat() for item in values] == [
        "2026-11-09",
        "2026-11-16",
        "2026-11-23",
        "2026-11-30",
        "2026-12-07",
        "2026-12-14",
        "2026-12-21",
        "2026-12-28",
    ]
    assert all((value.at is None) == as_date for value in values)


@pytest.mark.parametrize(
    "until,expected",
    [
        ("20261116T135959Z", None),
        ("20261116T140000Z", "2026-11-16T09:00:00-05:00"),
        ("20261116T140001Z", "2026-11-16T09:00:00-05:00"),
    ],
)
def test_rfc_utc_until_is_inclusive_and_compares_instants(until, expected):
    anchor = instant()
    rule = recurrence(anchor, f"FREQ=WEEKLY;UNTIL={until}")
    following = next_occurrence(rule, anchor, NOW)
    assert (following.local().isoformat() if following else None) == expected
    if following:
        assert next_occurrence(rule, following, NOW) is None


def test_rfc_utc_until_with_aware_anchor_preserves_wall_clock_across_dst():
    anchor = instant("2026-10-26T09:00:00-04:00")
    values = walk(recurrence(anchor, "FREQ=WEEKLY;UNTIL=20261231T235959Z"), anchor)
    assert len(values) == 10
    assert values[-1].local().isoformat() == "2026-12-28T09:00:00-05:00"
    assert all(item.local().hour == 9 for item in values)


def test_rfc_utc_until_during_second_fold_keeps_earlier_first_fold_slot():
    anchor = instant("2026-10-31T01:30:00-04:00")
    rule = recurrence(anchor, "FREQ=DAILY;UNTIL=20261101T061500Z")
    following = next_occurrence(rule, anchor, NOW)
    assert following is not None
    assert following.at.astimezone(UTC) == datetime(2026, 11, 1, 5, 30, tzinfo=UTC)
    assert next_occurrence(rule, following, NOW) is None


@pytest.mark.parametrize("mode", ["fixed", "after_completion"])
@pytest.mark.parametrize("rule", ["FREQ=DAILY;UNTIL=20261110T235959", "FREQ=DAILY;COUNT=2"])
def test_finite_series_cannot_restart_after_its_last_occurrence(mode, rule):
    anchor = civil()
    final = civil("2026-11-10")
    state = recurrence(anchor, rule, mode).model_copy(update={"occurrence_index": 1})
    assert next_occurrence(state, final, NOW + timedelta(days=1)) is None


def test_after_completion_durable_budget_exhausts_after_actual_occurrences():
    anchor = civil()
    state = recurrence(anchor, "FREQ=DAILY;COUNT=3", "after_completion")
    first_done = datetime(2026, 12, 9, 18, tzinfo=UTC)
    second = next_occurrence(state, anchor, first_done)
    assert second.date == date(2026, 12, 10)
    state = state.model_copy(update={"occurrence_index": 1})
    # Persistence roundtrip must retain the remaining budget across restarts.
    state = Recurrence.model_validate_json(state.model_dump_json())
    second_done = datetime(2027, 1, 10, 18, tzinfo=UTC)
    third = next_occurrence(state, second, second_done)
    assert third.date == date(2027, 1, 11)
    state = state.model_copy(update={"occurrence_index": 2})
    assert next_occurrence(state, third, datetime(2027, 2, 11, 18, tzinfo=UTC)) is None


@pytest.mark.parametrize("materialized", [0, 1, 3])
def test_json_roundtrip_preserves_continuation_at_each_finite_budget_stage(materialized):
    anchor = instant()
    state = recurrence(anchor, "FREQ=DAILY;COUNT=4", "after_completion")
    current = anchor
    completed = NOW
    for index in range(materialized):
        current = next_occurrence(state, current, completed)
        assert current is not None
        state = state.model_copy(update={"occurrence_index": index + 1})
        completed = current.at.astimezone(UTC) + timedelta(days=2)
    restored_state = Recurrence.model_validate_json(state.model_dump_json())
    restored_current = Temporal.model_validate_json(current.model_dump_json())

    def continuation(series, slot):
        result = []
        clock = completed
        for _ in range(5):
            following = next_occurrence(series, slot, clock)
            if following is None:
                return result
            result.append(following.at.astimezone(UTC))
            series = series.model_copy(update={"occurrence_index": series.occurrence_index + 1})
            slot = following
            clock = following.at.astimezone(UTC) + timedelta(days=2)
        pytest.fail("finite recurrence budget restarted after JSON roundtrip")

    original_slots = continuation(state, current)
    assert continuation(restored_state, restored_current) == original_slots
    assert len(original_slots) == 3 - materialized
    assert len(set(original_slots)) == len(original_slots)


@pytest.mark.parametrize("index", [-1, 1_000_001])
def test_recurrence_rejects_invalid_durable_occurrence_budget(index):
    with pytest.raises(ValidationError):
        Recurrence(
            rule="FREQ=DAILY;COUNT=3",
            anchor=civil(),
            timezone="America/New_York",
            occurrence_index=index,
        )


def test_bounded_search_reports_limit_instead_of_falsely_ending_sparse_series():
    anchor = civil()
    with pytest.raises(models.DomainError) as error:
        next_occurrence(recurrence(anchor, "FREQ=YEARLY;INTERVAL=1000"), anchor, NOW)
    assert error.value.code == "recurrence_limit"


def test_bounded_catchup_rejects_expensive_ancient_series_before_scanning(monkeypatch):
    anchor = civil("1000-01-01", "UTC")
    state = recurrence(anchor)

    def unexpected_rule_evaluation(*args, **kwargs):
        pytest.fail("ancient catch-up entered RRULE evaluation before enforcing its bound")

    monkeypatch.setattr(models, "rrulestr", unexpected_rule_evaluation)
    with pytest.raises(models.DomainError) as error:
        next_occurrence(state, civil("2026-11-09", "UTC"), NOW)
    assert error.value.code == "recurrence_limit"


def test_impossible_selector_distinguishes_search_limit_from_explicit_rule_ending():
    anchor = civil()
    impossible = "FREQ=MONTHLY;BYMONTH=2;BYMONTHDAY=30"
    with pytest.raises(models.DomainError) as error:
        next_occurrence(recurrence(anchor, impossible), anchor, NOW)
    assert error.value.code == "recurrence_limit"
    ended = recurrence(anchor, impossible + ";UNTIL=20261231T235959")
    assert next_occurrence(ended, anchor, NOW) is None


@pytest.mark.parametrize("mode", ["fixed", "after_completion"])
def test_recurrence_preview_is_repeatable_and_does_not_consume_or_mutate_input(mode):
    anchor = instant()
    state = recurrence(anchor, "FREQ=DAILY;COUNT=3", mode)
    before_state = state.model_dump(mode="json")
    before_current = anchor.model_dump(mode="json")
    completed = NOW + timedelta(days=15)
    first_preview = next_occurrence(state, anchor, completed)
    second_preview = next_occurrence(state, anchor, completed)
    assert first_preview is not None and second_preview == first_preview
    assert state.model_dump(mode="json") == before_state
    assert anchor.model_dump(mode="json") == before_current
    assert state.occurrence_index == 0


def test_after_completion_count_budget_is_not_consumed_by_elapsed_calendar_days():
    anchor = civil()
    completed = datetime(2026, 12, 9, 18, tzinfo=UTC)
    following = next_occurrence(
        recurrence(anchor, "FREQ=DAILY;COUNT=2", "after_completion"), anchor, completed
    )
    assert following is not None
    assert following.date == date(2026, 12, 10)


def test_fixed_weekly_count_counts_actual_matching_weekdays():
    anchor = civil()
    values = walk(recurrence(anchor, "FREQ=WEEKLY;BYDAY=MO,WE;COUNT=4"), anchor)
    assert [item.date.isoformat() for item in values] == [
        "2026-11-09",
        "2026-11-11",
        "2026-11-16",
        "2026-11-18",
    ]


@pytest.mark.parametrize("count", [1, 2, 5])
def test_count_limits_number_of_slots_including_anchor(count):
    anchor = civil()
    values = walk(recurrence(anchor, f"FREQ=DAILY;COUNT={count}"), anchor)
    assert len(values) == count


@pytest.mark.parametrize("until", ["20261109T090000", "20261108T235959"])
def test_until_at_or_before_anchor_has_no_successor(until):
    anchor = instant()
    assert next_occurrence(recurrence(anchor, f"FREQ=DAILY;UNTIL={until}"), anchor, NOW) is None


@pytest.mark.parametrize(
    "rule",
    [
        "FREQ=DAILY;INTERVAL=0",
        "FREQ=DAILY;INTERVAL=-1",
        "FREQ=DAILY;INTERVAL=1.5",
        "FREQ=DAILY;COUNT=-1",
        "FREQ=DAILY;COUNT=0",
        "FREQ=DAILY;COUNT=1.5",
        "FREQ=DAILY;COUNT=2;UNTIL=20261231T235959",
        "FREQ=DAILY;FREQ=WEEKLY",
        "FREQ=DAILY;INTERVAL=1;INTERVAL=2",
        "FREQ=DAILY;COUNT=2;COUNT=3",
        "FREQ=DAILY;BYDAY=MO;BYDAY=TU",
        "FREQ=DAILY;UNKNOWN=1",
        "FREQ=HOURLY",
        "FREQ=MINUTELY",
        "FREQ=SECONDLY",
        "FREQ=DAILY\nRDATE:20261110",
        "FREQ=DAILY\r\nEXDATE:20261110",
        "FREQ=DAILY;",
        "FREQ=DAILY;BYMONTH=13",
        "FREQ=DAILY;BYMONTH=0",
        "FREQ=DAILY;BYMONTH=-1",
        "FREQ=DAILY;BYMONTHDAY=32",
        "FREQ=DAILY;BYMONTHDAY=0",
        "FREQ=DAILY;BYYEARDAY=367",
        "FREQ=DAILY;BYYEARDAY=0",
        "FREQ=DAILY;BYWEEKNO=54",
        "FREQ=DAILY;BYDAY=XX",
        "FREQ=DAILY;BYHOUR=24",
        "FREQ=DAILY;BYMINUTE=60",
        "FREQ=DAILY;BYSECOND=60",
        "FREQ=DAILY;BYSECOND=-1",
        "FREQ=DAILY;BYDAY=0MO",
        "FREQ=DAILY;WKST=XX",
        "FREQ=DAILY;BYSETPOS=1",
        "FREQ=DAILY;BYDAY=MO;BYSETPOS=2",
        "FREQ=WEEKLY;BYDAY=MO;BYSETPOS=8",
        "FREQ=MONTHLY;BYDAY=MO;BYSETPOS=32",
    ],
)
def test_invalid_or_ambiguous_rrule_is_rejected_at_model_boundary(rule):
    with pytest.raises(ValidationError):
        recurrence(civil(), rule)


def test_monthly_last_weekday_selector_remains_supported():
    anchor = civil("2026-01-30")
    state = recurrence(anchor, "FREQ=MONTHLY;BYDAY=MO,TU,WE,TH,FR;BYSETPOS=-1;COUNT=4")
    assert [item.date.isoformat() for item in walk(state, anchor)] == [
        "2026-01-30",
        "2026-02-27",
        "2026-03-31",
        "2026-04-30",
    ]


@pytest.mark.parametrize("part", ["BYHOUR=9,10", "BYMINUTE=0,30", "BYSECOND=0,30"])
def test_policy_v1_day_or_longer_rejects_subday_expansion(part):
    with pytest.raises(ValidationError):
        recurrence(civil(), f"FREQ=DAILY;{part}")


@pytest.mark.parametrize(
    "field",
    [
        "created_at",
        "updated_at",
        "completed_at",
        "scheduled_at",
        "retry_at",
        "lease_until",
    ],
)
def test_every_reminder_instant_rejects_naive_timestamp(field):
    values = {
        "id": "synthetic-reminder",
        "workspace": "synthetic",
        "kind": "reminder",
        "scheduled_at": NOW,
        "recipient_id": "synthetic-person",
    }
    values[field] = datetime(2026, 11, 9, 18)
    with pytest.raises(ValidationError):
        Entity(**values)


@pytest.mark.parametrize("missing", ["scheduled_at", "recipient_id"])
def test_reminder_requires_explicit_schedule_and_recipient(missing):
    values = {
        "id": "synthetic-reminder",
        "workspace": "synthetic",
        "kind": "reminder",
        "scheduled_at": NOW,
        "recipient_id": "synthetic-person",
    }
    del values[missing]
    with pytest.raises(ValidationError):
        Entity(**values)


def test_reminder_instant_fields_roundtrip_without_losing_offsets_or_due_precision():
    scheduled = datetime.fromisoformat("2026-11-09T09:15:00+05:45")
    original = Entity(
        id="synthetic-reminder",
        workspace="synthetic",
        kind="reminder",
        scheduled_at=scheduled,
        recipient_id="synthetic-person",
        retry_at=scheduled + timedelta(minutes=1),
        lease_until=scheduled + timedelta(minutes=2),
        deadline=civil(zone="Asia/Kathmandu"),
    )
    restored = Entity.model_validate_json(original.model_dump_json())
    assert restored.scheduled_at == scheduled
    assert restored.retry_at == scheduled + timedelta(minutes=1)
    assert restored.lease_until == scheduled + timedelta(minutes=2)
    assert restored.deadline.at is None and restored.deadline.date == date(2026, 11, 9)


@pytest.mark.parametrize("value", ["2026-11-09T14:00:00Z", NOW, "2026-11-09T09:00:00-05:00"])
def test_normalize_now_produces_equivalent_utc_instant(value):
    actual = models.normalize_now(value)
    expected = NOW if value is NOW else datetime(2026, 11, 9, 14, tzinfo=UTC)
    assert actual == expected and actual.tzinfo is UTC


@pytest.mark.parametrize("value", ["2026-11-09T09:00:00", datetime(2026, 11, 9, 9)])
def test_normalize_now_rejects_naive_clock(value):
    with pytest.raises(ValueError):
        models.normalize_now(value)


def run_bounded_rule(rule, start="2000-01-01", current="2026-11-09"):
    """Reject or evaluate safely; hostile work cannot stall this pytest worker."""
    script = textwrap.dedent("""
        import importlib.util, json, sys
        from datetime import datetime, timezone
        from pydantic import ValidationError
        path, rule, start, current = sys.argv[1:]
        spec = importlib.util.spec_from_file_location('_bounded_temporal_models', path)
        m = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = m
        spec.loader.exec_module(m)
        try:
            anchor = m.Temporal(date=start, timezone='UTC')
            recurrence = m.Recurrence(rule=rule, anchor=anchor, timezone='UTC')
            result = m.next_occurrence(recurrence, m.Temporal(date=current, timezone='UTC'),
                                       datetime(2026, 11, 9, tzinfo=timezone.utc))
        except (ValidationError, m.DomainError) as error:
            print(json.dumps({'rejected': type(error).__name__}))
        else:
            print(json.dumps({'result': result.model_dump(mode='json') if result else None}))
    """)
    try:
        result = subprocess.run(
            [sys.executable, "-c", script, str(MODEL_PATH), rule, start, current],
            capture_output=True,
            text=True,
            timeout=4,
            check=False,
        )
    except subprocess.TimeoutExpired:
        pytest.fail(f"rule evaluation exceeded 4-second safety budget: {rule}")
    assert result.returncode == 0, f"unhandled failure for {rule}: {result.stderr}"
    return json.loads(result.stdout)


@pytest.mark.parametrize(
    "rule",
    [
        "FREQ=DAILY;INTERVAL=" + "9" * 200,
        "FREQ=YEARLY;INTERVAL=9999999999999999999999999",
        "FREQ=DAILY;COUNT=" + "9" * 200,
        "FREQ=DAILY;BYSETPOS=366",  # No daily candidate occupies this position.
        "FREQ=MONTHLY;BYMONTH=2;BYMONTHDAY=30",  # Impossible in every year.
        "FREQ=DAILY;BYHOUR="
        + ",".join(map(str, range(24)))
        + ";BYMINUTE="
        + ",".join(map(str, range(60)))
        + ";BYSECOND="
        + ",".join(map(str, range(60))),
    ],
)
def test_hostile_rule_is_rejected_or_evaluated_with_bounded_runtime(rule):
    run_bounded_rule(rule)
