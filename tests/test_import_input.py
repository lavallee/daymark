"""Invalid foreign text must not create a destination or echo private data."""

import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from daymark.cli import main


def import_snapshot(db, source):
    args = [
        "import",
        "--file",
        str(source),
        "--operation-id",
        "transfer",
        "--expected-version",
        "0",
    ]
    return main(["--db", str(db), *args])


@pytest.mark.parametrize(
    "data",
    [
        b"TYPE,CONTENT,UNKNOWN_HEADER\r\ntask,Private row text,Private value\r\n",
        b'{"entities": [{"title": "Private row text"}',
        b"\xff\xfe",
    ],
)
def test_unreadable_snapshot_refused_before_opening_destination(tmp_path, capsys, data):
    source = tmp_path / "project.csv"
    source.write_bytes(data)
    db = tmp_path / "destination.db"
    assert import_snapshot(db, source) == 5
    error = capsys.readouterr().err
    assert "native Daymark JSON snapshot" in error and "daymark export" in error
    assert "Private" not in error and str(source) not in error
    assert source.read_bytes() == data and not db.exists()


def test_native_snapshot_is_recognized_by_content_and_keeps_retry_semantics(tmp_path, capsys):
    source_db = tmp_path / "source.db"
    destination_db = tmp_path / "destination.db"
    payload = tmp_path / "native-with-different-extension.csv"
    assert main(["--db", str(source_db), "add", "Press proof", "--id", "proof"]) == 0
    assert main(["--db", str(source_db), "export", "--output", str(payload)]) == 0
    assert import_snapshot(destination_db, payload) == 0
    capsys.readouterr()
    assert (
        import_snapshot(destination_db, payload) == 0 and '"entities": 1' in capsys.readouterr().out
    )
    assert main(["--db", str(destination_db), "events", "--head"]) == 0
    assert '"cursor": 1' in capsys.readouterr().out


def test_bad_input_leaves_a_previous_release_database_untouched(tmp_path, capsys):
    db = tmp_path / "previous.db"
    fixture = Path(__file__).parent / "fixtures" / "daymark-0.4.0.sql"
    with closing(sqlite3.connect(db)) as connection:
        connection.executescript(fixture.read_text(encoding="utf-8"))
    before = db.read_bytes()
    missing = tmp_path / "missing-export.json"
    assert import_snapshot(db, missing) == 5
    assert "path and permissions" in capsys.readouterr().err
    assert db.read_bytes() == before and not (tmp_path / "previous.db-wal").exists()


@pytest.mark.parametrize("content", [
    "[]", '{"records":[]}', '{"schema_version":1,"workspace":"elsewhere"}',
    '{"schema_version":1,"entities":{}}',
])
def test_foreign_json_envelope_keeps_unopened_destination_absent(tmp_path, capsys, content):
    source = tmp_path / "tasks.json"
    source.write_text(content)
    destination = tmp_path / "destination.db"
    assert import_snapshot(destination, source) == 5
    assert "Invalid snapshot" in capsys.readouterr().err
    assert not destination.exists()
