"""A returning reader must distinguish an empty workspace from the wrong database."""

import json
import sqlite3
from contextlib import closing

import pytest

from daymark import Store
from daymark.cli import main


@pytest.mark.parametrize("command", [
    ["ready"], ["prime", "--brief"], ["find", "sample"], ["agenda"], ["show", "task"],
    ["profile"], ["project", "config", "press"], ["goal", "show", "goal"],
])
def test_reading_an_explicitly_missing_file_leaves_it_missing(tmp_path, capsys, command):
    db = tmp_path / "missing database.sqlite"
    assert main(["--db", str(db), *command, "--json"]) == 6
    error = json.loads(capsys.readouterr().err)["error"]
    assert error["code"] == "no_database" and any("daymark where" in hint for hint in error["details"])
    assert list(tmp_path.iterdir()) == []


def test_environment_typo_and_foreign_sqlite_are_not_initialized(tmp_path, monkeypatch, capsys):
    db = tmp_path / "another tracker.sqlite"
    monkeypatch.setenv("DAYMARK_DB", str(db))
    assert main(["ready", "--json"]) == 6 and not db.exists()
    capsys.readouterr()
    with closing(sqlite3.connect(db)) as con:
        con.execute("CREATE TABLE tracker_issues(id TEXT)")
        con.commit()
    before = db.read_bytes()
    assert main(["ready", "--json"]) == 6
    assert "no Daymark database" in json.loads(capsys.readouterr().err)["error"]["message"]
    assert db.read_bytes() == before and list(tmp_path.iterdir()) == [db]
    assert main(["workspaces"]) == 6 and db.read_bytes() == before
    capsys.readouterr()
    assert main(["mcp", "--read-only"]) == 6 and db.read_bytes() == before
    assert "daymark where" in capsys.readouterr().err
    assert main(["where", "--json"]) == 0
    selected = json.loads(capsys.readouterr().out)
    assert selected["file_exists"] and selected["source"] == "DAYMARK_DB"


def test_wrong_instruction_path_returns_a_typed_error_and_preserves_the_original(tmp_path, capsys):
    instructions = tmp_path / "AGENTS.md"
    original = b"# Studio rules\nUse the existing code tracker for defects.\n"
    instructions.write_bytes(original)
    assert main(["--db", str(instructions), "ready", "--json"]) == 5
    error = json.loads(capsys.readouterr().err)["error"]
    assert error["code"] == "invalid" and "daymark where" in str(error["details"])
    assert "Studio rules" not in str(error) and instructions.read_bytes() == original


def test_first_capture_still_creates_a_selected_store_and_empty_workspace_is_an_observation(tmp_path, capsys):
    db = tmp_path / "new workspace.sqlite"
    args = ["--db", str(db)]
    assert main([*args, "--workspace", "studio", "add", "Review the proof", "--id", "sample"]) == 0
    capsys.readouterr()
    assert main([*args, "--workspace", "other", "ready", "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == []
    assert main([*args, "--workspace", "studio", "ready", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)[0]["id"] == "sample"
    with closing(Store(db, read_only=True)) as store:
        assert store.head("studio") == 1 and store.head("other") == 0
    assert main(["--db", ":memory:", "ready", "--json"]) == 0


def test_offline_actor_matches_the_first_primer_when_no_actor_was_configured(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("DAYMARK_ACTOR", raising=False)
    db = tmp_path / "review.sqlite"
    args = ["--db", str(db)]
    assert main([*args, "where", "--json"]) == 0
    offline = json.loads(capsys.readouterr().out)
    assert not db.exists() and offline["actor"] == "agent"
    assert main([*args, "init"]) == 0
    capsys.readouterr()
    assert main([*args, "prime", "--brief", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["context"]["actor"] == offline["actor"]


def test_read_only_mcp_reports_missing_file_without_creating_it(tmp_path, capsys):
    db = tmp_path / "uncreated.sqlite"
    assert main(["--db", str(db), "mcp", "--read-only"]) == 6
    assert "no_database" in capsys.readouterr().err and not db.exists()


def test_missing_remote_credential_identifies_configuration_before_any_network_call(monkeypatch, capsys):
    monkeypatch.delenv("SAMPLE_UNSET_TOKEN", raising=False)
    args = ["--url", "http://sample.invalid", "--token-env", "SAMPLE_UNSET_TOKEN"]
    assert main([*args, "where", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["identity_source"] == "server credential"
    assert main([*args, "prime", "--brief", "--json"]) == 1
    error = json.loads(capsys.readouterr().err)["error"]
    assert error["code"] == "unauthenticated" and "--token-env" in error["details"][0]
