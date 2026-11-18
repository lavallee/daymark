"""A preference and its advice cross a session or service without changing their owner."""

import sqlite3
from contextlib import closing
from datetime import UTC, datetime, timedelta

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


@pytest.mark.parametrize(
    ("reply", "filters"),
    [
        ({"picks": []}, {"scope": ""}),
        ({"scope": "studio", "picks": [{"scope": "home"}]}, {"scope": "studio"}),
        ({"scope": "", "picks": [{"scope": "studio"}]}, {"scope": ""}),
        ({"context": "phone", "picks": [{"context_ids": ["desk"]}]}, {"context": "phone"}),
    ],
)
def test_service_advice_verifies_requested_filters_before_returning_content(reply, filters):
    def service(request):
        assert request.url.path == "/v1/advice"
        return httpx.Response(200, json={**reply, "notes": ["Synthetic unrelated detail"]})

    with Client("http://sample.invalid", "token", transport=httpx.MockTransport(service)) as client:
        with pytest.raises(DomainError) as exc:
            Tasks(client).advise(brief=True, **filters)
        assert exc.value.code == "invalid" and "Synthetic" not in str(exc.value)


def test_advice_retains_the_requested_context_when_only_three_context_ids_are_shown():
    with closing(Store(":memory:")) as store:
        tasks = Tasks(store, "trial")
        contexts = ["desk", "office", "travel", "phone", "review"]
        for context in contexts:
            tasks.add(context, id=context, kind="context")
        tasks.add("Review proof", id="proof", contexts=contexts)
        with (TestClient(create_app(store, {"token": {"workspace": "trial", "principal": "reader"}})) as app,
              Client("http://sample.invalid", "token", transport=app._transport) as client):
            for reader in (tasks, Tasks(client)):
                (row,) = reader.advise(context="phone", scope="", brief=True).picks
                assert row["context_ids"] == ["desk", "office", "phone"] and row["more_contexts"] == 2
                assert reader.advise(context="phone").picks[0]["context_ids"] == contexts


def test_missing_remote_profile_selection_recovers_by_its_inputs_without_forwarding_service_text():
    from daymark.mcp import handle

    def service(request):
        if request.url.path == "/v1/schema":
            return httpx.Response(200, json={"paths": {"/v1/advice": {"post": {}}, "/v1/profiles": {"get": {}}}})
        return httpx.Response(404, json={"detail": "Synthetic private service body"})

    with Client("https://sample.invalid", "token", transport=httpx.MockTransport(service)) as client:
        tasks = Tasks(client)
        for arguments, profile_hint, context_hint in [
            ({"view": "advise", "profile": "missing"}, True, False),
            ({"view": "advise", "context": "missing"}, False, True),
            ({"view": "advise", "profile": "missing", "context": "missing"}, True, True),
            ({"view": "profiles", "profile": "missing"}, True, False),
        ]:
            request = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                       "params": {"name": "daymark_view", "arguments": arguments}}
            response = handle(tasks, request)["result"]
            error = response["structuredContent"]["error"]
            assert response["isError"] and error["code"] == "not_found"
            assert ("view=profiles" in error["recovery"]) == profile_hint
            assert ("kind=context" in error["recovery"]) == context_hint
            assert "Synthetic private service body" not in str(response)
        with pytest.raises(ClientError) as exc:
            tasks.saved_profiles("missing")
        assert exc.value.status_code == 404 and "view=profiles" in exc.value.recovery


def test_open_task_cards_keep_filter_order_across_pages_and_older_adapter_shapes(tmp_path, monkeypatch):
    path = tmp_path / "press.db"
    with closing(Store(path)) as store, closing(Store(path)) as writer:
        tasks, other = Tasks(store, "press"), Tasks(writer, "press")
        tasks.add("Phone", kind="context", id="phone")
        for i in range(7):
            tasks.add(f"Proof {i}", id=f"p{i}", contexts=["phone"] if i < 6 else [],
                      scope="studio" if i < 5 or i == 6 else "home", description="Editing record")
        filters = {"kind": "task", "status": "open", "scope": "studio", "context": "phone"}
        complete = tasks.find("editing", limit=None, **filters)
        search = store.search
        boundaries = []

        def two_at_a_time(*args, **options):
            if options.get("projection") == "card":
                boundaries.append(options.get("after"))
                options["limit"] = 2
            page = search(*args, **options)
            if boundaries == [None]:
                other.add("Concurrent proof", id="p2a", contexts=["phone"], scope="studio",
                          description="Editing record")
            return page

        monkeypatch.setattr(store, "search", two_at_a_time)
        cards = tasks.find("editing", limit=None, projection="card", **filters)
        assert boundaries == [None, "p1", "p3"]
        assert [c["id"] for c in cards] == [e["id"] for e in complete] == ["p0", "p1", "p2", "p3", "p4"]
        assert all(c["updated_at"] and "description" not in c for c in cards)
        assert "p2a" in [e["id"] for e in tasks.find("editing", limit=None, **filters)]
        other.archive("p2a")
        assert tasks.find("editing", limit=-1, projection="card", **filters) == cards[:-1]
        for read in (tasks.ready, tasks.find):
            with pytest.raises(DomainError, match="Choose full entities"):
                read(brief=True, projection="entity")
        with (TestClient(create_app(store, {"token": {"workspace": "press", "principal": "reader"}})) as app,
              Client("http://sample.invalid", "token", transport=app._transport) as client):
            assert Tasks(client).find("editing", limit=None, projection="card", **filters) == cards


