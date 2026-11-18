"""Restoring a record returns it to its own lifecycle state."""

import json
from contextlib import closing

import httpx
import pytest
from fastapi.testclient import TestClient

from daymark import DomainError, Store, Tasks
from daymark.api import create_app
from daymark.cli import main
from daymark.client import Client
from daymark.mcp import call


def test_restore_wait_and_blocker_without_reviving_a_lease_or_cancelled_reminder():
    with closing(Store(":memory:")) as store:
        tasks = Tasks(store, "print", actor="worker", timezone="UTC")
        tasks.add("Reviewer", kind="person", id="reviewer")
        tasks.add("Check proof", id="check", source="proofs:edition:07", note="First pass")
        tasks.add("Print proof", id="print", after="check")
        tasks.focus("check")
        tasks.claim("check")
        tasks.wait("check", "a signed copy")
        reminder = tasks.remind("check", "2030-06-01 09:00", recipient="reviewer")
        assert tasks.get(reminder["id"])["reminder_state"] == "pending"
        archived = tasks.archive("check")
        assert tasks.get(reminder["id"])["reminder_state"] == "cancelled"
        assert "print" in {item["id"] for item in tasks.ready()}
        reviewed = tasks.brief(source="proofs:edition:07")
        restored = tasks.unarchive(
            reviewed["id"], review_token=reviewed["review_token"], op="restore-check"
        )
        assert restored["status"] == "waiting" and restored["waiting_on"] == "a signed copy"
        assert restored["claimed_by"] is None and restored["focus"] is None
        assert {
            k: v for k, v in archived.items() if k not in {"archived", "updated_at", "version"}
        } == {k: v for k, v in restored.items() if k not in {"archived", "updated_at", "version"}}
        assert tasks.ready() == [] and tasks.show("print")["blocked_by"] == ["check"]
        assert tasks.get(reminder["id"])["reminder_state"] == "cancelled"
        assert store.events("print")[-1]["action"] == "update"
        head = store.head("print")
        with pytest.raises(DomainError, match="already visible"):
            tasks.unarchive("check", op="second-restore")
        assert store.head("print") == head and store.receipt("print", "second-restore") is None
        tasks.archive("check")
        assert (
            tasks.unarchive("check", review_token=reviewed["review_token"], op="restore-check")
            == restored
        )
        assert tasks.get("check")["archived"]


@pytest.mark.parametrize("end", ["done", "cancel"])
def test_restore_finished_task_keeps_its_status_and_completion_time(end):
    with closing(Store(":memory:")) as store:
        tasks = Tasks(store)
        tasks.add("Check edition", id="proof")
        closed = getattr(tasks, end)("proof")
        tasks.archive("proof")
        restored = tasks.unarchive("proof")
        assert restored["archived"] is False and restored["status"] == closed["status"]
        assert restored["completed_at"] == closed["completed_at"] and tasks.ready() == []


def test_saved_fact_and_proposal_have_distinct_recovery_and_decision_paths():
    with closing(Store(":memory:")) as store:
        tasks = Tasks(store, "print", actor="preparer")
        tasks.remember("Use short labels", source="notes:choice:07")
        fact = tasks.brief(source="notes:choice:07")
        tasks.forget(fact["id"])
        assert not tasks.memories()
        tasks.unarchive(fact["id"])
        assert [row["id"] for row in tasks.memories()] == [fact["id"]]
        tasks.propose("Proof labels", id="cover")
        hidden = tasks.archive("cover")
        store.set_policy("print", deciders=["reviewer"])
        with pytest.raises(DomainError) as disallowed:
            tasks.accept("cover")
        assert disallowed.value.code == "forbidden"
        reviewer = Tasks(store, "print", actor="reviewer")
        with pytest.raises(DomainError, match="Unarchive") as conflict:
            reviewer.accept("cover", op="decision")
        assert conflict.value.code == "conflict" and store.receipt("print", "decision") is None
        assert (
            reviewer.unarchive("cover", expected_version=hidden["version"])["status"] == "proposed"
        )
        assert reviewer.accept("cover", op="decision")["status"] == "open"
        reviewer.propose("Second proof", id="other")
        reviewer.archive("other")
        dismissed = reviewer.dismiss("other")
        assert dismissed["archived"] and dismissed["status"] == "cancelled"


