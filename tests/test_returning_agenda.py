"""Old intentions retain their day when a person returns to the workspace."""

from contextlib import closing

from daymark import Store, Tasks
from daymark.render import agenda_text, prime_text


def test_earlier_plans_stay_distinct_from_today_and_hard_cutoffs():
    with closing(Store(":memory:")) as store:
        t = Tasks(store, "sample", timezone="America/New_York", now="2030-05-08T01:00:00Z")
        t.add("Last review", id="review", plan="2030-05-01")
        t.add("Pay deposit", id="deposit", plan="2030-05-01", due="2030-05-07")
        t.add("File notice", id="notice", due="2030-05-01")
        t.add("Print a proof", id="proof", plan="2030-05-07")
        before = store.export("sample")
        view = t.agenda()
        text = agenda_text(view, t.now())
        assert "OVERDUE (1)\n  notice " in text
        assert "TODAY (2)\n  deposit " in text
        assert "PLANNED EARLIER (1)\n  review " in text
        assert "3 today or planned earlier" in prime_text(t, brief=True)
        assert {item["id"] for item in view["today"]} == {"review", "deposit", "proof"}
        assert store.export("sample") == before
