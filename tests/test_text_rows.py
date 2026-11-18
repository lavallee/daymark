"""Task summaries stay on one line across CLI and MCP text reads."""

import json
from contextlib import closing
from shlex import split

import pytest

from daymark import Store, Tasks, render
from daymark.cli import main
from daymark.mcp import handle

TASK_ID = "tray 07/#?"
TITLE = "Check Cafe\u0301 📦\n棚のラベル\tعينة"
ROW_TITLE = r"Check Café 📦\n棚のラベル\tعينة"
SCOPE = "lab.v2-east"
NOTE = "First shelf\n\tBlue labels"
DESCRIPTION = "Inspect the tray.\nRecord its label."
MEMORY = "Label stock\nUpper shelf\tblue"


@pytest.fixture
def readers(tmp_path, capsys):
    database = str(tmp_path / "sample.sqlite3")
    with closing(Store(database)) as store:
        tasks = Tasks(store, "sample", actor="reviewer")
        tasks.add(TITLE, id=TASK_ID, scope=SCOPE, description=DESCRIPTION, note=NOTE)
        tasks.add("Check second sample", id="tray 08", scope=SCOPE)
        tasks.remember(MEMORY, scope=SCOPE)

        def cli(*args):
            assert main(["--db", database, "--workspace", "sample", "--as", "reviewer", *args]) == 0
            return capsys.readouterr().out

        def mcp(name="daymark_view", **args):
            response = handle(tasks, {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": name, "arguments": args},
            })["result"]
            assert not response["isError"], response
            return response.get("structuredContent", response["content"][0]["text"])

        yield store, cli, mcp


def test_ready_find_and_prime_keep_record_boundaries(readers):
    _, cli, mcp = readers
    expected = [
        f"{TASK_ID}  {ROW_TITLE}  [scope:{SCOPE}]",
        f"tray 08  Check second sample  [scope:{SCOPE}]",
    ]
    for text in (cli("ready"), mcp(view="ready"), cli("find"), mcp(view="find")):
        assert text.splitlines() == expected
    for text in (cli("ready", "--explain"), mcp(view="ready", explain=True)):
        assert len(text.splitlines()) == 3
        assert text.splitlines()[1].startswith(f"  {TASK_ID}  {ROW_TITLE}  [")

    args = ("find", "--scope", SCOPE, "--limit", "1")
    cli_hit, cli_next = cli(*args).splitlines()
    mcp_hit, mcp_next = mcp(view="find", scope=SCOPE, limit=1).splitlines()
    assert cli_hit == mcp_hit == f"{TASK_ID}  {ROW_TITLE}  [open]"
    after_arg = split(cli_next)[-1]
    assert after_arg == f"--after={TASK_ID}"
    assert cli(*args, after_arg).splitlines() == ["tray 08  Check second sample  [open]"]
    assert mcp_next == f"next: {TASK_ID}"
    assert mcp(view="find", scope=SCOPE, limit=1, after=TASK_ID) == (
        "tray 08  Check second sample  [open]"
    )

    for text in (cli("prime", "--brief"), mcp("daymark_prime", brief=True)):
        rows = text.splitlines()
        start = rows.index("Most pressing")
        assert rows[start + 1 : start + 4] == [
            f"  {TASK_ID}  {ROW_TITLE}", "  tray 08  Check second sample", "Remembered",
        ]
        assert rows[start + 4].startswith(r"  - Label stock\nUpper shelf\tblue  [scope:lab.v2-east]  (")


def test_store_structured_reads_and_detail_preserve_original_text(readers):
    store, cli, mcp = readers
    stored = store.get("sample", TASK_ID)
    assert (stored["id"], stored["title"], stored["scope"]) == (TASK_ID, TITLE, SCOPE)
    assert stored["description"] == DESCRIPTION
    assert stored["notes"][0]["text"] == NOTE

    cli_page = json.loads(cli("find", "--page", "--limit", "1", "--json"))
    mcp_page = mcp(view="find", limit=1, json=True)
    assert cli_page == mcp_page
    assert cli_page["next"] == TASK_ID
    hit = cli_page["items"][0]
    assert (hit["id"], hit["title"], hit["scope"]) == (TASK_ID, TITLE, SCOPE)

    for view in (json.loads(cli("ready", "--json")), mcp(view="ready", json=True)["items"]):
        assert (view[0]["id"], view[0]["title"], view[0]["scope"]) == (TASK_ID, TITLE, SCOPE)
    brief = json.loads(cli("prime", "--brief", "--json"))
    assert brief["ready"][0]["title"] == TITLE
    assert brief["memories"][0]["title"] == MEMORY

    for detail in (cli("show", TASK_ID), mcp(view="show", id=TASK_ID)):
        assert TITLE in detail
        assert DESCRIPTION in detail
        assert NOTE in detail
    note_id = stored["notes"][0]["id"]
    assert json.loads(cli("show", TASK_ID, "--note", note_id, "--json"))["text"] == NOTE
    assert mcp(view="show", id=TASK_ID, note=note_id, json=True)["text"] == NOTE


def test_summary_controls_are_escaped_without_changing_printable_text():
    for codepoint in (*range(32), *range(127, 160), 0x2028, 0x2029):
        character = chr(codepoint)
        row = render.line({"id": TASK_ID, "title": TITLE, "waiting_on": character})
        assert row == f"{TASK_ID}  {ROW_TITLE}  [waiting on {ascii(character)[1:-1]}]"

    page = {"items": [], "next": "tray\t07"}
    assert render.search_text(page) == "More matches. Read the exact next cursor with --json."
    assert render.search_text(page, mcp=True) == (
        "More matches. Read the exact next cursor with json: true."
    )


def test_prime_clips_the_title_preview_and_keeps_the_exact_id(readers):
    store, cli, mcp = readers
    tasks = Tasks(store, "sample", actor="reviewer")
    long_id = "tray 09/" + "x" * 120
    title = "📦" * 159 + "e\u0301 📋"
    tasks.add(title, id=long_id)

    for text in (cli("prime", "--brief"), mcp("daymark_prime", brief=True)):
        assert f"  {long_id}  {title[:160]}…" in text.splitlines()
    hit = mcp(view="find", after="tray 08", json=True)["items"][0]
    assert (hit["id"], hit["title"]) == (long_id, title)
