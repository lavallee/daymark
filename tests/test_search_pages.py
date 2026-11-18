import json

import pytest
from fastapi.testclient import TestClient

from daymark import DomainError, Store, Tasks
from daymark.api import create_app
from daymark.cli import main


def test_matching_paging_and_scope_preserve_facts_without_loading_notes():
    store = Store(":memory:")
    try:
        tasks = Tasks(store, "studio")
        tasks.add("Straße", description="Draft", id="first", scope="art", note="large" * 500)
        tasks.add("Second", description="Wide STRASSE", id="second", scope="art")
        tasks.add("Café", id="third")
        tasks.add("Straße", id="gone", scope="art")
        tasks.archive("gone")
        Tasks(store, "other").add("Straße", id="private", scope="art")
        page = tasks.search("strasse", scope="art", limit=1)
        assert page == {
            "items": [
                {
                    "id": "first",
                    "kind": "task",
                    "status": "open",
                    "version": 1,
                    "title": "Straße",
                    "scope": "art",
                }
            ],
            "next": "first",
        }
        tasks.archive("first")  # A removed cursor still resumes at the correct ID.
        page = tasks.search("strasse", scope="art", limit=1, after=page["next"])
        assert [item["id"] for item in page["items"]] == ["second"] and page["next"] is None
        assert [item["id"] for item in tasks.search("cafe\u0301", scope="")["items"]] == ["third"]
        for bad in (0, 1001, True):
            with pytest.raises(DomainError):
                tasks.search(limit=bad)
    finally:
        store.close()


def test_cli_pages_and_http_search_share_ids_but_not_full_descriptions(tmp_path, capsys):
    db = tmp_path / "demo.db"
    store = Store(db)
    try:
        tasks = Tasks(store)
        tasks.remember("Check the sample first.")
        tasks.add("Inspect sample", id="inspect", description="Full record stays in show.")
        tasks.add("Review sample", id="review")
        assert main(["--db", str(db), "find", "sample", "--page", "--limit", "1", "--json"]) == 0
        page = json.loads(capsys.readouterr().out)
        assert page["next"] == "inspect" and "description" not in page["items"][0]
        assert main(["--db", str(db), "find", "sample", "--json"]) == 0
        assert json.loads(capsys.readouterr().out)[0]["description"] == "Full record stays in show."
        app = create_app(store, {"token": {"principal": "reader", "workspace": "default"}})
        with TestClient(app, headers={"Authorization": "Bearer token"}) as http:
            assert http.post("/v1/search", json={"text": "sample", "limit": 1}).json() == page
            assert (
                len(
                    http.post("/v1/search", json={"text": "sample", "kind": "memory"}).json()[
                        "items"
                    ]
                )
                == 1
            )
            assert http.post("/v1/search", json={"workspace": "other"}).status_code == 422
    finally:
        store.close()


def test_claim_search_recovers_waiting_and_blocked_work_and_excludes_expired(tmp_path, capsys):
    from datetime import datetime, timedelta

    db = tmp_path / "work.db"
    store = Store(db)
    try:
        reader = Tasks(store, actor="reader")
        other = Tasks(store, actor="second")
        for id in ("a", "b", "c", "d", "e"):
            reader.add("Inspect sample", id=id, scope="proofs")
        for id in ("a", "b", "d"):
            reader.claim(id, minutes=60)
        other.claim("c")
        reader.wait("a", "a sample")
        reader.block("b", "e")
        reader.release("d")
        page = reader.search("sample", claimed=True, scope="proofs", limit=1)
        first = page["items"][0]
        assert page["next"] == first["id"] == "a"
        assert first["claimed_by"] == "reader" and first["status"] == "waiting"
        assert reader.search(claimed=True, status="waiting")["items"] == [first]
        second = reader.search(claimed=True, after="a")
        assert [r["id"] for r in second["items"]] == ["b"]
        expiry = datetime.fromisoformat(first["claim_until"])
        for at, expected in ((expiry - timedelta(microseconds=1), ["a"]), (expiry, [])):
            actor = Tasks(store, actor="reader", now=at)
            assert [
                r["id"] for r in actor.search(claimed=True, status="waiting")["items"]
            ] == expected
        assert main(["--db", str(db), "--as", "reader", "find", "--claimed", "--json"]) == 0
        assert [r["id"] for r in json.loads(capsys.readouterr().out)["items"]] == ["a", "b"]
        reader.done("b")
        assert [r["id"] for r in reader.search(claimed=True)["items"]] == ["a"]
    finally:
        store.close()


def test_http_claim_search_binds_principal_and_rejects_an_owner_override():
    store = Store(":memory:")
    try:
        for who in ("alpha", "beta"):
            actor = Tasks(store, "room", actor=who)
            actor.add("Check proof", id=who)
            actor.claim(who)
        other = Tasks(store, "elsewhere", actor="alpha")
        other.add("Check a different proof", id="hidden")
        other.claim("hidden")
        tokens = {key: {"workspace": "room", "principal": key} for key in ("alpha", "beta")}
        with TestClient(create_app(store, tokens)) as http:
            for who in tokens:
                auth = {"Authorization": "Bearer " + who}
                req = http.post("/v1/search", json={"claimed": True}, headers=auth)
                assert req.status_code == 200
                assert [(hit["id"], hit["claimed_by"]) for hit in req.json()["items"]] == [
                    (who, who)
                ]
                assert (
                    http.post(
                        "/v1/search", json={"claimed": True, "claimed_by": "beta"}, headers=auth
                    ).status_code
                    == 422
                )
    finally:
        store.close()


