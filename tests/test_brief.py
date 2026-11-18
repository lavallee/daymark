"""Bounded session pickup retains exact authors, note IDs, ordering and access."""

import json
import shlex
import sqlite3
from contextlib import closing

import pytest
from fastapi.testclient import TestClient

from daymark import DomainError, Store, Tasks
from daymark.api import create_app
from daymark.cli import main
from daymark.mcp import call
from daymark.render import brief_text


def make_notes(count):
    return [
        {
            "id": f"page/{i}",
            "author": "reviewer" if i % 2 else "writer",
            "at": "2030-01-01T18:00:00Z",
            "text": f"Note {i}\x00\n" + "📝" * 3000,
        }
        for i in range(count)
    ]


def seed(store, *, notes=9):
    store.execute(
        "sample",
        "seed",
        "create",
        {
            "id": "proof",
            "kind": "task",
            "title": "Synthetic review",
            "description": "Reference\x00 " * 500,
            "notes": make_notes(notes),
            "next_move": "agent",
        },
        expected_version=0,
    )
    return Tasks(store, "sample", actor="writer")


@pytest.mark.parametrize("compat", [False, True])
def test_notes_page_preserves_authorship_and_can_recover_every_character(compat):
    class CompatibilityConnection(sqlite3.Connection):
        def execute(self, sql, *args):
            if compat and "json_extract(" in sql:
                raise sqlite3.OperationalError("no such function: json_extract")
            return super().execute(sql, *args)

    conn = sqlite3.connect(":memory:", factory=CompatibilityConnection)
    try:
        store = Store(conn)
        tasks = seed(store)
        source = tasks.get("proof")["notes"]
        page = tasks.brief("proof")
        seen = []
        while True:
            first = len(source) - len(seen) - len(page["notes"])
            for offset, preview in enumerate(page["notes"]):
                original = source[first + offset]
                assert preview["text"] == original["text"][:240] and preview["truncated"]
                assert (preview["id"], preview["author"], preview["at"]) == (
                    original["id"],
                    original["author"],
                    original["at"],
                )
                assert tasks.read_note("proof", preview["id"]) == original
            seen = [n["id"] for n in page["notes"]] + seen
            assert page["omitted"]["notes"] == first
            if not first:
                break
            page = tasks.brief("proof", before_note=page["next_before_note"])
        assert seen == [n["id"] for n in source]
        assert page["description"] == tasks.get("proof")["description"][:240]
        assert page["truncated"] == ["description"]
        with pytest.raises(DomainError, match="not found"):
            tasks.brief("proof", before_note="absent")
        with pytest.raises(DomainError, match="not found"):
            tasks.read_note("proof", "absent")
    finally:
        conn.close()


def test_link_counts_and_lease_agree_with_full_detail_and_ready():
    store = Store(":memory:")
    try:
        worker = seed(store, notes=0)
        for i in range(8):
            worker.add("Check sample", parent="proof", id=f"section-{i}")
            worker.add("Deliver prerequisite", id=f"dep-{i}")
            worker.block("proof", f"dep-{i}")
        worker.claim("proof")
        for actor in ("writer", "incoming"):
            viewer = Tasks(store, "sample", actor=actor)
            page, full = viewer.brief("proof"), viewer.show("proof")
            assert page["readiness"]["reasons"] == full["readiness"]["reasons"]
            assert page["blocked_by"] == full["blocked_by"][:5]
            assert page["active_children"] == full["readiness"]["active_children"][:5]
            assert page["omitted"] == {
                "notes": 0,
                "newer_notes": 0,
                "blocked_by": 3,
                "active_children": 3,
            }
            assert page["claim"]["owner"] == "writer" and page["next_move"] == "agent"
    finally:
        store.close()


@pytest.mark.parametrize("compat", [False, True])
def test_tag_previews_expand_to_exact_label_and_context_ids(compat, tmp_path, capsys):
    class Connection(sqlite3.Connection):
        def execute(self, sql, *args):
            if compat and "json_extract(" in sql:
                raise sqlite3.OperationalError("no such function: json_extract")
            return super().execute(sql, *args)

    path = tmp_path / "tagged.db"
    with closing(sqlite3.connect(path, factory=Connection)) as connection:
        store = Store(connection)
        tasks = Tasks(store, "studio", actor="reader")
        tasks.add("Review cover", id="cover", source="press:job:S-41")
        plain = tasks.brief("cover")
        assert {"label_ids", "context_ids"}.isdisjoint(plain)
        links = {}
        for kind in ("label", "context"):
            ids = [f"{kind}-{n}" for n in (5, 2, 4, 3, 1)]
            for id in ids:
                tasks.add(id, kind=kind, id=id)
            links[kind + "s"] = ids
        tasks.edit("cover", **links)
        page = tasks.brief("cover")
        for kind in ("label", "context"):
            key = kind + "_ids"
            assert page[key] == links[kind + "s"][:3] and page["omitted"][key] == 2

        text = call(tasks, "daymark_view", {"view": "show", "source": "press:job:S-41", "brief": True})
        name, args = text.split("Read all labels and contexts: ")[1].splitlines()[0].split(" ", 1)
        assert json.loads(args) == {"view": "show", "source": "press:job:S-41"}
        expanded = call(tasks, name, json.loads(args))
        assert "labels: " + ", ".join(links["labels"]) in expanded
        assert "contexts: " + ", ".join(links["contexts"]) in expanded
        connection.commit()
    assert main(["--db", str(path), "--workspace", "studio", "show", "cover", "--brief"]) == 0
    follow = shlex.split(capsys.readouterr().out.split("Read all labels and contexts: ")[1].splitlines()[0])
    assert main(follow[1:]) == 0
    assert "label-1" in capsys.readouterr().out


