"""First and returning sessions in synthetic local and HTTP workspaces."""

import json
from datetime import UTC, datetime

from fastapi.testclient import TestClient

from daymark import Store, Tasks
from daymark.api import create_app
from daymark.cli import main
from daymark.render import session_context


def test_brief_prime_identifies_the_session_and_includes_its_own_claim(tmp_path, capsys):
    db = str(tmp_path / "desk.sqlite3")
    args = ["--db", db, "--workspace", "studio", "--as", "reviewer", "--tz", "UTC"]
    assert main([*args, "init"]) == 0
    assert main([*args, "add", "Review draft", "--id", "draft"]) == 0
    assert main([*args, "claim", "draft"]) == 0
    capsys.readouterr()
    assert main([*args, "prime", "--brief"]) == 0
    brief = capsys.readouterr().out
    assert brief.startswith(f"Daymark: {db}, workspace studio, actor reviewer")
    assert "1 ready" in brief and "Review draft" in brief and "Commands" not in brief
    assert main([*args, "prime", "--json"]) == 0
    state = json.loads(capsys.readouterr().out)
    assert state["context"] == {
        "database": db,
        "workspace": "studio",
        "actor": "reviewer",
        "timezone": "UTC",
    }
    assert state["summary"]["ready"] == 1 and state["ready"][0]["id"] == "draft"


def test_remote_principal_picks_up_own_claim_without_local_identity():
    store = Store(":memory:")
    try:
        tasks = Tasks(store, "studio", actor="server-worker")
        tasks.add("Review draft", id="draft")
        tasks.claim("draft")
        assert session_context(tasks, url="https://user:pass@example.invalid:8443/path?q=key") == {
            "endpoint": "https://example.invalid:8443",
            "identity_source": "server credential",
            "timezone": tasks.timezone,
        }
        app = create_app(store, {"token": {"workspace": "studio", "principal": "server-worker"}})
        with TestClient(app, headers={"Authorization": "Bearer token"}) as http:
            assert http.get("/v1/ready").json() == []
            assert [t["id"] for t in http.get("/v1/ready?mine=true").json()] == ["draft"]
            assert http.get("/v1/summary?mine=true").json()["ready"] == 1
    finally:
        store.close()


def test_summary_counts_beyond_ready_page_size():
    store = Store(":memory:")
    try:
        for i in range(1001):
            store.execute(
                "studio",
                f"op-{i}",
                "create",
                {"kind": "task", "title": "Draft", "id": f"draft-{i}"},
                0,
            )
        assert len(store.ready("studio", limit=1000)) == 1000
        assert store.summary("studio")["ready"] == 1001
    finally:
        store.close()


def test_show_explains_each_eligibility_gate_from_the_same_perspective_as_ready():
    store = Store(":memory:")
    try:
        now = datetime.now(UTC)
        tasks = Tasks(store, "studio", actor="worker", now=now, timezone="UTC")
        other = Tasks(store, "studio", actor="reviewer", now=now, timezone="UTC")
        tasks.add("Collect samples", id="collect")
        tasks.add("Review samples", id="review", after=["collect"], start="tomorrow")
        tasks.add("Review a section", id="section", parent="review")
        tasks.add("Routine check", id="check", every="week", plan="tomorrow")
        tasks.add("Consider a second draft", id="idea", proposed=True)
        tasks.claim("collect")

        def reasons(id, reader=tasks):
            state = reader.show(id)["readiness"]
            assert state["ready"] == (id in {item["id"] for item in reader.ready()})
            return set(state["reasons"])

        assert reasons("review") == {"active_children", "future_start", "dependencies"}
        assert tasks.show("review")["readiness"]["active_children"] == ["section"]
        assert reasons("check") == {"future_round"}
        assert reasons("idea") == {"proposed"}
        assert reasons("collect") == set()
        assert reasons("collect", other) == {"claimed_elsewhere"}
        tasks.done("collect")
        tasks.done("section")
        assert reasons("review") == {"future_start"}
        tasks.edit("review", start="")
        assert reasons("review") == set()
    finally:
        store.close()


def test_brief_json_limits_titles_memories_and_history_but_keeps_retrieval_ids(tmp_path, capsys):
    db = tmp_path / "long.db"
    store = Store(db)
    try:
        tasks = Tasks(store)
        tasks.add("A" * 2000, id="draft", description="x" * 30_000, note="y" * 2000)
        for i in range(7):
            tasks.add("m" * 1000, kind="memory", id=f"fact-{i}")
    finally:
        store.close()
    assert main(["--db", str(db), "prime", "--brief", "--json"]) == 0
    raw = capsys.readouterr().out
    state = json.loads(raw)
    assert len(raw.encode()) < 4000
    assert state["ready"][0]["title"] == "A" * 160 + "…"
    assert state["next_memory"] == "fact-4"
    assert [r["id"] for r in state["memories"]] == [f"fact-{i}" for i in range(5)]
    assert (
        main(
            ["--db", str(db), "find", "--kind", "memory", "--after", state["next_memory"], "--json"]
        )
        == 0
    )
    assert [r["id"] for r in json.loads(capsys.readouterr().out)["items"]] == ["fact-5", "fact-6"]