def test_archived_find_across_public_reads(tmp_path, capsys):
    import httpx

    from daymark.client import Client
    from daymark.mcp import handle

    db = tmp_path / "studio.db"
    store = Store(db)
    try:
        tasks = Tasks(store, "studio")
        tasks.propose("Try a zine", id="first", scope="art")
        tasks.propose("Later project", id="second", description="Sketch a zine", scope="art")
        tasks.propose("Try a zine", id="out-of-scope")
        tasks.add("Try a zine", id="live-task", scope="art")
        memory = tasks.remember("Zine paper stock")
        live_memory = tasks.remember("Zine folding guide")
        for entity_id in ("first", "second", "out-of-scope"):
            tasks.archive(entity_id)
        tasks.forget(memory["id"])
        private = Tasks(store, "elsewhere")
        private.propose("Try a zine", id="private", scope="art")
        private.archive("private")

        query = {"text": "zine", "status": "proposed", "limit": 1, "scope": "art", "archived": True}
        page = tasks.search(**query)
        assert page == {
            "items": [{
                "id": "first", "version": 2, "kind": "task", "title": "Try a zine",
                "status": "proposed", "scope": "art", "archived": True,
            }],
            "next": "first", "archived": True,
        }
        second = tasks.search(**query, after=page["next"])
        assert [hit["id"] for hit in second["items"]] == ["second"] and second["next"] is None
        assert tasks.search("zine", archived=True, status="open") == {"items": [], "next": None, "archived": True}
        archived_memory = tasks.search("zine", kind="memory", archived=True)
        assert [(hit["id"], hit["status"], hit["archived"]) for hit in archived_memory["items"]] == [
            (memory["id"], "open", True),
        ]
        assert [hit["id"] for hit in tasks.search("zine", kind="memory")["items"]] == [live_memory["id"]]
        assert [hit["id"] for hit in tasks.search("zine")["items"]] == ["live-task"]
        assert [hit["id"] for hit in tasks.find("zine")] == ["live-task"]
        assert tasks.show("first")["entity"]["archived"]
        with pytest.raises(DomainError, match="live claims"):
            tasks.search(archived=True, claimed=True)

        cli = ["--db", str(db), "--workspace", "studio", "find", "zine"]
        assert main(cli + ["--archived", "--limit", "1"]) == 0
        assert "[proposed]  [archived]" in capsys.readouterr().out
        assert main(cli + ["--archived", "--kind", "memory", "--json"]) == 0
        assert json.loads(capsys.readouterr().out) == archived_memory
        assert main(cli + ["--json"]) == 0
        assert [hit["id"] for hit in json.loads(capsys.readouterr().out)] == ["live-task"]

        app = create_app(store, {"token": {"principal": "reader", "workspace": "studio"}})
        with TestClient(app, headers={"Authorization": "Bearer token"}) as http:
            assert http.post("/v1/search", json=query).json() == page
            assert http.post("/v1/search", json={**query, "after": "first"}).json() == second
            assert http.post("/v1/search", json={**query, "claimed": True}).status_code == 422
            assert http.post("/v1/search", json={**query, "archived": "true"}).status_code == 422
            unknown_workspace = {"archived": True, "workspace": "elsewhere"}
            assert http.post("/v1/search", json=unknown_workspace).status_code == 422

            def forward(request):
                return http.post(request.url.path, content=request.content, headers=request.headers)

            with Client(
                "https://sample.invalid", "token", transport=httpx.MockTransport(forward)
            ) as client:
                assert Tasks(client).search(**query) == page
                assert Tasks(client).search("zine", kind="memory", archived=True) == archived_memory

        message = {
            "jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": "daymark_view", "arguments": {"view": "find", **query}},
        }
        assert "[archived]" in handle(tasks, message)["result"]["content"][0]["text"]
        message["params"]["arguments"]["json"] = True
        assert handle(tasks, message)["result"]["structuredContent"] == page
    finally:
        store.close()


@pytest.mark.parametrize("response", [404, 405, 422, 200])
def test_remote_archived_search_refuses_services_without_archived_results(response):
    import httpx

    from daymark.client import Client

    sent = []

    def server(request):
        sent.append((request.url.path, json.loads(request.content)))
        return httpx.Response(response, json={"items": [], "next": None})

    with (
        Client("https://sample.invalid", "synthetic", transport=httpx.MockTransport(server)) as client,
        pytest.raises(DomainError, match="Upgrade the service to search archived records"),
    ):
        Tasks(client).search("zine", archived=True)
    assert sent == [("/v1/search", {"text": "zine", "kind": "task", "limit": 50, "archived": True})]
