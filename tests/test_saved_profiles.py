"""A person's named ways of working are workspace choices, always explicit."""

import json

import pytest

from daymark import DomainError, Store, Tasks
from daymark.cli import main
from daymark.mcp import call


def fails(code, fn, *args, **kwargs):
    with pytest.raises(DomainError) as exc:
        fn(*args, **kwargs)
    assert exc.value.code == code


def test_named_copy_survives_restart_and_adoption_is_independent(tmp_path):
    path = tmp_path / "sample.sqlite3"
    store = Store(path)
    writer = Tasks(store, "studio", actor="writer")
    writer.add("Draft", id="a", minutes=15)
    writer.add("Proof", id="b", minutes=45)
    saved = writer.save_profile("short-break", profile="plain", settings=["time-fit.minutes=20"])
    assert saved["version"] == 1
    assert writer.profile()["name"] == "plain"
    assert len(writer.advise().picks) == 2
    store.close()

    store = Store(path)
    try:
        reader = Tasks(store, "studio", actor="reader")
        assert reader.saved_profiles("short-break") == saved
        assert [task["id"] for task in reader.advise(profile="short-break").picks] == ["a"]
        assert call(reader, "daymark_view", {"view": "profiles"}) == "short-break  version 1"
        assert "minutes=20" in call(
            reader, "daymark_view", {"view": "profiles", "profile": "short-break"}
        )
        assert (
            len(reader.advise(profile="short-break", settings=["time-fit.minutes=50"]).picks) == 2
        )
        assert reader.saved_profiles("short-break") == saved
        assert reader.profile("short-break") == saved["profile"]
        reader.save_profile(
            "short-break",
            profile="short-break",
            settings=["time-fit.minutes=50"],
            expected_version=1,
        )
        assert len(reader.advise().picks) == 1
        assert len(reader.advise(profile="short-break").picks) == 2
        assert Tasks(store, "elsewhere").saved_profiles() == []
        fails("not_found", Tasks(store, "elsewhere").advise, profile="short-break")
    finally:
        store.close()


def test_unknown_versions_and_recreated_names_cannot_clobber_configuration(tmp_path):
    store = Store(tmp_path / "sample.sqlite3")
    try:
        tasks = Tasks(store)
        first = tasks.save_profile("review", profile="assistant")
        fails("conflict", tasks.save_profile, "review", profile="plain")
        fails("conflict", tasks.remove_profile, "review", expected_version=0)
        second = tasks.save_profile("review", profile="plain", expected_version=first["version"])
        assert second["version"] > first["version"]
        tasks.remove_profile("review", expected_version=second["version"])
        new = tasks.save_profile("review", profile="dense")
        assert new["version"] > second["version"]
        fails(
            "conflict",
            tasks.save_profile,
            "review",
            profile="plain",
            expected_version=first["version"],
        )
        fails("invalid", tasks.save_profile, "plain")
        fails("invalid", tasks.save_profile, "new name")
        fails("invalid", tasks.save_profile, "review", expected_version=True)
        assert tasks.saved_profiles() == [new]
    finally:
        store.close()


@pytest.mark.parametrize("setting", ["profile", "profile.library"])
def test_competing_connections_preserve_intervening_edits(tmp_path, monkeypatch, setting):
    path = tmp_path / "shared.sqlite3"
    first, second = Store(path), Store(path)
    try:
        earlier, later = Tasks(first), Tasks(second)
        write = first.set_setting

        def intervening(workspace, key, value, **kwargs):
            if setting == "profile":
                later.profile(settings=["waiting-check.days=9"])
            else:
                later.save_profile("weekly", profile="dense")
            return write(workspace, key, value, **kwargs)

        monkeypatch.setattr(first, "set_setting", intervening)
        if setting == "profile":
            fails("conflict", earlier.profile, settings=["shortlist.size=2"])
            assert earlier.profile()["strategies"] == [
                {"use": "waiting-check", "with": {"days": 9}}
            ]
        else:
            fails("conflict", earlier.save_profile, "morning", profile="gentle")
            assert [entry["name"] for entry in earlier.saved_profiles()] == ["weekly"]
    finally:
        first.close()
        second.close()


def test_cli_export_import_replace_remove_and_input_errors(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("DAYMARK_DB", str(tmp_path / "sample.sqlite3"))

    def cli(*args, expected=0):
        assert main([*args, "--json"]) == expected
        captured = capsys.readouterr()
        return json.loads(captured.out or captured.err)

    assert main(["init"]) == 0
    capsys.readouterr()
    saved = cli("profile", "gentle", "--set", "shortlist.size=2", "--save-as", "writing")
    assert cli("profile", "--saved", "writing") == saved
    assert cli("profile")["name"] == "plain"
    file = tmp_path / "writing.json"
    file.write_text(json.dumps(saved))
    imported = cli("profile", "--from-file", str(file), "--save-as", "afternoon")
    assert imported["profile"]["strategies"] == saved["profile"]["strategies"]
    assert cli("profile", "--saved") == [imported, saved]
    cli("profile", "plain", "--save-as", "afternoon", expected=4)
    replaced = cli(
        "profile", "plain", "--save-as", "afternoon", "--if-version", str(imported["version"])
    )
    assert replaced["profile"]["strategies"] == []
    cli("profile", "--remove", "afternoon", "--if-version", str(imported["version"]), expected=4)
    cli("profile", "--remove", "afternoon", "--if-version", str(replaced["version"]))
    assert cli("profile", "--saved") == [saved]
    cli("profile", "--saved", "--set", "shortlist", expected=5)
    cli("profile", "--remove", "writing", expected=5)
    cli("profile", "plain", "--from-file", str(file), "--save-as", "duplicate", expected=5)
    file.write_text("[]")
    cli("profile", "--from-file", str(file), "--save-as", "duplicate", expected=5)
    assert cli("profile", "--saved") == [saved]