def test_http_serializes_a_bounded_payload_and_accepts_exact_note_id():
    store = Store(":memory:")
    try:
        tasks = seed(store, notes=300)
        headers = {"Authorization": "Bearer fixture"}
        app = create_app(store, {"fixture": {"workspace": "sample", "principal": "incoming"}})
        with TestClient(app) as client:
            page = client.get(
                "/v1/brief", params={"entity_id": "proof", "mine": True}, headers=headers
            )
            full = client.get("/v1/context/proof", headers=headers)
            assert full.status_code == page.status_code == 200
            assert len(page.content) < 5000 and len(full.content) > 3_000_000
            seen = page.json()["notes"][-1]
            note = client.get(
                "/v1/note", params={"entity_id": "proof", "note_id": seen["id"]}, headers=headers
            )
            assert note.json() == tasks.read_note("proof", seen["id"])
            assert client.get("/v1/brief?entity_id=proof").status_code == 401
            assert (
                client.get("/v1/note?entity_id=proof&note_id=x", headers=headers).status_code == 404
            )
        with pytest.raises(DomainError):
            Tasks(store, "other").brief("proof")
    finally:
        store.close()


def test_cli_and_mcp_expose_bounded_pickup_and_note_expansion(tmp_path, capsys):
    path = str(tmp_path / "sample.db")
    store = Store(path)
    try:
        tasks = seed(store)
        brief = tasks.brief("proof")
        assert call(
            tasks, "daymark_view", {"view": "show", "id": "proof", "brief": True}
        ) == brief_text(brief, mcp=True)
        expected = tasks.get("proof")["notes"][-1]
        note = call(tasks, "daymark_view", {"view": "show", "id": "proof", "note": expected["id"]})
        assert expected["text"] in note
        old_page = call(
            tasks,
            "daymark_view",
            {"view": "show", "id": "proof", "before_note": brief["next_before_note"]},
        )
        assert tasks.get("proof")["notes"][-4]["id"] in old_page
        with pytest.raises(DomainError, match="Choose"):
            call(
                tasks,
                "daymark_view",
                {"view": "show", "id": "proof", "brief": True, "note": expected["id"]},
            )
    finally:
        store.close()
    base = ["--db", path, "--workspace", "sample", "show", "proof"]
    for options, result in ((["--brief"], brief), (["--note", expected["id"]], expected)):
        assert main([*base, *options, "--json"]) == 0
        assert json.loads(capsys.readouterr().out) == result


def test_printed_note_followups_preserve_selector_and_workspace(tmp_path, capsys):
    path = str(tmp_path / "notes and proofs.db")
    workspace = "night shift"
    task_id = "-press proof"
    ref = "press:job:sample's $41"
    with closing(Store(path)) as store:
        tasks = Tasks(store, workspace, actor="writer")
        tasks.add("Sample proof", id=task_id, source=ref)
        for i in range(5):
            tasks.note(task_id, (f"Margin {i}. " * 40) + f"Evidence {i}")
        hold = tasks.brief(task_id)
        for args in ({"id": task_id}, {"source": ref}):
            text = call(tasks, "daymark_view", {"view": "show", "brief": True, **args})
            for label, want in (("Read older notes: ", "Margin 0"), ("Read full note: ", "Evidence 2")):
                native = next(line.split(label)[1] for line in text.splitlines() if label in line)
                name, params = native.split(" ", 1)
                params = json.loads(params)
                assert all(params[k] == v for k, v in args.items())
                assert want in call(tasks, name, params)
    prefix = ["--db", path, "--workspace", workspace, "--as", "new worker", "--tz", "UTC"]
    for selector in (["--", task_id], ["--source", ref]):
        assert main([*prefix, "show", "--brief", *selector]) == 0
        text = capsys.readouterr().out
        for label, want in (("Read older notes: ", "Margin 0"), ("Read full note: ", "Evidence 2")):
            line = next(line.split(label)[1] for line in text.splitlines() if label in line)
            argv = shlex.split(line)
            assert argv[0] == "daymark" and f"--workspace={workspace}" in argv
            assert main(argv[1:]) == 0
            assert want in capsys.readouterr().out
    # The remote command is a suffix for the same service/credential, including
    # mounted URLs that cannot safely be reconstructed from a display origin.
    remote = brief_text(hold, session={"endpoint": "https://sample.invalid"}, source=ref)
    assert "original daymark --url and --token-env" in remote
    follow = shlex.split(remote.split("Read full note: ", 1)[1].splitlines()[0])
    assert follow == ["show", "--source", ref, "--note", hold["notes"][0]["id"]]


