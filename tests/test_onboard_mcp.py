"""An editor can launch the printed configuration from a different directory."""

import json
import os
import sqlite3
import subprocess
import tomllib
from contextlib import closing
from pathlib import Path

import pytest

from daymark.cli import main


def parse_config(text, host):
    if host == "claude":
        return json.loads(text)["mcpServers"]["daymark"]
    return tomllib.loads(text)["mcp_servers"]["daymark"]


@pytest.mark.parametrize("host", ["claude", "codex"])
def test_print_local_config_and_launch_stdio(host, tmp_path, monkeypatch, capsys):
    db = tmp_path / 'Project “🗂” with spaces' / "studio.sqlite3"
    db.parent.mkdir()
    args = ["--db", str(db), "--workspace=-studio", "--as=desk 🗂", "--tz=Pacific/Honolulu"]
    assert main([*args, "init"]) == 0
    assert main([*args, "add", "Fixture proof", "--id", "proof"]) == 0
    capsys.readouterr()
    assert main([*args, "onboard", "--mcp-config", host]) == 0
    launch = parse_config(capsys.readouterr().out, host)
    assert Path(launch["command"]).is_absolute()
    # An editor's working directory, database and workspace settings differ.
    elsewhere = tmp_path / "editor"
    elsewhere.mkdir()
    (elsewhere / "daymark.py").write_text('raise RuntimeError("wrong installation")')
    monkeypatch.setenv("PYTHONPATH", str(elsewhere))
    monkeypatch.setenv("DAYMARK_DB", str(elsewhere / "incorrect.sqlite3"))
    monkeypatch.setenv("DAYMARK_WORKSPACE", "incorrect")
    requests = [
        {"jsonrpc": "2.0", "method": "tools/list", "id": 1},
        {"jsonrpc": "2.0", "method": "tools/call", "id": 2,
         "params": {"name": "daymark_prime", "arguments": {"brief": True}}},
    ]
    out = subprocess.run(
        [launch["command"], *launch["args"]], cwd=elsewhere, env=os.environ,
        input="".join(json.dumps(r) + "\n" for r in requests),
        capture_output=True, text=True, check=True, timeout=20,
    )
    listed, prime = [json.loads(line)["result"] for line in out.stdout.splitlines()]
    assert {tool["name"] for tool in listed["tools"]} == {"daymark_prime", "daymark_view"}
    text = prime["content"][0]["text"]
    assert all(value in text for value in ("-studio", "desk 🗂", "Pacific/Honolulu", "Fixture proof"))
    assert not (elsewhere / "incorrect.sqlite3").exists()


@pytest.mark.parametrize("host", ["claude", "codex"])
def test_print_remote_config_names_credential_without_reading_it(host, monkeypatch, capsys):
    monkeypatch.setenv("SAMPLE_STUDIO_TOKEN", "opaque-sample-value")
    args = ["--url", "https://studio.invalid/daymark", "--token-env", "SAMPLE_STUDIO_TOKEN",
            "--as", "untrusted-local-name", "onboard", "--mcp-config", host]
    assert main(args) == 0
    text = capsys.readouterr().out
    assert "opaque-sample-value" not in text and "untrusted-local-name" not in text
    server = parse_config(text, host)
    assert "--url=https://studio.invalid/daymark" in server["args"]
    assert server["args"][-2:] == ["mcp", "--read-only"]
    assert server.get("env", {}).get("SAMPLE_STUDIO_TOKEN") == (
        "${SAMPLE_STUDIO_TOKEN:-}" if host == "claude" else None
    )
    if host == "codex":
        assert server["env_vars"] == ["SAMPLE_STUDIO_TOKEN"]


@pytest.mark.parametrize("url", [
    "https://name:opaque-sample-value@studio.invalid/api",
    "https://studio.invalid/api?token=opaque-sample-value",
    "https://studio.invalid/#opaque-sample-value", "https://studio.invalid:bad",
])
def test_setup_refuses_to_print_credentials_or_malformed_endpoint(url, capsys):
    assert main(["--url", url, "onboard", "--mcp-config", "codex"]) == 5
    report = capsys.readouterr()
    assert not report.out and "opaque-sample-value" not in report.err


def test_setup_missing_database_leaves_directory_empty(tmp_path, capsys):
    assert main(["--db", str(tmp_path / "absent"), "onboard", "--mcp-config", "claude"]) == 6
    assert not capsys.readouterr().out and list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("fixture, status", [(None, 6), ("daymark-0.4.0.sql", 4)])
def test_setup_requires_a_database_the_read_only_launch_can_open(tmp_path, capsys, fixture, status):
    db = tmp_path / "old-or-unrelated.sqlite3"
    with closing(sqlite3.connect(db)) as conn:
        if fixture:
            conn.executescript((Path(__file__).parent / "fixtures" / fixture).read_text())
        else:
            conn.execute("CREATE TABLE separate_project(id)")
    before = db.read_bytes()
    assert main(["--db", str(db), "onboard", "--mcp-config", "codex"]) == status
    assert capsys.readouterr().out == ""
    assert db.read_bytes() == before
