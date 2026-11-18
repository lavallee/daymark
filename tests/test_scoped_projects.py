"""A project belongs to one scope. Its members can live in several."""

import json
import sqlite3
from contextlib import closing

import httpx
import pytest
from fastapi.testclient import TestClient

from daymark import DomainError, Store, Tasks
from daymark.api import create_app
from daymark.cli import main
from daymark.client import Client
from daymark.mcp import call


def test_scoped_project_health_keeps_foreign_facts_and_custom_person_values():
    class Connection(sqlite3.Connection):
        no_json = False

        def execute(self, sql, *args):
            if self.no_json and "json_extract(" in sql:
                raise sqlite3.OperationalError("no such function: json_extract")
            return super().execute(sql, *args)

    with closing(sqlite3.connect(":memory:", factory=Connection)) as conn:
        store = Store(conn)
        t = Tasks(store, "sample")
        t.add("Reader", id="person", kind="person", scope="home")
        t.goal("Printed", id="printed", scope="studio")
        t.project("Edition", id="press", scope="studio", fields="reader:Reader:person", goal="printed")
        t.project("Warehouse", id="warehouse", scope="home")
        t.add("Receive parcel", id="receive", project="warehouse", due="2030-05-14")
        t.add("Pack materials", id="pack", project="press", parent="receive", after=["receive"],
              plan="2030-05-01", values={"reader": "person"})
        t.add("Separately completed", id="old", project="warehouse")
        t.archive("old")
        t.add("Seal envelope", id="seal", project="press", after=["old"])
        expected = next(p for p in t.projects()["projects"] if p["id"] == "press")
        assert (expected["health"]["warnings"], expected["health"]["dependency_conflicts"],
                expected["health"]["blocked"]) == (1, 1, 1)
        for conn.no_json in (False, True):
            assert t.projects(scope="studio")["projects"] == [{**expected, "scope": "studio"}]


def test_scoped_portfolio_retains_counts_and_hides_outside_links(tmp_path, capsys):
    path = tmp_path / "work.sqlite3"
    with closing(Store(path)) as store:
        local = Tasks(store, "trial")
        local.add("Outside coordinator", kind="person", id="home-lead", scope="home")
        local.goal("Outside outcome", id="home-goal", scope="home")
        local.project(
            "Print materials", id="print", lead="home-lead", goal="home-goal", scope="studio"
        )
        local.project("Outside project", id="home-project", scope="home")
        local.project("General project", id="general")
        local.add("Outside prerequisite", id="home-blocker", scope="home")
        local.add(
            "Outside member",
            id="home-member",
            project="print",
            scope="home",
            after=["home-blocker"],
        )
        local.propose("Outside idea", id="home-idea", project="print", scope="home")
        auth = {"token": {"workspace": "trial", "principal": "reviewer"}}
        with (
            TestClient(create_app(store, auth)) as app,
            Client("http://sample.invalid", "token", transport=app._transport) as client,
        ):
            for reader in (local, Tasks(client)):
                full = reader.projects()
                assert set(full) == {"projects", "cursor"}
                project = next(p for p in full["projects"] if p["id"] == "print")
                assert project["facts"]["goal"]["id"] == "home-goal"
                assert project["facts"]["lead"] == "home-lead"
                assert "scope" not in project and "outside_scope" not in project

                view = reader.projects(scope="studio")
                assert view["scope"] == "studio" and view["member_scope"] == "whole_project"
                assert "cursor" not in view
                (row,) = view["projects"]
                assert row["id"] == "print" and row["scope"] == "studio"
                assert row["facts"]["goal"] is None and row["facts"]["lead"] is None
                assert set(row["outside_scope"]) == {"goal", "lead"}
                assert (
                    row["rollup"]["total"],
                    row["rollup"]["proposed"],
                    row["health"]["blocked"],
                ) == (1, 1, 1)
                assert "home-" not in json.dumps(view) and "Outside" not in json.dumps(view)
                assert [p["id"] for p in reader.projects(scope="")["projects"]] == ["general"]
                assert reader.projects(scope="elsewhere")["projects"] == []
                assert (
                    json.loads(
                        call(
                            reader,
                            "daymark_view",
                            {"view": "projects", "scope": "studio", "json": True},
                        )
                    )["scope"]
                    == "studio"
                )
                assert "Counts include all members" in call(
                    reader, "daymark_view", {"view": "projects", "scope": "studio"}
                )
    assert (
        main(["--db", str(path), "--workspace", "trial", "projects", "--scope", "studio", "--json"])
        == 0
    )
    assert json.loads(capsys.readouterr().out)["projects"][0]["id"] == "print"
    assert main(["--db", str(path), "--workspace", "trial", "projects", "--unscoped"]) == 0
    assert "General project" in capsys.readouterr().out


def test_old_or_unconfirmed_project_scope_never_reaches_the_reader():
    requests = []

    def service(request):
        requests.append(request.url.path)
        if request.url.path == "/v1/schema":
            return httpx.Response(
                200, json={"paths": {"/v1/projects": {"get": {"parameters": []}}}}
            )
        pytest.fail("A scoped portfolio was requested from a service that ignores the filter")

    with (
        Client("http://sample.invalid", "token", transport=httpx.MockTransport(service)) as client,
        pytest.raises(DomainError, match="does not support scope") as exc,
    ):
        Tasks(client).projects(scope="studio")
    assert "find kind=project with the same scope" in exc.value.details[0]
    assert requests == ["/v1/schema"]

    def unconfirmed(request):
        if request.url.path == "/v1/schema":
            return httpx.Response(
                200, json={"paths": {"/v1/projects": {"get": {"parameters": [{"name": "scope"}]}}}}
            )
        return httpx.Response(
            200, json={"scope": "studio", "member_scope": "whole_project",
                       "projects": [{"scope": "home", "title": "Outside"}]}
        )

    with (
        Client("http://sample.invalid", "token", transport=httpx.MockTransport(unconfirmed)) as client,
        pytest.raises(DomainError, match="did not confirm"),
    ):
        Tasks(client).projects(scope="studio")
