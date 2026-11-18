"""A plain-language retry must describe the same kernel operation."""

import sqlite3
from contextlib import ExitStack
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from daymark import DomainError, Store, Tasks
from daymark.api import create_app
from daymark.cli import main
from daymark.client import Client, ClientError
from daymark.mcp import handle


@pytest.fixture(params=["store", "http"])
def workspace(request, tmp_path):
    with ExitStack() as stack:
        store = Store(tmp_path / "samples.sqlite3")
        stack.callback(store.close)
        if request.param == "store":
            engine = store
        else:
            http = stack.enter_context(
                TestClient(
                    create_app(
                        store,
                        {
                            "sample-token": {"workspace": "sample", "principal": "worker"},
                        },
                    )
                )
            )

            def forward(request):
                response = http.request(
                    request.method,
                    request.url.raw_path.decode(),
                    headers=request.headers,
                    content=request.content,
                )
                return httpx.Response(response.status_code, content=response.content)

            engine = stack.enter_context(
                Client(
                    "http://daymark.invalid", "sample-token", transport=httpx.MockTransport(forward)
                )
            )
        yield (
            Tasks(
                engine,
                "sample",
                actor="worker",
                timezone="UTC",
                now=datetime(2030, 3, 8, 17, tzinfo=UTC),
            ),
            store,
        )


def refused(call, *args, **kwargs):
    with pytest.raises((DomainError, ClientError)) as failure:
        call(*args, **kwargs)
    assert failure.value.code == "conflict"
    if isinstance(failure.value, ClientError):
        assert failure.value.status_code == 409


def test_captured_request_is_checked_across_verbs_targets_and_fields(workspace):
    tasks, store = workspace
    first = tasks.add("First sample", id="a", op="one", due="tomorrow", note="Mark it")
    tasks.add("Second sample", id="b")
    refused(tasks.done, "b", op="one")
    refused(tasks.cancel, "a", op="one")
    refused(tasks.add, "Changed title", id="a", op="one", due="tomorrow", note="Mark it")
    refused(tasks.add, "First sample", id="a", op="one", due="tomorrow", note="Wrong note")
    refused(tasks.add, "First sample", id="b", op="one", due="tomorrow", note="Mark it")
    refused(tasks.remember, "First sample", op="one")
    tasks._now = datetime(2030, 3, 10, 19, tzinfo=UTC)
    assert tasks.add("First sample", id="a", op="one", due="tomorrow", note="Mark it") == first
    assert len(store.events("sample")) == 2


def test_compound_move_and_handoff_validate_every_part(workspace):
    tasks, _ = workspace
    tasks.project("Samples", id="p", preset="kanban")
    tasks.add("Sample A", id="a", project="p")
    tasks.add("Sample B", id="b", project="p")
    # Both neighbours must share a stage. Moving to the front has no neighbour constraint.
    first_move = tasks.move("a", stage="doing", first=True, op="place-sample")
    assert tasks.move("a", stage="doing", first=True, op="place-sample") == first_move
    refused(tasks.move, "a", stage="doing", last=True, op="place-sample")
    tasks.claim("a")
    before = tasks.release("a", note="First reading checked", op="handoff")
    refused(tasks.release, "a", note="Different handoff", op="handoff")
    refused(tasks.release, "a", op="handoff")
    tasks.claim("a")
    assert tasks.release("a", note="First reading checked", op="handoff") == before
    assert tasks.get("a")["claimed_by"] == "worker"


def test_dependency_retry_uses_original_before_state(workspace):
    tasks, _ = workspace
    for id in ("a", "b", "c"):
        tasks.add("Sample " + id, id=id)
    tasks.block("a", "b")
    receipt = tasks.block("a", "c", op="link")
    tasks.unblock("a", "b")
    assert tasks.block("a", "c", op="link") == receipt
    refused(tasks.block, "a", "b", op="link")
    assert tasks.get("a")["depends_on"] == ["c"]


def test_competing_read_can_be_guarded_by_caller_version(workspace):
    tasks, _ = workspace
    obs = tasks.add("Review sample", id="s")
    tasks.note("s", "New context")
    refused(tasks.edit, "s", priority=3, expected_version=obs["version"], op="edit")
    assert tasks.get("s")["priority"] == 0
    version = tasks.get("s")["version"]
    result = tasks.edit("s", priority=3, expected_version=version, op="edit")
    tasks.note("s", "Another reader")
    assert tasks.edit("s", priority=3, expected_version=version, op="edit") == result
    refused(tasks.edit, "s", priority=3, expected_version=version + 1, op="edit")
    assert tasks.get("s")["version"] == version + 2


def test_nonentity_receipts_are_never_plain_verb_success(workspace):
    tasks, store = workspace
    tasks.add("Compare samples", id="s")
    assert store.batch(
        "sample",
        [
            {
                "operation_id": "batch-child",
                "action": "update",
                "payload": {"id": "s", "priority": 2},
                "expected_version": 1,
            }
        ],
        request_id="batch",
    )["committed"]
    refused(tasks.edit, "s", priority=2, op="batch")
    refused(tasks.add, "Compare samples", op="batch")
    assert store.receipt("sample", "batch")["committed"]


