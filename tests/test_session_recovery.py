"""An embedding application owns its transaction and a saved cursor can survive transfer."""

import json
import sqlite3
from contextlib import closing

import pytest

from daymark import DomainError, Store, Tasks


def test_caller_owned_snapshot_conflict_keeps_outer_transaction_and_retry(tmp_path):
    path = tmp_path / "work.db"
    with closing(Store(path)) as writer, closing(sqlite3.connect(path)) as connection:
        producer = Tasks(writer, "proofs")
        producer.add("Inspect proof", id="check")
        worker = Tasks(Store(connection, upgrade=False), "proofs", actor="reader")
        connection.execute("CREATE TEMP TABLE application_note(value)")
        first = worker.orientation()
        connection.execute("INSERT INTO application_note VALUES(?)", ("keep this",))
        producer.edit("check", title="Inspect revised proof")
        assert worker.orientation()["activity_cursor"] == first["activity_cursor"]
        with pytest.raises(DomainError, match="after this SQLite snapshot") as stale:
            worker.note("check", "Finished review", op="report")
        assert stale.value.code == "conflict"
        assert connection.in_transaction
        assert connection.execute("SELECT value FROM application_note").fetchone() == ("keep this",)
        assert writer.receipt("proofs", "report") is None
        connection.commit()  # Only the application decides this.
        new = worker.note("check", "Finished review", op="report")
        assert connection.in_transaction and new["title"] == "Inspect revised proof"
        assert writer.receipt("proofs", "report") is None
        connection.commit()
        assert worker.note("check", "Finished review", op="report") == new
        connection.rollback()
        assert writer.receipt("proofs", "report") == new


def test_checkpoint_survives_backup_and_native_transfer_with_new_physical_event_ids(tmp_path):
    source_path = tmp_path / "initial.db"
    copied_path = tmp_path / "copy.db"
    with closing(Store(source_path)) as source, closing(Store(":memory:")) as restored:
        work = Tasks(source, "print", actor="proofreader")
        work.add("Proof 🌱", id="proof", description="☐\x00🌱" * 11000)
        anchor = work.orientation()["activity_checkpoint"]
        work.note("proof", "First change")
        work.edit("proof", title="Final proof")
        Tasks(restored, "other").add("Another workspace", id="other")
        restored.import_snapshot(
            "print", source.export("print"), operation_id="restore", expected_version=0
        )
        with closing(sqlite3.connect(copied_path)) as dest:
            source.db.backup(dest)
        with closing(Store(copied_path)) as copied:
            for instance in (source, restored, copied):
                page = instance.activity("print", after=anchor, limit=1)
                assert [row["seq"] for row in page["events"]] == [2]
                assert page["cursor"] == 2 and page["head"] == 3 and page["checkpoint"] != anchor
                last = instance.activity("print", after=page["checkpoint"])
                assert [row["seq"] for row in last["events"]] == [3]
                assert last["checkpoint"] == work.orientation()["activity_checkpoint"]
                assert instance.activity("print", after=last["checkpoint"])["events"] == []


def test_checkpoint_detects_changed_or_missing_boundary_and_workspace_but_not_imported_state():
    with closing(Store(":memory:")) as source, closing(Store(":memory:")) as replaced:
        tasks = Tasks(source, "print")
        zero = tasks.orientation()["activity_checkpoint"]
        assert source.activity("print", after=zero)["cursor"] == 0
        with pytest.raises(DomainError, match="checkpoint"):
            source.activity("web", after=zero)
        tasks.add("First proof", id="first")
        anchor = tasks.orientation()["activity_checkpoint"]
        tasks.add("Second proof", id="second")
        snapshot = source.export("print")
        event = snapshot["events"][0]
        # A replacement installation can preserve operation ID, actor and time
        # while restoring another version of the boundary event's recorded data.
        event["after_data"]["title"] = "Changed evidence"
        replaced.import_snapshot("print", snapshot, operation_id="restore", expected_version=0)
        with pytest.raises(DomainError, match="checkpoint"):
            replaced.activity("print", after=anchor)
        assert replaced.activity("print", after=1)["events"][0]["id"] == "second"
        with closing(Store(":memory:")) as sparse:
            snapshot["events"] = snapshot["events"][1:]
            sparse.import_snapshot("print", snapshot, operation_id="sparse", expected_version=0)
            with pytest.raises(DomainError, match="checkpoint"):
                sparse.activity("print", after=anchor)
            assert sparse.activity("print", after=zero)["events"][0]["seq"] == 2
            boundary = sparse.orientation("print")["activity_checkpoint"]
            sparse.import_snapshot(
                "print",
                {"schema_version": 1, "events": [event]},
                operation_id="backfill",
                expected_version=0,
            )
            assert sparse.activity("print", after=boundary)["events"] == []
        tasks.add("Imported fact", kind="memory", id="memory")
        third = source.get("print", "memory")
        before = replaced.orientation("print")
        replaced.import_snapshot(
            "print",
            {"schema_version": 1, "entities": [third]},
            operation_id="entity-only",
            expected_version=0,
        )
        after = replaced.orientation("print")
        assert after["activity_checkpoint"] == before["activity_checkpoint"]
        assert after["memories"] != before["memories"]


