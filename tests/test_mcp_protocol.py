"""Client-visible protocol behavior over the same stdio entry point a harness uses."""

import json
import subprocess
import sys
from contextlib import closing

import pytest

from daymark import Store, Tasks
from daymark.mcp import handle


def request(method, **params):
    return {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}


def tool(name, **arguments):
    return request("tools/call", name=name, arguments=arguments)


@pytest.fixture
def tasks():
    with closing(Store(":memory:")) as store:
        yield Tasks(store, "sample", timezone="UTC")


@pytest.mark.parametrize(
    ("method", "params"),
    [
        ("initialize", {"protocolVersion": "2024-11-05"}),
        ("initialize", {"protocolVersion": "2025-06-18"}),
        ("initialize", {"protocolVersion": "2025-11-25"}),
    ],
)
def test_initialize_agrees_on_supported_versions(tasks, method, params):
    result = handle(tasks, request(method, **params))["result"]
    assert result["protocolVersion"] == params["protocolVersion"]


@pytest.mark.parametrize(
    ("message", "code"),
    [
        (None, -32600),
        ([], -32600),
        ({"id": 2, "method": "ping"}, -32600),
        ({"jsonrpc": "2.0", "id": True, "method": "ping"}, -32600),
        (request("initialize"), -32602),
        (request("tools/call", name="unknown"), -32602),
        (request("tools/call", name="daymark_prime", arguments=[]), -32602),
        (request("tools/call"), -32602),
    ],
)
def test_client_can_distinguish_protocol_failure(tasks, message, code):
    assert handle(tasks, message)["error"]["code"] == code


@pytest.mark.parametrize(
    ("name", "fields"),
    [
        ("daymark_add", {"title": "Compare samples", "proposed": "false"}),
        ("daymark_add", {"title": "Compare samples", "priority": True}),
        ("daymark_add", {"title": "Compare samples", "after": [42]}),
        ("daymark_add", {"title": "Compare samples", "priority": 5}),
        ("daymark_act", {"id": "sample", "action": "done", "until": "tomorrow"}),
        ("daymark_remember", {"text": "Use metric samples", "op": ""}),
        ("daymark_view", {"view": "projects", "scope": "sample"}),
    ],
)
def test_bad_tool_arguments_get_recoverable_errors_before_any_write(tasks, name, fields):
    tasks.add("Collect samples", id="sample")
    result = handle(tasks, tool(name, **fields))["result"]
    assert result["isError"]
    assert result["content"][0]["text"].startswith("error: invalid:")
    assert tasks.get("sample")["version"] == 1
    assert len(tasks.engine.events("sample")) == 1


def test_stdio_restart_retries_keep_one_write_and_return_original_entity(tmp_path):
    db = tmp_path / "samples.sqlite3"
    args = [
        sys.executable,
        "-m",
        "daymark.cli",
        "--db",
        str(db),
        "--workspace",
        "sample",
        "--as",
        "sampler",
        "--tz",
        "UTC",
        "mcp",
    ]
    hello = request("initialize", protocolVersion="future-revision")
    initialized = {"jsonrpc": "2.0", "method": "notifications/initialized"}

    def session(*messages):
        wire = [hello, initialized, *messages]
        proc = subprocess.run(
            args,
            input="".join(json.dumps(m) + "\n" for m in wire),
            check=True,
            capture_output=True,
            text=True,
            timeout=15,
        )
        handshake, *results = [json.loads(line)["result"] for line in proc.stdout.splitlines()]
        assert handshake["protocolVersion"] == "2025-11-25"
        assert len(results) == len(messages)
        return results

    prime, added = session(
        tool("daymark_prime", brief=True),
        tool("daymark_add", title="Collect samples", op="add-sample"),
    )
    context = prime["content"][0]["text"]
    assert str(db) in context and "workspace sample" in context and "actor sampler" in context
    assert "Commands" not in context
    receipt = added["structuredContent"]
    task_id = receipt["id"]
    assert receipt == {"id": task_id, "kind": "task", "status": "open", "version": 1}
    assert task_id in added["content"][0]["text"]

    writes = [
        tool("daymark_edit", id=task_id, priority=2, op="edit-sample"),
        tool("daymark_act", id=task_id, action="note", text="Measured", op="note-sample"),
        tool("daymark_remember", text="Use metric readings", op="remember-sample"),
    ]
    first = session(*writes)
    assert all(not result["isError"] for result in first)
    assert session(*writes) == first
    with closing(Store(db)) as store:
        assert len(store.events("sample")) == 4
        assert store.get("sample", task_id)["version"] == 3


def test_legacy_protocol_keeps_text_only_write_results(tasks):
    result = handle(tasks, tool("daymark_add", title="Review the draft"), protocol="2024-11-05")[
        "result"
    ]
    assert "Review the draft" in result["content"][0]["text"]
    assert "structuredContent" not in result
