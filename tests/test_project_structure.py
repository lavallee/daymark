"""Structure a project may opt into: item types and hierarchy, milestones as
targets, goals, custom fields, presets and configuration, project facts and
health. None of it is required: a bare project is a list of tasks. All records
are synthetic.
"""

import json
from datetime import UTC, datetime

import httpx
import pytest
from fastapi.testclient import TestClient

from daymark import DomainError, Store, Tasks
from daymark.api import create_app
from daymark.cli import main
from daymark.client import Client
from daymark.mcp import TOOLS, handle
from daymark.render import compact, line, plan_text

NOW = datetime(2030, 4, 3, 15, 0, tzinfo=UTC)
ZONE = "Europe/London"


def code(expected, call, *args, **kwargs):
    with pytest.raises(DomainError) as error:
        call(*args, **kwargs)
    assert error.value.code == expected
    return error.value


@pytest.fixture
def tasks():
    store = Store(":memory:")
    yield Tasks(store, "w", actor="alice", timezone=ZONE, now=NOW)
    store.close()


def release(tasks):
    """A software release: epics hold stories and bugs, points add up, a beta milestone."""
    tasks.add("Ada", kind="person", id="ada")
    tasks.goal("Launch publicly", id="launch", target="2030-06-01",
               measure="Sign-ups:1000:250:people")  # fmt: skip
    tasks.project("Release 2", id="rel", preset="epics", goal="launch", lead="Ada",
                  target="2030-05-30")  # fmt: skip
    tasks.configure("rel", fields="points:Points:number,risk:Risk:option:low|high,"
                                   "owner2:Reviewer:person,spec:Spec:url,due2:Due:date,"
                                   "flag:Flag:bool,tags:Tags:options:ui|api,note:Note:text")  # fmt: skip
    tasks.add("Beta", id="beta", project="rel", is_milestone=True, due="2030-05-01")
    tasks.add("Accounts", id="acc", project="rel", type="epic")
    tasks.add("Sign-in", id="signin", project="rel", type="story", parent="acc",
              milestone="beta", values={"points": 3}, plan="2030-04-10", target="2030-05-05",
              assignee="ada")  # fmt: skip
    tasks.add("Fix crash", id="crash", project="rel", type="bug", parent="acc",
              milestone="beta", values={"points": 2}, after="signin", plan="2030-04-08",
              target="2030-04-20")  # fmt: skip


# -- a bare project stays a bare list


def test_a_bare_project_adds_nothing_to_its_tasks(tasks):
    tasks.project("Errands", id="errands")
    item = tasks.add("Buy stamps", id="stamps", project="errands", due="2030-04-10")
    assert compact(item) == {
        "id": "stamps", "kind": "task", "title": "Buy stamps", "status": "open", "version": 1,
        "project_id": "errands", "deadline": "2030-04-10",
    }  # fmt: skip
    assert line(item, NOW) == "stamps  Buy stamps  [due 2030-04-10]"
    view = tasks.plan("errands")
    assert view["types"] == [] and view["fields"] == [] and view["milestones"] == []
    assert view["warnings"] == [] and view["parents"] == {}


# -- item types and hierarchy


def test_types_are_declared_by_the_project_and_checked_on_write(tasks):
    release(tasks)
    error = code("invalid", tasks.edit, "crash", type="chore")
    assert error.details == ["epic", "story", "bug", "spike"]
    tasks.project("Plain", id="plain")
    error = code("invalid", tasks.add, "Typed", project="plain", type="bug")
    assert "declares no types" in str(error)
    code("invalid", tasks.configure, "rel", types="a:A,a:B")
    # Removing a type keeps the data and reports it.
    tasks.configure("rel", types="epic:Epic:violet:container,story:Story")
    assert tasks.get("crash")["type"] == "bug"
    assert {"item": "crash", "code": "type_undeclared", "detail": "bug"} in tasks.plan("rel")[
        "warnings"
    ]
    assert tasks.edit("crash", title="Fix the crash")["type"] == "bug"  # Untouched, not re-checked.


