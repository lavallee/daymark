"""A person can compare the exact version they reviewed with the current record."""

import json
import sqlite3
from contextlib import closing

import pytest
from fastapi.testclient import TestClient

from daymark import DomainError, Store, Tasks
from daymark.api import create_app
from daymark.cli import main
from daymark.mcp import handle
from daymark.render import comparison_text


def test_net_field_changes_show_the_terms_and_bounded_attributed_notes():
    with closing(Store(":memory:")) as store:
        work = Tasks(store, "samples", actor="reader")
        prefix = "Repeated sample text " * 30
        initial = work.propose("Order two labels", id="labels", description=prefix + "A" * 50)
        work.edit("labels", title="Order two hundred labels", description=prefix + "B" * 50)
        work.edit("labels", priority=1)
        work.edit("labels", priority=0)
        for i in range(5):
            work.note("labels", f"Note {i} " + "x" * 200)
        result = work.changes_since("labels", version=initial["version"])
        assert result["available"] and result["to_version"] == 9
        fields = {change["field"]: change for change in result["fields"]}
        assert set(fields) == {"description", "title"}  # Priority returned to its previous value.
        assert fields["title"]["before"] == "Order two labels"
        assert fields["title"]["after"] == "Order two hundred labels"
        assert fields["description"]["previews"]["after"]["offset"] > 0
        assert (
            "A" * 50 in fields["description"]["before"]
            and "B" * 50 in fields["description"]["after"]
        )
        assert result["notes"]["added"] == 5
        recent = result["notes"]["added_notes"]
        assert len(recent) == 3 and recent[0]["text"].startswith("Note 2")
        assert all(n["truncated"] and n["author"] == "reader" for n in recent)
        assert work.read_note("labels", recent[-1]["id"])["text"].endswith("x" * 200)
        latest = work.changes_since("labels", version=9)
        assert latest["fields"] == [] and "notes" not in latest
        assert "No net field changes" in comparison_text(latest)
        assert "5 added" in comparison_text(result) and "excerpt at" in comparison_text(result)
        for invalid in [0, -1, True, 1.0, 10, "1"]:
            with pytest.raises(DomainError) as error:
                work.changes_since("labels", version=invalid)
            assert error.value.code == "invalid"


def test_sparse_native_history_distinguishes_missing_from_unchanged():
    with closing(Store(":memory:")) as source:
        tasks = Tasks(source, "sample")
        tasks.add("First reading", id="sample")
        tasks.edit("sample", title="Second reading")
        tasks.edit("sample", title="Third reading")
        snapshot = source.export("sample")

    with closing(Store(":memory:")) as destination:
        # Keeping just the final event preserves v2 in its before image, but no v1.
        snapshot["events"] = snapshot["events"][-1:]
        destination.import_snapshot("sample", snapshot, operation_id="restore", expected_version=0)
        tasks = Tasks(destination, "sample")
        absent = tasks.changes_since("sample", version=1)
        assert absent["available"] is False and absent["reason"] == "missing_history"
        assert "fields" not in absent and "missing" in comparison_text(absent)
        present = tasks.changes_since("sample", version=2)
        assert present["fields"] == [
            {"field": "title", "before": "Second reading", "after": "Third reading"}
        ]
        assert tasks.changes_since("sample", version=3)["fields"] == []


def test_recreated_id_or_inconsistent_snapshot_cannot_substitute_for_a_reviewed_version():
    with closing(Store(":memory:")) as store:
        tasks = Tasks(store, "sample")
        tasks.add("First lifetime", id="same")
        store.execute("sample", "delete-old", "delete", {"id": "same"}, 1)
        tasks.add("New lifetime", id="same")
        result = tasks.changes_since("same", version=1)
        assert result["available"] is False and result["reason"] == "ambiguous_history"
        assert "First lifetime" not in json.dumps(result)


