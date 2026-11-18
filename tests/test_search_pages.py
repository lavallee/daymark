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
