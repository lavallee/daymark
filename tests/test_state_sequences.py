"""Bounded synthetic operation histories against a small independent reference model.

This exercises command ordering and rejected writes. It is not a concurrency proof;
barrier, lease and subprocess crash tests cover separate boundaries.
"""

from __future__ import annotations

import random

import pytest

from daymark import DomainError, Store


@pytest.mark.parametrize("seed", [13, 47, 83, 109, 211])
def test_bounded_command_histories_preserve_versions_scope_replay_and_restart(tmp_path, seed):
    randomizer = random.Random(seed)
    path = tmp_path / "history.db"
    store = Store(path)
    reference = {}
    receipts = []
    for workspace in ("one", "two"):
        for number in range(3):
            entity_id = f"work-{number}"
            payload = {"id": entity_id, "kind": "task", "title": f"Draft {number}"}
            operation_id = f"initial-{number}"
            created = store.execute(workspace, operation_id, "create", payload, expected_version=0)
            reference[workspace, entity_id] = {
                "version": 1,
                "title": payload["title"],
                "status": "open",
                "archived": False,
            }
            receipts.append((workspace, operation_id, "create", payload, 0, created))
    for step in range(40):
        workspace, entity_id = randomizer.choice(list(reference))
        expected = reference[workspace, entity_id]
        action = randomizer.choice(["update", "complete", "cancel", "reopen", "stale"])
        payload = {"id": entity_id}
        operation_id = f"step-{step}"
        before = store.get(workspace, entity_id)
        if action == "stale":
            with pytest.raises(DomainError) as error:
                store.execute(
                    workspace,
                    operation_id,
                    "update",
                    {**payload, "title": "stale"},
                    expected_version=expected["version"] - 1,
                )
            assert error.value.code == "conflict"
            assert store.get(workspace, entity_id) == before
            continue
        if action == "update":
            payload["title"] = f"Revision {step}"
        elif (
            action in {"complete", "cancel"} and expected["status"] in {"completed", "cancelled"}
        ) or (action == "reopen" and expected["status"] == "open"):
            continue
        version = expected["version"]
        result = store.execute(workspace, operation_id, action, payload, expected_version=version)
        expected["version"] += 1
        if action == "update":
            expected["title"] = payload["title"]
        elif action == "archive":
            expected["archived"] = True
        else:
            expected["status"] = {"complete": "completed", "cancel": "cancelled", "reopen": "open"}[
                action
            ]
        assert {key: result[key] for key in expected} == expected
        assert (
            store.execute(workspace, operation_id, action, payload, expected_version=version)
            == result
        )
        receipts.append((workspace, operation_id, action, payload, version, result))
        for (scope, identity), model in reference.items():
            actual = store.get(scope, identity)
            assert {key: actual[key] for key in model} == model
    snapshots = {workspace: store.export(workspace) for workspace in ("one", "two")}
    store.close()
    restarted = Store(path)
    imported = Store(tmp_path / "imported.db")
    for workspace, snapshot in snapshots.items():
        imported.import_snapshot(workspace, snapshot, operation_id="transfer", expected_version=0)
        assert restarted.list(workspace, include_archived=True) == imported.list(
            workspace, include_archived=True
        )
    for workspace, operation_id, action, payload, version, result in receipts:
        assert (
            restarted.execute(workspace, operation_id, action, payload, expected_version=version)
            == result
        )
        assert (
            imported.execute(workspace, operation_id, action, payload, expected_version=version)
            == result
        )
    restarted.close()
    imported.close()
