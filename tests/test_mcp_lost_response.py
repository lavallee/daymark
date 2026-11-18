"""Recover a committed write when the bridge loses the HTTP response."""

import json
from contextlib import closing

import httpx
import pytest
from fastapi.testclient import TestClient

from daymark import Store, Tasks
from daymark.api import create_app
from daymark.client import Client
from daymark.mcp import handle


@pytest.mark.parametrize("supplied", [None, "fixed-intent"])
def test_committed_write_returns_retry_key_without_repeating_stored_content(supplied):
    auth = {"fixture-token": {"workspace": "sample", "principal": "worker"}}
    with closing(Store(":memory:")) as store, TestClient(create_app(store, auth)) as server:
        posted = []

        def downstream(request):
            reply = server.request(
                request.method, request.url.raw_path.decode(), headers=request.headers,
                content=request.content,
            )
            if request.method == "POST" and request.url.path == "/v1/operations":
                posted.append(json.loads(request.content)["operation_id"])
                if len(posted) == 1:
                    assert reply.status_code == 200
                    raise httpx.ReadError("Synthetic lost response")
            return httpx.Response(reply.status_code, content=reply.content)

        def capture(args):
            with Client("http://sample.invalid", "fixture-token", transport=httpx.MockTransport(downstream)) as c:
                return handle(Tasks(c), {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                    "params": {"name": "daymark_add", "arguments": args}})["result"]

        args = {"title": "Record the mark", "note": "Synthetic reading Q-67"}
        if supplied is not None:
            args["op"] = supplied
        failure = capture(args)
        key = failure["structuredContent"]["error"]["operation_id"]
        assert key == posted[0] and (supplied is None or key == supplied)
        assert key in failure["content"][0]["text"] and args["note"] not in str(failure)
        receipt = capture({**args, "op": key})
        assert not receipt["isError"] and posted == [key, key]
        record = store.get("sample", receipt["structuredContent"]["id"])
        assert record["version"] == 1 and len(record["notes"]) == 1 and store.head("sample") == 1
