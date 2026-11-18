"""Folder selection and preservation of a project's existing instructions."""

import subprocess

import pytest

from daymark.cli import main
from daymark.render import ONBOARD


def test_folder_setup_keeps_its_database_out_of_git_and_refuses_conflicting_location(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    alternate = tmp_path / "other.db"
    monkeypatch.setenv("DAYMARK_DB", str(alternate))
    assert main(["init", "--here"]) == 5
    assert not alternate.exists() and not (tmp_path / ".daymark").exists()
    monkeypatch.delenv("DAYMARK_DB")
    assert main(["--url", "https://service.invalid", "init", "--here"]) == 5
    assert not (tmp_path / ".daymark").exists()
    assert main(["init", "--here"]) == 0
    subprocess.run(["git", "init", "-q"], check=True)
    for name in ("daymark.sqlite3", "daymark.sqlite3-wal", "daymark.sqlite3-shm"):
        subprocess.run(["git", "check-ignore", "-q", f".daymark/{name}"], check=True)
    ignore = tmp_path / ".daymark/.gitignore"
    ignore.write_text("custom\n")
    assert main(["init", "--here"]) == 0
    assert ignore.read_text() == "custom\n"


def test_followup_command_selects_explicit_database(tmp_path, capsys):
    path = tmp_path / "two words.db"
    assert main(["--db", str(path), "init"]) == 0
    assert f"daymark --db '{path}' prime" in capsys.readouterr().out


def test_check_and_refresh_preserve_bytes_around_the_section(tmp_path):
    target = tmp_path / "AGENTS.md"
    alias = tmp_path / "INSTRUCTIONS.md"
    alias.symlink_to(target)
    prefix, suffix = b"# Existing guidance\r\n\r\n", b"\r\n\r\nLast line without newline"
    target.write_bytes(prefix + b"<!-- daymark:begin -->\r\nold\r\n<!-- daymark:end -->" + suffix)
    target.chmod(0o640)
    assert main(["onboard", "--check", str(alias)]) == 1
    assert main(["onboard", "--write", str(alias)]) == 0
    assert alias.is_symlink()
    assert target.stat().st_mode & 0o777 == 0o640
    assert (
        target.read_bytes() == prefix + ONBOARD.rstrip("\n").replace("\n", "\r\n").encode() + suffix
    )
    stamp = target.stat().st_mtime_ns
    assert (
        main(["onboard", "--write", str(target)]) == main(["onboard", "--check", str(target)]) == 0
    )
    assert target.stat().st_mtime_ns == stamp


@pytest.mark.parametrize("markers", [("end", "begin"), ("begin",), ("begin", "begin", "end")])
def test_malformed_sections_leave_original_intact(tmp_path, markers):
    target = tmp_path / "AGENTS.md"
    original = "\n".join(f"<!-- daymark:{m} -->" for m in markers).encode()
    target.write_bytes(original)
    assert main(["onboard", "--write", str(target)]) == 5
    assert target.read_bytes() == original


def test_check_missing_instruction_file_has_no_side_effect(tmp_path):
    target = tmp_path / "AGENTS.md"
    assert main(["onboard", "--check", str(target)]) == 1
    assert not target.exists()


def test_where_uses_the_same_resolver_without_creating_or_migrating_files(
    tmp_path, monkeypatch, capsys
):
    import json

    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.chdir(project)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "user"))
    monkeypatch.delenv("DAYMARK_DB", raising=False)

    def where(*options):
        assert main([*options, "where", "--json"]) == 0
        return json.loads(capsys.readouterr().out)

    assert where()["database"] is None and list(project.iterdir()) == []
    missing = tmp_path / "missing.db"
    assert where("--db", str(missing))["file_exists"] is False and not missing.exists()
    user_db = tmp_path / "user/daymark/daymark.sqlite3"
    user_db.parent.mkdir(parents=True)
    user_db.write_bytes(b"untouched")
    assert where()["source"] == "user"
    legacy = project / "daymark.sqlite3"
    legacy.write_bytes(b"untouched")
    assert where()["source"] == "legacy"
    link = project / ".daymark"
    link.symlink_to(user_db.parent, target_is_directory=True)
    assert where()["source"] == "project" and where()["resolved_database"] == str(user_db)
    monkeypatch.setenv("DAYMARK_DB", str(legacy))
    assert where()["source"] == "DAYMARK_DB" and where("--db", str(missing))["source"] == "--db"
    assert where("--url", "https://worker:secret@host.invalid/path?token=secret") == {
        "source": "--url",
        "endpoint": "https://host.invalid",
        "identity_source": "server credential",
    }
    assert user_db.read_bytes() == legacy.read_bytes() == b"untouched"


@pytest.mark.parametrize(("fence", "close"), [(b"````markdown", b"````"), (b"~~~md", b"~~~")])
def test_fenced_instruction_example_is_preserved(tmp_path, capsys, fence, close):
    target = tmp_path / "AGENTS.md"
    example = fence + b"\n```\n<!-- daymark:begin -->\nliteral\n<!-- daymark:end -->\n" + close + b"\n"
    target.write_bytes(example)
    assert main(["onboard", "--check", str(target)]) == 1
    assert "missing" in capsys.readouterr().out
    assert main(["onboard", "--write", str(target)]) == 0
    assert target.read_bytes() == example + b"\n" + ONBOARD.encode()
    assert main(["onboard", "--check", str(target)]) == 0


def test_append_refuses_to_hide_the_snippet_in_an_unclosed_fence(tmp_path):
    target = tmp_path / "AGENTS.md"
    original = b"A sample\n```text\n"
    target.write_bytes(original)
    assert main(["onboard", "--write", str(target)]) == 5
    assert target.read_bytes() == original



def test_skill_prints_the_matching_source_without_a_database(tmp_path, monkeypatch, capsys):
    from pathlib import Path

    expected = Path(__file__).resolve().parents[1] / "skills/daymark/SKILL.md"
    monkeypatch.chdir(tmp_path)
    assert main(["onboard", "--skill"]) == 0
    assert capsys.readouterr().out == expected.read_text(encoding="utf-8")
    assert list(tmp_path.iterdir()) == []
