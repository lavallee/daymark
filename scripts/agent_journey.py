"""Create a disposable real workspace and grade the outcome of one agent session."""

import argparse
import json
import shlex
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

from daymark import Store, Tasks
from daymark.strategies import service_builtins

CASES = {
    "move-plan": "I will work on draft on May 6, 2030 instead. Its May 10 deadline stays. Make the change.",
    "two-dates": "Add two tasks. I intend to collect badges on May 4, 2030. I must submit the permit by May 7, 2030. Use the titles Collect badges and Submit permit.",
    "on-hold": "The print shop owes us the proof. Put the existing press task on hold for their reply. We haven't agreed on a follow-up date.",
    "suggestion": "Keep Design a poster as a suggestion I can decide on later.",
    "one-option": "Show me one ready task from the studio scope. Give me its ID. This is just for now. Leave the workspace as you found it.",
    "blocked": "Which unfinished prerequisite is keeping the invitation from being sent? Give me its ID and leave the tasks as they are.",
    "standing-window": "For everyone using this workspace, keep our neutral wording about dates. From now on, offer up to two ready options that each fit a 15-minute gap. Keep the tasks and our separately saved review as they are.",
    "return-after-week": "I have been away for a week. As of {day}, which agreed tasks had a hard deadline already past or coming by {cutoff}? Give me their IDs and deadlines, and tell me who owes a reply. Leave everything as you found it.",
}
MARKER = "daymark-synthetic-journey-v1"
WS = "trial"


def _week_away(tasks):
    today = datetime.now(UTC).date()

    def day(offset):
        return (today + timedelta(days=offset)).isoformat()

    for i in range(160):
        tasks.add(f"Revisit layout option {i}", id=f"card-{i:04}", plan=day(-7))
    for i in range(36):
        tasks.add(f"Proof variation {i}", id=f"line-{i:02}", waiting_on=f"draft response {i}")
    tasks.add("Submit paper sizes", id="g6j2", due=day(-3))
    tasks.add("Collect print authorization", id="d2k4", due=day(1) + "T16:00", waiting_on="printer confirmation")
    tasks.propose("Approval for an optional display", id="a3s4", due=day(1))
    tasks.add("Fold sample", id="s9t8", target=day(-2))
    tasks.add("Send final layout", id="h7f4", due=day(4))
    return CASES["return-after-week"].format(day=day(0), cutoff=day(2))


def setup(case, folder):
    folder.mkdir(parents=True, exist_ok=False)
    db = folder / "journey.sqlite3"
    store = Store(db)
    question = CASES[case]
    try:
        tasks = Tasks(store, WS, actor="coordinator", timezone="UTC")
        if case == "move-plan":
            tasks.add("Draft program", id="draft", plan="2030-05-03", due="2030-05-10")
        elif case == "on-hold":
            tasks.add("Print program", id="press")
        elif case == "one-option":
            for i in range(3):
                tasks.add(f"Studio proof {i}", id=f"card-{i}", scope="studio")
            tasks.add("Home project", id="home", scope="home", priority=1)
        elif case == "blocked":
            tasks.add("Approve text", id="approval")
            tasks.add("Send invitation", id="invite", after=["approval"])
        elif case == "return-after-week":
            question = _week_away(tasks)
        elif case == "standing-window":
            tasks.profile("plain", settings=["neutral-aging"])
            tasks.save_profile("weekly", profile="plain", settings=["project-review"])
            for task_id, minutes in (
                ("proof", 45),
                ("layout", 60),
                ("unknown", None),
                ("just-over", 16),
                ("boundary", 15),
            ):
                tasks.add(
                    task_id.capitalize(),
                    id=task_id,
                    scope="press",
                    minutes=minutes,
                    priority=2 if task_id == "boundary" else 1,
                )
            for task_id, minutes in (("fold", 5), ("label", 7), ("sort", 9)):
                tasks.add(
                    task_id.capitalize(), id=task_id, scope="studio", minutes=minutes, priority=2
                )
        trial = {
            "format": MARKER,
            "case": case,
            "baseline_head": store.head(WS),
            "baseline": store.list(WS, include_archived=True),
        }
        if case == "standing-window":
            trial["active_version"] = store.setting_state(WS, "profile")["version"]
            trial["library"] = store.setting_state(WS, "profile.library")
        (folder / "trial.json").write_text(json.dumps(trial, indent=2) + "\n")
    finally:
        store.close()
    command = shlex.join(
        ["daymark", "--db", str(db.resolve()), "--workspace", WS, "--as", "reviewer", "--tz", "UTC"]
    )
    return f"Work in this synthetic workspace:\n{command} prime\n\n{question}"


