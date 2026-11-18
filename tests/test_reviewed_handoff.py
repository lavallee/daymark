"""A tab may act on the claim it reviewed while another tab uses the same actor."""

from contextlib import closing

import httpx
import pytest

from daymark import DomainError, Store, Tasks
from daymark.client import Client, ClientError


@pytest.mark.parametrize("verb", ["release", "done"])
@pytest.mark.parametrize("note", [None, "Checked the proof"])
def test_reviewed_handoff_refuses_renewal_and_replays_after_turnover(verb, note):
    with closing(Store(":memory:")) as store:
        old_tab = Tasks(store, actor="desk")
        new_tab = Tasks(store, actor="desk")
        old_tab.add("Check the proof", id="proof")
        reviewed = old_tab.claim("proof")["version"]
        renewed = new_tab.claim("proof", minutes=75)
        finish = getattr(old_tab, verb)
        head = store.head("default")
        with pytest.raises(DomainError, match="Version mismatch"):
            finish("proof", note=note, expected_version=reviewed, op="from-tab")
        assert store.head("default") == head and new_tab.get("proof") == renewed

        sent = finish("proof", note=note, expected_version=renewed["version"], op="confirmed")
        assert sent["version"] == renewed["version"] + (2 if note else 1)
        if verb == "done":
            new_tab.reopen("proof")
        next_claim = Tasks(store, actor="next-desk").claim("proof")
        assert finish("proof", note=note, expected_version=renewed["version"], op="confirmed") == sent
        with pytest.raises(DomainError) as error:
            finish("proof", note=note, expected_version=reviewed, op="confirmed")
        assert error.value.code == "conflict" and new_tab.get("proof") == next_claim


def test_reviewed_completion_with_interrupted_legacy_note():
    with closing(Store(":memory:")) as store:
        tasks = Tasks(store, actor="desk")
        tasks.add("Check the proof", id="proof")
        reviewed = tasks.claim("proof")["version"]
        tasks.note("proof", "Checked the proof", op="from-tab:note")
        tasks.claim("proof", minutes=75)
        head = store.head("default")
        with pytest.raises(DomainError, match="Version mismatch"):
            tasks.done("proof", note="Checked the proof", expected_version=reviewed, op="from-tab")
        assert store.head("default") == head
        assert tasks.get("proof")["status"] == "open"


def test_genuinely_new_release_after_same_actor_reacquires():
    with closing(Store(":memory:")) as store:
        tasks = Tasks(store, actor="desk")
        tasks.add("Check the proof", id="proof")
        held = tasks.claim("proof")["version"]
        released = tasks.release("proof", expected_version=held, op="original")
        next_claim = tasks.claim("proof")
        assert tasks.release("proof", expected_version=held, op="original") == released
        with pytest.raises(DomainError, match="Version mismatch"):
            tasks.release("proof", expected_version=held, op="new")
        assert tasks.get("proof") == next_claim
        assert tasks.release("proof", expected_version=next_claim["version"], op="confirmed")["claimed_by"] is None


@pytest.mark.parametrize("has_brief", [False, True])
def test_client_distinguishes_missing_brief_route_from_missing_task(has_brief):
    def respond(request):
        if request.url.path == "/v1/schema":
            paths = {"/v1/brief": {"get": {"parameters": [{"name": "entity_id"}]}}} if has_brief else {}
            return httpx.Response(200, json={"paths": paths})
        return httpx.Response(404, json={"detail": "Not Found"})

    with Client("http://sample", "sample", transport=httpx.MockTransport(respond)) as client:
        if has_brief:
            with pytest.raises(ClientError) as missing:
                client.brief("proof")
            assert missing.value.status_code == 404
        else:
            with pytest.raises(DomainError) as unsupported:
                client.brief("proof")
            assert "full show by ID" in unsupported.value.details[0]