def test_checkpoint_cli_wire_and_authenticated_workspace(tmp_path, capsys):
    import httpx
    from fastapi.testclient import TestClient

    from daymark.api import create_app
    from daymark.cli import main
    from daymark.client import Client, ClientError
    from daymark.mcp import call

    path = tmp_path / "proofs.db"
    with closing(Store(path)) as store:
        tasks = Tasks(store, "print", actor="worker")
        tasks.add("Proof", id="proof")
        Tasks(store, "web").add("Page", id="page")
        token = tasks.orientation()["activity_checkpoint"]
        tasks.note("proof", "New evidence")
        app = create_app(
            store, {ws: {"workspace": ws, "principal": "worker"} for ws in ("print", "web")}
        )
        with TestClient(app) as http:

            def read(key, **params):
                return http.get(
                    "/v1/activity",
                    params={"after": token, **params},
                    headers={"Authorization": f"Bearer {key}"},
                )

            assert read("print").json() == tasks.activity(after=token)
            assert read("web", workspace="print").status_code == 409
            assert read("print", after=token + "x").status_code == 422
        assert "New evidence" in call(tasks, "daymark_view", {"view": "activity", "after": token})
        assert (
            main(
                ["--db", str(path), "--workspace", "print", "activity", "--after", token, "--json"]
            )
            == 0
        )
        assert token != json.loads(capsys.readouterr().out)["checkpoint"]

    calls = []

    def old_service(request):
        calls.append(request.url.params["after"])
        return httpx.Response(422)

    with (
        Client(
            "https://sample.invalid", "sample", transport=httpx.MockTransport(old_service)
        ) as client,
        pytest.raises(ClientError) as rejected,
    ):
        client.activity(after=token)
    assert rejected.value.code == "invalid" and calls == [token]


@pytest.mark.parametrize("encoding", ["UTF-16le", "UTF-16be"])
def test_portable_checkpoints_and_bounded_titles_on_caller_database_encoding(encoding):
    with closing(sqlite3.connect(":memory:")) as conn, closing(Store(":memory:")) as restored:
        conn.execute(f"PRAGMA encoding='{encoding}'")
        store = Store(conn)
        tasks = Tasks(store, "print")
        title = "🌱\x00☐" * 200
        tasks.add(title, id="proof", note=title, description=title * 100)
        tasks.add(title, kind="memory", id="memory")
        tasks.note("proof", title)
        before = tasks.orientation()
        assert before["ready"][0]["title"] == before["memories"][0]["title"] == title[:160] + "…"
        restored.import_snapshot(
            "print", store.export("print"), operation_id="restore", expected_version=0
        )
        assert tasks.brief("proof") == Tasks(restored, "print").brief("proof")
        assert restored.activity("print", after=before["activity_checkpoint"])["events"] == []


def test_pickup_commands_retain_database_workspace_and_exact_cursor(tmp_path, capsys):
    import shlex

    from daymark.cli import main
    from daymark.mcp import call

    path = str(tmp_path / "sample file.db")
    workspace = "print and proof"
    with closing(Store(path)) as store:
        tasks = Tasks(store, workspace, actor="night shift")
        for id in ["-a", "-b", "-c", "-d", "-e's sample", "final"]:
            tasks.add(id, id=id, kind="memory")
        assert main(["--db", path, "--workspace", workspace, "prime", "--brief"]) == 0
        lines = capsys.readouterr().out.splitlines()
        for label, expected in [
            ("More facts: ", "final"),
            ("Changes after this snapshot: ", "New evidence"),
        ]:
            argv = shlex.split(next(line.split(label)[1] for line in lines if label in line))
            assert argv[0] == "daymark"
            if expected == "New evidence":
                tasks.note("final", expected)
            assert main([*argv[1:], "--json"]) == 0
            assert expected in capsys.readouterr().out

        mcp_lines = call(tasks, "daymark_prime", {"brief": True}).splitlines()
        for label in ["More facts: ", "Changes after this snapshot: "]:
            name, args = next(line.split(label)[1] for line in mcp_lines if label in line).split(
                " ", 1
            )
            assert name == "daymark_view"
            result = call(tasks, name, json.loads(args))
            assert "final" in result if label.startswith("More") else "No activity." in result
