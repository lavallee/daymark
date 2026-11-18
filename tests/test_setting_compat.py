import sqlite3
from contextlib import closing

import pytest

from daymark import DomainError, Store
from daymark.cli import _copy_database

KEY = "setting:synthetic:profile"
SETTING = ("synthetic", "profile")


def legacy_write(db, value):
    if value is None:
        db.execute("DELETE FROM daymark_scheduler WHERE key=?", (KEY,))
    else:
        db.execute(
            "INSERT INTO daymark_scheduler VALUES(?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (KEY, value),
        )


def assert_conflict(call, *args, **kwargs):
    with pytest.raises(DomainError) as caught:
        call(*args, **kwargs)
    assert caught.value.code == "conflict"


def test_schema_six_setting_tracking_migration(tmp_path):
    path = tmp_path / "legacy.sqlite3"
    with closing(Store(path)) as initial:
        for (name,) in initial.db.execute(
            "SELECT name FROM sqlite_master WHERE type='trigger' AND tbl_name='daymark_scheduler'"
        ).fetchall():
            initial.db.execute(f"DROP TRIGGER {name}")
        initial.execute("synthetic", "create", "create", {"id":"sample","kind":"task"}, 0)
        legacy_write(initial.db, '"old"')
        initial.db.commit()
    for options in ({"read_only": True}, {"upgrade": False}):
        with closing(Store(path, **options)) as reader:
            assert reader.get("synthetic", "sample")["id"] == "sample"
            assert reader.setting(*SETTING) == "old"
            assert_conflict(reader.setting_state, *SETTING)
    with closing(sqlite3.connect(path)) as connection:
        candidate = Store(connection)
        assert candidate.setting_state(*SETTING) == {"value": "old", "version": 1}
        connection.rollback()
        assert_conflict(candidate.setting_state, *SETTING)
        with closing(Store(path, read_only=True)) as reader:
            assert_conflict(reader.setting_state, *SETTING)

        candidate.set_setting(*SETTING, "new", expected_version=1)
        connection.commit()
    with closing(Store(path, read_only=True)) as reader:
        assert reader.setting_state(*SETTING) == {"value": "new", "version": 2}


def test_legacy_writes_keep_revisions_across_transactions_and_native_copies(tmp_path):
    path = tmp_path / "tracked.sqlite3"
    with (
        closing(Store(path)) as candidate,
        closing(Store(path, read_only=True)) as reader,
        closing(sqlite3.connect(path)) as legacy,
    ):
        candidate.set_setting(*SETTING, "a", expected_version=0)
        for raw, value, version in (
            ('"a"', "a", 1),
            ('"b"', "b", 2),
            ('"c"', "c", 3),
            ('"a"', "a", 4),
            (None, None, 5),
            (None, None, 5),
            ('"a"', "a", 6),
        ):
            legacy_write(legacy, raw)
            legacy.commit()
            assert reader.setting_state(*SETTING) == {"value": value, "version": version}
        assert_conflict(candidate.set_setting, *SETTING, "stale", expected_version=1)
        legacy_write(legacy, None)
        legacy_write(legacy, '"rolled back"')
        legacy.rollback()
        assert reader.setting_state(*SETTING) == {"value": "a", "version": 6}
        legacy_write(legacy, '"intermediate"')
        legacy_write(legacy, '"a"')
        assert reader.setting_state(*SETTING) == {"value": "a", "version": 6}
        legacy.commit()
        assert reader.setting_state(*SETTING) == {"value": "a", "version": 8}
        candidate.set_setting(*SETTING, "a", expected_version=8)
        assert reader.setting_state(*SETTING) == {"value": "a", "version": 8}
        candidate.set_setting(*SETTING, None, expected_version=8)
        snapshot = candidate.export("synthetic")

    backup = tmp_path / "backup.sqlite3"
    _copy_database(path, backup)
    with closing(Store(backup, read_only=True)) as reader, closing(sqlite3.connect(backup)) as db:
        assert reader.setting_state(*SETTING) == {"value": None, "version": 9}
        legacy_write(db, '"restored"')
        db.commit()
        assert reader.setting_state(*SETTING) == {"value": "restored", "version": 10}
    with closing(Store(":memory:")) as destination:
        destination.import_snapshot("synthetic", snapshot, operation_id="import", expected_version=0)
        assert destination.setting_state(*SETTING) == {"value": None, "version": 0}
