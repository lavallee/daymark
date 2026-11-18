"""A person's named ways of working are workspace choices, always explicit."""

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing

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
        first_step = tasks.profile_catalog(strategy="first-step")
        assert first_step["evidence"] == "clinical"
        assert "therapy program" in first_step["evidence_note"]
        assert first_step["evidence_note"] in call(
            tasks, "daymark_view", {"view": "profiles", "strategy": "first-step"}
        )
        fails(
            "invalid",
            call,
            tasks,
            "daymark_view",
            {"view": "profiles", "profile": "plain", "strategy": "time-fit"},
        )

        class Installed:
            name = "proof-review"

            def load(self):
                pytest.fail("Strategy discovery executed an installed package")

        def entry_points(*, group, name=None):
            return (
                [Installed()]
                if group == "daymark.strategies" and name in (None, "proof-review")
                else []
            )

        monkeypatch.setattr("importlib.metadata.entry_points", entry_points)
        assert tasks.profile_catalog()["installed_plugins"] == ["proof-review"]
        assert tasks.profile_catalog(strategy="proof-review")["preview"] == {
            "set": ["proof-review"]
        }
        fails("not_found", tasks.profile_catalog, strategy="not-loaded")
    finally:
        store.close()


def test_import_of_a_partly_unavailable_profile_requires_an_explicit_drop(tmp_path):
    with closing(Store(tmp_path / "config.db")) as store:
        tasks = Tasks(store)
        config = {"name": "previous", "strategies": [], "skipped": ["missing-plugin"]}
        fails("invalid", tasks.save_profile, "review", profile=config)
        assert tasks.saved_profiles() == []
        saved = tasks.save_profile("review", profile=config, drops=["missing-plugin"])
        assert saved["profile"] == {"name": "review", "strategies": []}
        for bad in (None, "missing", [3], [""]):
            fails("invalid", tasks.save_profile, "broken", profile={**config, "skipped": bad})
        fails("invalid", tasks.advise, settings=["x" * 65_537])


def test_database_backup_preserves_profile_replay_and_guarded_undo_while_entity_export_omits_it(
    tmp_path, capsys
):
    source = tmp_path / "source.sqlite3"
    backup = tmp_path / "backup.sqlite3"
    restored = tmp_path / "restored.sqlite3"
    with closing(Store(source)) as store:
        tasks = Tasks(store, "studio")
        tasks.add("Sample proof", id="proof")
        entities = store.export("studio")
        active = tasks.profile("gentle", expected_version=0, op="choose")
        named = tasks.save_profile("quiet", expected_version=0, op="copy")
        assert store.export("studio") == entities
        assert main(["--db", str(source), "backup", "--file", str(backup)]) == 0
        tasks.profile("dense", expected_version=1, op="later")
    assert main(["--db", str(restored), "restore", "--file", str(backup)]) == 0
    capsys.readouterr()
    with closing(Store(restored, read_only=True)) as store:
        reader = Tasks(store, "studio")
        assert reader.profile()["name"] == "gentle" and reader.saved_profiles("quiet") == named
        assert (
            reader.advise(settings=["project-review"]).notes[-1].text
            == "No open projects to review."
        )
        assert reader.profile_catalog(strategy="time-fit")["parameters"]["minutes"]["default"] == 30
        assert "shortlist" in call(reader, "daymark_view", {"view": "profiles"}, read_only=True)
        fails(
            "forbidden",
            call,
            reader,
            "daymark_act",
            {"action": "use_profile", "id": "dense"},
            read_only=True,
        )
        assert store.db.total_changes == 0
    with closing(Store(restored)) as store:
        tasks = Tasks(store, "studio")
        assert tasks.profile("gentle", expected_version=0, op="choose") == active
        assert tasks.profile(undo="choose", expected_version=1, op="restore")["version"] == 2
        assert tasks.profile()["name"] == "plain"
        assert store.export("studio") == entities
    with closing(Store(source)) as store:
        assert Tasks(store, "studio").profile()["name"] == "dense"


def test_empty_invalid_adjustments_are_not_treated_as_reads_or_saved(tmp_path):
    with closing(Store(tmp_path / "validation.db")) as store:
        tasks = Tasks(store)
        for method in (tasks.profile, tasks.advise):
            fails("invalid", method, settings={})
            fails("invalid", method, drops="")
        fails("invalid", tasks.save_profile, "changed", settings=None)
        assert tasks.profile()["version"] == 0 and not tasks.saved_profiles()


