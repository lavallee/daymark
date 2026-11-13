"""toucan-source-daymark, run as a separate process through its installed entry point
against a temporary database. All records are synthetic.
"""

import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

from daymark import Store, Tasks

NOW = datetime(2030, 4, 3, 15, 0, tzinfo=UTC)
ZONE = "Europe/London"
BOARD = "todo:To do:open:grey,doing:Doing:open:blue,done:Done:completed:green"
ADAPTER = Path(sys.executable).parent / "toucan-source-daymark"


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "daymark.sqlite3"
    store = Store(path)
    tasks = Tasks(store, "home", actor="setup", timezone=ZONE, now=NOW)
    tasks.add("Ada", kind="person", id="ada")
    tasks.add("Urgent", kind="label", id="urgent")
    tasks.project("Website relaunch", id="site", stages=BOARD, description="The new site")
    tasks.add("Elsewhere", id="ext")
    tasks.add("Sitemap", id="map", project="site", plan="2030-04-10", target="2030-04-20",
              assignee="ada", after="ext")  # fmt: skip
    tasks.add("Copy", id="copy", project="site", after="map", due="2030-05-01", start="2030-04-15")
    tasks.add("Launch", id="launch", project="site", milestone=True, due="2030-05-10")
    tasks.propose("Compare hosts", id="idea", project="site")
    tasks.project("Commitments", id="rev-commitments", managed_by="rev")
    tasks.add("Projected", id="projected", project="rev-commitments")
    Tasks(store, "studio", timezone=ZONE, now=NOW).project("Album", id="album")
    store.close()
    return path


def run(db, *args, stdin=None, env=None):
    environment = {**os.environ, "DAYMARK_DB": str(db), "DAYMARK_TZ": ZONE, **(env or {})}
    process = subprocess.run(
        [str(ADAPTER), *args],
        input=json.dumps(stdin) if stdin is not None else "",
        capture_output=True,
        check=False,
        text=True,
        env=environment,
        timeout=60,
    )
    out = json.loads(process.stdout) if process.returncode == 0 else None
    err = json.loads(process.stderr) if process.returncode else None
    return process.returncode, out, err


def head(db):
    store = Store(db)
    try:
        return store.head("home")
    finally:
        store.close()


