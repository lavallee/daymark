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

        stamp = tasks.orientation()["activity_checkpoint"]
        tasks.note("final", "Review starting")
        tasks.note("final", "Review finished")
        assert (
            main(
                [
                    "--db",
                    path,
                    "--workspace",
                    workspace,
                    "activity",
                    "--after",
                    stamp,
                    "--limit",
                    "1",
                ]
            )
            == 0
        )
        follow = shlex.split(capsys.readouterr().out.split("More changes: ")[1].strip())
        assert main([*follow[1:], "--json"]) == 0
        assert json.loads(capsys.readouterr().out)["events"][0]["note"]["text"] == "Review finished"
        arguments = {"view": "activity", "after": stamp, "limit": 1}
        more = call(tasks, "daymark_view", arguments).split("More changes: ")[1]
        name, rest = more.split(" ", 1)
        assert name == "daymark_view" and json.loads(rest)["limit"] == 1
        assert "Review finished" in call(tasks, name, json.loads(rest))

        mcp_lines = call(tasks, "daymark_prime", {"brief": True}).splitlines()
        for label in ["More facts: ", "Changes after this snapshot: "]:
            name, args = next(line.split(label)[1] for line in mcp_lines if label in line).split(
                " ", 1
            )
            assert name == "daymark_view"
            result = call(tasks, name, json.loads(args))
            assert "final" in result if label.startswith("More") else "No activity." in result


def test_review_token_binds_recorded_content_across_changed_native_restore_and_http():
    from fastapi.testclient import TestClient

    from daymark.api import create_app
    from daymark.mcp import call

    with closing(Store(":memory:")) as source, closing(Store(":memory:")) as restored:
        original = Tasks(source, "print")
        original.propose("Approve first proof", id="cover", op="capture")
        before = original.brief("cover")["review_token"]
        snapshot = source.export("print")
        snapshot["operations"] = []
        for record in (snapshot["entities"][0], snapshot["events"][0]["after_data"]):
            record["title"] = "Approve alternate proof"
        restored.import_snapshot("print", snapshot, operation_id="restore", expected_version=0)
        worker = Tasks(restored, "print", actor="reviewer")
        current = worker.show("cover")["review_token"]
        assert current != before
        with pytest.raises(DomainError) as refusal:
            call(
                worker, "daymark_act", {"action": "dismiss", "id": "cover", "review_token": before}
            )
        assert (
            refusal.value.code == "conflict"
            and worker.show("cover")["entity"]["status"] == "proposed"
        )
        with TestClient(
            create_app(restored, {"sample": {"workspace": "print", "principal": "reviewer"}})
        ) as http:
            headers = {"Authorization": "Bearer sample"}
            body = {
                "action": "accept",
                "expected_version": 1,
                "operation_id": "decision",
                "payload": {"id": "cover", "review_token": before},
            }
            assert http.post("/v1/operations", headers=headers, json=body).status_code == 409
            body["payload"]["review_token"] = current
            accepted = http.post("/v1/operations", headers=headers, json=body)
            assert accepted.status_code == 200
            assert (
                accepted.json()["status"] == "open"
                and accepted.json()["title"] == "Approve alternate proof"
            )
            assert http.post("/v1/operations", headers=headers, json=body).json() == accepted.json()


def test_old_metadata_only_review_requires_reread_but_committed_receipt_still_replays(monkeypatch):
    import hashlib

    from daymark.store import canonical

    with closing(Store(":memory:")) as store, closing(Store(":memory:")) as restored:
        tasks = Tasks(store, "print", actor="reviewer")
        tasks.propose("Review proof", id="proof", op="capture")
        event = store.events("print")[0]
        evidence = [
            "daymark-review-v1",
            "print",
            "proof",
            event["seq"],
            event["operation_id"],
            event["created_at"],
            1,
        ]
        old_token = hashlib.sha256(canonical(evidence).encode()).hexdigest()[:32]
        with pytest.raises(DomainError) as stale:
            tasks.accept("proof", review_token=old_token, op="decision")
        assert stale.value.code == "conflict" and store.receipt("print", "decision") is None
        # Represent a write already accepted by the earlier development implementation.
        with monkeypatch.context() as old:
            old.setattr(store, "_review_token", lambda *args: old_token)
            result = tasks.accept("proof", review_token=old_token, op="decision")
        restored.import_snapshot(
            "print", store.export("print"), operation_id="restore", expected_version=0
        )
        replay = Tasks(restored, "print", actor="reviewer")
        assert replay.accept("proof", review_token=old_token, op="decision") == result
        assert restored.head("print") == store.head("print")


