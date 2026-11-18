"""Viewer-local hard cutoffs preserve civil dates, actual instants, and read filters."""

from contextlib import closing
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import httpx
import pytest

from daymark import DomainError, Store, Tasks
from daymark.client import Client
from daymark.mcp import handle
from daymark.render import agenda_data, agenda_text
from daymark.views import _end_of_day


def test_viewer_day_sorts_repeated_hours_and_keeps_overlapping_origin_dates():
    # Midnight in New York on a 25-hour Sunday.
    at = datetime(2030, 11, 3, 4, tzinfo=UTC)
    with closing(Store(":memory:")) as store:
        viewer = Tasks(store, "press", timezone="America/New_York", now=at)
        editor = Tasks(store, "press", timezone="Asia/Tokyo", now=at)
        viewer.add("Phone", kind="context", id="phone")
        for id, due in [
            ("later-fold", "2030-11-03T06:30+00:00"),
            ("earlier-fold", "2030-11-03T05:30+00:00"),
            ("midnight", "2030-11-03T04:00+00:00"),
            ("end", "2030-11-04T05:00+00:00"),
            ("civil", "2030-11-04"),
            ("later-civil", "2030-11-05"),
            ("late-civil", "2030-11-02"),
        ]:
            editor.add("Check the sample", id=id, due=due, scope="studio", contexts=["phone"])
        editor.add("In a different context", id="other-context", due="2030-11-03", scope="studio")
        editor.add("In a different scope", id="other-scope", due="2030-11-03", scope="home", contexts=["phone"])
        editor.add("A plan only", id="plan", plan="2030-11-03", scope="studio", contexts=["phone"])
        viewer.wait("later-fold", on="the desk")
        head = store.head("press")
        view = viewer.agenda(0, local_day=True, scope="studio", context="phone")
        rows = agenda_data(view, at)
        assert "notes" not in view["today"][0]
        assert [r["id"] for r in rows["today"]] == ["midnight", "earlier-fold", "later-fold"]
        assert [r["viewer_deadline"] for r in rows["today"]] == [
            "2030-11-03 00:00 America/New_York",
            "2030-11-03 01:30-04:00 America/New_York",
            "2030-11-03 01:30-05:00 America/New_York",
        ]
        assert rows["today"][-1]["status"] == "waiting"
        assert rows["upcoming"] == {}
        assert [r["id"] for r in rows["overdue"]] == ["late-civil"]
        [civil] = rows["civil_dates"]["2030-11-04"]
        assert civil["deadline_exact"] == {"date": "2030-11-04", "timezone": "Asia/Tokyo"}
        assert "viewer_deadline" not in civil
        assert rows["window"]["from"] == rows["window"]["through"] == "2030-11-03"
        assert "2030-11-04 Asia/Tokyo (date only)" in agenda_text(view, at)
        assert viewer.agenda(1, local_day=True, scope="studio", context="phone")["upcoming"]["2030-11-04"][0]["id"] == "end"
        assert "civil_dates" not in viewer.agenda(0, deadlines=True)
        call = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {
            "name": "daymark_view", "arguments": {
                "view": "agenda", "local_day": True, "scope": "studio", "context": "phone", "days": 0, "json": True,
            },
        }}
        assert handle(viewer, call, read_only=True)["result"]["structuredContent"] == rows
        assert store.head("press") == head


def test_skipped_civil_day_has_no_invented_midnight():
    # Samoa jumped from December 29 to December 31 in 2011.
    zone = ZoneInfo("Pacific/Apia")
    end = _end_of_day(date(2011, 12, 30), zone)
    assert end.astimezone(zone).date() == date(2011, 12, 29)
    assert (end + timedelta.resolution).astimezone(zone).date() == date(2011, 12, 31)
    with closing(Store(":memory:")) as store:
        at = datetime(2011, 12, 29, 1, tzinfo=UTC)
        origin = Tasks(store, timezone="Pacific/Apia", now=at)
        origin.add("Check the sample", id="skipped-date", due="2011-12-30")
        origin.add("Check the other sample", id="actual-date", due="2011-12-31")
        dates = Tasks(store, timezone="UTC", now=at).agenda(2, local_day=True)["civil_dates"]
        assert list(dates) == ["2011-12-31"]


@pytest.mark.parametrize("missing", ["day_zone", "context", "scope", "ack"])
def test_old_service_cannot_silently_widen_a_viewer_day(missing):
    reads = []

    def respond(request):
        reads.append(request.url.path)
        if request.url.path == "/v1/schema":
            params = [{"name": k} for k in ("day_zone", "scope", "context") if k != missing]
            return httpx.Response(200, json={"paths": {"/v1/agenda": {"get": {"parameters": params}}}})
        return httpx.Response(200, json={"today": [], "overdue": [], "upcoming": {}})

    with Client("https://sample.invalid", "sample", transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(DomainError) as error:
            client.agenda(days=0, day_zone="Pacific/Honolulu", context="phone", scope="press")
        assert error.value.code == "invalid"
    assert reads == (["/v1/schema", "/v1/agenda"] if missing == "ack" else ["/v1/schema"])
