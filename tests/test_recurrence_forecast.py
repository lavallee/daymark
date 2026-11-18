import json
import uuid
from contextlib import closing
from datetime import timedelta

import httpx
import pytest
from fastapi.testclient import TestClient

from daymark import DomainError, Store, Tasks
from daymark.api import Identity, create_app
from daymark.cli import main
from daymark.client import Client, ClientError
from daymark.mcp import handle
from daymark.models import normalize_now
from daymark.store import canonical

AT = normalize_now("2026-11-18T16:00:00Z")


@pytest.mark.parametrize("action", ["skip", "done"])
@pytest.mark.parametrize(
    ("scenario", "outcome", "day"),
    [
        ("moved", "would_create_next_round", "2026-10-28"),
        ("claimed", "would_create_next_round", "2026-10-28"),
        ("waiting", "would_create_next_round", "2026-10-28"),
        ("collision", "would_create_next_round", "2026-10-28"),
        ("occupied", "successor_id_reserved", None),
        ("recorded", "next_round_previously_created", None),
        ("deleted", "next_round_previously_created", None),
        ("reused", "next_round_previously_created", None),
        ("count", "recurrence_ended", None),
        ("completion_count", "recurrence_ended", None),
        ("until_equal", "would_create_next_round", "2026-11-25"),
        ("until_late", "recurrence_ended", None),
    ],
)
def test_forecast_matches_guarded_transition_on_native_backup(
    tmp_path, monkeypatch, action, scenario, outcome, day
):
    path, backup = tmp_path / "original.db", tmp_path / "copy.db"
    at = AT + timedelta(seconds=1) if scenario == "until_late" else AT
    monkeypatch.setattr(
        "daymark.store.normalize_now", lambda now=None: normalize_now(at if now is None else now)
    )
    with closing(Store(path)) as store:
        tasks = Tasks(store, timezone="America/New_York", now=AT)
        if scenario.startswith("until"):
            tasks.add(
                "Clean tray",
                id="round",
                due="2026-10-21 09:00",
                every="FREQ=WEEKLY;UNTIL=20261125T160000Z",
                after_completion=True,
            )
        elif scenario == "completion_count":
            tasks.add(
                "Clean tray",
                id="round",
                due="2026-10-21 09:00",
                every="FREQ=WEEKLY;COUNT=2",
                after_completion=True,
            )
            tasks.skip("round")
        else:
            tasks.add(
                "Inspect press",
                id="round",
                plan="2026-10-21",
                due="2026-10-22",
                every=f"FREQ=WEEKLY;COUNT={1 if scenario == 'count' else 5}",
            )
            tasks.edit("round", plan="2026-10-23")
        if scenario == "claimed":
            held = Tasks(store, actor="worker").claim("round", minutes=30)
            assert normalize_now(held["claim_until"]) > at
        if scenario == "waiting":
            tasks.wait("round", on="Delivery")
        if scenario == "collision":
            tasks.add("Unrelated errand", id="round.1")
        if scenario == "occupied":
            reserved = uuid.uuid5(uuid.NAMESPACE_URL, canonical(["default", "round", 1])).hex
            tasks.add("Unrelated errand", id=reserved)
        if scenario in {"recorded", "deleted", "reused"}:
            tasks.skip("round")
            if scenario != "recorded":
                child = tasks.get("round.1")
                store.execute("default", "remove", "delete", {"id": child["id"]}, child["version"])
            if scenario == "reused":
                tasks.add("Unrelated errand", id="round.1")
            tasks.reopen("round")
        before = store.export("default")

    id = "round.1" if scenario == "completion_count" else "round"
    original_bytes = path.read_bytes()
    with closing(Store(path, read_only=True)) as store:
        forecast = Tasks(store, now=at).recurrence_forecast(id, action=action)
        assert store.export("default") == before
    assert path.read_bytes() == original_bytes
    assert normalize_now(forecast["evaluated_at"]) == at
    assert forecast["outcome"] == outcome
    assert main(["--db", str(path), "backup", "--file", str(backup)]) == 0
    with closing(Store(backup)) as store:
        cursor = store.head("default")
        result = store.execute(
            "default",
            "trial",
            "complete" if action == "done" else "skip",
            {"id": id},
            expected_version=forecast["version"],
            actor="coordinator",
        )
        assert result["status"] == forecast["status"]
        assert result["claimed_by"] is result["claim_until"] is None
        assert [event["action"] for event in store.events("default", after=cursor)
                if event["entity_id"] == id] == ["complete" if action == "done" else "skip"]
        assert result["completed_at"] == forecast["completed_at"]
        created = [
            event["entity_id"]
            for event in store.events("default", after=cursor)
            if event["action"] == "recurrence.create"
        ]
        successor = forecast["successor"]
        if day is None:
            assert not created
            assert successor is None
        else:
            assert created == [successor["id"]]
            assert (successor["id"] != "round.1") == (scenario == "collision")
            child = store.get("default", created[0])
            assert {key: child[key] for key in successor if key != "occurrence_index"} == {
                key: value for key, value in successor.items() if key != "occurrence_index"
            }
            assert child["recurrence"]["occurrence_index"] == successor["occurrence_index"]
            if scenario.startswith("until"):
                assert child["deadline"]["at"] == day + "T11:00:00-05:00"
            else:
                assert child["planned"]["date"] == day
                assert child["deadline"]["date"] == "2026-10-22"