def test_late_native_history_merge_preserves_show_order_and_undo_target():
    with closing(Store(":memory:")) as source, closing(Store(":memory:")) as restored:
        work = Tasks(source, "print")
        work.add("First proof", id="cover", op="capture")
        work.edit("cover", title="Second proof", op="revise")
        work.edit("cover", title="Third proof", op="latest")
        snapshot = source.export("print")
        earlier, last = snapshot["events"][:-1], snapshot["events"][-1:]
        restored.import_snapshot(
            "print", {**snapshot, "events": last}, operation_id="initial", expected_version=0
        )
        restored.import_snapshot(
            "print",
            {"schema_version": 1, "events": earlier},
            operation_id="backfill",
            expected_version=0,
        )
        recovered = Tasks(restored, "print")
        assert recovered.show("cover")["history"] == work.show("cover")["history"]
        assert recovered.brief("cover")["review_token"] == work.brief("cover")["review_token"]
        assert (
            recovered.undo("cover", op="undo")["title"]
            == work.undo("cover")["title"]
            == "Second proof"
        )
        assert restored.head("print") == 4


def test_undo_refuses_sparse_or_different_current_state_without_writes():
    with closing(Store(":memory:")) as source:
        work = Tasks(source, "print")
        work.add("Original", id="cover")
        work.edit("cover", title="Second")
        work.edit("cover", title="Third")
        snapshot = source.export("print")
        for events_to_restore in [0, 1, 2, 3]:
            with closing(Store(":memory:")) as restored:
                history = snapshot["events"][:events_to_restore]
                materialized = {**snapshot["entities"][0], "title": "Current independent terms"}
                restored.import_snapshot(
                    "print",
                    {**snapshot, "events": history, "entities": [materialized]},
                    operation_id="restore",
                    expected_version=0,
                )
                before = restored.export("print")
                with pytest.raises(DomainError) as refused:
                    Tasks(restored, "print").undo("cover", op="restore-previous")
                assert refused.value.code == "conflict"
                assert restored.export("print") == before


def test_undo_preserves_legacy_defaults_and_existing_deletion_receipt():
    with closing(Store(":memory:")) as source, closing(Store(":memory:")) as restored:
        work = Tasks(source, "print")
        work.add("Original", id="cover")
        work.edit("cover", title="Revised")
        snapshot = source.export("print")
        for event in snapshot["events"]:
            for field in ("before_data", "after_data"):
                if event[field]:
                    del event[field]["focus"]  # Missing in an earlier entity representation.
        restored.import_snapshot("print", snapshot, operation_id="restore", expected_version=0)
        recovered = Tasks(restored, "print")
        assert recovered.undo("cover", op="undo")["title"] == "Original"
        deleted = restored.execute("print", "delete", "delete", {"id": "cover"}, 3)
        again = restored.execute(
            "print", "undo-delete", "undo", {"id": "cover"}, deleted["version"]
        )
        assert again["title"] == "Original"
        recovered.edit("cover", title="Current")
        assert (
            restored.execute("print", "undo-delete", "undo", {"id": "cover"}, deleted["version"])
            == again
        )


def test_undo_of_imported_delete_cannot_replace_a_live_record_at_same_version():
    with closing(Store(":memory:")) as source, closing(Store(":memory:")) as restored:
        work = Tasks(source, "print")
        work.add("Original", id="cover")
        tombstone = source.execute("print", "delete", "delete", {"id": "cover"}, 1)
        history = source.export("print")
        work.add("Replacement", id="cover")
        work.edit("cover", title="Revised replacement")
        fresh = work.get("cover")
        assert fresh["version"] == tombstone["version"]
        restored.import_snapshot(
            "print", {**history, "entities": [fresh]}, operation_id="restore", expected_version=0
        )
        with pytest.raises(DomainError) as refused:
            restored.execute("print", "undo-import", "undo", {"id": "cover"}, fresh["version"])
        assert refused.value.code == "conflict" and restored.get("print", "cover") == fresh
        assert restored.receipt("print", "undo-import") is None


