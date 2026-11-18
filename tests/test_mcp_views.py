"""Structured reads are explicit, small for lists, and usable by older clients."""

import json
from contextlib import closing

import pytest

from daymark import Store, Tasks
from daymark.mcp import handle


@pytest.fixture
def tasks():
    with closing(Store(":memory:")) as store:
        tasks = Tasks(store, "sample", actor="reviewer", timezone="UTC")
        tasks.goal("Check samples", id="g")
        tasks.project("Label stock", goal="g", id="p")
        tasks.add(
            "Label first sample",
            id="a",
            project="p",
            scope="proof",
            move="agent",
            priority=2,
            description="Detailed first label instructions",
        )
        tasks.add("Label second sample", id="b", project="p", scope="proof", after=["a"])
        tasks.note("a", "First sample uses the green label")
        yield tasks


def read(tasks, view, *, protocol="2025-11-25", **fields):
    msg = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": "daymark_view", "arguments": {"view": view, **fields}},
    }
    reply = handle(tasks, msg, protocol=protocol)
    assert "result" in reply, reply
    result = reply["result"]
    assert not result["isError"], result
    return result


@pytest.mark.parametrize(
    ("view", "fields", "key"),
    [
        ("ready", {}, "items"),
        ("find", {}, "items"),
        ("show", {"id": "a"}, "readiness"),
        ("show", {"id": "a", "brief": True}, "notes"),
        ("activity", {}, "cursor"),
        ("agenda", {}, "today"),
        ("projects", {}, "projects"),
        ("plan", {"id": "p", "by": "stage"}, "groups"),
        ("goals", {}, "goals"),
        ("goal", {"id": "g"}, "goal"),
        ("explore", {"id": "g"}, "conditions"),
        ("profiles", {}, "builtins"),
        ("profiles", {"profile": "plain"}, "strategies"),
        ("advise", {}, "picks"),
    ],
)
def test_requested_views_deliver_the_same_data_to_old_and_new_protocols(tasks, view, fields, key):
    modern = read(tasks, view, json=True, **fields)
    old = read(tasks, view, protocol="2024-11-05", json=True, **fields)
    default = read(tasks, view, **fields)
    assert key in modern["structuredContent"]
    assert json.loads(old["content"][0]["text"]) == modern["structuredContent"]
    assert "structuredContent" not in old and "structuredContent" not in default


def test_queue_rows_omit_long_records_but_full_show_retains_note_identity(tasks):
    text = "Record details " * 600
    tasks.note("a", text)
    ready = read(tasks, "ready", json=True)["structuredContent"]["items"]
    assert [item["id"] for item in ready] == ["a"]
    assert ready[0]["priority"] == 2
    assert {"notes", "description", "metadata"}.isdisjoint(ready[0])
    detail = read(tasks, "show", id="a", json=True)["structuredContent"]
    note = next(n for n in detail["entity"]["notes"] if n["text"] == text)
    single = read(tasks, "show", id="a", note=note["id"], json=True)["structuredContent"]
    assert single["text"] == text and single["author"] == "reviewer"


def test_find_uses_a_typed_cursor_and_preserves_filters(tasks):
    args = {"text": "Label", "kind": "task", "status": "open", "scope": "proof", "limit": 1}
    first = read(tasks, "find", json=True, **args)["structuredContent"]
    assert first["next"] == "a"
    second = read(tasks, "find", json=True, after=first["next"], **args)["structuredContent"]
    assert [row["id"] for row in second["items"]] == ["b"] and second["next"] is None
    assert "next: a" in read(tasks, "find", **args)["content"][0]["text"]
    assert (
        "Label second sample"
        in read(tasks, "find", after=first["next"], **args)["content"][0]["text"]
    )


def test_grouped_plan_projects_only_one_copy_of_a_task(tasks):
    plan = read(tasks, "plan", id="p", by="stage", json=True)["structuredContent"]
    assert [x["id"] for x in plan["tasks"]] == ["a", "b"]
    assert {"notes", "description", "metadata"}.isdisjoint(plan["tasks"][0])
    assert any(group["items"] == ["a", "b"] for group in plan["groups"])


