"""A preference and its advice cross a session or service without changing their owner."""

import sqlite3
from contextlib import closing

import httpx
import pytest
from fastapi.testclient import TestClient

from daymark import DomainError, Store, Tasks, strategies
from daymark.api import create_app
from daymark.client import Client, ClientError
from daymark.render import advice_text, profile_text


def test_a_competing_completion_cannot_split_ready_picks_and_project_review(tmp_path, monkeypatch):
    path = tmp_path / "studio.db"
    with closing(Store(path)) as reader, closing(Store(path)) as writer:
        tasks = Tasks(reader, "studio")
        other = Tasks(writer, "studio")
        other.project("Layout", id="layout")
        other.add("Check proof", id="proof", project="layout")
        original = reader.ready

        def finish_after_ready(*args, **kwargs):
            picked = original(*args, **kwargs)
            other.done("proof")
            return picked

        monkeypatch.setattr(reader, "ready", finish_after_ready)
        first = tasks.advise(profile="plain", settings=["project-review"])
        assert [item["id"] for item in first.picks] == ["proof"]
        assert "Each open project has an open or waiting task" in first.notes[0].text
        monkeypatch.setattr(reader, "ready", original)
        later = tasks.advise(profile="plain", settings=["project-review"])
        assert later.picks == [] and "no open or waiting task" in later.notes[0].text


def test_advice_reselects_an_active_profile_changed_before_the_snapshot(tmp_path, monkeypatch):
    from daymark.strategies import preferences

    path = tmp_path / "studio.db"
    with closing(Store(path)) as reader, closing(Store(path)) as writer:
        tasks = Tasks(reader, "studio")
        owner = Tasks(writer, "studio")
        owner.add("Check proof", id="proof")
        owner.add("Check cover", id="cover")
        original = preferences.select_profile
        attempts = []

        def select(*args, **kwargs):
            chosen = original(*args, **kwargs)
            if not attempts:
                owner.profile("plain", settings=["shortlist.size=1"])
            attempts.append(chosen)
            return chosen

        monkeypatch.setattr(preferences, "select_profile", select)
        result = tasks.advise()
        assert len(attempts) == 2 and len(result.picks) == 1
        assert result.profile_source["version"] == 1


def test_snapshot_releases_before_extension_execution_and_preserves_caller_transactions(
    tmp_path, monkeypatch
):
    with closing(Store(tmp_path / "reader.db")) as store:
        external = strategies.Strategy(
            "fixture-check-snapshot",
            "hygiene",
            "Inspect snapshot.",
            "convention",
            lambda view, advice, params: advice.notes.append(
                strategies.Note("fixture-check-snapshot", str(store.db.in_transaction))
            ),
            reads=(),
        )
        monkeypatch.setitem(strategies._REGISTRY, external.name, external)
        tasks = Tasks(store, "studio")
        tasks.add("Proof", id="proof")
        assert tasks.advise(profile="plain", settings=[external.name]).notes[0].text == "False"
    with closing(sqlite3.connect(":memory:")) as connection:
        embedded = Tasks(Store(connection), "studio")
        embedded.add("Proof", id="proof")
        assert [p["id"] for p in embedded.advise().picks] == ["proof"]
        assert connection.in_transaction
        connection.rollback()
        assert not embedded.advise().picks
        assert not connection.in_transaction
        assert not connection.execute("SELECT name FROM sqlite_master").fetchall()
    with closing(Store(tmp_path / "reader.db", read_only=True)) as store:
        assert [p["id"] for p in Tasks(store, "studio").advise().picks] == ["proof"]
        assert not store.db.in_transaction