def test_recurring_successor_uses_latest_native_creation_after_history_backfill():
    with closing(Store(":memory:")) as source, closing(Store(":memory:")) as restored:
        work = Tasks(source, "print")
        work.add("Original series", id="proof", plan="2030-01-01", every="day")
        source.execute("print", "delete", "delete", {"id": "proof"}, 1)
        work.add("Revised series", id="proof", plan="2030-02-01", every="day")
        snapshot = source.export("print")
        restored.import_snapshot(
            "print",
            {**snapshot, "events": snapshot["events"][-1:]},
            operation_id="restore",
            expected_version=0,
        )
        restored.import_snapshot(
            "print",
            {"schema_version": 1, "events": snapshot["events"][:-1]},
            operation_id="older",
            expected_version=0,
        )
        for store in (source, restored):
            Tasks(store, "print").done("proof")
            assert store.get("print", "proof.1")["planned"]["date"] == "2030-02-02"


@pytest.mark.parametrize(
    ("release", "workspace", "id"), [("0.3.0", "home", "passport"), ("0.4.0", "fixture", "t1")]
)
def test_real_release_fixtures_keep_undoable_history_after_schema_upgrade(
    tmp_path, release, workspace, id
):
    from pathlib import Path

    path = tmp_path / "previous.db"
    fixture = Path(__file__).parent / "fixtures" / f"daymark-{release}.sql"
    with closing(sqlite3.connect(path)) as db:
        db.executescript(fixture.read_text())
    with closing(Store(path)) as store:
        previous = next(
            event["before_data"]
            for event in reversed(store.events(workspace))
            if event["entity_id"] == id
        )
        work = Tasks(store, workspace)
        assert work.brief(id)["review_token"]
        changed = work.undo(id, op="restore-prior")
        assert changed["status"] == previous["status"] and changed["title"] == previous["title"]
        assert work.undo(id, op="restore-prior") == changed


def test_native_cursor_integer_boundary_preserves_receipts_and_rejects_overflow_atomically():
    from fastapi.testclient import TestClient

    from daymark.api import create_app

    upper = 2**63 - 1
    with closing(Store(":memory:")) as src, closing(Store(":memory:")) as dest:
        tasks = Tasks(src, "print")
        tasks.add("Boundary proof", id="proof", op="capture")
        snapshot = src.export("print")
        snapshot["events"][0]["seq"] = upper + 1
        with TestClient(
            create_app(dest, {"reader": {"workspace": "print", "principal": "agent"}})
        ) as http:
            response = http.post(
                "/v1/import",
                json={"snapshot": snapshot, "operation_id": "restore", "expected_version": 0},
                headers={"Authorization": "Bearer reader"},
            )
            assert response.status_code == 422 and dest.head("print") == 0
        snapshot["events"][0]["seq"] = upper - 1
        dest.import_snapshot("print", snapshot, operation_id="restore", expected_version=0)
        work = Tasks(dest, "print")
        stamp = work.orientation()["activity_checkpoint"]
        work.edit("proof", title="Last event", op="last")
        assert dest.head("print") == upper and type(dest.head("print")) is int
        last = work.activity(after=stamp)
        assert last["cursor"] == upper and work.activity(after=last["checkpoint"])["events"] == []
        assert dest.export("print")["events"][-1]["seq"] == upper
        with pytest.raises(DomainError) as conflict:
            work.note("proof", "Beyond boundary", op="excess")
        assert conflict.value.code == "conflict" and work.get("proof")["notes"] == []
        assert dest.receipt("print", "excess") is None
        assert work.edit("proof", title="Last event", op="last")["version"] == 2
        assert Tasks(dest, "other").add("Unrelated", id="proof")["version"] == 1
        with pytest.raises(DomainError, match="Invalid event cursor"):
            dest.events("print", after=upper + 1)