def test_parents_roll_up_their_children_in_order(tasks):
    release(tasks)
    tasks.move("crash", first=True)
    view = tasks.plan("rel")
    acc = view["parents"]["acc"]
    assert acc["children"] == ["crash", "signin"]
    assert (acc["total"], acc["done"], acc["sums"]) == (2, 0, {"points": 5})
    assert (acc["start"], acc["end"]) == ("2030-04-08", "2030-05-05")
    tasks.done("crash")
    acc = tasks.plan("rel")["parents"]["acc"]
    assert acc["percent_done"] == 50 and acc["by_status"] == {"completed": 1, "open": 1}
    assert acc["sums"] == {"points": 5}
    # An epic with open children is not ready; finished children release it.
    assert "acc" not in [x["id"] for x in tasks.ready(limit=50)]
    tree = tasks.tree("acc", depth=1)
    assert [c["id"] for c in tree["children"]] == ["crash", "signin"]
    top = tasks.tree("rel", depth=1)
    assert [c["id"] for c in top["children"]] == ["beta", "acc"] and top["children"][1]["more"] == 2
    shallow = tasks.tree("acc", depth=1)
    assert all("more" in c for c in shallow["children"])
    code("invalid", tasks.tree, "acc", depth=0)


def test_hierarchy_guard_rails_are_warnings_not_refusals(tasks):
    release(tasks)
    tasks.add("Elsewhere", id="loose")
    tasks.add("Nested bug", id="nested", project="rel", type="bug", parent="crash")
    tasks.add("Stray", id="stray", project="rel", parent="loose")
    warnings = {(w["item"], w["code"]) for w in tasks.plan("rel")["warnings"]}
    assert ("nested", "parent_not_container") in warnings
    assert ("stray", "parent_elsewhere") in warnings


# -- milestones as targets


def test_milestones_collect_work_and_report_without_completing_themselves(tasks):
    release(tasks)
    beta = next(m for m in tasks.plan("rel")["milestones"] if m["id"] == "beta")
    assert (beta["date"], beta["total"], beta["sums"]) == ("2030-05-01", 2, {"points": 5})
    assert beta["late"] == ["signin"]  # Ends 2030-05-05, after the beta's date.
    assert tasks.plan("rel")["health"]["past_milestone"] == 1
    tasks.done("signin")
    tasks.done("crash")
    beta = next(m for m in tasks.plan("rel")["milestones"] if m["id"] == "beta")
    assert beta["items_done"] is True and beta["reached"] is False  # Never auto-completed.
    tasks.done("beta")
    assert next(m for m in tasks.plan("rel")["milestones"])["reached"] is True


def test_milestone_membership_is_checked(tasks):
    release(tasks)
    code("invalid", tasks.edit, "crash", milestone="acc")  # Not a milestone.
    code("invalid", tasks.edit, "beta", milestone="beta")  # Not itself.
    code("invalid", tasks.edit, "crash", milestone="missing")
    tasks.project("Other", id="other")
    tasks.add("Other beta", id="obeta", project="other", is_milestone=True)
    code("invalid", tasks.edit, "crash", milestone="obeta")  # Not another project's.
    moved = tasks.edit("crash", project="other")
    assert moved["milestone_id"] is None  # Membership stays with the project left behind.
    code("invalid", tasks.engine.execute, "w", "del", "delete", {"id": "beta"},
         expected_version=tasks.get("beta")["version"])  # fmt: skip
    tasks.edit("beta", is_milestone=False)
    assert {"item": "signin", "code": "milestone_invalid", "detail": "beta"} in tasks.plan("rel")[
        "warnings"
    ]
    other = Tasks(tasks.engine, "elsewhere", timezone=ZONE, now=NOW)
    other.project("Rel", id="rel")
    code("invalid", other.add, "Cross", project="rel", milestone="beta")


# -- custom fields


