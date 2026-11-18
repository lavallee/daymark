"""Explicit local clock input needs a real, unambiguous instant."""

import json
from contextlib import closing

import pytest

from daymark import DomainError, Store, Tasks
from daymark.cli import main
from daymark.mcp import handle
from daymark.render import when
from daymark.tasks import parse_when


@pytest.mark.parametrize(
    ("zone", "day", "clock", "reason", "examples"),
    [
        ("America/New_York", "2030-11-03", "01:30", "occurs twice", ["-04:00", "-05:00"]),
        ("America/New_York", "2030-03-10", "02:30", "does not exist", ["03:30:00-04:00"]),
        ("Australia/Lord_Howe", "2030-04-07", "01:45", "occurs twice", ["+11:00", "+10:30"]),
        ("Australia/Lord_Howe", "2030-10-06", "02:15", "does not exist", ["02:45:00+11:00"]),
    ],
)
def test_repeated_and_skipped_clocks_refuse_absolute_and_relative_forms(zone, day, clock, reason, examples):
    for expression in (day + "T" + clock, "today " + clock):
        with pytest.raises(DomainError, match=reason) as refused:
            parse_when(expression, zone, now=day + "T12:00:00Z")
        assert refused.value.code == "invalid" and zone in str(refused.value)
        assert all(value in refused.value.details[0] for value in examples)


def test_legacy_operations_still_replay_their_original_local_time():
    zone = "America/New_York"
    now = "2030-03-10T12:00:00Z"
    raw = "2030-03-10T02:30"
    # A pre-change absolute gap was stored with the pre-transition offset.
    deadline = {"at": raw + ":00-05:00", "date": None, "timezone": zone, "expression": None}
    shifted = {**deadline, "at": "2030-03-10T03:30:00-04:00", "expression": "today 02:30"}
    with closing(Store(":memory:")) as store:
        tasks = Tasks(store, "lab", actor="worker", timezone=zone, now=now)
        create = {"id": "sample", "kind": "task", "title": "Sample", "deadline": deadline}
        original = store.execute("lab", "add", "create", create, 0, actor="worker")
        assert tasks.add("Sample", id="sample", due=raw, op="add") == original
        calls = [
            ("edit", {"planned": deadline}, lambda: tasks.edit("sample", plan=raw, op="edit")),
            ("wait", {"waiting_on": "reading", "status": "waiting", "earliest_start": shifted},
             lambda: tasks.wait("sample", "reading", until="today 02:30", op="wait")),
            ("defer", {"earliest_start": deadline}, lambda: tasks.defer("sample", raw, op="defer")),
        ]
        for operation, fields, replay in calls:
            version = tasks.get("sample")["version"]
            expected = store.execute("lab", operation, "update", {"id": "sample", **fields}, version, actor="worker")
            assert replay() == expected
        with pytest.raises(DomainError, match="does not exist"):
            tasks.edit("sample", plan=raw, op="fresh")
        assert store.head("lab") == 4


def test_full_mcp_and_cli_detail_preserve_zone_offset_precision_and_date_evidence(tmp_path, capsys):
    path = tmp_path / "sample.db"
    with closing(Store(path)) as store:
        tasks = Tasks(store, "lab", timezone="Australia/Lord_Howe", now="2030-04-07T01:00:00Z")
        observed = []
        for id, offset in (("early", "+11:00"), ("late", "+10:30")):
            deadline = "2030-04-07T01:45:03.123456" + offset
            added = tasks.add("Measure the sample", id=id, due=deadline, plan="tomorrow")
            stored = added["deadline"]
            read = handle(tasks, {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {
                "name": "daymark_view", "arguments": {"view": "show", "id": id, "json": True},
            }})["result"]["structuredContent"]
            assert read["temporal"]["deadline"] == {"at": stored["at"], "timezone": stored["timezone"]}
            assert read["temporal"]["planned"]["expression"] == "tomorrow"
            assert read["entity"]["deadline"] == when(stored)
            assert offset in read["entity"]["deadline"] and "03.123456" in read["entity"]["deadline"]
            observed.append(read)
        assert observed[0]["temporal"]["deadline"] != observed[1]["temporal"]["deadline"]
    main(["--db", str(path), "--workspace", "lab", "show", "late", "--json"])
    assert json.loads(capsys.readouterr().out)["temporal"] == observed[1]["temporal"]


def test_misspelled_daymark_timezone_refuses_capture_instead_of_using_host_zone(tmp_path, monkeypatch, capsys):
    from daymark.cli import main

    db = str(tmp_path / "work.db")
    monkeypatch.setenv("TZ", "America/New_York")
    monkeypatch.setenv("DAYMARK_TZ", "America/Los_Angles")
    assert main(["--db", db, "add", "Call the printer", "--id", "call", "--due", "tomorrow 09:00"]) == 5
    assert "IANA" in capsys.readouterr().err
    with closing(Store(db)) as store:
        assert store.head("default") == 0
    assert main(["--db", db, "--tz", "UTC", "add", "Call the printer", "--id", "call"]) == 0
    assert main(["--db", db, "--tz", "Unrecognized/Zone", "prime"]) == 5


def test_ambiguous_numeric_correction_preserves_distinct_civil_dates():
    with closing(Store(":memory:")) as store:
        tasks = Tasks(store, "studio", timezone="Europe/Madrid")
        original = tasks.add("Prepare the sketch", id="proof", source="print:proof:S-41",
                             plan="2030-05-07", target="2030-06-18", due="2030-06-24")
        with pytest.raises(DomainError, match="Unrecognized date"):
            tasks.edit("proof", due="07/08/2030", expected_version=original["version"])
        assert tasks.get("proof") == original and store.head("studio") == 1

        tasks.edit("proof", due="2030-08-07", expected_version=original["version"])
        item = tasks.get("proof")
        for field, day in (("planned", "2030-05-07"), ("latest_target", "2030-06-18"),
                           ("deadline", "2030-08-07")):
            assert item[field] == {"date": day, "timezone": "Europe/Madrid", "at": None, "expression": None}
        assert item["earliest_start"] is None and item["notes"] == []
        comparison = tasks.changes_since(source="print:proof:S-41", version=1)
        assert [change["field"] for change in comparison["fields"]] == ["deadline"]