def test_list_read_write_changes_conflict_replay_and_readonly(db):
    status, out, _ = run(db, "list")
    assert status == 0
    assert out == {"sources": [
        {"ref": "home/rev-commitments", "title": "Commitments", "readonly": True,
         "description": "Daymark project in workspace home"},
        {"ref": "home/site", "title": "Website relaunch", "readonly": False,
         "description": "The new site"},
        {"ref": "studio/album", "title": "Album", "readonly": False,
         "description": "Daymark project in workspace studio"},
    ]}  # fmt: skip

    status, plan, _ = run(db, "read", "home/site")
    assert status == 0 and plan["plan"] == 1 and plan["cursor"] == str(head(db))
    assert plan["source"] == {"ref": "home/site", "title": "Website relaunch",
                              "description": "The new site", "readonly": False}  # fmt: skip
    fields = {f["id"]: f for f in plan["fields"]}
    assert fields["stage"]["options"] == [
        {"id": "todo", "label": "To do", "color": "grey"},
        {"id": "doing", "label": "Doing", "color": "blue"},
        {"id": "done", "label": "Done", "color": "green", "done": True},
    ]
    assert fields["assignee"]["options"] == [{"id": "ada", "label": "Ada"}]
    assert fields["labels"]["options"] == [{"id": "urgent", "label": "Urgent"}]
    assert fields["status"]["writable"] is False
    assert all(f["writable"] for f in plan["fields"] if f["id"] != "status")
    items = {i["id"]: i for i in plan["items"]}
    assert list(items) == ["map", "copy", "launch"]  # Rank order; the proposal is not work yet.
    assert items["map"] == {
        "id": "map", "version": 1, "title": "Sitemap", "detail": None, "stage": "todo",
        "assignee": "ada", "start": "2030-04-10", "end": "2030-04-20", "deadline": None,
        "priority": None, "labels": [], "dependsOn": [], "parent": None, "milestone": False,
        "done": False, "estimate": None, "url": None, "status": "open",
    }  # fmt: skip
    assert items["copy"]["dependsOn"] == ["map"]
    assert (items["copy"]["start"], items["copy"]["end"]) == ("2030-04-15", "2030-05-01")
    assert items["launch"]["milestone"] is True

    cursor = plan["cursor"]
    assert run(db, "changes", "home/site", "--after", cursor)[1] == {
        "cursor": cursor, "changed": False}  # fmt: skip

    changes = {"changes": [
        {"op": "update", "id": "map", "version": 1, "mutation": "m-1",
         "set": {"stage": "done", "end": "2030-04-22", "labels": ["urgent"], "priority": "2",
                 "dependsOn": []},
         "place": {"after": "launch"}},
        {"op": "create", "mutation": "m-2", "set": {"title": "New card", "stage": "doing",
                                                    "assignee": "ada", "estimate": 90},
         "place": {"after": None}},
        {"op": "update", "id": "copy", "version": 7, "mutation": "m-3", "set": {"title": "x"}},
        {"op": "update", "id": "launch", "version": 1, "mutation": "m-4",
         "set": {"url": "https://example.invalid"}},
        {"op": "update", "id": "nope", "version": 1, "mutation": "m-5", "set": {"title": "x"}},
    ]}  # fmt: skip
    status, out, _ = run(db, "write", "home/site", stdin=changes)
    assert status == 0
    results = {r["mutation"]: r for r in out["results"]}
    map_item = results["m-1"]["item"]
    assert results["m-1"]["ok"] and map_item["stage"] == "done" and map_item["done"] is True
    assert (map_item["start"], map_item["end"]) == ("2030-04-10", "2030-04-22")
    assert map_item["labels"] == ["urgent"] and map_item["priority"] == "2"
    assert results["m-2"]["ok"] and results["m-2"]["item"]["stage"] == "doing"
    assert results["m-2"]["item"]["estimate"] == 90
    new_id = results["m-2"]["item"]["id"]
    assert results["m-3"] | {"item": None} == {
        "mutation": "m-3", "ok": False, "error": "conflict", "message": "Changed upstream",
        "item": None}  # fmt: skip
    # The current item, to go back to. The first reorder of a project nobody had
    # ordered ranked its other tasks, once, which moved their versions.
    assert results["m-3"]["item"]["version"] == 2
    assert results["m-4"]["error"] == "invalid" and results["m-4"]["item"]["id"] == "launch"
    assert results["m-5"]["error"] == "not_found" and "item" not in results["m-5"]
    assert out["cursor"] == str(head(db))

    status, after, _ = run(db, "read", "home/site")
    assert [i["id"] for i in after["items"]] == [new_id, "copy", "launch", "map"]
    store = Store(db)
    map_task = store.get("home", "map")
    assert map_task["depends_on"] == ["ext"]  # The dependency outside the plan survives.
    assert map_task["status"] == "completed" and map_task["deadline"] is None
    assert map_task["latest_target"] == {"date": "2030-04-22", "at": None, "timezone": ZONE,
                                         "expression": None}  # fmt: skip
    assert {e["actor"] for e in store.events("home", after=int(cursor))} == {"toucan"}
    store.close()

    assert run(db, "changes", "home/site", "--after", cursor)[1]["changed"] is True
    cursor = after["cursor"]
    assert run(db, "changes", "home/site", "--after", cursor)[1]["changed"] is False

    # Replaying the same write writes nothing and answers with the current items.
    status, again, _ = run(db, "write", "home/site", stdin=changes)
    assert status == 0 and again["cursor"] == cursor
    replayed = {r["mutation"]: r for r in again["results"]}
    assert replayed["m-1"]["ok"] and replayed["m-1"]["item"] == map_item
    assert replayed["m-2"]["item"]["id"] == new_id
    assert run(db, "read", "home/site")[1]["items"] == after["items"]

    # A deadline is moved only by writing it; a plan's end never moves it.
    write = {"changes": [{"op": "update", "id": "copy", "version": 2, "mutation": "m-6",
                          "set": {"end": "2030-05-20"}}]}  # fmt: skip
    copy = run(db, "write", "home/site", stdin=write)[1]["results"][0]["item"]
    assert (copy["end"], copy["deadline"]) == ("2030-05-20", "2030-05-01")
    # An end before the task's "not before" date is refused, not clamped.
    write = {"changes": [{"op": "update", "id": "copy", "version": 3, "mutation": "m-7",
                          "set": {"start": "2030-04-01", "end": "2030-04-02"}}]}  # fmt: skip
    refused = run(db, "write", "home/site", stdin=write)[1]["results"][0]
    assert refused["error"] == "invalid" and refused["item"]["version"] == 3
    write = {"changes": [{"op": "update", "id": "copy", "version": 3, "mutation": "m-8",
                          "set": {"start": "2030-06-01"}}]}  # fmt: skip
    assert run(db, "write", "home/site", stdin=write)[1]["results"][0]["message"] == (
        "start is after end")

    # A project another system manages is read-only.
    status, plan, _ = run(db, "read", "home/rev-commitments")
    assert plan["source"]["readonly"] is True and plan["source"]["managedBy"] == "rev"
    assert not any(f["writable"] for f in plan["fields"])
    status, _, err = run(db, "write", "home/rev-commitments", stdin={"changes": [
        {"op": "update", "id": "projected", "version": 1, "mutation": "r-1",
         "set": {"title": "Edited"}}]})  # fmt: skip
    assert status == 7 and err["error"] == "forbidden"
    store = Store(db)
    assert store.get("home", "projected")["title"] == "Projected"
    store.close()


