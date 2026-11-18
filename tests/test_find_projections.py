"""Ordinary find preserves full records and human listing details across transports."""

import json
import sqlite3
from contextlib import closing

import httpx
import pytest
from fastapi.testclient import TestClient

from daymark import Client, DomainError, Store, Tasks
from daymark import search as search_view
from daymark.api import create_app
from daymark.cli import main
from daymark.mcp import call
from daymark.render import listing


@pytest.mark.parametrize("compat", [False, True])
def test_unicode_ids_legacy_slices_filters_and_exact_listing(compat):
    class Connection(sqlite3.Connection):
        def execute(self, sql, *args):
            if compat and "json_extract(" in sql:
                raise sqlite3.OperationalError("no such function: json_extract")
            return super().execute(sql, *args)

    with closing(sqlite3.connect(":memory:", factory=Connection)) as db:
        store = Store(db)
        t = Tasks(store, "press", actor="writer", timezone="UTC", now="2030-05-03T10:00:00Z")
        t.add("Phone", kind="context", id="phone")
        t.goal("Print", id="print")
        t.condition("Ready to print", goal="print", state="partly_met", id="condition")
        t.add("Reviewer", id="reviewer", kind="person")
        t.add("Straße", id="b", description="long" * 300, note="Unabridged proof note.",
              scope="studio", contexts=["phone"], priority=1, due="2030-05-03", plan="2030-05-02",
              minutes=15, move="agent", advances=["condition"])
        t.add("Café", id="a", description="Straße")
        t.block("b", "a")
        t.claim("b")
        t.remind("b", "2030-05-04 10:00", recipient="reviewer")
        t.add("STRAẞE", id="c", scope="home")
        t.add("STRASSE", id="d")
        t.archive("d")
        assert [x["id"] for x in t.find("STRASSE")] == ["a", "b", "c"]
        assert t.find("STRASSE", scope="studio", context="phone") == [t.get("b")]
        assert t.find("STRASSE", limit=-1) == [t.get("a"), t.get("b")]
        assert t.find("cafe\u0301", limit=None) == [t.get("a")]
        page = t.search("STRASSE", limit=2)
        assert [r["id"] for r in page["items"]] == [r["id"] for r in t.find("STRASSE", limit=2)]
        for kind in ("task", "condition", "reminder", "context", "person", "goal"):
            full = t.find(kind=kind)
            lean = t.find(kind=kind, brief=True)
            assert listing(lean, t.now()) == listing(full, t.now())
            assert all({"description", "notes", "metadata"}.isdisjoint(row) for row in lean)


def test_default_cli_mcp_and_authenticated_projection_agree(tmp_path, capsys):
    path = tmp_path / "sample.db"
    with closing(Store(path)) as store:
        t = Tasks(store, "room", actor="reader")
        t.add("Straße", id="one", description="Separate metadata", note="Detailed note.")
        t.add("Other", id="two", description="STRASSE")
        Tasks(store, "second").add("Straße", id="hidden")
        expected = t.find("STRASSE")
        assert call(t, "daymark_view", {"view": "find", "text": "STRASSE"}) == listing(expected)
        assert main(["--db", str(path), "--workspace", "room", "find", "STRASSE"]) == 0
        assert capsys.readouterr().out.strip() == listing(expected)
        assert main(["--db", str(path), "--workspace", "room", "find", "STRASSE", "--json"]) == 0
        assert json.loads(capsys.readouterr().out)[0]["description"] == "Separate metadata"
        auth = {"sample": {"workspace": "room", "principal": "reader"}}
        with TestClient(create_app(store, auth)) as app, Client(
            "http://sample.invalid", "sample", transport=app._transport, read_only=True
        ) as client:
            received = []

            def inspect(response):
                if response.request.url.path == "/v1/search":
                    response.read()
                    received.append(response.json())

            client._http.event_hooks["response"].append(inspect)
            remote = Tasks(client)
            assert remote.find("STRASSE") == expected
            assert received[-1]["projection"] == "entity"
            assert listing(remote.find("STRASSE", brief=True)) == listing(expected)
            assert received[-1]["projection"] == "line" and "Detailed note." not in str(received[-1])
            for search in (t.search, remote.search, client.search):
                with pytest.raises(DomainError, match="Only tasks have work claims"):
                    search(kind="person", claimed=True, source="press:task:1")
            assert len(received) == 2
            rejected = app.post("/v1/search", headers={"Authorization": "Bearer sample"},
                                json={"kind": "person", "claimed": True, "source": "press:task:1"})
            assert rejected.status_code == 422


def test_selected_full_rows_share_snapshot_with_unicode_matching(tmp_path, monkeypatch):
    path = tmp_path / "interleaved.db"
    with closing(Store(path)) as reader, closing(Store(path)) as writer:
        reader.db.execute("PRAGMA journal_mode=WAL")
        original = Tasks(reader).add("Straße", id="proof")
        read_records = search_view._records

        def update_outside_snapshot(*args):
            Tasks(writer).edit("proof", title="Renamed")
            return read_records(*args)

        monkeypatch.setattr(search_view, "_records", update_outside_snapshot)
        assert Tasks(reader).find("STRASSE") == [original]
        assert reader.get("default", "proof")["title"] == "Renamed"


def test_older_search_service_keeps_scope_context_and_casefold_in_query_fallback():
    requests = []

    def serve(req):
        requests.append(req.url.path)
        if req.url.path == "/v1/schema":
            return httpx.Response(200, json={"paths": {"/v1/search": {"post": {}}}})
        assert req.url.path == "/v1/query"
        terms = json.loads(req.content)["filters"]["and"]
        assert {"field": "scope", "op": "eq", "value": "press"} in terms
        assert {"field": "context_ids", "op": "contains", "value": "phone"} in terms
        return httpx.Response(200, json=[{"id": "a", "kind": "task", "scope": "press",
            "title": "Straße", "description": "", "context_ids": ["phone"], "notes": ["preserved"]}])

    with Client("http://sample.invalid", "token", transport=httpx.MockTransport(serve), read_only=True) as c:
        t = Tasks(c)
        assert t.find("STRASSE", scope="press", context="phone")[0]["notes"] == ["preserved"]
        assert "notes" not in t.find("STRASSE", scope="press", context="phone", brief=True)[0]
        assert requests == ["/v1/schema", "/v1/query", "/v1/query"]


@pytest.mark.parametrize("option", [{"projection": "line"}, {"context": "phone"}])
def test_unconfirmed_projection_or_context_never_looks_like_a_success(option):
    def partial_service(req):
        if req.url.path == "/v1/schema":
            return httpx.Response(200, json={"paths": {"/v1/search": {"post": {
                "parameters": [{"name": k} for k in option]}}}})
        assert req.url.path == "/v1/search"
        return httpx.Response(200, json={"items": [{"title": "Unrelated detail"}], "next": None})

    with (
        Client("http://sample.invalid", "token", transport=httpx.MockTransport(partial_service)) as client,
        pytest.raises(DomainError, match="did not confirm"),
    ):
        client.search(**option)
