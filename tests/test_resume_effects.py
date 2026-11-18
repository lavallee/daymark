"""Compare a waiting task's preview with its real reminders after resuming."""

from contextlib import closing

import pytest

from daymark import Store, Tasks, render


@pytest.mark.parametrize(("start", "now", "clears"), [
    (None, "2030-05-04T06:59:59Z", False),
    ("2030-05-04", "2030-05-04T06:59:59Z", True),
    ("2030-05-04", "2030-05-04T07:00:00Z", False),
    ("2030-05-04T08:00", "2030-05-04T14:59:59Z", True),
    ("2030-05-04T08:00", "2030-05-04T15:00:00Z", False),
])
def test_resume_preview_matches_real_operation(start, now, clears):
    with closing(Store(":memory:")) as store:
        tasks = Tasks(store, "studio", timezone="America/Los_Angeles", now=now)
        tasks.add("Print the sample", id="print", due="2030-05-10")
        tasks.wait("print", "proof", until=start)
        tasks.add("Desk", id="desk", kind="person")
        reminders = [
            tasks.remind("print", when, recipient="desk", op=op)["id"]
            for op, when in (("proof", "2030-05-04T12:00"), ("cutoff", "2030-05-09T16:00"))
        ]
        before = store.export("studio")
        # Read from another zone. The start opens in its recorded zone.
        reader = Tasks(store, "studio", timezone="Asia/Tokyo", now=now)
        full, brief = reader.show("print"), reader.brief("print")
        assert full["if_resumed_now"] == brief["if_resumed_now"] == {
            "earliest_start": "clear" if clears else "preserve",
            "active_reminders": "cancel" if clears else "preserve",
            "deadline": "preserve",
        }
        for text in (render.show_text(full, reader.now()), render.brief_text(brief)):
            assert ("cancels all active reminders" if clears else "keeps the existing schedule") in text
        assert store.export("studio") == before
        result = tasks.resume("print", op="reply")
        assert result["status"] == "open" and result["waiting_on"] is None
        assert result["deadline"] == full["entity"]["deadline"]
        assert result["earliest_start"] == (None if clears else full["entity"]["earliest_start"])
        assert [tasks.get(r)["reminder_state"] for r in reminders] == [
            "cancelled" if clears else "pending"
        ] * 2
        assert "if_resumed_now" not in reader.brief("print")
        assert tasks.resume("print", op="reply") == result


def test_archived_wait_has_no_resume_projection():
    with closing(Store(":memory:")) as store:
        tasks = Tasks(store)
        tasks.add("Request proof", id="proof", waiting_on="approval")
        tasks.archive("proof")
        assert "if_resumed_now" not in tasks.show("proof")
        assert "if_resumed_now" not in tasks.brief("proof")