def test_cli_forecast_is_opt_in(tmp_path, capsys):
    path = tmp_path / "sample.db"
    with closing(Store(path)) as store:
        tasks = Tasks(store)
        tasks.add("Inspect press", id="round", plan="2026-10-21", every="week")
    args = ["--db", str(path), "show", "round"]
    assert main(args) == 0
    assert "Calendar forecast" not in capsys.readouterr().out
    assert main([*args, "--forecast", "skip"]) == 0
    assert "Would create round.1" in capsys.readouterr().out

    missing = tmp_path / "missing.db"
    assert main(["--db", str(missing), "show", "round", "--forecast", "skip"]) != 0
    assert not missing.exists()


def test_four_missed_rounds_each_require_a_separate_guarded_action(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "daymark.store.normalize_now", lambda now=None: normalize_now(AT if now is None else now)
    )
    path, backup = tmp_path / "original.db", tmp_path / "copy.db"
    with closing(Store(path)) as store:
        Tasks(store, timezone="UTC").add(
            "Inspect press", id="weekly", plan="2026-10-21", every="FREQ=WEEKLY;COUNT=5"
        )
    assert main(["--db", str(path), "backup", "--file", str(backup)]) == 0
    with closing(Store(backup)) as store:
        tasks = Tasks(store, now=AT)
        current = "weekly"
        for n, day in enumerate(("2026-10-28", "2026-11-04", "2026-11-11", "2026-11-18"), 1):
            forecast = tasks.recurrence_forecast(current, action="skip")
            assert forecast["successor"]["planned"]["date"] == day
            store.execute(
                "default",
                f"skip-{n}",
                "skip",
                {"id": current},
                expected_version=forecast["version"],
            )
            current = forecast["successor"]["id"]
            assert tasks.get(current)["planned"]["date"] == day
            assert len(store.list("default", kind="task")) == n + 1
        assert tasks.recurrence_forecast(current, action="done")["outcome"] == "recurrence_ended"


def test_slot_can_change_at_same_source_version(tmp_path):
    path = tmp_path / "shared.db"
    with closing(Store(path)) as writer:
        Tasks(writer).add("Inspect press", id="weekly", plan="2026-10-21", every="week")
        with closing(Store(path, read_only=True)) as reader:
            tasks = Tasks(reader, now=AT)
            before = tasks.recurrence_forecast("weekly", action="done")
            Tasks(writer).add("Unrelated errand", id="weekly.1")
            after = tasks.recurrence_forecast("weekly", action="done")
        assert before["version"] == after["version"]
        assert before["successor"]["id"] == "weekly.1"
        assert after["successor"]["id"] != "weekly.1"


def mcp_result(tasks, **arguments):
    return handle(tasks, {
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": "daymark_view", "arguments": arguments},
    }, read_only=True)["result"]