def test_exact_source_lookup_applies_to_show_brief_and_an_individual_note(tasks):
    ref = "samples:sheet:Q:41"
    tasks.add("Read the source sample", id="source-task", source=ref)
    tasks.note("source-task", "Read the full sheet")
    detail = read(tasks, "show", source=ref, json=True)["structuredContent"]
    assert detail["entity"]["id"] == "source-task"
    note_id = detail["entity"]["notes"][0]["id"]
    assert (
        read(tasks, "show", source=ref, brief=True, json=True)["structuredContent"]
        == (read(tasks, "show", id="source-task", brief=True, json=True)["structuredContent"])
    )
    note = read(tasks, "show", source=ref, note=note_id, json=True)["structuredContent"]
    assert note["text"] == "Read the full sheet" and note["id"] == note_id
    for fields in ({}, {"source": ref, "id": "a"}):
        reply = handle(
            tasks,
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "daymark_view", "arguments": {"view": "show", **fields}},
            },
        )["result"]
        assert reply["isError"] and "exactly one" in reply["content"][0]["text"]


def test_ready_explanation_reports_matching_dependencies_and_example_ids(tasks):
    data = read(tasks, "ready", scope="proof", explain=True, json=True)["structuredContent"]
    assert data["ready_count"] == 1
    assert data["held_count"] == 1
    assert data["reasons"]["dependencies"] == {"count": 1, "examples": ["b"]}
    text = read(tasks, "ready", scope="proof", explain=True)["content"][0]["text"]
    assert "prerequisites (1): b" in text


def test_old_http_service_that_ignores_brief_is_projected_before_the_host(tasks):
    import httpx

    from daymark.client import Client

    tasks.edit("a", title="Long title " * 40)
    tasks.note("a", "Detached note. " * 600)
    full = tasks.ready()

    def service(request):
        assert request.url.path == "/v1/ready" and request.url.params["brief"] == "true"
        return httpx.Response(200, json=full)

    with Client(
        "http://sample.invalid", "sample", transport=httpx.MockTransport(service)
    ) as client:
        remote = Tasks(client, "sample", actor="reviewer", timezone="UTC")
        data = read(remote, "ready", json=True)["structuredContent"]
    row = data["items"][0]
    assert row["id"] == "a" and len(row["title"]) == 161
    assert row["why"] and {"description", "notes", "metadata"}.isdisjoint(row)


def write(tasks, tool, **fields):
    return handle(
        tasks,
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "daymark_" + tool, "arguments": fields},
        },
    )["result"]


def test_context_entities_are_assignable_and_filter_ready_before_limiting(tasks):
    made = write(tasks, "add", kind="context", title="At the sample bench")
    context = made["structuredContent"]["id"]
    write(tasks, "edit", id="b", contexts=[context])
    ready = read(tasks, "ready", context=context, explain=True, limit=1, json=True)[
        "structuredContent"
    ]
    assert ready["ready"] == [] and ready["reasons"]["dependencies"]["examples"] == ["b"]
    new = write(tasks, "add", title="Read the bench dial", contexts=[context])
    ready = read(tasks, "ready", context=context, limit=1, json=True)["structuredContent"]
    assert [x["id"] for x in ready["items"]] == [new["structuredContent"]["id"]]
    assert ready["items"][0]["context_ids"] == [context]


def test_profile_write_returns_workspace_guard_and_exact_recovery(tasks):
    active = read(tasks, "profiles", json=True)["structuredContent"]["active"]
    args = {
        "action": "use_profile",
        "id": "plain",
        "expected_version": active["version"],
        "op": "plain-1",
    }
    result = write(tasks, "act", **args)
    assert not result["isError"], result
    data = result["structuredContent"]
    assert (data["name"], data["workspace"], data["version"]) == (
        "plain",
        "sample",
        active["version"] + 1,
    )
    assert data["undo"] == {"operation_id": "plain-1", "expected_version": data["version"]}
    assert "profile --undo=plain-1" in result["content"][0]["text"]
    assert write(tasks, "act", **args)["structuredContent"] == data
    assert write(tasks, "act", **{**args, "op": "plain-2"})["isError"]
    assert write(tasks, "act", action="use_profile")["isError"]


def test_claimed_search_reports_the_current_workers_lease_in_both_formats(tasks):
    tasks.claim("a")
    data = read(tasks, "find", claimed=True, json=True)["structuredContent"]
    assert [row["id"] for row in data["items"]] == ["a"]
    assert data["items"][0]["claimed_by"] == "reviewer" and data["items"][0]["claim_until"]
    assert "claimed by reviewer" in read(tasks, "find", claimed=True)["content"][0]["text"]