def test_plain_profile_helpers_preserve_minimal_host_adapter_and_return_shapes():
    from daymark import strategies
    from daymark.strategies.profiles import PROFILES

    class Settings:
        def __init__(self):
            self.values = {}
            self.writes = 0

        def set_setting(self, workspace, name, value):
            self.values[workspace, name] = value
            self.writes += 1

        def setting(self, workspace, name):
            return self.values.get((workspace, name))

    engine = Settings()
    tasks = Tasks(engine, "studio")
    assert strategies.stored_profile(tasks) == PROFILES["plain"] and not engine.writes
    result = strategies.store_profile(
        tasks, {"name": "daily", "strategies": [], "skipped": ["gone"]}
    )
    assert result == {"name": "daily", "strategies": []}
    assert strategies.stored_profile(tasks) == result == tasks.profile()
    assert engine.writes == 1


def test_installed_strategy_with_a_missing_dependency_keeps_active_advice_available(
    tmp_path, monkeypatch
):
    from daymark import strategies

    class Broken:
        name = "sample-proof-warnings"

        def load(self):
            raise ModuleNotFoundError("Optional sample library unavailable")

    def entries(*, group, name=None):
        return [Broken()] if group == "daymark.strategies" and name in (None, Broken.name) else []

    monkeypatch.setattr("importlib.metadata.entry_points", entries)
    with closing(Store(tmp_path / "plugins.db")) as store:
        tasks = Tasks(store)
        tasks.add("Check samples", id="samples")
        raw = {"name": "editor", "strategies": [{"use": Broken.name}, {"use": "shortlist"}]}
        store.set_setting("default", "profile", raw)
        before = store.setting_state("default", "profile")
        advice = tasks.advise()
        assert [task["id"] for task in advice.picks] == ["samples"]
        assert any(Broken.name in n.text for n in advice.notes)
        assert store.setting_state("default", "profile") == before
        fails("invalid", tasks.advise, profile="plain", settings=[Broken.name])
        monkeypatch.setattr("importlib.metadata.entry_points", lambda **kw: [Broken(), Broken()])
        fails("conflict", strategies.get, Broken.name)


def test_retired_parameters_remain_visible_and_survive_unrelated_edits_until_explicitly_removed(
    tmp_path,
):
    from daymark.render import profile_text

    with closing(Store(tmp_path / "parameters.db")) as store:
        tasks = Tasks(store, "studio")
        tasks.add("First", id="first")
        original = {
            "name": "review",
            "strategies": [{"use": "shortlist", "with": {"former_size": 1}}],
        }
        store.set_setting("studio", "profile", original)
        current = tasks.profile()
        stage = current["strategies"][0]
        assert stage["with"] == {"size": 6} and stage["unavailable_parameters"] == {
            "former_size": 1
        }
        assert "shortlist.former_size=1" in profile_text(current)
        assert any("shortlist.former_size" in n.text for n in tasks.advise().notes)
        assert store.setting("studio", "profile") == original
        adjusted = tasks.profile(settings=["neutral-aging"], expected_version=1, op="adjust")
        assert adjusted["strategies"][0]["unavailable_parameters"] == {"former_size": 1}
        saved = tasks.save_profile("portable", profile=adjusted)
        assert saved["profile"]["strategies"][0]["with"]["former_size"] == 1
        # Independent changes keep the old setting in raw data and their guarded undo restores it.
        assert store.setting("studio", "profile")["strategies"][0]["with"]["former_size"] == 1
        resolved = tasks.profile(
            drops=["shortlist.former_size"],
            settings=["shortlist.size=1"],
            expected_version=2,
            op="repair",
        )
        assert resolved["strategies"][0] == {"use": "shortlist", "with": {"size": 1}}
        assert not any("unavailable" in n.text for n in tasks.advise().notes)
        assert tasks.profile(undo="repair", expected_version=3)["strategies"][0][
            "unavailable_parameters"
        ] == {"former_size": 1}
        assert tasks.saved_profiles("portable") == saved


