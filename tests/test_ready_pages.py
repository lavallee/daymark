"""Ready's bounded read must preserve ordering, snapshots and the full entity."""

import sqlite3
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