def test_custom_field_values_are_typed_by_the_project(tasks):
    release(tasks)
    good = {"points": 2.5, "risk": "high", "owner2": "ada", "spec": "https://example.invalid/s",
            "due2": "2030-04-30", "flag": True, "tags": ["ui", "api"], "note": "Check logs"}  # fmt: skip
    item = tasks.edit("crash", values=good)
    assert item["values"] == good
    for key, bad in (("points", "3"), ("points", True), ("risk", "medium"), ("owner2", "nobody"),
                     ("spec", "ftp://x"), ("due2", "30/04/2030"), ("flag", "yes"),
                     ("tags", ["ui", "ui"]), ("tags", "ui"), ("note", 4)):  # fmt: skip
        code("invalid", tasks.edit, "crash", values={key: bad})
    error = code("invalid", tasks.edit, "crash", values={"severity": 1})
    assert "severity" not in error.details and "points" in error.details
    assert "points" not in tasks.edit("crash", values={"points": None})["values"]
    plain = Tasks(tasks.engine, "w", timezone=ZONE, now=NOW)
    code("invalid", plain.add, "No project", values={"points": 1})


def test_removed_declarations_keep_values_and_flag_them(tasks):
    release(tasks)
    tasks.edit("crash", values={"risk": "high", "tags": ["api"]})
    tasks.configure("rel", fields="points:Points:number,risk:Risk:option:low")
    item = tasks.get("crash")
    assert item["values"]["tags"] == ["api"] and item["values"]["risk"] == "high"
    codes = {(w["item"], w["code"], w["detail"]) for w in tasks.plan("rel")["warnings"]}
    assert ("crash", "option_removed", "risk") in codes
    assert ("crash", "value_undeclared", "tags") in codes
    # Editing another value keeps the flagged ones as they are.
    assert tasks.edit("crash", values={"points": 8})["values"]["tags"] == ["api"]
    tasks.undo("crash")
    assert tasks.get("crash")["values"]["points"] == 2


def test_values_filter_and_sum(tasks):
    release(tasks)
    big = tasks.engine.query("w", {"field": "values.points", "op": "gte", "value": 3})
    assert [x["id"] for x in big] == ["signin"]
    assert tasks.engine.query("w", {"field": "values.risk", "op": "exists", "value": False})
    code("invalid", tasks.engine.query, "w", {"field": "values.", "op": "eq", "value": 1})
    assert tasks.plan("rel")["rollup"]["sums"] == {"points": 5}


# -- presets and configuration


def test_presets_write_a_starting_configuration_and_nothing_else(tasks):
    presets = tasks.presets()
    assert set(presets) == {"simple", "kanban", "milestones", "epics", "event"}
    assert all(len(p["description"]) < 90 for p in presets.values())
    tasks.project("Move house", id="move", preset="event")
    project = tasks.get("move")
    assert [s["id"] for s in project["stages"]][:2] == ["plan", "book"]
    assert "preset" not in json.dumps(project)  # A project never remembers its preset.
    tasks.project("Simple", id="simple", preset="simple")
    assert tasks.config("simple") == {"stages": [], "types": [], "fields": []}
    code("invalid", tasks.project, "Nope", preset="waterfall")
    # Parts override a preset.
    tasks.project("Board", id="board", preset="kanban", stages="todo:To do,done:Done:completed")
    assert [s["id"] for s in tasks.config("board")["stages"]] == ["todo", "done"]


def test_configuration_reads_and_applies_as_one_document(tasks):
    release(tasks)
    document = tasks.config("rel")
    tasks.project("Release 3", id="rel3")
    tasks.configure("rel3", **document)  # Copied between projects in one write.
    assert tasks.config("rel3") == document
    version = tasks.get("rel3")["version"]
    tasks.configure("rel3", types="")  # Piecemeal: only types change.
    after = tasks.config("rel3")
    assert after["types"] == [] and after["fields"] == document["fields"]
    assert tasks.get("rel3")["version"] == version + 1
    code("invalid", tasks.configure, "rel3")


# -- goals