def test_source_review_cannot_restore_a_replacement_with_the_same_id_or_title():
    with closing(Store(":memory:")) as store:
        work = Tasks(store, "print")
        for id, source in [("page/1", "proofs:page:01"), ("page/2", "proofs:page:02")]:
            work.propose("Proof", id=id, source=source)
            work.archive(id)
        old = work.brief(source="proofs:page:01")
        store.execute("print", "delete-old", "delete", {"id": old["id"]}, old["version"])
        work.propose("Proof", id=old["id"], source="proofs:page:01")
        work.archive(old["id"])
        with pytest.raises(DomainError) as refused:
            work.unarchive(
                old["id"],
                expected_version=old["version"],
                review_token=old["review_token"],
                op="restore",
            )
        assert refused.value.code == "conflict"
        fresh = work.brief(source="proofs:page:01")
        assert (
            work.unarchive(fresh["id"], review_token=fresh["review_token"], op="restore")["id"]
            == "page/1"
        )
        assert work.get("page/2")["archived"]


def test_cli_mcp_and_http_restore_a_reviewed_record(tmp_path, capsys):
    path = tmp_path / "archive.db"
    with closing(Store(path)) as store:
        work = Tasks(store, "print", actor="reviewer")
        work.propose("Proof", id="cover", source="proofs:cover:07")
        work.archive("cover")
        base = ["--db", str(path), "--workspace", "print", "--as", "reviewer"]
        assert main([*base, "accept", "cover"]) == 4
        assert "Unarchive" in capsys.readouterr().err
        assert main([*base, "show", "--source", "proofs:cover:07", "--brief", "--json"]) == 0
        before = json.loads(capsys.readouterr().out)
        command = [
            *base,
            "unarchive",
            before["id"],
            "--if-review",
            before["review_token"],
            "--op",
            "restore",
            "--json",
        ]
        assert main(command) == 0
        receipt = json.loads(capsys.readouterr().out)
        assert receipt["status"] == "proposed"
        assert main(command) == 0 and json.loads(capsys.readouterr().out) == receipt

        work.archive("cover")
        full = work.brief("cover")
        assert "proposed" in call(
            work,
            "daymark_act",
            {
                "action": "unarchive",
                "id": "cover",
                "expected_version": full["version"],
                "review_token": full["review_token"],
                "op": "mcp-restore",
            },
        )
        work.archive("cover")
        with TestClient(
            create_app(store, {"sample": {"workspace": "print", "principal": "reviewer"}})
        ) as server:

            def forward(request):
                response = server.request(
                    request.method,
                    request.url.raw_path.decode(),
                    headers=request.headers,
                    content=request.content,
                )
                return httpx.Response(response.status_code, content=response.content)

            with Client(
                "https://sample.invalid", "sample", transport=httpx.MockTransport(forward)
            ) as client:
                remote = Tasks(client)
                review = remote.brief(source="proofs:cover:07")
                boundary = server.post(
                    "/v1/operations",
                    headers={"Authorization": "Bearer sample"},
                    json={
                        "operation_id": "raw-decision",
                        "action": "accept",
                        "payload": {"id": "cover"},
                        "expected_version": review["version"],
                    },
                )
                assert boundary.status_code == 409
                args = {"op": "remote-restore", "review_token": review["review_token"]}
                assert remote.unarchive(review["id"], **args) == remote.unarchive(
                    review["id"], **args
                )
                assert remote.accept("cover", op="signed")["status"] == "open"


def test_old_service_cannot_accept_a_concurrent_archive():
    with closing(Store(":memory:")) as store:
        work = Tasks(store, "print")
        original = work.propose("Check", id="cover")
        archived = work.archive("cover")
    writes = []

    def old(request):
        if request.method == "GET":
            return httpx.Response(200, json=archived if writes else original)
        writes.append(json.loads(request.content))
        assert len(writes) == 1 and writes[0]["expected_version"] == original["version"]
        return httpx.Response(409)

    with (
        Client("https://sample.invalid", "sample", transport=httpx.MockTransport(old)) as service,
        pytest.raises(DomainError, match="Unarchive"),
    ):
        Tasks(service).accept("cover")
    assert len(writes) == 1


@pytest.mark.parametrize("same_op", [True, False])
def test_two_sessions_restore_the_same_revision_once(tmp_path, same_op):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    path = tmp_path / "shared.sqlite"
    with closing(Store(path)) as source:
        work = Tasks(source, "print", actor="operator")
        work.propose("Proof", id="cover")
        hidden = work.archive("cover")
        both_read = Barrier(2)

        def restore(index):
            with closing(Store(path)) as connection:
                worker = Tasks(connection, "print", actor="operator")
                get = connection.get

                def first_read(workspace, id):
                    observed = get(workspace, id)
                    connection.get = get
                    both_read.wait(timeout=10)
                    return observed

                connection.get = first_read
                try:
                    return worker.unarchive(
                        "cover", op="recover" if same_op else f"recover-{index}"
                    )
                except DomainError as error:
                    assert error.code == "conflict" and "already visible" in str(error)
                    return None

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(restore, range(2)))
        assert sum(r is not None for r in results) == (2 if same_op else 1)
        assert all(r["version"] == hidden["version"] + 1 for r in results if r)
        assert len(source.events("print", after=2)) == 1
