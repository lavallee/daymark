"""A plain-language retry must describe the same kernel operation."""

from contextlib import ExitStack
from datetime import UTC, datetime

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
