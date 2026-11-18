"""An actor can set responsibility independently of a task's claim or project."""

from contextlib import closing

import pytest

from daymark import Store, Tasks
from daymark.mcp import handle


def invoke(tasks, name, **arguments):
    result = handle(tasks, {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                            "params": {"name": "daymark_" + name, "arguments": arguments}})
    return result["result"]


@pytest.mark.parametrize("name", ["add", "edit"])
def test_standalone_assignee_survives_rename_and_rejects_another_person_on_replay(name):
    with closing(Store(":memory:")) as store:
        tasks = Tasks(store, "lab", actor="operator")
        tasks.add("Seal custodian", kind="person", id="custodian")
        tasks.add("Seal custodian", kind="principal", id="automation")
        fields = {"title": "Check the sleeve"} if name == "add" else {"id": "sleeve"}
        if name == "edit":
            tasks.add("Check the sleeve", id="sleeve")
        ambiguous = invoke(tasks, name, **fields, assignee="Seal custodian", op="assign")
        error = ambiguous["structuredContent"]["error"]
        assert error["code"] == "invalid" and error["details"] == ["automation", "custodian"]

        tasks.edit("automation", title="Automated checks")
        assigned = invoke(tasks, name, **fields, assignee="Seal custodian", op="assign")
        receipt = assigned["structuredContent"]
        assert receipt["assignee_id"] == "custodian" and receipt["status"] == "open"
        assert "Assigned to custodian" in assigned["content"][0]["text"]
        tasks.edit("custodian", title="Shift custodian")
        for renamed in (False, True):
            if renamed:
                tasks.edit("automation", title="Seal custodian")
            refused = invoke(tasks, name, **fields, assignee="Seal custodian", op="assign")
            conflict = refused["structuredContent"]["error"]
            assert conflict["code"] == "conflict" and "custodian" in conflict["details"][0]
            assert invoke(tasks, name, **fields, assignee="custodian", op="assign") == assigned
        row = tasks.get(receipt["id"])
        assert row["project_id"] is None and row["claimed_by"] is None and row["waiting_on"] is None
        cleared = invoke(tasks, "edit", id=row["id"], assignee="", op="clear")["structuredContent"]
        assert cleared["assignee_id"] is None
