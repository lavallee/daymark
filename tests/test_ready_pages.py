"""Ready's bounded read must preserve ordering, snapshots and the full entity."""

import json
import sqlite3
from contextlib import closing
from datetime import UTC, datetime, timedelta

from daymark import Store, Tasks, views


class WithoutJsonFunctions(sqlite3.Connection):
    """Exercise the embedded compatibility path on builds that do have JSON."""

    def execute(self, sql, *args):
        if "json_extract(" in sql:
            raise sqlite3.OperationalError("no such function: json_extract")
        return super().execute(sql, *args)


def test_projected_pages_match_full_entities_with_and_without_json_functions():
    now = datetime.now(UTC)
    for factory in (sqlite3.Connection, WithoutJsonFunctions):
        connection = sqlite3.connect(":memory:", factory=factory)
        try:
            store = Store(connection)
            work = Tasks(store, "work", actor="reader", now=now, timezone="UTC")
            for i in range(48):
                work.add(
                    f"Review sample {i}",
                    id=f"sample-{i}",
                    due=f"+{i % 9}d" if i % 2 else None,
                    scope="proofs" if i % 3 else None,
                    plan=f"+{i % 4}d" if i % 4 == 1 else None,
                    start="+1d" if i % 7 == 1 else None,
                    move=("agent", "person", "answer", None)[i % 4],
                    priority=i % 5,
                    after="sample-0" if i > 1 and i % 7 == 3 else None,
                    note="Synthetic details " * 200,
                )
            work.add("Leaf", id="leaf", parent="sample-11")
            work.propose("Proposal", id="idea")
            work.done("sample-5")
            work.wait("sample-8", "reply")
            work.focus("sample-12")
            work.claim("sample-4")
            Tasks(store, "work", actor="other").claim("sample-7")
            work.add("Weekly inspection", id="cycle", plan="+1d", every="week")
            for owner in ("reader", "other", None):
                for at in (now, now + timedelta(days=2)):
                    # Check each entity independently through the detail read.
                    eligible = [
                        item
                        for item in store.list("work", kind="task")
                        if store.context("work", item["id"], owner=owner, now=at)["readiness"][
                            "ready"
                        ]
                    ]
                    for scope in (None, "", "proofs"):
                        for move in (None, "agent"):
                            expected = sorted(
                                (
                                    item
                                    for item in eligible
                                    if views.in_scope(item, scope)
                                    and (move is None or item["next_move"] == move)
                                ),
                                key=lambda item: views.urgency(item, at)[0],
                            )
                            for limit in (1, 7, 100):
                                assert (
                                    store.ready(
                                        "work",
                                        now=at,
                                        owner=owner,
                                        scope=scope,
                                        next_move=move,
                                        limit=limit,
                                    )
                                    == expected[:limit]
                                )
                    assert store.summary("work", now=at, owner=owner)["ready"] == len(eligible)
        finally:
            connection.close()


def test_limited_page_hydrates_from_the_snapshot_that_ranked_it(tmp_path, monkeypatch):
    path = tmp_path / "ready.sqlite3"
    reader, writer = Store(path), Store(path)
    try:
        before = Tasks(reader).add("First title", id="sample")
        original = views._fetch

        def edit_during_read(store, workspace, ids):
            Tasks(writer).edit("sample", title="Second title")
            return original(store, workspace, ids)

        monkeypatch.setattr(views, "_fetch", edit_during_read)
        assert reader.ready("default", limit=1) == [before]
        assert writer.get("default", "sample")["title"] == "Second title"
    finally:
        reader.close()
        writer.close()


def test_ready_summary_is_bounded_on_the_wire_and_uses_authenticated_claims():
    from fastapi.testclient import TestClient

    from daymark.api import create_app

    store = Store(":memory:")
    try:
        owner = Tasks(store, "sample", actor="worker")
        title = "📝\x00" * 200
        owner.add(title, id="proof", move="agent", priority=1, minutes=12, note="Context belongs in show.")
        owner.claim("proof")
        brief = owner.ready_summary()
        assert brief[0]["title"] == title[:160] + "…"
        assert brief[0]["claimed_by"] == "worker"
        assert brief[0]["estimate_minutes"] == 12
        assert "description" not in brief[0] and "notes" not in brief[0]
        assert owner.ready()[0]["title"] == title
        app = create_app(
            store,
            {
                "owner": {"workspace": "sample", "principal": "worker"},
                "other": {"workspace": "sample", "principal": "another"},
            },
        )
        with TestClient(app) as client:
            endpoint = "/v1/ready?brief=true&mine=true"
            assert client.get(endpoint, headers={"Authorization": "Bearer owner"}).json() == brief
            assert client.get(endpoint, headers={"Authorization": "Bearer other"}).json() == []
    finally:
        store.close()