def test_failures_are_coded_json_on_standard_error(db, tmp_path):
    assert run(db, "read", "home/missing")[0:3:2] == (3, {"error": "not_found",
                                                          "message": "No such project"})
    assert run(db, "read", "studio/site")[2]["error"] == "not_found"  # Workspaces stay apart.
    assert run(db, "read", "no-slash")[2]["error"] == "invalid"
    assert run(db, "changes", "home/site", "--after", "x")[2]["error"] == "invalid"
    assert run(db, "changes", "home/site")[2]["error"] == "invalid"
    assert run(db, "frobnicate")[2]["error"] == "invalid"
    assert run(db, "write", "home/site", stdin=[1])[2]["error"] == "invalid"
    _, out, _ = run(db, "write", "home/site", stdin={"changes": [{"op": "delete"}]})
    assert out["results"][0]["error"] == "invalid"
    missing = tmp_path / "absent.sqlite3"
    assert run(missing, "list") == (0, {"sources": []}, None)
    assert run(missing, "read", "home/site")[2]["error"] == "unavailable"
    assert not missing.exists()  # The adapter never creates a database.
    assert "toucan.plan/1" in subprocess.run([str(ADAPTER)], capture_output=True, check=False,
                                             text=True).stdout  # fmt: skip


def test_the_adapter_runs_without_the_service_dependencies(db):
    blocker = (
        "import sys\n"
        "for name in ('fastapi', 'httpx', 'uvicorn', 'starlette'):\n"
        "    sys.modules[name] = None\n"
        "from daymark.toucan import main\n"
        "raise SystemExit(main(['read', 'home/site']))\n"
    )
    process = subprocess.run(
        [sys.executable, "-c", blocker],
        capture_output=True,
        check=False,
        text=True,
        env={**os.environ, "DAYMARK_DB": str(db)},
        timeout=60,
    )
    assert process.returncode == 0, process.stderr
    assert json.loads(process.stdout)["source"]["ref"] == "home/site"
