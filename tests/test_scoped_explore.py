"""Scope limits the condition evidence displayed while linked work still determines gaps."""

import json
from contextlib import closing

import httpx
import pytest
from fastapi.testclient import TestClient

from daymark import DomainError, Store, Tasks
from daymark.api import create_app
from daymark.cli import main
from daymark.client import Client
from daymark.mcp import handle


def explore(tasks, *, structured, **fields):
    return handle(tasks, {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {
        "name": "daymark_view", "arguments": {"view": "explore", "json": structured, **fields},
    }}, read_only=True)["result"]


def test_related_work_outside_scope_changes_counts_but_keeps_its_details_out_of_the_answer(tmp_path, capsys):
    db = tmp_path / "sample.sqlite"
    with closing(Store(db)) as store:
        tasks = Tasks(store, "lab")
        tasks.goal("Other scope goal", scope="home", id="other-goal")
        tasks.add("Other prerequisite", kind="condition", scope="home", id="other-prereq",
                  goal="other-goal", condition="unmet")
        for id, title, after in (("label", "Label approved", None), ("slot", "Slot booked", None),
                                 ("ready", "Samples ready", ["other-prereq"])):
            tasks.add(title, kind="condition", scope="studio", id=id, goal="other-goal", condition="unknown", after=after)
        tasks.add("Other scope task", id="other-task", scope="home", advances=["label"])
        tasks.propose("Check label", id="proposal", scope="studio", advances=["label"])
        hidden = ("other-goal", "other-prereq", "other-task", "Other scope", "Other prerequisite")
        with TestClient(create_app(store, {"token": {"workspace": "lab", "principal": "observer"}})) as svc, Client(
            "http://sample.invalid", "token", transport=svc._transport,
        ) as client:
            for reader in (tasks, Tasks(client)):
                fields = {"scope": "studio", "id": "other-goal"}
                result = explore(reader, structured=True, **fields)
                assert not result["isError"]
                view = result["structuredContent"]
                assert view["scope"] == "studio" and view["goal"] is None
                assert set(view["frontier"]) == {"label", "slot"} and view["gaps"] == ["slot"]
                rows = {c["id"]: c for c in view["conditions"]}
                assert rows["ready"]["waiting_on"] == []
                assert rows["ready"]["outside_scope"] == {"goal": True, "requires": 1, "waiting_on": 1}
                assert rows["label"]["outside_scope"] == {"goal": True, "advancing": {"open": 1}}
                assert [r["id"] for r in rows["label"]["advancing"]] == ["proposal"]
                text = explore(reader, structured=False, **fields)["content"][0]["text"]
                assert "linked work outside this scope: 1 open" in text and "waiting on 1 outside this scope" in text
                assert not any(s in text or s in json.dumps(view) for s in hidden)
            correction = explore(tasks, structured=True, view="goals", scope="studio")["structuredContent"]["error"]
            assert correction["code"] == "invalid" and "explore with the same scope" in correction["recovery"]
            assert tasks.explore("other-goal")["goal"]["title"] == "Other scope goal"
            assert tasks.explore(scope="")["conditions"] == []
            assert store.head("lab") == 7
    assert main(["--db", str(db), "--workspace", "lab", "explore", "other-goal", "--scope", "studio", "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == view
    assert main(["--db", str(db), "--workspace", "lab", "explore", "--unscoped"]) == 0
    assert capsys.readouterr().out.strip() == "No conditions recorded in this scope."


@pytest.mark.parametrize("advertised", [True, False])
def test_older_services_must_confirm_scoped_exploration_before_any_detail_is_displayed(advertised):
    requests = []

    def old(req):
        requests.append(req.url.path)
        if req.url.path == "/v1/schema":
            params = [{"name": "scope"}] if advertised else []
            return httpx.Response(200, json={"paths": {"/v1/explore": {"get": {"parameters": params}}}})
        assert req.url.params["scope"] == "studio"
        return httpx.Response(200, json={"conditions": [{"title": "Other scope task"}]})

    with Client("http://sample.invalid", "token", transport=httpx.MockTransport(old)) as client:
        with pytest.raises(DomainError, match="scope") as error:
            client.explore(scope="studio")
        assert error.value.code == "invalid" and "Other scope task" not in str(error.value)
    assert requests == (["/v1/schema", "/v1/explore"] if advertised else ["/v1/schema"])