def test_goals_roll_up_their_projects_and_tasks(tasks):
    release(tasks)
    tasks.add("Write the announcement", id="post", goal="launch", target="2030-06-10")
    view = tasks.goal_plan("launch")
    assert [p["id"] for p in view["projects"]] == ["rel"]
    assert view["tasks"] == ["post"]
    assert view["rollup"]["total"] == 5 and view["at_risk"] == ["post"]
    assert view["measure"] == {"label": "Sign-ups", "target": 1000.0, "current": 250.0,
                               "unit": "people", "percent": 25}  # fmt: skip
    assert view["target"]["end"] == "2030-06-01"
    tasks.done("post")
    listed = tasks.goals()["goals"]
    assert listed[0]["rollup"]["done"] == 1 and listed[0]["rollup"]["at_risk"] == 0
    code("invalid", tasks.edit, "rel", measure={"label": "x", "target": 1})
    code("not_found", tasks.goal_plan, "rel")
    other = Tasks(tasks.engine, "elsewhere", timezone=ZONE, now=NOW)
    code("not_found", other.goal_plan, "launch")
    assert other.goals()["goals"] == []


# -- facts and health


def test_project_facts_and_health_are_reported_not_scored(tasks):
    release(tasks)
    view = tasks.plan("rel")
    assert view["facts"]["lead"] == "ada" and view["facts"]["end"] == "2030-05-30"
    assert view["facts"]["goal"] == {"id": "launch", "title": "Launch publicly"}
    health = view["health"]
    assert health["blocked"] == 1 and health["dependency_conflicts"] == 1
    assert health["unowned"] == 3 and health["beyond_target"] == 0
    assert view["dependency_conflicts"] == [{"item": "crash", "blocker": "signin",
                                             "start": "2030-04-08", "blocker_end": "2030-05-05"}]  # fmt: skip
    assert set(health) == {"percent_done", "overdue", "blocked", "unowned", "past_milestone",
                           "dependency_conflicts", "warnings", "span", "beyond_target"}  # fmt: skip


# -- reading a plan as text


def test_plan_text_groups_by_what_is_asked(tasks):
    release(tasks)
    view = tasks.plan("rel")
    by_stage = plan_text(view, NOW)
    assert "TO DO (4)" in by_stage and "points 5" in by_stage
    by_milestone = plan_text(view, NOW, by="milestone")
    assert "MILESTONE beta Beta (2)" in by_milestone and "NO MILESTONE (1)" in by_milestone
    assert "Epic [epic] (1)" in plan_text(view, NOW, by="type")
    assert "Accounts [acc] (2)" in plan_text(view, NOW, by="parent")
    assert "Ada [ada] (1)" in plan_text(view, NOW, by="assignee")
    assert "Points: 3 (1)" in plan_text(view, NOW, by="points")
    code("invalid", plan_text, view, NOW, by="colour")
    for i in range(300):
        tasks.add(f"Task {i}", project="rel", values={"points": i % 5})
    big = plan_text(tasks.plan("rel"), NOW)
    assert len(big.splitlines()) < 320 and "TO DO (304)" in big


# -- the surfaces


