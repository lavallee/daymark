"""Every legal imported key remains addressable through the authenticated client."""

from contextlib import closing

import httpx
import pytest
from fastapi.testclient import TestClient

from daymark import DomainError, Store, Tasks
from daymark.api import create_app
from daymark.client import Client


def test_addressable_entity_project_goal_and_receipt_ids_round_trip():
    with closing(Store(":memory:")) as store:
        local = Tasks(store, "room", actor="reader")
        Tasks(store, "other").add("Private sample", id="crate/07")
        auth = {"sample": {"principal": "reader", "workspace": "room"}}
        with TestClient(create_app(store, auth)) as service:

            def forward(request):
                response = service.request(
                    request.method,
                    request.url.raw_path.decode(),
                    content=request.content,
                    headers=dict(request.headers),
                )
                return httpx.Response(response.status_code, content=response.content)

            with Client(
                "https://sample.invalid", "sample", transport=httpx.MockTransport(forward)
            ) as client:
                remote = Tasks(client)
                for id in ("crate/07", ".", "..", "tray%2F07", "tray#?é", "/shelf/"):
                    saved = remote.add("Check synthetic sample", id=id, op="seed/" + id)
                    noted = remote.note(id, "Checked against sample.", op="review/" + id)
                    assert client.get(id) == noted
                    assert remote.show(id)["entity"]["title"] == saved["title"]
                    assert client.receipt("review/" + id) == noted
                    assert client.tree(id) == store.tree("room", id)
                    assert remote.note(id, "Checked against sample.", op="review/" + id) == noted
                local.project("Inspect a crate", id="project/07")
                local.goal("Finished sample", id="goal/07")
                assert client.plan("project/07") == local.plan("project/07")
                assert client.changes("project/07", 0) == local.changes("project/07", 0)
                assert client.goal_plan("goal/07") == local.goal_plan("goal/07")
                with pytest.raises(DomainError):
                    remote.show()