def test_guarded_comparison_through_cli_http_and_mcp_uses_the_same_workspace_snapshot(
    tmp_path, capsys
):
    db = tmp_path / "records.sqlite"
    source = "sample:label:two/labels"
    with closing(Store(db)) as store:
        first = Tasks(store, "first")
        second = Tasks(store, "second")
        for work, title in ((first, "Two labels"), (second, "Unrelated reading")):
            work.propose(title, id="intake/labels", source=source)
            work.edit("intake/labels", title="Revised " + title)
        expected = first.changes_since(source=source, version=1)
        with TestClient(
            create_app(store, {"fixture": {"workspace": "first", "principal": "reviewer"}}),
            headers={"Authorization": "Bearer fixture"},
        ) as http:
            assert (
                http.get(
                    "/v1/comparison", params={"entity_id": "intake/labels", "since_version": 1}
                ).json()
                == expected
            )
            original = {"system": "sample", "kind": "label", "ref_id": "two/labels"}
            assert (
                http.get(
                    "/v1/source", params={**original, "detail": "changes", "since_version": 1}
                ).json()
                == expected
            )
            assert (
                http.get(
                    "/v1/source", params={**original, "detail": "show", "since_version": 1}
                ).status_code
                == 422
            )
        req = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "daymark_view",
                "arguments": {"view": "show", "source": source, "json": True, "since_version": 1},
            },
        }
        assert handle(first, req)["result"]["structuredContent"] == expected
        req["params"]["arguments"]["brief"] = True
        assert handle(first, req)["result"]["isError"]
    assert (
        main(
            [
                "--db",
                str(db),
                "--workspace",
                "first",
                "show",
                "--source",
                source,
                "--since-version",
                "1",
                "--json",
            ]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out) == expected


def test_sqlite_without_json_functions_has_the_same_comparison():
    class WithoutJson(sqlite3.Connection):
        def execute(self, query, *args):
            if "json_extract(" in query:
                raise sqlite3.OperationalError("no such function: json_extract")
            return super().execute(query, *args)

    with closing(sqlite3.connect(":memory:", factory=WithoutJson)) as connection:
        work = Tasks(Store(connection))
        work.add("First", id="x")
        work.edit("x", title="Second")
        assert work.changes_since("x", version=1)["fields"] == [
            {"field": "title", "before": "First", "after": "Second"}
        ]


@pytest.mark.parametrize("verb", ["accept", "dismiss", "edit"])
def test_review_token_binds_the_record_even_when_the_id_and_version_are_reused(verb):
    with closing(Store(":memory:")) as store:
        tasks = Tasks(store, "sample", actor="reviewer")
        tasks.propose("Two labels", id="labels", op="original")
        previous = tasks.brief("labels")["review_token"]
        assert previous and tasks.show("labels")["review_token"] == previous
        store.execute("sample", "delete", "delete", {"id": "labels"}, 1)
        tasks.propose("Two hundred labels", id="labels", op="replacement")
        args = {"title": "Reviewed"} if verb == "edit" else {}
        run = getattr(tasks, verb)
        with pytest.raises(DomainError) as refused:
            run("labels", expected_version=1, review_token=previous, op="try", **args)
        assert refused.value.code == "conflict" and tasks.get("labels")["status"] == "proposed"
        current = tasks.brief("labels")["review_token"]
        assert current != previous
        tasks.add("Unrelated request", id="other")
        assert tasks.brief("labels")["review_token"] == current
        committed = run("labels", review_token=current, op="new-decision", **args)
        assert committed["version"] == 2
        assert run("labels", review_token=current, op="new-decision", **args) == committed
        with pytest.raises(DomainError) as wrong_replay:
            run("labels", review_token=previous, op="new-decision", **args)
        assert wrong_replay.value.code == "conflict"


def test_snapshot_review_token_survives_native_round_trip_but_requires_evidence():
    with closing(Store(":memory:")) as store:
        work = Tasks(store, "sample")
        work.add("First sample", id="sample")
        work.edit("sample", title="Second sample")
        token = work.brief("sample")["review_token"]
        snapshot = store.export("sample")
    with closing(Store(":memory:")) as restored:
        # Native imports can arrive out of event insertion order; logical cursors survive.
        snapshot["events"].reverse()
        restored.import_snapshot("sample", snapshot, operation_id="restore", expected_version=0)
        assert Tasks(restored, "sample").show("sample")["review_token"] == token
    with closing(Store(":memory:")) as restored:
        snapshot["events"] = []
        restored.import_snapshot("sample", snapshot, operation_id="restore", expected_version=0)
        tasks = Tasks(restored, "sample")
        assert tasks.brief("sample")["review_token"] is None
        with pytest.raises(DomainError) as refused:
            tasks.edit("sample", review_token=token, title="Stale request")
        assert refused.value.code == "conflict"
        tasks.edit("sample", expected_version=2, title="Fresh request")
        assert tasks.show("sample")["review_token"]


def test_decision_token_from_another_workspace_refuses_and_cli_replays(tmp_path, capsys):
    db = tmp_path / "review.sqlite"
    with closing(Store(db)) as store:
        for ws in ("sample", "other"):
            Tasks(store, ws).propose("Retain one sample", id="intake", op="identical-op")
        first = Tasks(store, "sample").brief("intake")["review_token"]
        other = Tasks(store, "other").brief("intake")["review_token"]
    base = ["--db", str(db), "--workspace", "sample"]
    assert main([*base, "accept", "intake", "--if-review", other]) == 4
    result = [
        *base,
        "accept",
        "intake",
        "--if-review",
        first,
        "--if-version",
        "1",
        "--op",
        "decision",
        "--json",
    ]
    assert main(result) == 0 and json.loads(capsys.readouterr().out)["status"] == "open"
    assert main(result) == 0 and json.loads(capsys.readouterr().out)["version"] == 2
