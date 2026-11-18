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
        ("initialize", {"protocolVersion": "2025-03-26"}),
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
    assert receipt == {
        "id": task_id,
        "kind": "task",
        "title": "Collect samples",
        "status": "open",
        "version": 1,
    }
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


@pytest.mark.parametrize("revision", ["2024-11-05", "2025-03-26"])
def test_legacy_protocol_keeps_text_only_write_results(tasks, revision):
    result = handle(tasks, tool("daymark_add", title="Review the draft"), protocol=revision)[
        "result"
    ]
    assert "Review the draft" in result["content"][0]["text"]
    assert "structuredContent" not in result


def test_march_batches_receive_one_reply_per_request_and_preserve_read_only(tasks):
    from io import StringIO

    from daymark.mcp import serve

    tasks.add("Collect samples", id="sample")
    notification = {"jsonrpc": "2.0", "method": "notifications/initialized"}
    wire = [
        request("initialize", protocolVersion="2025-03-26"), [notification], [],
        [notification, {**request("ping"), "id": 2},
         {**tool("daymark_act", action="done", id="sample"), "id": 3},
         [], {**request("initialize", protocolVersion="2025-11-25"), "id": 4}],
        [{**tool("daymark_view", view="show", id="sample", brief=True, json=True), "id": 5}],
    ]
    stdout = StringIO()
    serve(tasks, StringIO("".join(json.dumps(item) + "\n" for item in wire)), stdout, read_only=True)
    hello, empty, mixed, last = [json.loads(line) for line in stdout.getvalue().splitlines()]
    assert hello["result"]["protocolVersion"] == "2025-03-26"
    assert empty["error"]["code"] == -32600
    assert [r["id"] for r in mixed] == [2, 3, None, 4]
    assert mixed[0]["result"] == {} and "forbidden" in mixed[1]["result"]["content"][0]["text"]
    assert mixed[2]["error"]["code"] == mixed[3]["error"]["code"] == -32600
    assert json.loads(last[0]["result"]["content"][0]["text"])["version"] == 1
    assert handle(tasks, wire[-1], protocol="2025-11-25")["error"]["code"] == -32600


def test_stale_decision_names_recovery_without_changing_the_proposal(tasks):
    original = tasks.propose("Review first sample", id="proposal")
    revised = tasks.edit("proposal", title="Review revised sample")
    message = tool(
        "daymark_act", action="accept", id="proposal", expected_version=original["version"]
    )
    modern = handle(tasks, message)["result"]
    error = modern["structuredContent"]["error"]
    assert modern["isError"] and error["code"] == "conflict"
    assert "person" in error["recovery"] and "current proposal" in error["recovery"]
    old = handle(tasks, message, protocol="2024-11-05")["result"]
    assert "structuredContent" not in old and error["recovery"] in old["content"][0]["text"]
    assert tasks.get("proposal") == revised


def test_http_failure_keeps_the_code_and_excludes_service_response_body():
    import httpx

    from daymark.client import Client

    with Client(
        "https://sample.invalid",
        "example-secret",
        transport=httpx.MockTransport(lambda _: httpx.Response(403, text="unrelated-private-data")),
    ) as client:
        result = handle(Tasks(client), tool("daymark_view", view="show", id="sample"))["result"]
    assert result["structuredContent"]["error"]["code"] == "forbidden"
    assert all(value not in str(result) for value in ("unrelated-private-data", "example-secret"))


