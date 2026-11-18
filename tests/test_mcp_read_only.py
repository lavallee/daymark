"""Read-only sessions expose only reads and cannot replay an old write through MCP."""

import json
import sqlite3
import subprocess
import sys
from contextlib import closing

import httpx
import pytest

from daymark import DomainError, Store, Tasks
from daymark.client import Client
from daymark.mcp import handle


def rpc(method, **params):
    return {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}


def tool(name, **fields):
    return rpc("tools/call", name="daymark_" + name, arguments=fields)


def stdio(db, *messages):
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "daymark.cli",
            "--db",
            str(db),
            "--workspace",
            "sample",
            "mcp",
            "--read-only",
        ],
        input="".join(json.dumps(message) + "\n" for message in messages),
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )


def test_real_stdio_lists_two_tools_rejects_direct_writes_and_preserves_local_evidence(tmp_path):
    db = tmp_path / "reader #1?.sqlite"
    with closing(Store(db)) as store:
        work = Tasks(store, "sample")
        work.add("Check the reference", id="prior", op="capture-1")
        work.edit("prior", title="Check the reference sheet")
        work.add("Write the report", id="next", after=["prior"])
        initial = store.export("sample")
    contents = db.read_bytes()
    messages = [
        rpc("initialize", protocolVersion="2025-11-25"),
        rpc("tools/list"),
        tool("prime"),
        tool("prime", brief=True),
        tool("view", view="show", id="next", json=True),
        tool("add", title="Check the reference", op="capture-1"),
        tool("edit", id="prior", title="Changed"),
        tool("act", action="done", id="prior", op="try-done"),
        tool("remember", text="Facts are durable"),
        tool("act", action="use_profile", id="gentle"),
    ]
    for _ in range(2):
        proc = stdio(db, *messages)
        assert proc.returncode == 0, proc.stderr
        init, tools, prime, brief, show, *writes = [
            json.loads(line)["result"] for line in proc.stdout.splitlines()
        ]
        assert "Read-only" in init["instructions"]
        assert [t["name"] for t in tools["tools"]] == ["daymark_prime", "daymark_view"]
        assert all(t["annotations"]["readOnlyHint"] for t in tools["tools"])
        for result in (prime, brief):
            text = result["content"][0]["text"]
            assert "Read-only" in text and "daymark add" not in text
        assert show["structuredContent"]["blocked_by"] == ["prior"]
        assert all(
            r["isError"] and r["structuredContent"]["error"]["code"] == "forbidden" for r in writes
        )
    assert db.read_bytes() == contents
    with closing(Store(db, read_only=True)) as store:
        assert store.export("sample") == initial


def test_read_only_open_cannot_mutate_or_upgrade_and_sees_committed_updates(tmp_path):
    path = tmp_path / "live.sqlite"
    with closing(Store(path)) as writer, closing(Store(path, read_only=True)) as reader:
        write, read = Tasks(writer), Tasks(reader)
        write.add("First title", id="sample")
        assert read.show("sample")["entity"]["title"] == "First title"
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            reader.db.execute("UPDATE daymark_entities SET version=version+1")
        reader.db.rollback()
        write.edit("sample", title="Revised title")
        assert read.show("sample")["entity"]["version"] == 2
    with closing(sqlite3.connect(path)) as db:
        db.execute("UPDATE daymark_scheduler SET value='1' WHERE key='schema'")
        db.commit()
    before = path.read_bytes()
    failed = stdio(path, rpc("tools/list"))
    assert failed.returncode == 4 and failed.stdout == "" and "upgrade" in failed.stderr
    assert path.read_bytes() == before
    missing = tmp_path / "missing.sqlite"
    failed = stdio(missing, rpc("tools/list"))
    assert failed.returncode == 6 and "existing database" in failed.stderr
    assert not missing.exists()


def test_http_reader_allows_search_queries_but_refuses_writes_before_transport():
    sent = []

    def server(request):
        sent.append((request.method, request.url.path))
        return httpx.Response(
            200, json={"items": [], "next": None} if request.url.path == "/v1/search" else []
        )

    with Client(
        "https://sample.invalid", "synthetic", transport=httpx.MockTransport(server), read_only=True
    ) as client:
        tasks = Tasks(client)
        assert handle(tasks, tool("view", view="find", json=True), read_only=True)["result"][
            "structuredContent"
        ] == {"items": [], "next": None}
        assert client.query({"and": []}) == []
        result = handle(tasks, tool("act", action="done", id="sample"), read_only=True)["result"]
        assert result["isError"]
        with pytest.raises(DomainError) as refused:
            client.create("op", {"title": "A refused write"})
        assert refused.value.code == "forbidden"
    assert sent == [("POST", "/v1/search"), ("POST", "/v1/query")]
