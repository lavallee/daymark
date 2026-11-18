"""Project membership selects candidates before the shared readiness rule and its limit."""

import json
from contextlib import closing

import httpx
import pytest
from fastapi.testclient import TestClient

from daymark import DomainError, Store, Tasks
from daymark.api import create_app
from daymark.cli import main
from daymark.client import Client
from daymark.mcp import call


def test_project_readiness_is_exact_across_scopes_and_surfaces(tmp_path, capsys):
    db = tmp_path / "work.sqlite3"
    with closing(Store(db)) as store:
        t = Tasks(store, "trial", actor="reader")
        t.project("Print program", id="press", scope="studio")
        t.add("Phone", kind="context", id="phone")
        t.add("Other studio work", id="other", scope="studio", priority=1)
        t.add("Check settings", project="press", id="settings", move="agent")
        t.add("Bring swatches", project="press", id="swatches", scope="home", contexts=["phone"])
        t.add("Read guide", project="press", id="later", scope="studio", start="tomorrow")
        t.add("Await cover", project="press", id="wait", waiting_on="printer")
        t.propose("Fold guide", project="press", id="suggested")
        t.add("Finish proof", project="press", id="proof", after=["other"])
        t.add("Prepare packet", project="press", id="container")
        t.add("Separate child", parent="container", id="child", scope="studio")
        t.add("Not a project member", parent="press", id="parent-only")
        t.add("Held by coworker", project="press", id="claimed")
        Tasks(store, "trial", actor="coworker").claim("claimed")
        before = store.head("trial")
        auth = {"token": {"workspace": "trial", "principal": "reader"}}
        with (
            TestClient(create_app(store, auth)) as app,
            Client("http://sample.invalid", "token", transport=app._transport) as client,
        ):
            for reader in (t, Tasks(client)):
                ordinary = reader.ready(project="press")
                assert {r["id"] for r in ordinary} == {"settings", "swatches"}
                assert all(r["project_id"] == "press" for r in ordinary)
                assert reader.ready(project="press", scope="studio") == []
                assert [
                    r["id"] for r in reader.ready(project="press", scope="home", context="phone")
                ] == ["swatches"]
                assert [r["id"] for r in reader.ready(project="press", scope="", move="agent")] == [
                    "settings"
                ]
                brief = reader.ready_summary(project="press", limit=1)
                explanation = reader.explain_ready(project="press", limit=1)
                assert brief == explanation["ready"] and brief[0]["project_id"] == "press"
                assert explanation["filters"]["project"] == "press"
                assert (explanation["ready_count"], explanation["held_count"]) == (2, 6)
                assert {k: v["examples"] for k, v in explanation["reasons"].items()} == {
                    "future_start": ["later"],
                    "waiting": ["wait"],
                    "proposed": ["suggested"],
                    "dependencies": ["proof"],
                    "active_children": ["container"],
                    "claimed_elsewhere": ["claimed"],
                }
                result = json.loads(
                    call(
                        reader, "daymark_view", {"view": "ready", "project": "press", "json": True}
                    )
                )
                assert {r["id"] for r in result["items"]} == {"settings", "swatches"}
                for wrong in ("missing", "phone"):
                    for verb in (reader.ready, reader.explain_ready):
                        with pytest.raises(DomainError, match="Project not found") as err:
                            verb(project=wrong)
                        assert err.value.code == "not_found" and "kind project" in err.value.details[0]
                    with pytest.raises(DomainError, match="Project not found"):
                        reader.take(project=wrong, op=f"absent-{wrong}")
                    assert store.receipt("trial", f"absent-{wrong}") is None
        for bad in ("missing", "phone"):
            with pytest.raises(DomainError, match="Project not found"):
                t.ready(project=bad)
        with pytest.raises(DomainError, match="Project not found"):
            Tasks(store, "other-workspace").explain_ready(project="press")
        assert store.head("trial") == before
    assert (
        main(
            [
                "--db",
                str(db),
                "--workspace",
                "trial",
                "--as",
                "reader",
                "ready",
                "--project",
                "press",
                "--explain",
                "--json",
            ]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out)["ready_count"] == 2


def test_old_and_unconfirmed_project_filter_refuse_to_return_a_broader_queue():
    calls = []

    def legacy(request):
        calls.append(request.url.path)
        assert request.url.path == "/v1/schema"
        return httpx.Response(
            200, json={"paths": {"/v1/ready": {"get": {}}, "/v1/ready/explanation": {"get": {}}}}
        )

    with Client("http://sample.invalid", "token", transport=httpx.MockTransport(legacy)) as client:
        for method in (client.ready, client.explain_ready):
            with pytest.raises(DomainError, match="does not support project"):
                method(project="press")
        with pytest.raises(DomainError, match="does not support project"):
            Tasks(client, actor="worker").take(project="press", op="pick")
    assert calls == ["/v1/schema"]

    def wrong(request):
        if request.url.path == "/v1/schema":
            query = {"get": {"parameters": [{"name": "project"}]}}
            return httpx.Response(
                200, json={"paths": {"/v1/ready": query, "/v1/ready/explanation": query}}
            )
        row = {"id": "other-work", "project_id": "home"}
        return httpx.Response(
            200,
            json=[row]
            if request.url.path == "/v1/ready"
            else {"filters": {"project": "press"}, "ready": [row]},
        )

    with Client("http://sample.invalid", "token", transport=httpx.MockTransport(wrong)) as client:
        for method in (client.ready, client.explain_ready):
            with pytest.raises(DomainError, match="outside the requested project"):
                method(project="press")