def test_remote_preferences_use_the_authenticated_workspace_and_never_select_client_code(
    tmp_path, monkeypatch
):
    loads = []

    class Extension:
        name = "proof-only"

        def load(self):
            loads.append(self.name)
            return strategies.Strategy(
                self.name,
                "hygiene",
                "Proof review.",
                "convention",
                lambda view, advice, params: advice.notes.append(
                    strategies.Note(self.name, "Proof.")
                ),
                reads=(),
            )

    monkeypatch.setattr(
        "importlib.metadata.entry_points",
        lambda **kw: [Extension()] if kw.get("name") in (None, Extension.name) else [],
    )
    monkeypatch.setattr(strategies, "_REGISTRY", dict(strategies._REGISTRY))
    auth = {
        "token": {"workspace": "studio", "principal": "worker"},
        "two": {"workspace": "private", "principal": "worker"},
    }
    with (
        closing(Store(tmp_path / "service.db")) as store,
        TestClient(create_app(store, auth)) as service,
    ):
        local = Tasks(store, "studio", actor="worker")
        for id in ("a", "b", "c"):
            local.add("Proof " + id, id=id)
        local.claim("a")
        Tasks(store, "studio", actor="another").claim("c")
        Tasks(store, "private").save_profile("other-team", profile="dense")
        local.save_profile(
            "short-review", profile={"strategies": [{"use": "shortlist", "with": {"size": 1}}]}
        )
        store.set_setting(
            "studio",
            "profile",
            {
                "name": "proofs",
                "strategies": [
                    {"use": Extension.name, "with": {"samples": 2}},
                    {"use": "shortlist", "with": {"size": 2}},
                ],
            },
        )
        # No HTTP client can use an installed service extension, even after it was
        # registered by a different caller. Catalog metadata alone does not load it.
        before = store.export("studio")
        calls = []

        def forward(request):
            calls.append((request.method, request.url.path))
            response = service.request(
                request.method,
                request.url.raw_path.decode(),
                content=request.content,
                headers=dict(request.headers),
            )
            return httpx.Response(response.status_code, content=response.content)

        with Client(
            "http://example.invalid",
            "token",
            transport=httpx.MockTransport(forward),
            read_only=True,
        ) as client:
            remote = Tasks(client, "private", actor="another")
            out = remote.advise()
            assert calls == [("POST", "/v1/advice")]
            assert {p["id"] for p in out.picks} == {"a", "b"}
            assert out.profile_source == {
                "kind": "workspace",
                "name": "proofs",
                "version": 1,
                "adjusted": False,
                "execution": "service",
                "workspace": "studio",
                "skipped": [Extension.name],
            }
            assert "built-in strategies" in out.notes[0].text and not loads
            assert remote.profile()["skipped"] == [Extension.name]
            assert remote.profile_catalog()["saved"] == [{"name": "short-review", "version": 1}]
            assert remote.saved_profiles("short-review")["profile"]["name"] == "short-review"
            assert len(remote.advise(profile="short-review").picks) == 1
            assert len(remote.advise(profile="plain", settings=["shortlist.size=1"]).picks) == 1
            with pytest.raises(ClientError) as absent:
                remote.advise(profile="other-team")
            assert absent.value.status_code == 404
            with pytest.raises(DomainError, match="built-in base"):
                remote.advise(settings=[Extension.name])
            assert not loads
            opted = remote.advise(profile="plain", settings=[Extension.name])
            assert opted.profile_source["execution"] == "client-preview"
            assert opted.notes[0].text == "Proof." and loads == [Extension.name]
            assert remote.advise().profile_source["skipped"] == [Extension.name]
            with pytest.raises(DomainError, match="local database"):
                remote.profile("dense")
        assert service.post("/v1/advice", json={}).status_code == 401
        assert service.get("/v1/profiles").status_code == 401
        headers = {"Authorization": "Bearer token"}
        assert (
            service.post("/v1/advice", json={"set": [Extension.name]}, headers=headers).status_code
            == 404
        )
        for body in ({"workspace": "private"}, {"op": "persist"}, {"profile": "plain", "set": [4]}):
            assert service.post("/v1/advice", json=body, headers=headers).status_code == 422
        assert store.export("studio") == before and len(loads) == 1


def test_old_service_reports_plain_fallback_and_retains_one_call_builtin_previews():
    asked = []

    def older(request):
        path = request.url.path
        asked.append(path)
        if path == "/v1/schema":
            return httpx.Response(200, json={"paths": {"/v1/ready": {"get": {}}}})
        if path == "/v1/ready":
            return httpx.Response(200, json=[])
        return httpx.Response(404)

    with Client(
        "http://example.invalid", "token", transport=httpx.MockTransport(older), read_only=True
    ) as client:
        remote = Tasks(client)
        result = remote.advise()
        assert result.profile_source["execution"] == "legacy-client"
        assert "cannot report its workspace profile" in advice_text(result)
        assert "cannot report its workspace profile" in profile_text(remote.profile())
        preview = remote.advise(profile="plain", settings=["shortlist.size=1"])
        assert preview.profile_source["adjusted"] and preview.profile_source["name"] == "plain"
        assert remote.profile_catalog()["saved"] is None
        assert asked.count("/v1/advice") == 1 and asked.count("/v1/profiles") == 1
        with pytest.raises(DomainError, match="Upgrade the service"):
            remote.saved_profiles("short-review")


def test_compact_service_advice_preserves_choices_steps_and_claims_without_detail_bodies(tmp_path):
    from daymark.mcp_views import read_view

    path = tmp_path / "press.db"
    with (
        closing(Store(path)) as store,
        TestClient(
            create_app(store, {"token": {"workspace": "press", "principal": "reviewer"}})
        ) as service,
    ):
        tasks = Tasks(store, "press", actor="reviewer")
        for number in range(4):
            tasks.add(f"Context {number}", kind="context", id=f"c{number}")
        tasks.add(
            "Proof",
            id="proof",
            contexts=["c0", "c1", "c2", "c3"],
            minutes=15,
            description="Synthetic context. " * 1000,
            note="Context for detail. " * 400,
            checklist=[{"id": "step", "text": "Compare pages", "completed": False}],
        )
        tasks.claim("proof")
        tasks.profile("plain", settings=["first-step"])
        calls = []

        def forward(request):
            response = service.request(
                request.method,
                request.url.raw_path.decode(),
                headers=dict(request.headers),
                content=request.content,
            )
            calls.append((request, response))
            return httpx.Response(response.status_code, content=response.content)

        with Client(
            "http://example.invalid", "token", transport=httpx.MockTransport(forward)
        ) as client:
            remote = Tasks(client)
            full = remote.advise()
            assert full.picks[0]["description"] and full.picks[0]["notes"]
            full_size = len(calls[-1][1].content)
            brief = read_view(remote, {"view": "advise", "json": True}).data
            size = len(calls[-1][1].content)
            assert size < full_size / 10
            assert b"Synthetic context" not in calls[-1][1].content
            assert (
                brief["picks"] == read_view(tasks, {"view": "advise", "json": True}).data["picks"]
            )
            (row,) = brief["picks"]
            assert row["claimed_by"] == "reviewer" and row["estimate_minutes"] == 15
            assert row["context_ids"] == ["c0", "c1", "c2"] and row["more_contexts"] == 1
            assert brief["steps"] == {"proof": "Compare pages"}
            assert (
                read_view(remote, {"view": "advise"}).splitlines()[1:]
                == read_view(tasks, {"view": "advise"}).splitlines()
            )
