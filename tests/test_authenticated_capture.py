"""Capture-note authorship is stamped on the actual write; imported provenance survives."""

from contextlib import closing

import httpx
import pytest
from fastapi.testclient import TestClient

from daymark import Store, Tasks
from daymark.api import create_app
from daymark.client import Client, ClientError


def test_identity_and_note_provenance_agree_across_proxy_labels_and_retries():
    class InstrumentedClient(Client):
        pass

    auth = {
        "first-token": {"workspace": "sample-lab", "principal": "shift-agent"},
        "second-token": {"workspace": "other-lab", "principal": "other-worker"},
    }
    with closing(Store(":memory:")) as store, TestClient(create_app(store, auth)) as http:
        assert http.get("/v1/identity").status_code == 401
        for credential, expected in auth.items():
            response = http.get("/v1/identity", headers={"Authorization": "Bearer " + credential})
            assert response.json() == {
                "workspace": expected["workspace"],
                "actor": expected["principal"],
            }

        with InstrumentedClient(
            "http://testserver", "first-token", transport=http._transport
        ) as client:
            tasks = Tasks(client, workspace="display", actor="shell")
            assert client.identity() == {"workspace": "sample-lab", "actor": "shift-agent"}
            first = tasks.add(
                "Compare samples", id="sample", note="First reading", op="create-sample"
            )
            assert first["notes"][0]["author"] == "shift-agent"
            tasks.note("sample", "Second reading")
            retry = Tasks(client, actor="different-shell").add(
                "Compare samples", id="sample", note="First reading", op="create-sample"
            )
            assert retry == first
            assert {n["author"] for n in tasks.get("sample")["notes"]} == {"shift-agent"}
            assert store.head("sample-lab") == 2 and store.head("display") == 0
            with pytest.raises(ClientError) as refused:
                tasks.add(
                    "Compare samples", id="sample", note="Changed reading", op="create-sample"
                )
            assert refused.value.code == "conflict"

            # An ordinary raw create can carry an existing attributed record. This is
            # also how the 0.5 plain capture stored its initial, locally supplied author.
            original = client.create(
                "old-op",
                {
                    "kind": "task",
                    "id": "historic",
                    "title": "Earlier reading",
                    "notes": [
                        {
                            "id": "recorded-note",
                            "at": "2030-01-01T12:00:00+00:00",
                            "author": "recorder",
                            "text": "First reading",
                        }
                    ],
                },
            )
            assert original["notes"][0]["author"] == "recorder"
            assert (
                tasks.add("Earlier reading", id="historic", note="First reading", op="old-op")
                == original
            )
            assert store.events("sample-lab")[-1]["actor"] == "shift-agent"


def test_legacy_server_cannot_confirm_initial_note_author_and_keeps_audit_actor():
    auth = {"token": {"workspace": "sample", "principal": "shift-agent"}}
    with closing(Store(":memory:")) as store, TestClient(create_app(store, auth)) as http:

        def legacy(request):
            if request.url.path in {"/v1/identity", "/v1/receipt-context"}:
                return httpx.Response(404, json={"detail": "Not Found"})
            response = http.request(
                request.method,
                request.url.raw_path.decode(),
                headers=request.headers,
                content=request.content,
            )
            return httpx.Response(response.status_code, content=response.content)

        with Client(
            "https://sample.invalid", "token", transport=httpx.MockTransport(legacy)
        ) as client:
            tasks = Tasks(client, actor="display")
            assert client.identity() is None
            made = tasks.add("Check sample", id="sample", note="Check count", op="trial")
            assert made["notes"][0]["author"] is None
            assert store.events("sample")[0]["actor"] == "shift-agent"


def test_invalid_capture_attribution_shape_refuses_before_writing():
    auth = {"token": {"workspace": "sample", "principal": "incoming"}}
    with closing(Store(":memory:")) as store, TestClient(create_app(store, auth)) as http:
        request = {
            "operation_id": "capture",
            "action": "create",
            "expected_version": 0,
            "payload": {"kind": "task", "id": "sample"},
            "attribute_capture_note": True,
        }
        assert (
            http.post(
                "/v1/operations", json=request, headers={"Authorization": "Bearer token"}
            ).status_code
            == 422
        )
        assert store.head("sample") == 0
