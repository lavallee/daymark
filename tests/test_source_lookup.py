"""Recover captured entities through their exact workspace-local source triple."""

import json

import httpx
import pytest
from fastapi.testclient import TestClient

from daymark import DomainError, Store, Tasks
from daymark.api import create_app
from daymark.cli import main
from daymark.client import Client


def test_find_original_capture_after_duplicate_even_when_archived(tmp_path, capsys):
    path = str(tmp_path / "fixture.sqlite")
    base = ["--db", path]
    source = "catalog:drawing:page:42"
    assert main([*base, "propose", "Inspect sample", "--id", "original", "--source", source]) == 0
    assert main([*base, "propose", "Different suggested action", "--source", source]) == 4
    assert main([*base, "--as", "human", "dismiss", "original"]) == 0
    assert main([*base, "archive", "original"]) == 0
    capsys.readouterr()
    assert main([*base, "show", "--source", source, "--json"]) == 0
    entity = json.loads(capsys.readouterr().out)["entity"]
    assert entity["id"] == "original" and entity["archived"] and entity["status"] == "cancelled"
    assert main([*base, "show", "--source", "catalog:drawing:page", "--json"]) == 3
    assert "not_found" in capsys.readouterr().err
    assert main([*base, "show", "--source", "catalog:drawing:", "--json"]) == 5


def test_source_addresses_by_three_exact_components_across_http():
    store = Store(":memory:")
    try:
        # Low-level clients can use punctuation inside *any* component.
        source = {
            "system": "catalog:example",
            "kind": "section/identifier",
            "id": "proof/42?edition=2",
        }
        for workspace in ("one", "two"):
            t = Tasks(store, workspace, actor="author")
            t.propose(
                f"Sample for {workspace}",
                id=f"record-{workspace}",
                source=source,
                note="First sample",
            )
        first = Tasks(store, "one", actor="reader").brief(source=source)
        app = create_app(
            store,
            {
                "first": {"workspace": "one", "principal": "reader"},
                "second": {"workspace": "two", "principal": "reader"},
            },
        )
        with TestClient(app) as http:

            def forward(request):
                reply = http.request(
                    request.method,
                    request.url.raw_path.decode(),
                    headers=dict(request.headers),
                    content=request.content,
                )
                return httpx.Response(reply.status_code, content=reply.content)

            with Client(
                "http://daymark.invalid", "first", transport=httpx.MockTransport(forward)
            ) as client:
                remote = Tasks(client)
                assert remote.show(source=source)["entity"]["id"] == "record-one"
                assert remote.brief(source=source) == first
                assert (
                    remote.read_note(None, first["notes"][0]["id"], source=source)["text"]
                    == "First sample"
                )
            second = http.get(
                "/v1/source",
                params={
                    "system": source["system"],
                    "kind": source["kind"],
                    "ref_id": source["id"],
                    "detail": "brief",
                },
                headers={"Authorization": "Bearer second"},
            )
            assert second.status_code == 200 and second.json()["id"] == "record-two"
            assert (
                http.get(
                    "/v1/source", params={"system": "catalog", "kind": "post", "ref_id": "x"}
                ).status_code
                == 401
            )
        for selector in (
            {"id": "record-one", "source": source},
            {},
            {"source": "one:two"},
            {"source": {"system": "one"}},
        ):
            with pytest.raises(DomainError) as error:
                Tasks(store, "one").show(**selector)
            assert error.value.code == "invalid"
    finally:
        store.close()


def test_source_resolution_and_inspection_observe_the_same_database_revision(tmp_path, monkeypatch):
    path = tmp_path / "fixture.sqlite"
    reader, writer = Store(path), Store(path)
    try:
        original = Tasks(reader).add("First artifact", id="old", source="catalog:sample:42")
        original_context = reader.context("default", "old")
        original_get = reader.get

        def change_after_lookup(workspace, id):
            writer.execute(
                workspace,
                "delete-original",
                "delete",
                {"id": "old"},
                expected_version=original["version"],
            )
            Tasks(writer).add(
                "Replacement attribution", id="replacement", source="catalog:sample:42"
            )
            return original_get(workspace, id)

        monkeypatch.setattr(reader, "get", change_after_lookup)
        assert reader.context("default", source="catalog:sample:42") == original_context
        assert (
            writer.context("default", source="catalog:sample:42")["entity"]["id"] == "replacement"
        )
    finally:
        reader.close()
        writer.close()
