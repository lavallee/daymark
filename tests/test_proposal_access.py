"""Proposal policy can be inspected without deciding, and is checked again on a new write."""

from contextlib import closing

import httpx
import pytest
from fastapi.testclient import TestClient

from daymark import Store, Tasks
from daymark.api import create_app
from daymark.client import Client, ClientError
from daymark.mcp import handle


def view(tasks, *, read_only=False, **kwargs):
    return handle(tasks, {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": "daymark_view", "arguments": {"view": "show", **kwargs}}}, read_only=read_only)["result"]


def test_authenticated_policy_is_current_private_and_independent_of_claim_owner_query():
    auth = {
        "reviewer-token": {"workspace": "lab", "principal": "reviewer"},
        "reader-token": {"workspace": "lab", "principal": "reader"},
    }
    with closing(Store(":memory:")) as store, TestClient(create_app(store, auth)) as server:
        t = Tasks(store, "lab", actor="fixture")
        t.add("Check proposal", id="p", proposed=True, source="sample:catalog:proposal")
        assert t.show("p")["decision_access"] == {"actor": "fixture", "policy_allows": True, "rule": "any_writer"}
        store.set_policy("lab", deciders=["reviewer", "another-configured-decider"])
        headers = {"Authorization": "Bearer reader-token"}
        for path in (
            "/v1/context/p?owner=reviewer", "/v1/brief?entity_id=p&owner=reviewer",
            "/v1/source?system=sample&kind=catalog&ref_id=proposal&detail=show&owner=reviewer",
        ):
            r = server.get(path, headers=headers)
            assert r.status_code == 200
            assert r.json()["decision_access"] == {"actor": "reader", "policy_allows": False, "rule": "named_deciders"}
            assert "another-configured-decider" not in r.text
        with Client("http://sample.invalid", "reviewer-token", transport=server._transport) as c:
            work = Tasks(c, "other-workspace", actor="reader")
            for brief in (False, True):
                fields = {"id": "p", "brief": brief}
                policy = view(work, read_only=True, json=True, **fields)["structuredContent"]["decision_access"]
                assert policy == {"actor": "reviewer", "policy_allows": True, "rule": "named_deciders", "session_read_only": True}
                text = view(work, read_only=True, **fields)["content"][0]["text"]
                assert "person's decision" in text and "read-only" in text
            reviewed = work.show("p")
            store.set_policy("lab", deciders=["reader"])
            with pytest.raises(ClientError) as stale:
                work.accept("p", review_token=reviewed["review_token"], expected_version=1, op="decision")
            assert stale.value.code == "forbidden"
            assert work.brief("p")["decision_access"]["policy_allows"] is False
            assert work.get("p")["status"] == "proposed" and store.head("lab") == 1


def test_old_service_policy_is_unknown_without_any_probe_write():
    with closing(Store(":memory:")) as store:
        t = Tasks(store, "lab")
        t.add("Check proposal", id="p", proposed=True)
        old = t.show("p")
        old.pop("decision_access")
        methods = []

        def service(req):
            methods.append(req.method)
            return httpx.Response(200, json=old)

        with Client("http://sample.invalid", "token", transport=httpx.MockTransport(service)) as c:
            remote = Tasks(c, actor="guessed")
            read = view(remote, json=True, id="p", read_only=True)["structuredContent"]
            assert read["decision_access"] == {"policy_allows": None, "session_read_only": True}
            text = view(remote, id="p")["content"][0]["text"]
            assert "policy permission for this actor is unknown" in text
        assert methods == ["GET", "GET"]
