"""A human decision should apply to the evidence and task that were reviewed."""

import json

import pytest

from daymark import DomainError, Store, Tasks
from daymark.cli import main
from daymark.mcp import call
from daymark.render import brief_text, show_text


@pytest.mark.parametrize("decision", ["accept", "dismiss"])
def test_decision_preserves_consent_under_edits_and_retries(decision):
    store = Store(":memory:")
    try:
        intake, human = (
            Tasks(store, "sample", actor="intake"),
            Tasks(store, "sample", actor="decider"),
        )
        store.set_policy("sample", deciders=["decider"])
        original = intake.propose(
            "Confirm a sample",
            id="proof",
            source="catalog:drawing:42",
            waiting_on="supplier reply",
            move="agent",
        )
        review = human.brief("proof")
        assert review["version"] == original["version"]
        assert review["waiting_on"] == "supplier reply"
        assert "source catalog:drawing:42" in brief_text(
            review
        ) and "source catalog:drawing:42" in show_text(human.show("proof"))
        new = intake.edit("proof", title="Inspect a different sample")
        choose = getattr(human, decision)
        with pytest.raises(DomainError) as error:
            choose("proof", expected_version=review["version"], op="choice")
        assert error.value.code == "conflict" and intake.get("proof") == new
        with pytest.raises(DomainError) as denied:
            getattr(intake, decision)("proof", expected_version=new["version"])
        assert denied.value.code == "forbidden"
        decided = choose("proof", expected_version=new["version"], op="choice")
        assert choose("proof", expected_version=new["version"], op="choice") == decided
        assert decided["status"] == ("waiting" if decision == "accept" else "cancelled")
        assert not human.ready() and human.take(op="pickup") is None
        if decision == "accept":
            human.resume("proof")
            assert human.take(op="new-pickup")["id"] == "proof"
    finally:
        store.close()


def test_guarded_cli_and_mcp_decisions_use_the_reviewed_version(tmp_path, capsys):
    path = str(tmp_path / "example.sqlite")
    base = ["--db", path, "--as", "decider"]
    assert (
        main([*base, "propose", "Proof sample", "--id", "proof", "--source", "catalog:drawing:v2"])
        == 0
    )
    capsys.readouterr()
    assert main([*base, "show", "proof", "--brief", "--json"]) == 0
    review = json.loads(capsys.readouterr().out)
    assert main([*base, "edit", "proof", "--title", "Proof revised sample"]) == 0
    capsys.readouterr()
    assert main([*base, "accept", "proof", "--if-version", str(review["version"])]) == 4
    assert "conflict" in capsys.readouterr().err
    store = Store(path)
    try:
        decision = Tasks(store, actor="decider")
        with pytest.raises(DomainError) as stale:
            call(
                decision,
                "daymark_act",
                {"action": "dismiss", "id": "proof", "expected_version": review["version"]},
            )
        assert stale.value.code == "conflict"
        assert "cancelled" in call(
            decision,
            "daymark_act",
            {
                "action": "dismiss",
                "id": "proof",
                "expected_version": decision.get("proof")["version"],
            },
        )
    finally:
        store.close()


def test_many_source_references_remain_recoverable_from_full_detail():
    store = Store(":memory:")
    try:
        refs = [{"system": "catalog", "kind": "sample", "id": str(i)} for i in range(5)]
        store.execute(
            "sample",
            "create",
            "create",
            {"kind": "task", "id": "proof", "title": "Proof sample", "source_refs": refs},
            0,
        )
        t = Tasks(store, "sample")
        assert t.brief("proof")["source_refs"] == refs[:3]
        assert t.brief("proof")["omitted"]["source_refs"] == 2
        assert all(f"source catalog:sample:{i}" in show_text(t.show("proof")) for i in range(5))
    finally:
        store.close()
