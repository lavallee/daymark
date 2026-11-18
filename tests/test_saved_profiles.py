"""A person's named ways of working are workspace choices, always explicit."""

import json
import threading
from concurrent.futures import ThreadPoolExecutor

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
        catalog = call(reader, "daymark_view", {"view": "profiles"})
        assert "short-break  version 1" in catalog and "Active profile: plain" in catalog
        assert "gentle" in catalog
        assert "shortlist" in call(
            reader, "daymark_view", {"view": "profiles", "profile": "gentle"}
        )
        assert "minutes=20" in call(
            reader, "daymark_view", {"view": "profiles", "profile": "short-break"}
        )
        assert (
            len(reader.advise(profile="short-break", settings=["time-fit.minutes=50"]).picks) == 2
        )
        assert reader.saved_profiles("short-break") == saved
        adopted = reader.profile("short-break")
        assert adopted["strategies"] == saved["profile"]["strategies"]
        assert adopted["version"] == 1 and adopted["workspace"] == "studio"
        reader.save_profile(
            "short-break",
            profile="short-break",
            settings=["time-fit.minutes=50"],
            expected_version=1,
        )
        active = reader.advise()
        named = reader.advise(profile="short-break")
        assert len(active.picks) == 1 and len(named.picks) == 2
        assert active.to_dict()["profile_source"] == {
            "kind": "workspace",
            "version": 1,
            "name": "short-break",
            "adjusted": False,
        }
        assert named.to_dict()["profile_source"] == {
            "kind": "saved",
            "name": "short-break",
            "version": 2,
            "adjusted": False,
        }
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


def test_competing_connections_compose_independent_adjustments(tmp_path):
    path = tmp_path / "shared.sqlite3"
    first, second = Store(path), Store(path)
    gate = threading.Barrier(2)

    def write(store, label, stage):
        task = Tasks(store)
        gate.wait(timeout=5)
        task.profile(settings=[stage], op=f"adjust-{label}")
        task.save_profile(label, profile="plain", op=f"save-{label}")

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            a = executor.submit(write, first, "morning", "shortlist.size=2")
            b = executor.submit(write, second, "weekly", "waiting-check.days=9")
            a.result(timeout=10)
            b.result(timeout=10)
        chosen = Tasks(first).profile()
        assert chosen["version"] == 2
        assert {stage["use"] for stage in chosen["strategies"]} == {"shortlist", "waiting-check"}
        assert [entry["name"] for entry in Tasks(first).saved_profiles()] == ["morning", "weekly"]
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


@pytest.mark.parametrize(
    ("workspace", "key"), [("", "profile"), ("w", ""), ("w", "nested:profile"), ("w", "../other")]
)
def test_setting_keys_are_validated_identically_on_read_and_write(tmp_path, workspace, key):
    store = Store(tmp_path / "settings.sqlite3")
    try:
        fails("invalid", store.setting, workspace, key)
        fails("invalid", store.setting_state, workspace, key)
        fails("invalid", store.set_setting, workspace, key, {})
        fails("invalid", store.setting_before, workspace, key, "prior")
    finally:
        store.close()


def test_preference_receipts_replay_before_derived_state_changes(tmp_path):
    path = tmp_path / "prefs.sqlite3"
    store = Store(path)
    tasks = Tasks(store, "test")
    before = store.export("test")
    saved = tasks.save_profile("quiet", profile="gentle", op="save")
    adopted = tasks.profile("quiet", expected_version=0, op="adopt")
    removed = tasks.remove_profile("quiet", expected_version=saved["version"], op="remove")
    changed = tasks.profile(settings=["shortlist.size=1"], expected_version=1, op="adjust")
    store.close()
    store = Store(path)
    try:
        tasks = Tasks(store, "test")
        assert tasks.save_profile("quiet", profile="gentle", op="save") == saved
        assert (
            tasks.remove_profile("quiet", expected_version=saved["version"], op="remove") == removed
        )
        assert tasks.profile("quiet", expected_version=0, op="adopt") == adopted
        assert (
            tasks.profile(settings=["shortlist.size=1"], expected_version=1, op="adjust") == changed
        )
        assert tasks.saved_profiles() == []
        assert tasks.profile()["version"] == 2
        fails("conflict", tasks.save_profile, "other", profile="gentle", op="save")
        fails("conflict", tasks.profile, "plain", op="save")
        fails("conflict", tasks.profile, undo="adopt", expected_version=2, op="older")
        restored = tasks.profile(undo="adjust", expected_version=2, op="restore")
        assert restored["strategies"] == adopted["strategies"]
        assert restored["version"] == 3
        assert tasks.profile(undo="adjust", expected_version=2, op="restore") == restored
        fails("conflict", tasks.profile, "plain", expected_version=2, op="stale")
        assert store.export("test") == before
        other = Tasks(store, "other")
        assert other.profile()["version"] == 0
        fails("not_found", other.profile, undo="adopt", op="undo-other")
    finally:
        store.close()


def test_undo_first_choice_restores_unset_default_and_preserves_revision(tmp_path):
    store = Store(tmp_path / "prefs.sqlite3")
    try:
        tasks = Tasks(store, "w")
        assert tasks.profile()["version"] == 0
        adopted = tasks.profile("gentle", op="choose")
        assert adopted["undo"] == {"operation_id": "choose", "expected_version": 1}
        restored = tasks.profile(undo="choose", expected_version=1, op="restore")
        assert restored["name"] == "plain" and restored["version"] == 2
        assert store.setting("w", "profile") is None
        fails("conflict", tasks.profile, "dense", expected_version=0, op="stale")
        assert tasks.profile("gentle", op="choose") == adopted
        assert tasks.profile()["version"] == 2
    finally:
        store.close()