def test_brief_methods_keep_planning_facts_and_a_registered_extension_gets_full_records(monkeypatch):
    from dataclasses import replace

    from daymark.render import list_record
    from daymark.strategies.builtin import BUILTINS
    from daymark.strategies.profiles import PROFILES

    class Connection(sqlite3.Connection):
        no_json = False

        def execute(self, sql, *args):
            if self.no_json and "json_extract(" in sql:
                raise sqlite3.OperationalError("no such function: json_extract")
            return super().execute(sql, *args)

    now = datetime(2030, 5, 1, 10, tzinfo=UTC)
    with closing(sqlite3.connect(":memory:", factory=Connection)) as conn:
        store = Store(conn)
        t = Tasks(store, "press", now=now)
        t.add("Reader", kind="person", id="reader")
        t.add("Phone", kind="context", id="phone")
        t.project("Edition", id="press", preset="epics")
        t.goal("Printed", id="goal")
        t.condition("Proof approved", id="go", goal="goal", state="unmet")
        t.add("Proof", id="proof", scope="studio", contexts=["phone"], minutes=15,
              project="press", type="bug", assignee="reader", move="answer", advances=["go"],
              plan="2030-05-01", due="2030-05-02", description="Complete proof history",
              metadata={"source": "synthetic"}, note="Earlier decision",
              checklist=[{"id": "read", "text": "Open the proof"}])
        t.focus("proof")
        t.claim("proof")
        t.add("Keep reviewing", id="again", every="day", plan="2030-05-01", minutes=45)
        t.add("Prepare cover", id="other")
        t.add("Wait for plates", id="wait", waiting_on="press")
        for conn.no_json in (False, True):
            for profile in PROFILES:
                full = t.advise(profile=profile)
                assert full.picks[0]["description"] == "Complete proof history"
                full.picks = [list_record(item, now, line_fields=True) for item in full.picks]
                assert t.advise(profile=profile, brief=True).to_dict() == full.to_dict()
            (card,) = t.ready(projection="card", scope="studio", context="phone")
            assert card["checklist"][0]["text"] == "Open the proof"
            assert not {"notes", "metadata", "description"}.intersection(card)
        conn.no_json = False

        def read_details(view, advice, params):
            proof = next(item for item in view.ready if item["id"] == "proof")
            assert proof["notes"][0]["text"] == "Earlier decision"
            assert proof["description"] == "Complete proof history"
            assert proof["metadata"] == {"source": "synthetic"}
            assert next(item for item in view.open if item["id"] == "proof") == proof
            advice.notes.append(strategies.Note("shortlist", "Read full details."))

        monkeypatch.setitem(strategies._REGISTRY, "shortlist",
                            replace(BUILTINS["shortlist"], apply=read_details, reads=("open",)))
        result = t.advise(profile="plain", settings=["shortlist.size=2"], brief=True)
        assert result.notes[0].text == "Read full details."
        assert all("notes" not in item for item in result.picks)


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


def test_explicit_client_extension_receives_only_the_requested_scope_and_context(monkeypatch):
    def review(view, advice, params):
        advice.notes.append(strategies.Note("open-proof", "Reviewed open tasks.", [x["id"] for x in view.open]))

    extension = strategies.Strategy("open-proof", "hygiene", "Review open work.", "convention", review,
                                    reads=("open",))
    monkeypatch.setitem(strategies._REGISTRY, extension.name, extension)
    with closing(Store(":memory:")) as store:
        local = Tasks(store, "trial")
        local.add("Phone", kind="context", id="phone")
        for key, scope, tags in (("matching", "studio", ["phone"]), ("other-tag", "studio", []),
                                 ("other-scope", "home", ["phone"]), ("unscoped", "", ["phone"])):
            local.add(key, id=key, scope=scope, contexts=tags)
        auth = {"token": {"workspace": "trial", "principal": "reviewer"}}
        with TestClient(create_app(store, auth)) as app, Client(
            "http://sample.invalid", "token", transport=app._transport
        ) as client:
            returned = []

            def inspect(response):
                if response.request.url.path == "/v1/query":
                    response.read()
                    returned.append([row["id"] for row in response.json()])

            client._http.event_hooks["response"].append(inspect)
            for scope, expected in (("studio", "matching"), ("", "unscoped")):
                options = {"profile": "plain", "settings": [extension.name], "scope": scope, "context": "phone"}
                assert local.advise(**options).notes[0].ids == [expected]
                assert Tasks(client).advise(**options).notes[0].ids == [expected]
                assert returned[-1] == [expected]


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
        assert asked.count("/v1/advice") == 1 and "/v1/profiles" not in asked
        assert asked.count("/v1/schema") == 1
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
            # The caller clock can be ahead of the service that evaluated its live claim.
            remote.now = lambda: datetime.now(UTC) + timedelta(hours=2)
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
