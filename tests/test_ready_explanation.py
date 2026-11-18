import json
from datetime import UTC, datetime

from fastapi.testclient import TestClient

from daymark import Store, Tasks
from daymark.api import create_app
from daymark.cli import main


def test_explanation_counts_distinct_held_work_and_bounds_each_group():
    store = Store(":memory:")
    try:
        tasks = Tasks(store, "studio", actor="editor", now=datetime.now(UTC))
        tasks.add("Outline", id="outline", move="agent", scope="print")
        tasks.add("Fact check", id="fact", move="agent", scope="print")
        tasks.add("Design", id="design", move="person", scope="print")
        for i in range(8):
            tasks.add(
                f"Approval {i}",
                id=f"app-{i}",
                after=["outline"],
                start="tomorrow",
                move="agent",
                scope="print",
            )
        tasks.add("Ask supplier", id="quote", waiting_on="supplier", move="agent", scope="print")
        tasks.propose("Second design", id="idea", move="agent", scope="print")
        tasks.add("Elsewhere", id="offscope", move="agent")
        other = Tasks(store, "studio", actor="other")
        other.claim("fact")
        view = tasks.explain_ready(move="agent", scope="print", limit=1)
        assert view["ready_count"] == 1 and view["held_count"] == 11
        assert view["ready"] == tasks.ready_summary(move="agent", scope="print", limit=1)
        assert view["reasons"] == {
            "dependencies": {"count": 8, "examples": ["app-0", "app-1", "app-2"]},
            "future_start": {"count": 8, "examples": ["app-0", "app-1", "app-2"]},
            "claimed_elsewhere": {"count": 1, "examples": ["fact"]},
            "waiting": {"count": 1, "examples": ["quote"]},
            "proposed": {"count": 1, "examples": ["idea"]},
        }
        tasks.done("outline")
        assert "dependencies" not in tasks.explain_ready(scope="print")["reasons"]
        assert other.explain_ready(scope="print")["ready_count"] == 2
        with TestClient(
            create_app(store, {"token": {"workspace": "studio", "principal": "other"}}),
            headers={"Authorization": "Bearer token"},
        ) as http:
            remote = http.get(
                "/v1/ready/explanation", params={"scope": "print", "mine": True}
            ).json()
            assert {x["id"] for x in remote["ready"]} == {"fact", "design"}
    finally:
        store.close()


def test_empty_and_matching_cli_shapes(tmp_path, capsys):
    args = ["--db", str(tmp_path / "db")]
    assert main([*args, "init"]) == 0
    assert main([*args, "add", "Draft", "--id", "draft"]) == 0
    capsys.readouterr()
    assert main([*args, "ready", "--explain", "--move", "agent"]) == 0
    assert capsys.readouterr().out.strip() == "No live tasks match this view."
    assert main([*args, "ready", "--explain", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["ready"][0]["id"] == "draft"


def test_ready_uses_recorded_context_before_limiting_and_preserves_dependencies():
    import pytest

    from daymark import DomainError

    store = Store(":memory:")
    try:
        tasks = Tasks(store)
        tasks.add("Phone calls", id="phone", kind="context")
        tasks.add("Desk", id="desk", kind="context")
        tasks.add(
            "Order paper", id="order", scope="studio", contexts=["desk", "phone"], move="agent"
        )
        tasks.add(
            "Proof page", id="proof", scope="studio", contexts=["desk"], move="agent", priority=1
        )
        tasks.add(
            "Call about delivery", id="call", scope="studio", contexts=["phone"], after=["proof"]
        )
        tasks.add("Personal call", id="home", scope="home", contexts=["phone"])
        tasks.add("Untagged", id="ingress", scope="studio", priority=1)
        assert [x["id"] for x in tasks.ready(context="phone", scope="studio", limit=1)] == ["order"]
        view = tasks.explain_ready(context="phone", scope="studio")
        assert view["filters"]["context"] == "phone"
        assert view["reasons"]["dependencies"]["examples"] == ["call"]
        assert tasks.summary(context="phone", scope="studio")["ready"] == 1
        assert tasks.agenda(context="phone", scope="studio")["later_or_undated"] == 2
        with pytest.raises(DomainError, match="Context not found"):
            Tasks(store, "different").ready(context="phone")
        with pytest.raises(DomainError, match="Context not found"):
            tasks.ready(context="order")
    finally:
        store.close()


def test_older_service_cannot_widen_a_context_filtered_explanation():
    import httpx
    import pytest

    from daymark import DomainError
    from daymark.client import Client

    requested = []

    def older(request):
        requested.append(request.url.path)
        if request.url.path.endswith("/schema"):
            return httpx.Response(
                200,
                json={
                    "paths": {
                        "/v1/ready/explanation": {
                            "get": {"parameters": [{"name": "scope"}, {"name": "mine"}]}
                        }
                    }
                },
            )
        return httpx.Response(200, json={"ready_count": 4})

    with Client("https://sample.invalid", "sample", transport=httpx.MockTransport(older)) as client:
        with pytest.raises(DomainError, match="does not support context"):
            Tasks(client).explain_ready(context="phone")
        assert requested == ["/v1/schema"]
        assert Tasks(client).explain_ready() == {"ready_count": 4}