def test_caller_rollback_removes_preferences_and_receipts_together():
    import sqlite3

    con = sqlite3.connect(":memory:")
    try:
        store = Store(con)
        con.commit()
        tasks = Tasks(store, "w")
        saved = tasks.profile("gentle", op="choose")
        assert saved["version"] == 1
        con.rollback()
        assert tasks.profile()["version"] == 0
        fails("not_found", tasks.profile, undo="choose", op="undo")
        assert tasks.profile("dense", op="choose")["name"] == "dense"
    finally:
        con.close()


def test_legacy_setting_and_compare_and_set_revision(tmp_path):
    store = Store(tmp_path / "legacy.sqlite3")
    try:
        # The v0.5.0 setting row predates per-setting revisions.
        store.db.execute(
            "INSERT INTO daymark_scheduler VALUES(?,?)",
            ("setting:w:profile", '{"name":"legacy","strategies":[]}'),
        )
        store.db.commit()
        tasks = Tasks(store, "w")
        assert tasks.profile() == {
            "name": "legacy",
            "strategies": [],
            "workspace": "w",
            "version": 1,
        }
        changed = tasks.profile("gentle", expected_version=1, op="adopt")
        assert changed["version"] == 2
        fails("conflict", store.set_setting, "w", "profile", None, expected_version=1)
        restored = tasks.profile(undo="adopt", expected_version=2)
        assert restored["version"] == 3 and restored["name"] == "legacy"
        assert store.setting("w", "profile") == {"name": "legacy", "strategies": []}
    finally:
        store.close()


def test_simultaneous_same_operation_returns_one_named_copy(tmp_path):
    path = tmp_path / "race.sqlite3"
    stores = [Store(path), Store(path)]
    gate = threading.Barrier(2)

    def save(store):
        gate.wait(timeout=5)
        return Tasks(store).save_profile("quiet", profile="gentle", op="same")

    try:
        with ThreadPoolExecutor(max_workers=2) as workers:
            results = list(workers.map(save, stores))
        assert results[0] == results[1]
        assert Tasks(stores[0]).saved_profiles() == [results[0]]
        assert stores[0].setting_state("default", "profile.library")["version"] == 1
    finally:
        for store in stores:
            store.close()


def test_cli_prints_reversible_quoted_command_and_accepts_retries(tmp_path, monkeypatch, capsys):
    import shlex

    monkeypatch.setenv("DAYMARK_DB", str(tmp_path / "test.sqlite3"))
    assert main(["init"]) == 0
    capsys.readouterr()
    args = [
        "--workspace=--studio group",
        "profile",
        "gentle",
        "--if-version",
        "0",
        "--op=--first ' choice",
    ]
    assert main(args) == 0
    first = capsys.readouterr().out
    command = shlex.split(first.split("Restore the previous settings in this database with ")[1])
    assert command[1] == f"--db={tmp_path / 'test.sqlite3'}"
    monkeypatch.setenv("DAYMARK_DB", str(tmp_path / "other.sqlite3"))
    assert main(command[1:]) == 0
    assert not (tmp_path / "other.sqlite3").exists()
    monkeypatch.setenv("DAYMARK_DB", str(tmp_path / "test.sqlite3"))
    restored = capsys.readouterr().out
    assert "profile: plain" in restored and "active version 2" in restored
    assert main(args) == 0 and capsys.readouterr().out == first
    assert main(["--workspace=--studio group", "profile", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["version"] == 2


def test_catalog_discovers_a_time_window_and_inspects_bounds_without_loading_plugins(
    tmp_path, monkeypatch, capsys
):
    db = tmp_path / "preferences.db"
    monkeypatch.setenv("DAYMARK_DB", str(db))
    store = Store(db)
    try:
        tasks = Tasks(store)
        for i in range(32):
            tasks.save_profile(f"studio-{i}", profile="assistant")
        catalog = tasks.profile_catalog()
        assert catalog["adjustments"]["time-fit"] == {"minutes": 30}
        assert all(set(entry) == {"name", "version"} for entry in catalog["saved"])
        assert len(json.dumps(catalog, separators=(",", ":"))) < 2500
        mcp_catalog = call(tasks, "daymark_view", {"view": "profiles"})
        assert "time-fit.minutes=30" in mcp_catalog
        mcp_detail = call(
            tasks, "daymark_view", {"view": "profiles", "strategy": "time-fit", "json": True}
        )
        detail = json.loads(mcp_detail)
        param = detail["parameters"]["minutes"]
        assert (param["minimum"], param["default"], param["maximum"]) == (1, 30, 1440)
        assert detail["evidence"] == "practitioner" and "individually" in detail["summary"]
        assert main(["strategies", "time-fit", "--json"]) == 0
        assert json.loads(capsys.readouterr().out) == detail
        fails(
            "invalid",
            call,
            tasks,
            "daymark_view",
            {"view": "profiles", "profile": "plain", "strategy": "time-fit"},
        )

        def unexpected_plugin_import(*args, **kwargs):
            pytest.fail("Strategy discovery evaluated an installed package")

        monkeypatch.setattr("importlib.metadata.entry_points", unexpected_plugin_import)
        fails("not_found", tasks.profile_catalog, strategy="not-loaded")
    finally:
        store.close()
