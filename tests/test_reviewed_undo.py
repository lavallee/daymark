"""Undo can only reverse the event that the worker actually reviewed."""

from contextlib import closing

import pytest
from fastapi.testclient import TestClient

from daymark import DomainError, Store, Tasks
from daymark.api import create_app
from daymark.client import Client, ClientError
from daymark.mcp import handle


@pytest.fixture(params=["local", "http"])
def context(request):
    with closing(Store(":memory:")) as store:
        if request.param == "local":
            yield store, Tasks(store, "lab", actor="worker")
        else:
            auth = {"sample-token": {"workspace": "lab", "principal": "worker"}}
            with TestClient(create_app(store, auth)) as app, Client(
                "http://sample.invalid", "sample-token", transport=app._transport
            ) as client:
                yield store, Tasks(client, actor="display-label")


def test_undo_refuses_instead_of_rebasing_onto_an_intervening_edit(context, monkeypatch):
    store, tasks = context
    tasks.add("Original title", id="sample", priority=3)
    tasks.edit("sample", title="Revised title")
    execute = store.execute

    def interleave(workspace, operation_id, action, payload, expected_version=None, actor=None):
        if action == "undo":
            monkeypatch.setattr(store, "execute", execute)
            execute(workspace, "concurrent-priority", "update", {"id": "sample", "priority": 1},
                    expected_version=expected_version, actor="other")
        return execute(workspace, operation_id, action, payload, expected_version, actor)

    monkeypatch.setattr(store, "execute", interleave)
    with pytest.raises((DomainError, ClientError)) as error:
        tasks.undo("sample", op="undo-title")
    assert error.value.code == "conflict"
    row = tasks.get("sample")
    assert (row["title"], row["priority"], row["version"]) == ("Revised title", 1, 3)
    assert store.receipt("lab", "undo-title") is None


def test_undo_exact_review_and_replay_are_bound_to_the_current_revision(context):
    store, tasks = context
    tasks.add("Original title", id="sample")
    tasks.edit("sample", title="Revised title")
    token = tasks.show("sample")["review_token"]

    def undo(op, review):
        return handle(tasks, {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {
            "name": "daymark_act", "arguments": {
                "action": "undo", "id": "sample", "op": op, "expected_version": 2, "review_token": review,
            },
        }})["result"]

    assert undo("wrong-review", "0" * 32)["structuredContent"]["error"]["code"] == "conflict"
    receipt = undo("undo-title", token)
    assert receipt["structuredContent"]["title"] == "Original title"
    tasks.edit("sample", priority=1)
    assert undo("undo-title", token) == receipt
    assert undo("new-undo", token)["structuredContent"]["error"]["code"] == "conflict"
    assert tasks.get("sample")["priority"] == 1 and store.head("lab") == 4
