# /// script
# requires-python = ">=3.11"
# dependencies = ["mcp==2.3.0"]
# ///
"""Exercise the installed daymark executable with an independent MCP client."""

import argparse
import asyncio
import os
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


@asynccontextmanager
async def connect(executable, db, actor, *, read_only=False):
    args = ["--db", str(db), "--workspace", "samples", "--as", actor, "--tz", "UTC", "mcp"]
    if read_only:
        args.append("--read-only")
    launch = StdioServerParameters(command=str(executable), args=args)
    async with stdio_client(launch) as (reader, writer), ClientSession(reader, writer) as session:
        hello = await session.initialize()
        assert hello.server_info.name == "daymark"
        yield session


async def call(session, name, **args):
    reply = await session.call_tool("daymark_" + name, args)
    assert not reply.is_error, reply.content
    return reply.structured_content


async def journey(executable, db):
    source = "example:sample:Q/41"
    note = "The reference mark is visible. Record the final reading next."
    actor = "sample-worker"
    async with connect(executable, db, actor) as worker:
        tools = {t.name for t in (await worker.list_tools()).tools}
        assert tools == {"daymark_prime", "daymark_view", "daymark_add", "daymark_edit",
                         "daymark_act", "daymark_remember"}
        added = await call(worker, "add", title="Inspect the sample proof", source=source,
                           move="agent", scope="studio", op="sample-add")
        claimed = await call(worker, "act", action="take", scope="studio", op="sample-take")
        assert claimed["id"] == added["id"] and claimed["claimed_by"] == actor
        release = {"action": "release", "id": added["id"], "text": note, "op": "sample-release"}
        receipt = await call(worker, "act", **release)
        assert receipt["claimed_by"] is None

    async with connect(executable, db, actor) as restarted:
        assert await call(restarted, "act", **release) == receipt

    async with connect(executable, db, "next-worker", read_only=True) as reader:
        assert {t.name for t in (await reader.list_tools()).tools} == {"daymark_prime", "daymark_view"}
        show = {"view": "show", "source": source, "json": True}
        read = await call(reader, "view", **show)
        assert read["entity"]["version"] == receipt["version"] and read["entity"]["status"] == "open"
        notes = read["entity"]["notes"]
        assert [(n["author"], n["text"]) for n in notes] == [(actor, note)]
        denied = await reader.call_tool("daymark_act", {"action": "done", "id": added["id"]})
        assert denied.is_error
        assert denied.structured_content["error"]["code"] == "forbidden"
        assert (await call(reader, "view", **show))["entity"] == read["entity"]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--daymark", required=True, type=Path, help="Path to the candidate daymark executable")
    exe = parser.parse_args().daymark.resolve()
    if not exe.is_file() or not os.access(exe, os.X_OK):
        parser.error(f"daymark executable not found at {exe}")
    with tempfile.TemporaryDirectory(prefix="daymark mcp ") as root:
        asyncio.run(journey(exe, Path(root) / "samples.sqlite"))
    print("MCP SDK 2.3.0 verified capture, claim, handoff, restart replay and read-only pickup.")
