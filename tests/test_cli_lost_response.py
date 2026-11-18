"""Authenticated CLI retries retain one intent even if the successful response is lost."""

import json
import shlex
from contextlib import closing

import httpx
import pytest
from fastapi.testclient import TestClient

from daymark import Store, cli, client
from daymark.api import create_app


def connect_cli(monkeypatch, transport):
    class TestServiceClient(client.Client):
        def __init__(self, url, token, **kwargs):
            super().__init__(url, token, transport=httpx.MockTransport(transport), **kwargs)

    monkeypatch.setattr(client, "Client", TestServiceClient)
    monkeypatch.setenv("SAMPLE_TOKEN", "fixture-token")
    return ["--url", "http://sample.invalid", "--token-env", "SAMPLE_TOKEN"]


@pytest.mark.parametrize("supplied", [None, "sample's $key"])
def test_cli_recovers_committed_capture_without_echoing_content(monkeypatch, capsys, supplied):
    with closing(Store(":memory:")) as store, TestClient(create_app(store, {
        "fixture-token": {"workspace": "lab", "principal": "worker"},
    })) as service:
        posted = []

        def downstream(request):
            reply = service.request(request.method, request.url.raw_path.decode(),
                                    headers=request.headers, content=request.content)
            if request.url.path == "/v1/operations":
                posted.append(json.loads(request.content)["operation_id"])
                if len(posted) == 1:
                    assert reply.status_code == 200
                    raise httpx.ReadError("Synthetic lost response")
            return httpx.Response(reply.status_code, content=reply.content)

        command = [*connect_cli(monkeypatch, downstream), "add", "Measure the sample",
                   "--note", "Synthetic note J-12", "--json"]
        assert cli.main(command + (["--op", supplied] if supplied else [])) == 1
        err = capsys.readouterr().err
        error = json.loads(err)["error"]
        key = error["operation_id"]
        assert key == posted[0] and (supplied is None or supplied == key)
        assert error["details"][0].endswith("--op " + shlex.quote(key))
        assert all(data not in err for data in ("Measure the sample", "J-12", "fixture-token"))
        assert cli.main(command + ["--op", key]) == 0
        record = store.get("lab", json.loads(capsys.readouterr().out)["id"])
        assert posted == [key, key] and store.head("lab") == 1 and len(record["notes"]) == 1


@pytest.mark.parametrize(("args", "status"), [
    (["add", "Sample"], 401), (["add", "Sample"], 403),
    (["show", "missing"], 503), (["project", "config", "sample"], 503), (["profile"], 503),
])
def test_cli_only_offers_write_recovery_for_uncertain_writes(monkeypatch, capsys, args, status):
    command = connect_cli(monkeypatch, lambda request: httpx.Response(status))
    assert cli.main([*command, *args, "--json"]) != 0
    error = json.loads(capsys.readouterr().err)["error"]
    assert "operation_id" not in error and all("--op" not in detail for detail in error["details"])
