"""The reminder request can precede its database timestamp by a civil day."""

from contextlib import closing

import pytest

from daymark import Store, Tasks
from daymark.models import DomainError


def test_relative_reminder_restores_original_civil_time_after_transfer(tmp_path):
    zone = "America/New_York"
    with closing(Store(tmp_path / "source.db")) as store:
        first = Tasks(store, "example", actor="desk", timezone=zone, now="2030-05-01T23:59:59-04:00")
        first.add("Sample reviewer", kind="person", id="reviewer")
        first.add("Review sample", id="job")
        result = first.remind("job", "tomorrow 09:00", recipient="reviewer", op="notify")
        assert result["scheduled_at"] == "2030-05-02T13:00:00Z"
        snapshot = store.export("example")
    with closing(Store(tmp_path / "restored.db")) as store:
        store.import_snapshot("example", snapshot, operation_id="import", expected_version=0)
        later = Tasks(store, "example", actor="desk", timezone=zone, now="2030-05-06T12:00:00Z")
        assert later.remind("job", "tomorrow 09:00", recipient="reviewer", op="notify") == result
        with pytest.raises(DomainError, match="different request"):
            later.remind("job", "+1d 09:00", recipient="reviewer", op="notify")
        other_zone = Tasks(store, "example", actor="desk", timezone="America/Toronto", now=later.now())
        with pytest.raises(DomainError, match="different request"):
            other_zone.remind("job", "tomorrow 09:00", recipient="reviewer", op="notify")
        with pytest.raises(DomainError, match="different request"):
            later.remind("job", "tomorrow 09:00", recipient="reviewer", channel="other", op="notify")
        assert store.list("example", kind="reminder") == [result]


def test_brief_distinguishes_a_pending_proposal_from_a_local_recording(tmp_path):
    from daymark.mcp import call
    from daymark.render import brief_text
    from daymark.worker import Worker

    with closing(Store(tmp_path / "brief.db")) as store:
        tasks = Tasks(store, "sample", timezone="Europe/London", now="2030-07-02T08:30:00Z")
        tasks.add("Sample reviewer", id="reviewer", kind="person")
        tasks.propose("Read sample", id="proof")
        reminder = tasks.remind("proof", "today 09:00", recipient="reviewer")
        args = {"view": "show", "id": reminder["id"], "brief": True}
        read = tasks.brief(reminder["id"])
        assert read["scheduled_at"] == "2030-07-02T08:00:00Z"
        assert (read["reminder_state"], read["recipient_id"], read["channel"]) == (
            "pending", "reviewer", "recording",
        )
        assert read["held_for_decision"]
        text = call(tasks, "daymark_view", args)
        assert "09:00 Europe/London for reviewer" in text
        assert "Task: proof" in text and "linked proposal to be accepted" in text
        worker = Worker(store)
        assert worker.run_once(now=tasks.now())["claimed"] == 0
        tasks.accept("proof")
        assert "held_for_decision" not in tasks.brief(reminder["id"])
        assert worker.run_once(now=tasks.now())["recorded"] == 1
        delivered = tasks.brief(reminder["id"])
        assert delivered["reminder_state"] == "delivered" and delivered["attempts"] == 1
        assert not {"lease_token", "delivery_key", "lease_owner"} & delivered.keys()
        assert "Recorded locally." in brief_text(delivered)