def test_summary_counts_agenda_buckets_without_hydrating_bodies(monkeypatch):
    now = datetime(2030, 4, 3, 15, tzinfo=UTC)
    store = Store(":memory:")
    try:
        tasks = Tasks(store, "sample", now=now, actor="worker", timezone="UTC")
        tasks.add("Past cutoff", id="late", due="2030-04-02", scope="work")
        tasks.add("Waiting but late", id="late-wait", due="2030-04-02", waiting_on="reply")
        tasks.add("Past plan", id="plan", plan="2030-04-01", scope="work")
        tasks.add("Today", id="today", due="2030-04-03", scope="work")
        tasks.add("Later", id="next", due="2030-04-08", scope="work")
        tasks.add("Outside horizon", id="later", due="2030-04-12")
        tasks.add("Waiting", id="pause", waiting_on="reply", scope="work")
        tasks.propose("Proposal with earlier deadline", id="idea", due="2030-04-01")
        tasks.add("Open", id="open", description="x" * 50_000)
        expected = {}
        for scope in ("work", "", None):
            agenda = tasks.agenda(scope=scope)
            expected[scope] = {
                name: len(agenda[name]) for name in ("overdue", "today", "waiting", "proposed")
            }
            expected[scope]["next_7_days"] = sum(map(len, agenda["upcoming"].values()))
            expected[scope]["ready"] = len(tasks.ready(scope=scope))

        def fail(*args, **kwargs):
            raise AssertionError("Aggregate counts must not fetch full entities")

        monkeypatch.setattr(store, "_rows", fail)
        monkeypatch.setattr(views, "_fetch", fail)
        for scope, counts in expected.items():
            assert tasks.summary(scope=scope) == counts
    finally:
        store.close()


def test_future_ready_reasons_name_the_date_used_for_ranking():
    from daymark import render

    now = datetime(2030, 4, 1, 12, tzinfo=UTC)
    with closing(Store(":memory:")) as store:
        tasks = Tasks(store, "work", now=now, timezone="UTC")
        only_target = tasks.add("Send sample", id="sample", target="+20d")
        tasks.add("Prepare proof", id="proof", due="+40d", plan="+12d")
        tasks.add("Print sheets", id="print", due="+30d", target="+18d")
        tasks.add("Check stock", id="stock")
        tasks.add("Inspect", id="inspect", due="+25d", target="+25d")
        got = [(r["id"], r["why"]) for r in tasks.ready_summary()]
        assert got == [("proof", "planned in 12d"), ("print", "target in 18d"),
                       ("sample", "target in 20d"), ("inspect", "due in 25d"), ("stock", "no date")]
        assert "target 2030-04-21" in render.line(only_target, now)


def test_views_page_ids_with_the_embedding_connections_sqlite_limit():
    import sqlite3
    from contextlib import closing

    with closing(sqlite3.connect(":memory:")) as connection:
        store = Store(connection)
        tasks = Tasks(store, "sample", timezone="UTC", now="2030-10-03T12:00:00Z")
        ids = [f"part-{number:02}" for number in range(42)]
        for id in ids:
            tasks.add("Review sample", id=id, plan="today")
        tasks.add("Prepare collection", id="collect", after=ids)
        connection.setlimit(sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER, 32)
        handoff = tasks.brief("collect")
        assert handoff["blocked_by"] == ids[:5] and handoff["omitted"]["blocked_by"] == 37
        assert {item["id"] for item in tasks.ready(limit=42)} == set(ids)
        assert {item["id"] for item in tasks.agenda()["today"]} == set(ids)


def test_cli_ready_brief_and_mcp_structured_queue_return_the_same_small_page(tmp_path, capsys):
    from daymark.cli import main
    from daymark.mcp_views import read_view

    path = tmp_path / "sample.db"
    with closing(Store(path)) as store:
        tasks = Tasks(store, "default")
        tasks.add("Check " + "labels " * 60, id="check", note="Sample trail " * 650)
        expected = read_view(tasks, {"view": "ready", "json": True}).data["items"]
    cli = ["--db", str(path), "ready"]
    assert main([*cli, "--brief", "--json"]) == 0
    compact = capsys.readouterr().out
    assert json.loads(compact) == expected
    assert "Sample trail" not in compact and len(compact.encode()) < 650
    assert main([*cli, "--json"]) == 0
    assert "Sample trail" in capsys.readouterr().out
    assert main([*cli, "--brief"]) == 0
    assert expected[0]["why"] in capsys.readouterr().out