@pytest.mark.parametrize("action", ["skip", "done"])
def test_independent_mcp_and_authenticated_http_reads(tmp_path, action, monkeypatch, capsys):
    path = tmp_path / "shared.db"
    id, source = "round/%", "fixture:entry:press/1"
    with closing(Store(path)) as writer:
        tasks = Tasks(writer, "work")
        record = tasks.add("Inspect press", id=id, source=source, due="2026-10-21",
                           every="week", after_completion=True)
        huge = "x" * 100_000
        writer.execute("work", "bulk-fixture", "create", {
            **{key: record[key] for key in ("planned", "deadline", "recurrence")},
            "id": "bulk", "kind": "task", "title": "Inspect hopper", "series_id": huge, "occurrence_key": huge,
            "metadata": {"payload": huge},
        }, 0)
        Tasks(writer, "elsewhere").add("Other workspace", id=id)
        before = writer.export("work")
        head = writer.head("work")

        with closing(Store(path, read_only=True)) as mcp, closing(Store(path, read_only=True)) as service:
            reader = Tasks(mcp, "work", now=AT)
            auth = {"sample-token": Identity("work", "reader"),
                    "other-token": Identity("elsewhere", "reader")}
            with TestClient(create_app(service, auth)) as app:
                seen = []

                def forward(request):
                    seen.append((request.method, request.url.path, dict(request.url.params)))
                    return app.request(request.method, request.url.path, params=request.url.params,
                                       headers=request.headers)

                transport = httpx.MockTransport(forward)
                with Client("https://sample.invalid", "sample-token", transport=transport, read_only=True) as client:
                    remote = Tasks(client, "elsewhere", now=AT)
                    expected = reader.recurrence_forecast(id, action=action)
                    for session in (reader, remote):
                        for selector in ({"id": id}, {"source": source}):
                            result = mcp_result(session, view="show", forecast=action, json=True, **selector)
                            assert result["structuredContent"] == expected
                            result = mcp_result(session, view="show", forecast=action, **selector)
                            assert "Would create" in result["content"][0]["text"]
                            ordinary = mcp_result(session, view="show", json=True, **selector)
                            assert "successor" not in ordinary["structuredContent"]
                    for session in (reader, remote):
                        bulk = mcp_result(session, view="show", id="bulk", forecast=action, json=True)
                        assert len(json.dumps(bulk)) < 2048
                    assert app.get("/v1/context", params={"entity_id": id, "forecast": action}).status_code == 401
                    assert app.get("/v1/context", params={"entity_id": id, "forecast": action},
                                   headers={"Authorization": "Bearer other-token"}).status_code == 422
                    with pytest.raises(ClientError) as error:
                        remote.recurrence_forecast(source="fixture:entry:missing", action=action)
                    assert error.value.status_code == 404
                    assert seen[-1] == ("GET", "/v1/source", {
                        "system": "fixture", "kind": "entry", "ref_id": "missing", "detail": "show",
                        "forecast": action, "now": AT.isoformat(),
                    })

                class RoutedClient(Client):
                    def __init__(self, url, token, **options):
                        super().__init__(url, token, transport=transport, **options)

                monkeypatch.setattr("daymark.client.Client", RoutedClient)
                monkeypatch.setenv("DAYMARK_TOKEN", "sample-token")
                assert main(["--url", "https://sample.invalid", "show", "--source", source,
                             "--forecast", action, "--json"]) == 0
                assert json.loads(capsys.readouterr().out)["id"] == id
                assert all(method == "GET" for method, _, _ in seen)
                assert mcp.db.total_changes == service.db.total_changes == 0
        assert writer.head("work") == head
        assert writer.export("work") == before


@pytest.mark.parametrize("selector", [{"id": "round/%"}, {"source": "fixture:entry:round"}])
def test_legacy_service_refuses_forecast_before_task_request(selector):
    def legacy(request):
        assert request.method == "GET"
        assert request.url.path == "/v1/schema"
        return httpx.Response(200, json={"paths": {"/v1/context/{entity_id}": {
            "get": {"parameters": [{"name": "entity_id", "in": "path"}]},
        }}})

    with (
        Client("https://sample.invalid", "token", transport=httpx.MockTransport(legacy)) as client,
        pytest.raises(DomainError, match="does not support forecast"),
    ):
        Tasks(client).recurrence_forecast(action="done", **selector)


@pytest.mark.parametrize("options", [
    {"view": "ready"}, {"brief": True}, {"note": "N"}, {"before_note": "N"},
    {"since_version": 1}, {"source": "fixture:entry:round"},
])
def test_forecast_mcp_rejects_mixed_views_and_selectors(options):
    with closing(Store(":memory:")) as store:
        tasks = Tasks(store)
        tasks.add("Inspect press", id="round", due="2026-10-21", every="week")
        arguments = {"view": "show", "id": "round", "forecast": "skip"} | options
        result = mcp_result(tasks, **arguments)
        assert result["isError"]
        assert result["structuredContent"]["error"]["code"] == "invalid"


@pytest.mark.parametrize("action", ["skip", "done"])
@pytest.mark.parametrize("state", ["proposed", "archived", "completed", "cancelled"])
def test_forecast_matches_transition_refusals(state, action):
    with closing(Store(":memory:")) as store:
        tasks = Tasks(store, now=AT)
        item = tasks.add("Inspect press", id="round", due="2026-10-21", every="week",
                         proposed=state == "proposed")
        if state != "proposed":
            item = {"archived": tasks.archive, "completed": tasks.done,
                    "cancelled": tasks.skip}[state]("round")
        with pytest.raises(DomainError) as forecast:
            tasks.recurrence_forecast("round", action=action)
        with pytest.raises(DomainError) as write:
            store.execute("default", "trial", "complete" if action == "done" else action,
                          {"id": "round"}, expected_version=item["version"])
        assert (forecast.value.code, str(forecast.value)) == (write.value.code, str(write.value))