def test_unavailable_stages_keep_their_settings_and_order_across_independent_edits(tmp_path):
    first = {"use": "proof-filter", "with": {"proof": {"include": ["draft"]}, "limit": 2}}
    last = {"use": "proof-outline", "with": {"depth": 1}}
    stages = [{"use": "shortlist", "with": {"size": 3}}, first, {"use": "neutral-aging"}, last]
    with closing(Store(tmp_path / "press.db")) as store:
        tasks = Tasks(store, "press")
        # This portable export comes from a different installation.
        named = tasks.save_profile("proof", profile={"name": "proof", "strategies": stages})
        assert named["profile"]["strategies"][1::2] == [first, last]
        result = tasks.profile("proof", expected_version=0, op="choose")
        assert result["skipped"] == ["proof-filter", "proof-outline"]
        edited = tasks.profile(
            drops=["shortlist"], settings=["shortlist.size=1"], expected_version=1, op="move"
        )
        assert (
            tasks.profile(
                drops=["shortlist"], settings=["shortlist.size=1"], expected_version=1, op="move"
            )
            == edited
        )
        expected = [
            first,
            {"use": "neutral-aging", "with": {}},
            last,
            {"use": "shortlist", "with": {"size": 1}},
        ]
        assert store.setting("press", "profile")["strategies"] == expected
        assert tasks.save_profile("backup")["profile"]["strategies"] == expected
        assert (
            tasks.save_profile("copy", profile="proof")["profile"]["strategies"]
            == named["profile"]["strategies"]
        )
        fails("not_found", tasks.profile, settings=["proof-filter.limit=1"])
        fails(
            "not_found", tasks.save_profile, "invalid", profile="proof", settings=["proof-outline"]
        )
        fails("conflict", tasks.profile, drops=["proof-filter"], expected_version=1)
        assert tasks.profile() == {
            k: v for k, v in edited.items() if k not in {"operation_id", "undo"}
        }
        dropped = tasks.profile(drops=["proof-filter"], expected_version=2, op="drop")
        assert dropped["skipped"] == ["proof-outline"]
        assert tasks.profile(undo="drop", expected_version=3)["skipped"] == [
            "proof-filter",
            "proof-outline",
        ]
        assert store.setting("press", "profile")["strategies"] == expected
        for stage in (
            {"use": "proof-filter", "with": [1]},
            {"use": "proof-filter", "unknown": 3},
            {"use": "shortlist", "with": {"future": True}},
            {"use": "shortlist", "with": {"size": 0}},
        ):
            fails("invalid", tasks.save_profile, "invalid", profile={"strategies": [stage]})
        assert tasks.saved_profiles("backup")["profile"]["strategies"] == expected


def test_mcp_only_profile_restore_is_replayable_and_cannot_undo_an_entity_with_the_same_id(
    tmp_path,
):
    from daymark.mcp import handle

    def invoke(tasks, name, arguments, **options):
        request = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        }
        return handle(tasks, request, **options)["result"]

    with closing(Store(tmp_path / "restore.db")) as store:
        tasks = Tasks(store, "studio")
        tasks.add("Unrelated proof", id="choice")
        tasks.edit("choice", title="Approved title")
        before = store.export("studio")
        adopted = invoke(
            tasks,
            "daymark_act",
            {"action": "use_profile", "id": "gentle", "op": "choice", "expected_version": 0},
        )
        receipt = adopted["structuredContent"]
        restore = receipt["restore_call"]
        assert restore["name"] == "daymark_act" and restore["arguments"] == {
            "action": "restore_profile",
            "id": "choice",
            "expected_version": 1,
            "op": "choice:restore-profile",
        }
        assert "Restore this preference in MCP" in adopted["content"][0]["text"]
        assert invoke(tasks, **restore, read_only=True)["isError"]
        short = dict(restore["arguments"])
        del short["expected_version"]
        assert invoke(tasks, restore["name"], short)["isError"]
        assert invoke(tasks, restore["name"], {**restore["arguments"], "set": ["shortlist"]})[
            "isError"
        ]
        assert (
            invoke(Tasks(store, "other"), **restore)["structuredContent"]["error"]["code"]
            == "not_found"
        )
        restored = invoke(tasks, **restore)
        assert restored["structuredContent"]["name"] == "plain" and tasks.profile()["version"] == 2
        assert tasks.get("choice")["title"] == "Approved title" and store.export("studio") == before
        second = invoke(
            tasks,
            "daymark_act",
            {"action": "use_profile", "id": "dense", "op": "next", "expected_version": 2},
        )
        assert second["structuredContent"]["version"] == 3
        assert invoke(tasks, **restore) == restored and tasks.profile()["name"] == "dense"
        stale = invoke(tasks, **restored["structuredContent"]["restore_call"])
        assert stale["structuredContent"]["error"]["code"] == "conflict"
        assert "profiles" in stale["structuredContent"]["error"]["recovery"]
