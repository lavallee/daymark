"""Point a returning worker to a concrete circular wait without changing the graph."""

from contextlib import closing

import pytest

from daymark import Store, Tasks
from daymark.mcp import call
from daymark.render import brief_text, show_text


def test_show_names_a_three_task_mixed_cycle_and_preserves_it_for_explicit_repair(tmp_path):
    with closing(Store(tmp_path / "press.db")) as store:
        work = Tasks(store, "press", actor="worker")
        work.add("Bind edition", id="bind", scope="binding", move="agent")
        work.add("Collate sheets", id="collate", parent="bind", move="agent")
        work.add("Dry ink", id="dry", scope="printing", after="bind", move="agent")
        work.block("collate", "dry")
        assert work.ready() == [] and work.take() is None
        head = store.head("press")
        expected = [
            {"relation": "child", "id": "collate"},
            {"relation": "prerequisite", "id": "dry"},
            {"relation": "prerequisite", "id": "bind"},
        ]
        full, brief = work.show("bind"), work.brief("bind")
        assert full["readiness"]["cycle"] == brief["readiness"]["cycle"] == expected
        for text in [
            show_text(full),
            brief_text(brief),
            call(work, "daymark_view", {"view": "show", "id": "bind", "brief": True}),
        ]:
            assert "bind → child collate → prerequisite dry → prerequisite bind" in text
        assert store.head("press") == head

        with closing(Store(":memory:")) as restored:
            restored.import_snapshot(
                "press", store.export("press"), operation_id="copy", expected_version=0
            )
            returning = Tasks(restored, "press")
            assert returning.brief("bind")["readiness"]["cycle"] == expected
            returning.unblock("dry", "bind")
            assert "cycle" not in returning.brief("bind")["readiness"]
            assert [row["id"] for row in returning.ready()] == ["dry"]
            returning.done("dry")
            assert [row["id"] for row in returning.ready()] == ["collate"]
            returning.done("collate")
            assert [row["id"] for row in returning.ready()] == ["bind"]


@pytest.mark.parametrize("cut", ["done", "cancel", "archive"])
def test_inactive_task_breaks_the_witness_and_state_restoration_can_expose_it(cut):
    with closing(Store(":memory:")) as store:
        work = Tasks(store, "one")
        work.add("Bind edition", id="bind")
        work.add("Check proof", id="check", parent="bind", after="bind")
        getattr(work, cut)("check")
        assert "cycle" not in work.brief("bind")["readiness"]
        assert "cycle" not in work.show("check")["readiness"]
        (work.unarchive if cut == "archive" else work.reopen)("check")
        assert work.brief("check")["readiness"]["cycle"] == [
            {"relation": "prerequisite", "id": "bind"},
            {"relation": "child", "id": "check"},
        ]
        other = Tasks(store, "two")
        other.add("Bind edition", id="bind")
        other.add("Check proof", id="check", parent="bind")
        assert "cycle" not in other.brief("bind")["readiness"]


def test_bounded_search_does_not_claim_that_a_deep_graph_is_acyclic():
    with closing(Store(":memory:")) as store:
        work = Tasks(store)
        work.add("Parent", id="top")
        work.add("First child", id="sub", parent="top")
        previous = "sub"
        for n in range(7):
            id = f"next-{n}"
            work.add("Prerequisite", id=id)
            work.block(previous, id)
            previous = id
        work.block(previous, "top")
        assert not work.ready()
        assert work.brief("top")["readiness"] == {"ready": False, "reasons": ["active_children"]}