def _date(row, field):
    return (row.get(field) or {}).get("date")


def _return_answers(answer, before):
    expected = {task_id: before[task_id] for task_id in ("g6j2", "d2k4")}
    if not isinstance(answer, list) or not all(isinstance(row, dict) for row in answer):
        return False, False, False
    if not all(isinstance(row.get("id"), str) for row in answer):
        return False, False, False
    given = {row["id"]: row for row in answer}
    matches = len(answer) == len(expected) and given.keys() == expected.keys()
    if not matches:
        return False, False, False
    # An exact civil date and a timezone-aware instant make different commitments.
    civil = expected["g6j2"]["deadline"]["date"]
    try:
        instant = datetime.fromisoformat(given["d2k4"]["deadline"])
        expected_instant = datetime.fromisoformat(expected["d2k4"]["deadline"]["at"])
    except (KeyError, TypeError, ValueError):
        dates = False
    else:
        dates = given["g6j2"].get("deadline") == civil and instant == expected_instant
    waits = all(
        "waiting_on" in given[task_id] and given[task_id]["waiting_on"] == row["waiting_on"]
        for task_id, row in expected.items()
    )
    return matches, dates, waits


def grade(store, trial, answer):
    case = trial["case"]
    entities = store.list(WS, include_archived=True)
    before = {row["id"]: row for row in trial["baseline"]}
    rows = {row["id"]: row for row in entities}
    tasks = [row for row in entities if row["kind"] == "task" and not row["archived"]]
    if case == "standing-window":
        reader = Tasks(store, WS, actor="returning-reader", timezone="UTC")
        with service_builtins():
            default = reader.advise()
            press = reader.advise(scope="press")
            studio = reader.advise(scope="studio")
        return {
            "standing_profile_changed": store.setting_state(WS, "profile")["version"]
            > trial["active_version"],
            "saved_review_unchanged": store.setting_state(WS, "profile.library")
            == trial["library"],
            "entities_and_events_unchanged": before == rows
            and store.head(WS) == trial["baseline_head"],
            "all_stages_available_to_the_scorer": not (default.profile_source or {}).get("skipped"),
            "neutral_date_wording_retained": default.tone == "neutral",
            "two_known_fits_from_the_workspace": len(default.picks) == 2
            and all(
                row["estimate_minutes"] is not None and row["estimate_minutes"] <= 15
                for row in default.picks
            ),
            "exact_fit_survives_longer_and_unknown_estimates": [row["id"] for row in press.picks]
            == ["boundary"],
            "two_of_three_short_options": len(studio.picks) == 2
            and {row["id"] for row in studio.picks} <= {"fold", "label", "sort"},
        }
    checks = {
        "workspace_preferences_unchanged": all(
            store.setting_state(WS, key)["version"] == 0 for key in ("profile", "profile.library")
        )
    }
    if case in {"blocked", "one-option", "return-after-week"}:
        if case == "return-after-week":
            ids, dates, waits = _return_answers(answer, before)
            checks.update(
                agreed_deadlines_only=ids,
                precise_deadlines=dates,
                recorded_waits=waits,
                decision_policy_unchanged=store.policy(WS) == {"deciders": []},
            )
        else:
            checks["answered_with_matching_id"] = (
                answer == "approval" if case == "blocked" else answer in {"card-0", "card-1", "card-2"}
            )
        checks["entities_and_events_unchanged"] = (
            before == rows and store.head(WS) == trial["baseline_head"]
        )
    elif case == "move-plan":
        row = rows.get("draft", {})
        checks["planned_day_changed"] = _date(row, "planned") == "2030-05-06"
        checks["deadline_preserved"] = _date(row, "deadline") == "2030-05-10"
        expected = {
            **before["draft"],
            **{k: row.get(k) for k in ("planned", "version", "updated_at")},
        }
        checks["only_the_requested_task_field_changed"] = rows == {"draft": expected}
    elif case == "two-dates":
        by_title = {row["title"]: row for row in tasks}
        badges, permit = by_title.get("Collect badges", {}), by_title.get("Submit permit", {})
        checks["two_requested_tasks"] = len(entities) == len(tasks) == 2 and bool(badges and permit)
        checks["intention_preserved"] = _date(badges, "planned") == "2030-05-04" and not badges.get(
            "deadline"
        )
        checks["hard_cutoff_preserved"] = _date(
            permit, "deadline"
        ) == "2030-05-07" and not permit.get("planned")
        checks["no_invented_commitment_details"] = all(
            row["status"] == "open"
            and not any(row[k] for k in ("priority", "earliest_start", "latest_target"))
            for row in tasks
        )
    elif case == "suggestion":
        checks["one_pending_proposal"] = len(entities) == len(tasks) == 1 and (
            tasks[0]["title"],
            tasks[0]["status"],
        ) == ("Design a poster", "proposed")
        checks["no_unrequested_dates"] = all(
            not row[field]
            for row in tasks
            for field in ("planned", "deadline", "earliest_start", "latest_target")
        )
    elif case == "on-hold":
        row = rows.get("press", {})
        checks["existing_task_is_waiting"] = (
            set(rows) == {"press"} and row.get("status") == "waiting"
        )
        checks["waiting_party_recorded"] = "shop" in (row.get("waiting_on") or "").casefold()
        expected = {
            **before["press"],
            **{k: row.get(k) for k in ("status", "waiting_on", "version", "updated_at")},
        }
        checks["only_wait_details_changed"] = rows == {"press": expected}
        checks["followup_date_unset"] = not any(
            row.get(k) for k in ("planned", "earliest_start", "deadline", "latest_target")
        )
    return checks


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="action", required=True)
    start = commands.add_parser(
        "start", help="Create a new synthetic workspace and print the person's request"
    )
    start.add_argument("case", choices=CASES)
    start.add_argument("directory", type=Path, help="A new directory")
    check = commands.add_parser("check", help="Read the workspace and grade the resulting state")
    check.add_argument("directory", type=Path)
    reply = check.add_mutually_exclusive_group()
    reply.add_argument("--answer", help="The ID returned to the person for a read-only case")
    reply.add_argument(
        "--answer-file", type=Path,
        help="JSON array of returned id, deadline and waiting_on for return-after-week",
    )
    args = parser.parse_args()
    if args.action == "start":
        if args.directory.exists():
            parser.error("start needs a new directory")
        print(setup(args.case, args.directory))
        return 0
    trial = json.loads((args.directory / "trial.json").read_text())
    if trial.get("format") != MARKER or trial.get("case") not in CASES:
        parser.error("not a Daymark synthetic trial")
    answer = args.answer
    if args.answer_file:
        if trial["case"] != "return-after-week":
            parser.error("--answer-file applies to return-after-week")
        try:
            answer = json.loads(args.answer_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            parser.error("--answer-file must name a readable JSON file")
    db = (args.directory / "journey.sqlite3").resolve()
    connection = sqlite3.connect(f"{db.as_uri()}?mode=ro", uri=True)
    try:
        checks = grade(Store(connection, upgrade=False), trial, answer)
    finally:
        connection.close()
    print(
        json.dumps(
            {"case": trial["case"], "passed": all(checks.values()), "checks": checks}, indent=2
        )
    )
    return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
