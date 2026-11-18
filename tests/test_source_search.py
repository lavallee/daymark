"""Find a source inside a working scope without loading unrelated entity detail."""

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

REF = {"system": "catalog", "kind": "proof", "id": "Q:41/é"}
REF_TEXT = "catalog:proof:Q:41/é"


def find(tasks, **args):
    return handle(tasks, {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": "daymark_view", "arguments": {
            "view": "find", "source": REF_TEXT, "json": True, **args,
        }}})["result"]["structuredContent"]


def test_exact_source_is_a_scoped_compact_search_not_a_detail_read(tmp_path, capsys):
    db = tmp_path / "sample.sqlite"
    with closing(Store(db)) as store:
        t = Tasks(store, "lab", actor="inspector")
        t.add("Other work", id="other", scope="home")
        t.add("Check measurements", id="proof", move="agent", scope="studio", source=REF,
              note="This detail belongs only in an explicit detail read.")
        Tasks(store, "elsewhere").add("Separate title", id="different", scope="studio", source=REF)
        t.claim("proof")
        with TestClient(create_app(store, {"t": {"workspace": "lab", "principal": "inspector"}})) as server, Client(
            "http://sample.invalid", "t", transport=server._transport,
        ) as client:
            for tasks in (t, Tasks(client, actor="wrong")):
                data = find(tasks, scope="studio", claimed=True)
                assert data["source"] == REF and data["next"] is None
                (hit,) = data["items"]
                assert hit["id"] == "proof" and hit["claimed_by"] == "inspector"
                assert {"notes", "description", "history"}.isdisjoint(hit)
                for excluded in ({"scope": ""}, {"scope": "home"}, {"kind": "person"},
                                 {"status": "completed"}, {"text": "absent"}, {"after": "proof"}):
                    result = tasks.search(source=REF, **excluded)
                    assert result == {"items": [], "next": None, "source": REF}
                assert tasks.search("CHECK", source=REF)["items"][0]["id"] == "proof"
            t.archive("proof")
            assert find(t, scope="studio")["items"] == []
            assert find(Tasks(client), scope="studio", archived=True)["items"][0]["archived"]
    assert main(["--db", str(db), "--workspace", "lab", "find", "--scope", "studio",
                 "--source", REF_TEXT, "--archived", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["items"][0]["id"] == "proof"


@pytest.mark.parametrize("code", [200, 404, 422])
def test_unknown_or_ignored_source_filter_never_falls_back_to_an_unscoped_read(code):
    reqs = []

    def legacy(req):
        reqs.append((req.method, req.url.path))
        assert json.loads(req.content)["source"] == REF
        return httpx.Response(code, json={"items": [{"title": "Out-of-scope private note"}], "next": None})

    with Client("http://sample.invalid", "t", transport=httpx.MockTransport(legacy)) as c:
        result = find(Tasks(c), scope="studio")
        assert result["error"]["code"] == "invalid" and "Upgrade" in result["error"]["message"]
        assert "private note" not in str(result)
        with pytest.raises(DomainError, match="Source must be"):
            c.search(source="invalid")
    assert reqs == [("POST", "/v1/search")]


def test_typed_find_rejects_unknown_filters_before_they_can_look_like_empty_results(tmp_path, capsys):
    db = tmp_path / "sample.sqlite"
    with closing(Store(db)) as store:
        tasks = Tasks(store, "lab")
        tasks.propose("Review sample", id="proposal-a")
        for fields, correction in [
            ({"kind": "proposal"}, "status=proposed"),
            ({"kind": "decision"}, "move=answer"),
            ({"kind": "all"}, "Choose one of"),
            ({"status": "done"}, "status=completed"),
            ({"status": "pending"}, "proposed"),
        ]:
            for method in (tasks.find, tasks.search):
                with pytest.raises(DomainError, match=correction) as exc:
                    method(**fields)
                assert exc.value.code == "invalid"
            response = find(tasks, **fields)
            assert response["error"]["code"] == "invalid" and correction in response["error"]["message"]
        assert tasks.search(status="completed")["items"] == []
        assert tasks.find(status="proposed")[0]["id"] == "proposal-a"
        # Raw query filters remain extensible; type names are also valid query fields.
        assert store.query("lab", {"field": "kind", "op": "eq", "value": "future-kind"}) == []
    for flags in ([], ["--page"]):
        assert main(["--db", str(db), "find", "--kind", "proposal", "--json", *flags]) != 0
        data = json.loads(capsys.readouterr().err)
        assert data["error"]["code"] == "invalid" and "status=proposed" in data["error"]["message"]


def test_typed_find_rejects_unknown_filters_before_contacting_legacy_server():
    def no_request(req):
        pytest.fail(f"invalid typed filter reached {req.url.path}")

    with Client("http://sample.invalid", "t", transport=httpx.MockTransport(no_request)) as client:
        for operation in (Tasks(client).find, Tasks(client).search):
            with pytest.raises(DomainError, match="status=completed"):
                operation(status="done")
