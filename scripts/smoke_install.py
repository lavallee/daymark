"""Run this outside a checkout against an installed Daymark distribution."""

import tempfile
from pathlib import Path

from daymark import Store

with tempfile.TemporaryDirectory() as directory:
    store = Store(Path(directory) / "work.db")
    task = store.execute(
        "demo",
        "first-task",
        "create",
        {
            "id": "draft",
            "kind": "task",
            "title": "Draft a proposal",
            "planned": {"date": "2030-04-03", "timezone": "Europe/London"},
        },
        expected_version=0,
    )
    assert task["version"] == 1
    replay = store.execute(
        "demo",
        "first-task",
        "create",
        {
            "id": "draft",
            "kind": "task",
            "title": "Draft a proposal",
            "planned": {"date": "2030-04-03", "timezone": "Europe/London"},
        },
        expected_version=0,
    )
    assert replay == task
    updated = store.execute(
        "demo", "finish-draft", "complete", {"id": "draft"}, expected_version=task["version"]
    )
    assert updated["status"] == "completed"
    assert store.get("demo", "draft")["status"] == "completed"
    assert store.export("demo")["entities"]
print("Installed core: create, replay, complete, read and export passed")
