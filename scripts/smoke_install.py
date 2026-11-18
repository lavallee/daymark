"""Run this outside a checkout against an installed Daymark distribution."""

import json
import subprocess
import sys
import tempfile
from importlib.metadata import version
from importlib.util import find_spec
from pathlib import Path

import daymark
from daymark import Client, DomainError

assert set(daymark.__all__) <= set(dir(daymark))
assert "daymark.models" not in sys.modules
from daymark import Store, __version__

assert version("daymark-core") == __version__
assert subprocess.check_output(
    [sys.executable, "-I", "-m", "daymark.cli", "--version"], text=True
).strip() == f"daymark {__version__}"

if find_spec("httpx") is None:
    try:
        Client("http://127.0.0.1", "sample")
    except DomainError as error:
        assert "daymark-core[client]" in " ".join(error.details)
    else:
        raise AssertionError("Expected client installation guidance from a core-only install")

with tempfile.TemporaryDirectory() as directory:
    skill = subprocess.check_output(
        [sys.executable, "-I", "-m", "daymark.cli", "onboard", "--skill"], cwd=directory, text=True
    )
    assert skill.startswith("---\nname: daymark\n") and "daymark prime" in skill
    assert not list(Path(directory).iterdir())
    store = Store(Path(directory) / "work.db")
    task = store.execute(
        "demo",
        "first-task",
        "create",
        {
            "id": "draft",
            "kind": "task",
            "title": "Draft a proposal",
            "planned": {"date": "2030-04-03", "timezone": "Europe/London"},
        },
        expected_version=0,
    )
    assert task["version"] == 1
    replay = store.execute(
        "demo",
        "first-task",
        "create",
        {
            "id": "draft",
            "kind": "task",
            "title": "Draft a proposal",
            "planned": {"date": "2030-04-03", "timezone": "Europe/London"},
        },
        expected_version=0,
    )
    assert replay == task
    updated = store.execute(
        "demo", "finish-draft", "complete", {"id": "draft"}, expected_version=task["version"]
    )
    assert updated["status"] == "completed"
    assert store.get("demo", "draft")["status"] == "completed"
    assert store.export("demo")["entities"]
    store.close()

    cli = [sys.executable, "-I", "-m", "daymark.cli"]
    configuration = subprocess.check_output([
        *cli, "--db", str(Path(directory) / "work.db"), "--workspace", "demo",
        "onboard", "--mcp-config", "claude",
    ], cwd=directory, text=True)
    server = json.loads(configuration)["mcpServers"]["daymark"]
    tools = subprocess.check_output(
        [server["command"], *server["args"]], cwd=directory, text=True,
        input=json.dumps({"jsonrpc": "2.0", "method": "tools/list", "id": 1}) + "\n",
    )
    assert {tool["name"] for tool in json.loads(tools)["result"]["tools"]} == {
        "daymark_prime", "daymark_view"
    }
    if find_spec("httpx") is None:
        remote = subprocess.run(
            [*cli, "--url", "https://workspace.invalid", "onboard", "--mcp-config", "codex"],
            cwd=directory, text=True, check=False, capture_output=True,
        )
        assert remote.returncode == 5 and not remote.stdout and "daymark-core[client]" in remote.stderr
print("Installed core: create, replay, complete, export and read-only editor launch passed")