def test_reminder_receipt_and_worker_preserve_the_distinction_from_a_deadline(tasks):
    from datetime import UTC, datetime

    from daymark.worker import Worker

    tasks = Tasks(tasks.engine, "sample", timezone="Europe/London")
    draft = tasks.propose("Prepare proof", id="draft", plan="2030-05-02", due="2030-05-10")
    tasks.add("Sample reviewer", kind="person", id="reviewer")
    fields = {
        "action": "remind",
        "id": "draft",
        "recipient": "reviewer",
        "when": "2030-05-03T09:00",
        "op": "review-reminder",
    }

    def schedule(**changes):
        return handle(tasks, tool("daymark_act", **(fields | changes)))["result"]

    incomplete = schedule(when="2030-05-03")
    assert incomplete["isError"] and tasks.engine.list("sample", kind="reminder") == []
    response = schedule()
    assert not response["isError"]
    data = response["structuredContent"]
    assert data == {
        "id": data["id"],
        "kind": "reminder",
        "title": "",
        "status": "open",
        "version": 1,
        "task_id": "draft",
        "channel": "recording",
        "recipient_id": "reviewer",
        "reminder_state": "pending",
        "scheduled_at": "2030-05-03T08:00:00Z",
    }
    assert "2030-05-03 09:00 Europe/London" in response["content"][0]["text"]
    assert schedule() == response
    assert schedule(when="2030-05-04T09:00")["isError"]
    assert tasks.get("draft") == draft
    worker = Worker(tasks.engine)
    at = datetime(2030, 5, 3, 8, tzinfo=UTC)
    assert worker.run_once(now=at)["claimed"] == 0
    tasks.accept("draft")
    assert worker.run_once(now=at)["recorded"] == 1
    assert worker.run_once(now=at)["claimed"] == 0
    assert tasks.get(data["id"])["reminder_state"] == "delivered"
    assert schedule() == response  # Original schedule receipt; reread for delivery status.


def test_missing_tool_input_and_incomplete_service_results_are_distinct(tasks, monkeypatch):
    missing = handle(tasks, tool("daymark_act", action="note", id="sample"))["result"]
    assert missing["isError"] and "missing argument text" in missing["content"][0]["text"]

    monkeypatch.setattr(tasks, "show", lambda *_args, **_kwargs: {})
    failure = handle(tasks, tool("daymark_view", view="show", id="sample", json=True))
    assert failure["error"] == {"code": -32603, "message": "The request failed"}



def test_temporary_advice_recovers_from_misplaced_filters(tasks):
    for fields in ({"minutes": "15"}, {"move": "answer"}):
        result = handle(tasks, tool("daymark_view", view="advise", **fields))["result"]
        error = result["structuredContent"]["error"]
        assert error["code"] == "invalid" and "time-fit.minutes=15" in error["recovery"]


@pytest.mark.parametrize("selector", [{"id": "sample"}, {"source": "press:job:S-41"}])
def test_misplaced_activity_selector_recovers_to_the_exact_task(tasks, selector):
    tasks.add("Sample proof", id="sample", source="press:job:S-41")
    tasks.add("Sample proof", id="other", note="Unrelated instruction.")
    tasks.note("sample", "Check the overlay at F3.")

    def read(view, **args):
        return handle(tasks, tool("daymark_view", view=view, json=True, **args))["result"]

    error = read("activity", **selector)["structuredContent"]["error"]
    assert error["code"] == "invalid" and "show" in error["recovery"]
    assert "brief=true" in error["recovery"] and "since_version" in error["recovery"]
    brief = read("show", brief=True, **selector)["structuredContent"]
    assert brief["id"] == "sample" and len(brief["notes"]) == 1
    full = read("show", note=brief["notes"][0]["id"], **selector)["structuredContent"]
    assert full["text"] == "Check the overlay at F3."


def test_invalid_service_credential_is_distinct_from_a_transport_failure_and_redacted():
    import httpx

    from daymark.client import Client

    def reject(_request):
        return httpx.Response(401, json={"detail": "Untrusted body containing synthetic-private-text"})

    with Client("http://sample.invalid", "sample-token", transport=httpx.MockTransport(reject)) as remote:
        result = handle(Tasks(remote), tool("daymark_add", title="Read sample"))["result"]
    error = result["structuredContent"]["error"]
    assert error["code"] == "unauthenticated" and "bearer" in error["recovery"]
    assert "operation_id" not in error
    assert "sample-token" not in str(result) and "synthetic-private-text" not in str(result)