def test_mcp_and_cli_refuse_a_stale_edit(tmp_path, capsys):
    db = str(tmp_path / "sample.sqlite3")
    assert main(["--db", db, "add", "Compare samples", "--id", "s"]) == 0
    assert main(["--db", db, "edit", "s", "--priority", "1", "--if-version", "1"]) == 0
    assert main(["--db", db, "edit", "s", "--priority", "4", "--if-version", "1"]) == 4
    with ExitStack() as stack:
        store = Store(db)
        stack.callback(store.close)
        tasks = Tasks(store)
        reply = handle(
            tasks,
            {
                "jsonrpc": "2.0",
                "id": "edit",
                "method": "tools/call",
                "params": {
                    "name": "daymark_edit",
                    "arguments": {
                        "id": "s",
                        "priority": 4,
                        "expected_version": 1,
                    },
                },
            },
        )
        assert reply["result"]["isError"]
        assert "conflict" in reply["result"]["content"][0]["text"]
        assert tasks.get("s")["priority"] == 1


def test_identical_concurrent_capture_returns_one_committed_record(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    path = tmp_path / "sample.sqlite3"
    barrier = Barrier(2)

    class SimultaneousStore(Store):
        def execute(self, *args, **kwargs):
            if not getattr(self, "crossed", False):
                self.crossed = True
                barrier.wait(timeout=10)
            return super().execute(*args, **kwargs)

    def capture(_):
        store = SimultaneousStore(path)
        try:
            return Tasks(store, actor="worker").add("Record readings", note="Counted", op="same")
        finally:
            store.close()

    Store(path).close()
    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = pool.map(capture, range(2))
    assert first == second
    store = Store(path)
    try:
        assert len(store.events("default")) == 1
        assert len(first["notes"]) == 1
    finally:
        store.close()


def test_assessment_evidence_is_atomic_and_replayable_after_restore(workspace):
    tasks, store = workspace
    tasks.goal("Compare the samples", id="g")
    baseline = tasks.condition("Both samples labeled", id="condition", goal="g")
    with pytest.raises((DomainError, ClientError)):
        tasks.assess("condition", "met", evidence="x" * 100001, op="assessment")
    assert tasks.get("condition") == baseline
    done = tasks.assess("condition", "met", evidence="Labels checked", op="assessment")
    refused(tasks.assess, "condition", "met", evidence="Different evidence", op="assessment")
    refused(tasks.assess, "condition", "met", op="assessment")
    copy = Store(":memory:")
    try:
        copy.import_snapshot(
            "sample", store.export("sample"), operation_id="restore", expected_version=0
        )
        restored = Tasks(copy, "sample", actor="worker")
        assert (
            restored.assess("condition", "met", evidence="Labels checked", op="assessment") == done
        )
    finally:
        copy.close()


def test_replay_after_delete_preserves_original_date_and_actor(workspace):
    tasks, store = workspace
    original = tasks.add("Rotate samples", every="2 days", op="repeat", note="Marked")
    tasks._now = datetime(2030, 3, 26, 17, tzinfo=UTC)
    assert tasks.add("Rotate samples", every="2 days", op="repeat", note="Marked") == original
    refused(tasks.add, "Rotate samples", every="3 days", op="repeat", note="Marked")
    result = tasks.defer(original["id"], "tomorrow", op="defer")
    store.execute("sample", "delete", "delete", {"id": original["id"]}, expected_version=2)
    tasks._now = datetime(2030, 4, 1, 17, tzinfo=UTC)
    assert tasks.defer(original["id"], "tomorrow", op="defer") == result
    refused(Tasks(store, "sample", actor="colleague").defer, original["id"], "tomorrow", op="defer")


def test_changed_structured_date_cannot_borrow_a_relative_expression(workspace):
    tasks, _ = workspace
    before = tasks.add("Compare samples", due="tomorrow", op="date")
    changed = {**before["deadline"], "date": "2030-06-12"}
    refused(tasks.add, "Compare samples", due=changed, op="date")


def test_replay_checks_principal_established_by_http(tmp_path):
    store = Store(tmp_path / "principals.sqlite3")
    try:
        with TestClient(
            create_app(
                store,
                {
                    "token-one": {"workspace": "sample", "principal": "first"},
                    "token-two": {"workspace": "sample", "principal": "second"},
                },
            )
        ) as http:
            tasks = Tasks(store, "sample", actor="first")
            tasks.add("Compare samples", id="s", op="recorded")
            url = "/v1/receipt-context"
            params = {"operation_id": "recorded", "action": "create", "entity_id": "s"}
            assert (
                http.get(
                    url, params=params, headers={"Authorization": "Bearer token-one"}
                ).status_code
                == 200
            )
            assert (
                http.get(
                    url, params=params, headers={"Authorization": "Bearer token-two"}
                ).status_code
                == 409
            )
            assert http.get(url, params=params).status_code == 401
    finally:
        store.close()


def test_existing_0_3_capture_replays_with_its_original_defaults(tmp_path, capsys):
    path = tmp_path / "legacy.sqlite3"
    fixture = Path(__file__).parent / "fixtures" / "daymark-0.3.0.sql"
    db = sqlite3.connect(path)
    try:
        db.executescript(fixture.read_text())
    finally:
        db.close()
    store = Store(path)
    try:
        op = next(
            event["operation_id"]
            for event in store.events("home")
            if event["entity_id"] == "rake" and event["action"] == "create"
        )
        assert "advances" not in store.receipt("home", op)
        actor = next(
            event["actor"] for event in store.events("home") if event["operation_id"] == op
        )
        args = [
            "--db",
            str(path),
            "--workspace",
            "home",
            "--as",
            actor,
            "--tz",
            "Europe/London",
            "add",
            "Rake leaves",
            "--id",
            "rake",
            "--project",
            "garden",
            "--note",
            "Use the green rake",
            "--op",
            op,
        ]
        assert main(args) == 0
        assert "rake  Rake leaves" in capsys.readouterr().out
        assert store.get("home", "rake")["status"] == "cancelled"
    finally:
        store.close()
