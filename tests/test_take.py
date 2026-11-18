"""A worker can atomically take one delegated item and safely replay the result."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from fastapi.testclient import TestClient

from daymark import DomainError, Store, Tasks
from daymark.api import create_app
from daymark.cli import main
from daymark.mcp import call


def test_worker_pool_claims_distinct_tasks_and_replays_after_release(tmp_path):
    path = tmp_path / "queue.db"
    seed = Store(path)
    creator = Tasks(seed, "queue", actor="operator")
    count = 8
    ids = [f"sample-{i}" for i in range(count - 2)]
    for id in ids:
        creator.add("Review sample", id=id, move="agent")
    barrier = Barrier(count)

    def attempt(i):
        store = Store(path)
        try:
            worker = Tasks(store, "queue", actor=f"worker-{i}")
            barrier.wait(timeout=30)
            result = worker.take(op=f"take-{i}")
            assert worker.take(op=f"take-{i}") == result
            return result
        finally:
            store.close()

    try:
        with ThreadPoolExecutor(max_workers=count) as workers:
            results = list(workers.map(attempt, range(count)))
        assert sorted(x["id"] for x in results if x) == sorted(ids)
        assert len([x for x in results if x is None]) == 2
        assert len(creator.activity(after=len(ids))["events"]) == len(ids)
        for i, result in enumerate(results):
            if result:
                assert result["claimed_by"] == f"worker-{i}"
                Tasks(seed, "queue", actor=f"worker-{i}").release(result["id"])
        for i, result in enumerate(results):
            assert Tasks(seed, "queue", actor=f"worker-{i}").take(op=f"take-{i}") == result
        assert len(creator.ready()) == len(ids)
    finally:
        seed.close()


def test_take_uses_ready_rules_order_scope_and_explicit_delegation():
    store = Store(":memory:")
    try:
        tasks = Tasks(store, actor="worker")
        tasks.add("Human task", id="human", move="person")
        tasks.add("Undecided", id="unset")
        tasks.add("Decision", id="question", move="answer")
        tasks.add("Needs reply", id="waiting", move="agent", waiting_on="reply")
        tasks.add("Deferred", id="defer", move="agent", start="+7d")
        tasks.propose("Suggestion", id="proposal", move="agent")
        tasks.add("Folder", id="parent", move="agent")
        tasks.add("Child belongs to a person", id="child", parent="parent", move="person")
        tasks.add("Blocked", id="blocked", move="agent", after="human")
        tasks.add("Elsewhere", id="elsewhere", move="agent", scope="other")
        tasks.add("Standard", id="standard", move="agent", scope="proofs")
        tasks.add("Focused", id="focused", move="agent", scope="proofs")
        tasks.focus("focused")
        assert tasks.take(scope="proofs", minutes=2)["id"] == "focused"
        assert tasks.take(scope="proofs")["id"] == "standard"
        assert tasks.take(scope="proofs") is None
        assert tasks.take(scope="") is None
        assert tasks.take()["id"] == "elsewhere"
        assert tasks.take() is None
    finally:
        store.close()


def test_take_rejects_unlabelled_local_worker_and_changed_request():
    store = Store(":memory:")
    try:
        plain = Tasks(store)
        with pytest.raises(DomainError, match="distinct worker"):
            plain.take()
        # The existing explicit claim still allows the default actor.
        plain.add("Proof sample", id="sample")
        assert plain.claim("sample")["claimed_by"] == "agent"
        worker = Tasks(store, actor="worker")
        assert worker.take(op="pick") is None
        with pytest.raises(DomainError, match="acting worker"):
            store.execute(
                "default",
                "pick",
                "take",
                {"owner": "worker", "scope": None, "lease_seconds": 3600},
                expected_version=0,
                actor="different",
            )
        for options in ({"minutes": 3}, {"scope": "different"}):
            with pytest.raises(DomainError, match="different request"):
                worker.take(op="pick", **options)
        with pytest.raises(DomainError, match="different request"):
            Tasks(store, actor="second").take(op="pick")
        for options in (
            {"minutes": True},
            {"minutes": 0},
            {"minutes": 10081},
            {"scope": "bad scope"},
        ):
            with pytest.raises(DomainError):
                worker.take(**options)
    finally:
        store.close()


def test_take_in_failed_batch_rolls_back_selection_event_and_receipt():
    store = Store(":memory:")
    try:
        worker = Tasks(store, actor="worker")
        before = worker.add("Review sample", id="sample", move="agent")
        operations = [
            {
                "action": "take",
                "operation_id": "pick",
                "expected_version": 0,
                "payload": {"owner": "worker"},
            },
            {
                "action": "complete",
                "operation_id": "oops",
                "expected_version": 5,
                "payload": {"id": "sample"},
            },
        ]
        assert not store.batch("default", operations, actor="worker")["committed"]
        assert worker.get("sample") == before
        assert store.receipt("default", "pick") is None
        assert store.head("default") == 1
        assert worker.take(op="pick")["id"] == "sample"
    finally:
        store.close()


def test_take_uses_http_principal_and_validates_wire_shapes():
    store = Store(":memory:")
    try:
        tasks = Tasks(store, "test", actor="local")
        tasks.add("Proof sample", id="sample", move="agent")
        app = create_app(store, {"demo": {"workspace": "test", "principal": "authenticated"}})
        with TestClient(app) as client:
            body = {
                "action": "take",
                "operation_id": "x",
                "payload": {"owner": "ignored"},
                "expected_version": 0,
            }
            result = client.post(
                "/v1/operations", json=body, headers={"Authorization": "Bearer demo"}
            )
            assert (
                result.status_code == 200 and result.json()["task"]["claimed_by"] == "authenticated"
            )
        assert "No unclaimed" in call(tasks, "daymark_act", {"action": "take"})
        with pytest.raises(DomainError, match="does not take: id"):
            call(tasks, "daymark_act", {"action": "take", "id": "sample"})
        with pytest.raises(DomainError, match="missing argument id"):
            call(tasks, "daymark_act", {"action": "claim"})
    finally:
        store.close()


def test_cli_take_empty_queue_returns_json_null(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("DAYMARK_ACTOR", raising=False)
    path = str(tmp_path / "sample.sqlite")
    assert main(["--db", path, "take"]) == 5
    assert "--as NAME" in capsys.readouterr().err
    assert main(["--db", path, "--as", "worker", "take", "--json"]) == 0
    assert capsys.readouterr().out.strip() == "null"


def test_take_context_is_an_exact_delegation_filter_bound_to_the_operation():
    store = Store(":memory:")
    try:
        t = Tasks(store, actor="worker")
        t.add("Desk", id="desk", kind="context")
        t.add("Field", id="field", kind="context")
        t.add("Untargeted", id="anywhere", move="agent", priority=1)
        t.add("Field choice", id="field-work", move="agent", scope="project", contexts=["field"])
        t.add("Desk choice", id="desk-work", move="agent", scope="project", contexts=["desk"])
        t.add("Person decision", id="human", move="answer", scope="project", contexts=["desk"])
        assert t.take(context="desk", scope="project", op="pick")["id"] == "desk-work"
        with pytest.raises(DomainError) as changed:
            t.take(context="field", scope="project", op="pick")
        assert changed.value.code == "conflict"
        assert t.take(context="desk", scope="project") is None
        assert t.take(context="field", scope="project")["id"] == "field-work"
        with pytest.raises(DomainError, match="Context"):
            t.take(context="")
    finally:
        store.close()
