"""Scoped orientation cannot disclose titles or continuations outside the requested scope."""

import json
from contextlib import closing

import httpx
import pytest
from fastapi.testclient import TestClient

from daymark import DomainError, Store, Tasks
from daymark.api import create_app
from daymark.cli import main
from daymark.client import Client
from daymark.mcp import handle


def prime(tasks, scope, *, read_only=False):
    req = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {
        "name": "daymark_prime", "arguments": {"scope": scope},
    }}
    return handle(tasks, req, read_only=read_only)["result"]


def seed(store):
    t = Tasks(store, "lab", actor="worker")
    t.add("Private home reading", id="home", scope="home", priority=1)
    t.add("Private general work", id="general", priority=1)
    t.add("Scan original", id="scan", scope="studio")
    t.add("Post scan", id="post", scope="studio", after=["home"])
    t.add("Private home proposal", id="idea", scope="home", proposed=True)
    t.add("Private home preference", kind="memory", id="a-home", scope="home")
    t.add("General preference", kind="memory", id="a-global")
    for i in range(6):
        t.add(f"Studio fact {i}", kind="memory", id=f"z-{i}", scope="studio")
    return t


def test_scope_precedes_selection_count_and_memory_paging_locally_and_over_http(tmp_path, capsys):
    db = tmp_path / "lab.sqlite"
    with closing(Store(db)) as store:
        local = seed(store)
        auth = {"test-token": {"workspace": "lab", "principal": "worker"}}
        with TestClient(create_app(store, auth)) as app, Client(
            "http://sample.invalid", "test-token", transport=app._transport
        ) as client:
            remote = Tasks(client, "wrong-workspace", actor="wrong-worker")
            for t in (local, remote):
                snapshot = t.orientation(scope="studio")
                assert snapshot["summary"] == t.summary(scope="studio")
                assert snapshot["summary"]["ready"] == 1 and snapshot["summary"]["proposed"] == 0
                assert [r["id"] for r in snapshot["ready"]] == ["scan"]
                assert snapshot["next_memory"] == "z-4" and len(snapshot["memories"]) == 5
                assert "activity_cursor" not in snapshot and "activity_checkpoint" not in snapshot
                assert snapshot["scope"] == "studio"
                for ro in (False, True):
                    text = prime(t, "studio", read_only=ro)["content"][0]["text"]
                    assert "Scope: studio" in text and "Scan original" in text
                    assert "Private" not in text and "General preference" not in text
                    assert 'daymark_view {"view":"activity"' not in text
                    hint = next(line.split("daymark_view ", 1)[1] for line in text.splitlines() if "More facts:" in line)
                    follow = handle(t, {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                                        "params": {"name": "daymark_view", "arguments": json.loads(hint)}})
                    more = follow["result"]["content"][0]["text"]
                    assert "Studio fact 5" in more and "Private" not in more and "General preference" not in more
                assert t.orientation(scope="absent")["ready"] == []
                unscoped = t.orientation(scope="")
                assert [i["id"] for i in unscoped["ready"]] == ["general"]
                assert [i["id"] for i in unscoped["memories"]] == ["a-global"]
                assert "activity_checkpoint" in t.orientation()
    assert main(["--db", str(db), "--workspace", "lab", "prime", "--scope", "studio", "--json"]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["scope"] == "studio" and "Private" not in json.dumps(output)


def test_old_service_cannot_silently_widen_a_scoped_snapshot():
    seen = []

    def old(request):
        seen.append(request.url.path)
        return httpx.Response(200, json={"paths": {"/v1/orientation": {"get": {"parameters": []}}}})

    with Client("http://sample.invalid", "test-token", transport=httpx.MockTransport(old)) as c:
        tasks = Tasks(c)
        with pytest.raises(DomainError, match="does not support scope"):
            tasks.orientation(scope="studio")
        reply = prime(tasks, "studio")
        assert reply["isError"] and "Keep the same scope" in reply["content"][0]["text"]
    assert seen == ["/v1/schema"]


@pytest.mark.parametrize(("scope", "operation", "values"), [("studio", "eq", "studio"), ("", "in", [None, ""])])
def test_legacy_scope_filters_reach_the_service_before_any_memory_bodies(scope, operation, values):
    with closing(Store(":memory:")) as store:
        seed(store)
        returned_ids = []

        def service(req):
            if req.url.path == "/v1/search":
                return httpx.Response(404)
            assert req.url.path == "/v1/query"
            query = json.loads(req.content)["filters"]
            assert {"field": "scope", "op": operation, "value": values} in query["and"]
            items = store.query("lab", query)
            returned_ids.extend(x["id"] for x in items)
            return httpx.Response(200, json=items)

        with Client("http://sample.invalid", "t", transport=httpx.MockTransport(service)) as c:
            assert Tasks(c).search(kind="memory", scope=scope)["items"]
        assert all(id.startswith("z-") for id in returned_ids) if scope else returned_ids == ["a-global"]


@pytest.mark.parametrize("section", ["ready", "memories"])
def test_scope_echo_without_matching_nested_records_refuses_before_rendering(section):
    def service(req):
        if req.url.path == "/v1/schema":
            return httpx.Response(200, json={"paths": {"/v1/orientation": {"get": {"parameters": [{"name": "scope"}]}}}})
        snapshot = {"scope": "studio", "ready": [], "memories": [], "activity_cursor": 42}
        snapshot[section] = [{"scope": "home", "title": "Sensitive sample outside the requested view"}]
        return httpx.Response(200, json=snapshot)

    with Client("http://sample.invalid", "t", transport=httpx.MockTransport(service)) as c:
        result = prime(Tasks(c), "studio")
    assert result["structuredContent"]["error"]["code"] == "invalid"
    assert "Sensitive sample" not in str(result) and "42" not in str(result)


@pytest.mark.parametrize("verb", [
    ["prime"], ["advise"], ["ready"], ["agenda"], ["take"], ["find"], ["projects"], ["explore", "goal"],
])
def test_named_scope_and_unscoped_are_mutually_exclusive(verb):
    from daymark.cli import parser

    cli = parser()
    assert cli.parse_args([*verb, "--scope", "studio"]).scope == "studio"
    assert cli.parse_args([*verb, "--unscoped"]).scope == ""
    assert cli.parse_args(verb).scope is None
    with pytest.raises(SystemExit) as error:
        cli.parse_args([*verb, "--scope", "studio", "--unscoped"])
    assert error.value.code == 2