@pytest.mark.parametrize("compat", [False, True])
def test_full_context_projects_related_titles_status_and_reminder_without_note_trails(compat):
    class CompatibilityConnection(sqlite3.Connection):
        def execute(self, sql, *args):
            if compat and "json_extract(" in sql:
                raise sqlite3.OperationalError("no such function: json_extract")
            return super().execute(sql, *args)

    connection = sqlite3.connect(":memory:", factory=CompatibilityConnection)
    try:
        store = Store(connection)
        tasks = Tasks(store, "sample")
        tasks.add("Set of samples", id="set")
        title = "Proof\x00📖"
        tasks.add(title, id="page", parent="set", after="set", note="note " * 1000)
        tasks.add("Closed", id="closed", parent="set", after="set")
        tasks.done("closed")
        tasks.add("Reader", id="reader", kind="person")
        reminder = tasks.remind("set", "2030-01-01T14:00:00Z", recipient="reader")
        result = tasks.show("set")
        assert result["children"] == [
            {"id": "closed", "title": "Closed", "status": "completed"},
            {"id": "page", "title": title, "status": "open"},
        ]
        assert result["dependents"] == ["page"]
        assert result["readiness"]["active_children"] == ["page"]
        assert result["reminders"] == [
            {"id": reminder["id"], "scheduled_at": reminder["scheduled_at"], "state": "pending"}
        ]
    finally:
        connection.close()


def test_brief_identifies_period_and_the_date_that_really_advances():
    store = Store(":memory:")
    try:
        tasks = Tasks(store, now="2030-04-01T12:00:00Z", timezone="UTC")
        tasks.add("Inspect sample", id="inspect", every="week", plan="2030-04-04", due="2030-04-05")
        first = tasks.brief("inspect")
        assert first["series_id"] == "inspect"
        assert first["recurrence"] == {
            "mode": "fixed",
            "rule": "FREQ=WEEKLY",
            "timezone": "UTC",
            "occurrence_index": 0,
            "advances": "planned",
        }
        assert "Round 1 in series inspect" in brief_text(first)
        tasks.done("inspect")
        second = tasks.brief("inspect.1")
        assert second["recurrence"]["occurrence_index"] == 1 and second["series_id"] == "inspect"
        assert (
            second["planned"]["date"] == "2030-04-11" and second["deadline"]["date"] == "2030-04-05"
        )
        tasks.add("Post sample", id="post", every="week", due="2030-04-08")
        assert tasks.brief("post")["recurrence"]["advances"] == "deadline"
        tasks.done("post")
        assert tasks.brief("post.1")["deadline"]["date"] == "2030-04-15"
    finally:
        store.close()


def test_blocker_previews_keep_dependency_order_and_exclude_notes_and_finished_work():
    with closing(Store(":memory:")) as store:
        tasks = Tasks(store, "sample")
        for i in range(8):
            tasks.add(
                f"Prerequisite {i}" + ("📝" * 300 if i == 4 else ""),
                id=f"p{i}",
                note="Detached details " * 450,
                waiting_on="sample reading" if i == 6 else None,
            )
        tasks.done("p7")
        tasks.archive("p5")
        tasks.add("Review result", id="result", after=[f"p{i}" for i in reversed(range(8))])
        brief, full = tasks.brief("result"), tasks.show("result")
        assert brief["blockers"] == full["blockers"]
        assert [row["id"] for row in brief["blockers"]] == ["p6", "p4", "p3", "p2", "p1"]
        assert brief["blockers"][0]["status"] == "waiting"
        assert len(brief["blockers"][1]["title"]) == 160 and brief["blockers"][1]["truncated"]
        assert brief["omitted"]["blocked_by"] == 1
        assert full["blocked_by"][-1] == "p0"
        assert "Detached details" not in json.dumps(brief["blockers"])
        assert "p6  Prerequisite 6 [waiting]" in brief_text(brief)
