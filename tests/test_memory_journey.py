"""A remembered preference can be found, corrected and retired by a later session."""

import json
from contextlib import closing

import pytest

from daymark import DomainError, Store, Tasks
from daymark.cli import main
from daymark.mcp import call
from daymark.render import prime_text


def test_shared_memory_session_finds_revises_and_retires_one_fact(tmp_path, capsys):
    path = str(tmp_path / "proofs.sqlite")

    def cli(*args, actor="returning", expected=0):
        assert main(["--db", path, "--workspace", "proofs", "--as", actor, *args]) == expected
        output = capsys.readouterr()
        return output.err if expected else output.out

    with closing(Store(path)) as store:
        work = Tasks(store, "proofs")
        for i in range(12):
            work.add(
                f"Reference for sample {i}",
                kind="memory",
                id=f"00-{i:02}",
                scope="print" if i % 2 else "web",
            )
        work.add("Inspect sample", id="task", move="agent")
        Tasks(store, "other").remember("Separate workspace convention")
    saved = json.loads(
        cli(
            "remember",
            "Use a short proof",
            "--scope",
            "print",
            "--source",
            "review:request:42",
            "--op",
            "remember-proof",
            "--json",
            actor="first",
        )
    )
    assert saved["kind"] == "memory" and saved["scope"] == "print"
    state = json.loads(cli("prime", "--brief", "--json"))
    assert len(state["memories"]) == 5 and state["next_memory"]
    assert all(m["scope"] in {"print", "web"} for m in state["memories"])
    assert saved["id"] not in {m["id"] for m in state["memories"]}
    seen = json.loads(cli("find", "short proof", "--kind", "memory", "--scope", "print", "--json"))
    assert [m["id"] for m in seen["items"]] == [saved["id"]]
    fact = json.loads(cli("show", "--source", "review:request:42", "--brief", "--json"))
    assert fact["id"] == saved["id"] and not fact.get("archived")
    assert "remembered fact" in cli("forget", "task", expected=5)
    new = json.loads(
        cli(
            "edit",
            fact["id"],
            "--title",
            "Use a detailed proof",
            "--if-review",
            fact["review_token"],
            "--json",
        )
    )
    assert new["version"] == fact["version"] + 1
    assert "conflict" in cli("forget", fact["id"], "--if-review", fact["review_token"], expected=4)
    current = json.loads(cli("show", fact["id"], "--brief", "--json"))
    args = ("forget", fact["id"], "--if-review", current["review_token"], "--op", "retire")
    assert "archived" in cli(*args)
    assert "archived" in cli(*args)
    assert (
        json.loads(cli("find", "detailed proof", "--kind", "memory", "--page", "--json"))["items"]
        == []
    )
    assert "archived" in cli("show", "--source", "review:request:42", "--brief")
    with closing(Store(path)) as store:
        history = Tasks(store, "proofs").show(fact["id"])
        assert [(e["action"], e["actor"]) for e in history["history"]] == [
            ("archive", "returning"),
            ("update", "returning"),
            ("create", "first"),
        ]
        assert store.get("proofs", "task")["archived"] is False
        assert "Separate workspace convention" not in prime_text(Tasks(store, "proofs"))
        assert Tasks(store, "other").memories()[0]["title"] == "Separate workspace convention"


def test_forget_replays_an_existing_memory_archive_and_guards_the_actual_write(monkeypatch):
    with closing(Store(":memory:")) as store:
        worker = Tasks(store, actor="reader")
        memory = worker.remember("Print a short proof", op="capture")
        old_archive = worker.archive(memory["id"], op="old-forget")
        assert worker.forget(memory["id"], op="old-forget") == old_archive
        worker.undo(memory["id"])
        assert worker.forget(memory["id"], op="old-forget") == old_archive
        assert not worker.get(memory["id"])["archived"]  # The retry did not archive again.
        worker.add("Temporary preference", kind="memory", id="replace")
        execute = store.execute

        def replace_at_write(workspace, op, action, payload, expected_version, **kwargs):
            if action == "forget":
                execute(workspace, "remove-old", "delete", {"id": "replace"}, 1)
                execute(
                    workspace,
                    "reuse-id",
                    "create",
                    {"id": "replace", "kind": "task", "title": "Inspect a proof"},
                    0,
                )
            return execute(workspace, op, action, payload, expected_version, **kwargs)

        monkeypatch.setattr(store, "execute", replace_at_write)
        with pytest.raises(DomainError, match="remembered fact"):
            worker.forget("replace", op="retire-it")
        assert not worker.get("replace")["archived"]
        assert store.receipt("default", "retire-it") is None


def test_mcp_remember_and_guarded_forget_preserve_kind_and_stale_facts():
    with closing(Store(":memory:")) as store:
        worker = Tasks(store, actor="reader")
        with pytest.raises(DomainError, match="needs text"):
            worker.remember("  ")
        call(
            worker,
            "daymark_remember",
            {
                "text": "Use short proofs",
                "scope": "print",
                "source": "sample:decision:one",
                "op": "save",
            },
        )
        record = worker.brief(source="sample:decision:one")
        worker.edit(record["id"], title="Use detailed proofs")
        with pytest.raises(DomainError, match="changed"):
            call(
                worker,
                "daymark_act",
                {"action": "forget", "id": record["id"], "review_token": record["review_token"]},
            )
        worker.add("Print proof", id="task")
        with pytest.raises(DomainError, match="memory ID"):
            call(worker, "daymark_act", {"action": "forget", "id": "task"})
        assert "archived" in call(
            worker, "daymark_act", {"action": "forget", "id": record["id"], "expected_version": 2}
        )