@pytest.fixture
def cli(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    monkeypatch.setenv("DAYMARK_TZ", ZONE)
    for name in ("DAYMARK_DB", "DAYMARK_WORKSPACE", "DAYMARK_ACTOR"):
        monkeypatch.delenv(name, raising=False)

    def run(*argv):
        status = main(list(argv))
        captured = capsys.readouterr()
        return status, captured.out, captured.err

    run("init")
    return run


def test_cli_project_family_and_values(cli, tmp_path):
    assert "event:" in cli("project", "presets")[1]
    assert cli("project", "add", "Release", "--id", "rel", "--preset", "epics")[0] == 0
    assert cli("add", "Ada", "--kind", "person", "--id", "ada")[0] == 0
    assert cli("project", "set", "rel", "--lead", "Ada", "--target", "2030-05-30")[0] == 0
    assert cli("project", "fields", "rel", "points:Points:number,risk:Risk:option:low|high")[0] == 0
    assert cli("project", "types", "rel", "epic:Epic:violet:container,bug:Bug:red")[0] == 0
    assert cli("add", "Beta", "--id", "beta", "--project", "rel", "--is-milestone", "--due",
               "2030-05-01")[0] == 0  # fmt: skip
    out = cli("add", "Crash", "--id", "crash", "--project", "rel", "--type", "bug", "--milestone",
              "beta", "--set", "points=3", "--set", "risk=high", "--json")[1]  # fmt: skip
    item = json.loads(out)
    assert item["values"] == {"points": 3, "risk": "high"} and item["milestone_id"] == "beta"
    status, _, err = cli("edit", "crash", "--set", "points=lots")
    assert status == 5 and "number" in err
    status, _, err = cli("edit", "crash", "--set", "size=3")
    assert status == 5 and "Unknown field" in err
    assert json.loads(cli("edit", "crash", "--set", "risk=", "--json")[1])["values"] == {
        "points": 3
    }
    text = cli("plan", "rel", "--by", "milestone")[1]
    assert "MILESTONE beta Beta (1)" in text and "lead Ada" in text
    assert "crash  Crash" in cli("plan", "rel", "--tree")[1]
    config = json.loads(cli("project", "config", "rel")[1])
    assert [f["id"] for f in config["fields"]] == ["points", "risk"]
    copy = tmp_path / "config.json"
    copy.write_text(json.dumps(config))
    assert cli("project", "add", "Release 3", "--id", "rel3")[0] == 0
    assert json.loads(cli("project", "config", "rel3", "--apply", str(copy))[1]) == config
    copy.write_text(json.dumps({"stages": [], "colour": "red"}))
    assert cli("project", "config", "rel3", "--apply", str(copy))[0] == 5
    assert cli("goal", "add", "Launch", "--id", "g", "--target", "2030-06-01", "--measure",
               "Sign-ups:1000:100")[0] == 0  # fmt: skip
    assert cli("project", "set", "rel", "--goal", "g")[0] == 0
    assert "Sign-ups 100/1000 (10%)" in cli("goal", "show", "g")[1]
    assert cli("goal", "set", "g", "--measure", "Sign-ups:1000:400")[0] == 0
    assert "40%" in cli("goals")[1]
    assert "crash  Crash" in cli("tree", "rel", "--depth", "1")[1]
    status, _, err = cli("project", "set", "rel")
    assert status == 5


def test_mcp_and_http_read_goals_and_structured_plans(tmp_path):
    store = Store(tmp_path / "http.db")
    tasks = Tasks(store, "w", actor="alice", timezone=ZONE, now=NOW)
    release(tasks)

    def tool(name, **arguments):
        message = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                   "params": {"name": name, "arguments": arguments}}  # fmt: skip
        result = handle(tasks, message)["result"]
        return result["isError"], result["content"][0]["text"]

    assert len(json.dumps(TOOLS)) < 6000
    assert "MILESTONE beta" in tool("daymark_view", view="plan", id="rel", by="milestone")[1]
    assert "Launch publicly" in tool("daymark_view", view="goals")[1]
    assert "at risk" not in tool("daymark_view", view="goal", id="launch")[1]
    failed, text = tool("daymark_add", title="Spike", project="rel", type="spike",
                        values={"points": 1}, milestone="beta")  # fmt: skip
    assert not failed and "spike" in text.lower()
    app = create_app(store, {"token": {"workspace": "w", "principal": "alice"}})
    with TestClient(app) as http:

        def forward(request):
            reply = http.request(request.method, request.url.raw_path.decode(),
                                 content=request.content, headers=dict(request.headers))  # fmt: skip
            return httpx.Response(reply.status_code, content=reply.content)

        with Client("http://daymark.invalid", "token",
                    transport=httpx.MockTransport(forward)) as client:  # fmt: skip
            assert client.goals()["goals"][0]["id"] == "launch"
            assert client.goal_plan("launch")["projects"][0]["id"] == "rel"
            assert [c["id"] for c in client.tree("acc")["children"]] == ["signin", "crash"]
            assert client.plan("rel")["health"]["dependency_conflicts"] == 1
            ops = [{"operation_id": "h:1", "action": "update",
                    "payload": {"id": "crash", "values": {"points": 5}},
                    "expected_version": client.get("crash")["version"]}]  # fmt: skip
            first = client.batch(ops, request_id="h", request={"set": 5})
            assert client.batch(ops, request_id="h", request={"set": 5})["replayed"] is True
            assert first["committed"]
        auth = {"Authorization": "Bearer token"}
        assert http.get("/v1/goals/missing/plan", headers=auth).status_code == 404
        assert http.get("/v1/tree/acc?depth=11", headers=auth).status_code == 422
    store.close()
