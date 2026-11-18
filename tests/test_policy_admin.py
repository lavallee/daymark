import json
import sqlite3
from contextlib import closing

from daymark import Store
from daymark.cli import main


def test_policy_handover_retry_and_transfer(tmp_path, capsys):
    source = tmp_path / "source.sqlite3"
    backup = tmp_path / "backup.sqlite3"
    restored = tmp_path / "restored.sqlite3"
    destination = tmp_path / "destination.sqlite3"
    snapshot = tmp_path / "snapshot.json"

    def cli(*args, db=source, status=0):
        assert main(["--db", str(db), "--workspace", "sample", *map(str, args)]) == status
        out, err = capsys.readouterr()
        return (json.loads(out) if out else None) if status == 0 else err

    assert main(["--db", str(source), "init"]) == 0
    capsys.readouterr()
    assert cli("policy", "--versioned") == {"deciders": [], "version": 0}
    assert cli("policy", "--deciders", "owner-a") == {"deciders": ["owner-a"]}
    assert cli("policy") == {"deciders": ["owner-a"]}
    reviewed = cli("policy", "--versioned")
    assert reviewed == {"deciders": ["owner-a"], "version": 1}

    handover = ("policy", "--deciders", "owner-b", "--if-version", "1", "--operation-id", "handover")
    cli(*handover)  # The operator loses this result.
    stale = ("policy", "--deciders", "owner-a,editor-b", "--if-version", "1", "--operation-id", "edit")
    assert "read it again with policy --versioned" in cli(*stale, status=4)
    assert cli("policy", "--versioned") == {"deciders": ["owner-b"], "version": 2}
    assert cli("policy", "--deciders", "owner-b,editor-b") == {"deciders": ["editor-b", "owner-b"]}
    assert cli(*handover) == {"deciders": ["owner-b"], "version": 2}
    assert "reused" in cli(*handover[:2], "editor-b", *handover[3:], status=4)
    assert cli("policy", "--versioned") == {"deciders": ["editor-b", "owner-b"], "version": 3}

    cli("propose", "Review sample", "--id", "sample-proposal", "--json")
    cli("backup", "--file", backup)
    cli("restore", "--file", backup, db=restored)
    assert cli("policy", "--versioned", db=restored) == {"deciders": ["editor-b", "owner-b"], "version": 3}
    assert cli(*handover, db=restored) == {"deciders": ["owner-b"], "version": 2}

    cli("export", "--output", snapshot)
    set_destination = ("policy", "--deciders", "destination-owner", "--if-version", 0,
                       "--operation-id", "destination-setup")
    assert cli(*set_destination, db=destination) == {"deciders": ["destination-owner"], "version": 1}
    assert cli("import", "--file", snapshot, "--operation-id", "copy", "--expected-version", 0,
               db=destination)["entities"] == 1
    assert cli("policy", "--versioned", db=destination) == {"deciders": ["destination-owner"], "version": 1}
    assert cli(*set_destination, db=destination) == {"deciders": ["destination-owner"], "version": 1}
    # Import leaves the source operation ID available in the destination.
    clear = ("policy", "--clear", "--if-version", 1, "--operation-id", "handover")
    assert cli(*clear, db=destination) == {"deciders": [], "version": 2}
    assert cli(*clear, db=destination) == {"deciders": [], "version": 2}


def test_policy_tracking_counts_v05_scheduler_upserts(tmp_path, capsys):
    path = tmp_path / "shared.sqlite3"

    def v05_write(connection, actor):
        # The tagged v0.5.0 Store.set_policy uses this upsert.
        connection.execute(
            "INSERT INTO daymark_scheduler VALUES(?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            ("policy:sample", json.dumps({"deciders": [actor]})),
        )
        connection.commit()

    with closing(Store(path)) as initial:
        for suffix in ("insert", "update", "delete"):
            initial.db.execute(f"DROP TRIGGER daymark_policy_{suffix}")
        initial.db.commit()
    with closing(sqlite3.connect(path)) as legacy:
        v05_write(legacy, "owner-a")
        with closing(Store(path, read_only=True)) as untracked:
            assert untracked.policy("sample") == {"deciders": ["owner-a"]}
        cli = ["--db", str(path), "--workspace", "sample", "policy"]
        assert main([*cli, "--versioned"]) == 0
        assert json.loads(capsys.readouterr().out) == {"deciders": ["owner-a"], "version": 1}
        with closing(Store(path)) as current:
            assert current.stored_schema() == "6"
            assert current.policy_state("sample") == {"deciders": ["owner-a"], "version": 1}
            v05_write(legacy, "owner-b")
            v05_write(legacy, "owner-a")
            v05_write(legacy, "owner-a")
            assert current.policy_state("sample") == {"deciders": ["owner-a"], "version": 3}


def test_invalid_policy_arguments_do_not_create_database(tmp_path, capsys):
    path = tmp_path / "missing.sqlite3"
    prefix = ["--db", str(path), "policy"]
    guard = ["--if-version", "0", "--operation-id", "edit"]
    for args in (
        ["--clear", "--if-version", "0"],
        ["--clear", "--operation-id", "edit"],
        ["--clear", "--if-version", "-1", "--operation-id", "edit"],
        ["--clear", "--if-version", "0", "--operation-id", ""],
        ["--clear", "--if-version", "0", "--operation-id", "x" * 201],
        ["--versioned", "--clear"],
        guard,
        ["--deciders", " , ", *guard],
        ["--deciders", "x" * 201],
        ["--deciders", ",".join(f"actor-{n}" for n in range(101))],
        ["--deciders", "actor-\udcff"],
    ):
        assert main([*prefix, *args]) == 5
        assert "invalid" in capsys.readouterr().err
        assert not list(tmp_path.iterdir())

    for args in ([], ["--versioned"]):
        assert main([*prefix, *args]) == 6
        assert "no_database" in capsys.readouterr().err
        assert not list(tmp_path.iterdir())


def test_policy_cli_refuses_remote_configuration(capsys):
    assert main(["--url", "https://example.invalid", "policy", "--clear",
                 "--if-version", "0", "--operation-id", "edit"]) == 5
    assert "requires local database access" in capsys.readouterr().err


def test_policy_guard_preserves_existing_profile_and_receipt(tmp_path, capsys):
    db = tmp_path / "shared.sqlite3"
    prefix = ["--db", str(db), "--workspace", "sample"]

    def call(args, status=0):
        assert main([*prefix, *args]) == status
        out, err = capsys.readouterr()
        return json.loads(out) if status == 0 else err

    profile = ["profile", "plain", "--if-version", "0", "--op", "profile-edit", "--json"]
    saved = call(profile)
    guard = ["policy", "--deciders", "owner-a", "--if-version", "0", "--operation-id"]
    assert "reused" in call([*guard, "profile-edit"], status=4)
    assert call(["policy", "--versioned"]) == {"deciders": [], "version": 0}
    assert call([*guard, "policy-edit"]) == {"deciders": ["owner-a"], "version": 1}
    assert call(profile) == saved
    restored = call(["profile", "--undo", "profile-edit", "--op", "profile-undo", "--json"])
    assert restored["version"] == 2
    assert call(["policy", "--versioned"]) == {"deciders": ["owner-a"], "version": 1}
