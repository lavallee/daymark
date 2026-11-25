"""Daymark's command-line interface."""

from __future__ import annotations

import argparse
import json
import os
import shlex
import sqlite3
import sys
from contextlib import closing
from pathlib import Path
from typing import Any

from .snapshot_files import copy_database as _copy_database
from .snapshot_files import write_export

_QUICK_START = """Daymark keeps track of a person's tasks, plans and decisions across sessions.

Choose where to work
  daymark where                     Show the selected database and workspace
  daymark init --here               Set up here (daymark init is per-user)

Capture work
  daymark propose "Review cover"    Save a suggestion for someone's decision
  daymark add "Review cover"        Record work already agreed

Return to the same folder
  daymark prime --brief             See the current state
  daymark find --status proposed    Find suggestions still awaiting a decision
  daymark show ID                   Read one record

  daymark propose --help            See the options for one command
  daymark --help                    List every command and global option"""


def _activity_cursor(text):
    from .cursors import numeric_or_checkpoint

    return numeric_or_checkpoint(text)


def _json_file(path: str) -> Any:
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def _print(value: Any) -> None:
    print(json.dumps(value, default=str, sort_keys=True))


SIMPLE = {
    "prime", "activity", "ready", "agenda", "show", "add", "propose", "edit", "done", "skip", "cancel",
    "reopen", "archive", "unarchive", "undo", "accept", "dismiss", "claim", "take", "release", "note", "wait",
    "ask", "answer", "watch", "followup", "inbox", "digest", "seen",
    "resume", "defer", "block", "unblock", "remind", "find", "remember", "forget", "mcp",
    "focus", "unfocus", "project", "projects", "plan", "move", "changes", "goal", "goals",
    "tree", "advise", "profile", "strategies", "condition", "assess", "explore",
}  # fmt: skip


def data_home() -> Path:
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share") / "daymark"


def _location(explicit: str | None) -> tuple[str | None, str]:
    """The file used by an everyday command, and how it was selected."""
    if explicit:
        return explicit, "--db"
    if os.environ.get("DAYMARK_DB"):
        return os.environ["DAYMARK_DB"], "DAYMARK_DB"
    here = Path.cwd()
    for folder in (here, *here.parents):
        candidate = folder / ".daymark" / "daymark.sqlite3"
        if candidate.is_file():
            return str(candidate), "project"
    for path, source in (
        (here / "daymark.sqlite3", "legacy"),
        (data_home() / "daymark.sqlite3", "user"),
    ):
        if path.is_file():
            return str(path), source
    return None, "none"


def resolve_db(explicit: str | None) -> str | None:
    return _location(explicit)[0]


def _where(args) -> int:
    if args.url:
        from .render import endpoint_origin

        result = {"source": "--url", "endpoint": endpoint_origin(args.url),
                  "identity_source": "server credential"}
        message = f"Endpoint {result['endpoint']} (--url). The server credential selects the workspace and actor."
    else:
        file, source = _location(args.db)
        database = str(Path(file).absolute()) if file and file != ":memory:" else file
        actor = "agent" if args.actor is None else args.actor
        result = {"database": database, "source": source, "workspace": args.workspace,
                  "actor": actor, "file_exists": bool(file and Path(file).is_file())}
        message = f"Workspace {args.workspace}, actor {actor}."
        if file:
            message += f"\nDatabase {database} (selected by {source})."
            if file != ":memory:":
                real = str(Path(file).resolve())
                if database != real:
                    result["resolved_database"] = real
                    message += f"\nResolves to {real}."
                if not result["file_exists"]:
                    message += "\nThe selected file does not exist yet."
        else:
            message += "\nNo database selected. Use daymark init or daymark init --here."
    _print(result) if args.json else print(message)
    return 0


def _field_options(item: argparse.ArgumentParser, *, editing: bool) -> None:
    if editing:
        item.add_argument("--title")
    item.add_argument("--due", metavar="D", help="Hard deadline")
    item.add_argument("--plan", metavar="D", help="Intended day or time; may move freely")
    item.add_argument("--start", metavar="D", help="Not actionable before this")
    item.add_argument("--target", metavar="D", help="Soft latest target")
    item.add_argument(
        "--every", metavar="R", help="Repeat: day, week, '2 weeks', weekdays, mon,thu"
    )
    item.add_argument(
        "--after-completion", action="store_true", help="Repeat from the actual completion"
    )
    item.add_argument("-p", "--priority", type=int, choices=range(5), help="1 most important")
    item.add_argument("-d", "--description")
    item.add_argument("--after", action="append", metavar="ID", help="Depends on (repeatable)")
    item.add_argument("--parent", metavar="ID")
    item.add_argument("--project", metavar="ID")
    item.add_argument("--goal", metavar="ID")
    item.add_argument(
        "--assignee", metavar="WHO", help="A person's ID or exact name (an unknown name is refused)"
    )
    item.add_argument("--label", action="append", dest="labels", metavar="ID")
    item.add_argument("--context", action="append", dest="contexts", metavar="ID")
    item.add_argument("--waiting-on", metavar="WHO", help="A reply or prerequisite that parks open work")
    item.add_argument("--move", choices=["person", "answer", "agent"], help="Whose move it is")
    item.add_argument("--scope", metavar="NAME", help='Part of life it belongs to ("" clears)')
    item.add_argument(
        "--advances", action="append", metavar="ID", help="A condition this moves (repeatable)"
    )
    item.add_argument("--minutes", type=int, help="Estimated effort")
    item.add_argument("--stage", metavar="S", help="Stage on the project's board; sets the status")
    item.add_argument(
        "--is-milestone",
        action=argparse.BooleanOptionalAction,
        help="Mark (or unmark) this task as a milestone",
    )
    item.add_argument(
        "--milestone", metavar="ID", help='The milestone it counts toward ("" clears)'
    )
    item.add_argument("--type", metavar="T", help='One of its project\'s types ("" clears)')
    item.add_argument(
        "--set",
        action="append",
        dest="values",
        metavar="FIELD=VALUE",
        help="A project field's value (repeatable; empty clears)",
    )
    if editing:
        item.add_argument(
            "--managed-by", metavar="SYSTEM", help='A project\'s owning system ("" clears)'
        )
    if not editing:
        item.add_argument("--kind", default="task", help="task, project, goal, person, label, ...")
        item.add_argument("--note", help="First progress note")
        item.add_argument("--source", metavar="SYSTEM:KIND:ID", help="Origin; refuses duplicates")
        item.add_argument("--id", help="Choose the ID instead of a generated short one")


STATES = ["met", "partly_met", "unmet", "unknown"]


def _scope_filter(parser, help_text):
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--scope", metavar="NAME", help=help_text)
    group.add_argument("--unscoped", action="store_const", const="", dest="scope",
                       help="Only records with no scope")


def _simple_commands(sub) -> None:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--json", action="store_true", help="Compact JSON instead of text")
    write = argparse.ArgumentParser(add_help=False)
    write.add_argument("--op", help="Operation ID; repeat it to retry a write safely")

    def add(name, help, *, writes=False):
        return sub.add_parser(name, help=help, parents=[common, *([write] if writes else [])])

    add("where", "Explain which database and workspace everyday commands will use")
    init = sub.add_parser("init", help="Create a database: per-user, or --here for this folder")
    init.add_argument("--here", action="store_true", help="Use ./.daymark/ in this directory")
    prime = add("prime", "Print the current state and workflow guide; read this first")
    _scope_filter(prime, "Return a compact orientation for one scope")
    prime.add_argument("--brief", action="store_true", help="Skip the workflow guide")
    onboard = sub.add_parser("onboard", help="Print (or --write) the snippet for AGENTS.md")
    mode = onboard.add_mutually_exclusive_group()
    mode.add_argument("--mcp-config", choices=["claude", "codex"], help="Print a read-only MCP launch configuration for this installation")
    mode.add_argument("--skill", action="store_true", help="Print the full agent skill for this installed version")
    mode.add_argument("--write", metavar="FILE", help="Insert or refresh the snippet in FILE")
    mode.add_argument("--check", metavar="FILE", help="Check the snippet without writing (1 if missing or outdated)")
    advise = add("advise", "What to offer and what to mention, by the workspace's profile")
    advise.add_argument("--profile", metavar="NAME", help="Try a profile without adopting it")
    _scope_filter(advise, "Only this scope")
    advise.add_argument("--context", metavar="ID", help="Only tasks tagged with this context")
    profile = add("profile", "Show or choose the way of working for this workspace", writes=True)
    profile.add_argument("name", nargs="?", help="Adopt this profile (see `daymark strategies`)")
    profile_action = profile.add_mutually_exclusive_group()
    profile_action.add_argument("--saved", nargs="?", const="", metavar="NAME",
                                help="List saved profiles, or inspect one by name")
    profile_action.add_argument("--save-as", metavar="NAME", help="Save a named copy without adopting it")
    profile_action.add_argument("--remove", metavar="NAME", help="Remove a named copy")
    profile_action.add_argument("--undo", metavar="OP", help="Restore the settings before an active profile change")
    profile.add_argument("--if-version", type=int, metavar="N", help="Expected active or saved version")
    profile.add_argument("--from-file", metavar="FILE", help="Import a profile JSON file with --save-as")
    for command in (advise, profile):
        command.add_argument(
            "--set", action="append", default=[], metavar="STRATEGY[.PARAM=VALUE]",
            help="Include a strategy or adjust one parameter",
        )
        command.add_argument("--drop", action="append", default=[], metavar="STRATEGY")
    methods = add("strategies", "List strategies and profiles, or inspect one strategy")
    methods.add_argument("strategy", nargs="?", metavar="NAME", help="Show purpose, parameters and limits")
    ready = add("ready", "Tasks that can be done now, most pressing first")
    ready.add_argument("--project", metavar="ID", help="Only explicit members of this project")
    ready.add_argument("--context", metavar="ID", help="Only tasks tagged with this context")
    ready_detail = ready.add_mutually_exclusive_group()
    ready_detail.add_argument("--explain", action="store_true", help="Show why live work is held back within these filters")
    ready_detail.add_argument("--brief", action="store_true", help="Short task previews and ordering reasons")
    ready.add_argument("-n", "--limit", type=int, default=20)
    ready.add_argument("--move", choices=["person", "answer", "agent"], help="Only this next move")
    for view in (ready,):
        _scope_filter(view, "Only this scope")
    agenda = add("agenda", "Overdue, today, the days ahead, waiting and proposed")
    agenda.add_argument("--context", metavar="ID", help="Only tasks tagged with this context")
    agenda.add_argument("--days", type=int, default=7)
    agenda.add_argument("--deadlines", action="store_true", help="Only overdue and upcoming hard deadlines, including today")
    agenda.add_argument("--local-day", action="store_true", help="Hard deadlines by day in the session timezone; separate civil dates")
    _scope_filter(agenda, "Only this scope")
    activity = add("activity", "Recent workspace changes and handoff notes")
    activity.add_argument("--after", type=_activity_cursor, metavar="CURSOR", help="Read on from a saved cursor")
    activity.add_argument("-n", "--limit", type=int, default=20, help="Events per page (1–100)")
    show = add("show", "One entity with blockers, notes, reminders and history")
    selector = show.add_mutually_exclusive_group(required=True)
    selector.add_argument("id", nargs="?")
    selector.add_argument("--source", metavar="SYSTEM:KIND:ID", help="Inspect the entity captured from this source")
    detail = show.add_mutually_exclusive_group()
    detail.add_argument(
        "--forecast", choices=("skip", "done"), help="Read-only recurrence forecast"
    )
    detail.add_argument("--brief", action="store_true", help="Bounded handoff with recent notes")
    detail.add_argument("--before-note", metavar="NOTE_ID", help="Brief with the preceding note page")
    detail.add_argument("--since-version", type=int, metavar="N", help="Compare the current record with a reviewed version")
    detail.add_argument("--note", metavar="NOTE_ID", help="Read one complete note from its ID")
    for name, help in (("add", "Capture a task"), ("propose", "Suggest a task for a person")):
        item = add(name, help, writes=True)
        item.add_argument("title")
        _field_options(item, editing=False)
    edit = add("edit", 'Change fields; pass "" to clear a date or link', writes=True)
    edit.add_argument("id")
    edit.add_argument("--if-review", metavar="TOKEN", help="Match the exact entity revision returned by show")
    edit.add_argument("--if-version", type=int, help="Refuse if the task changed since this version")
    _field_options(edit, editing=True)
    for name, help in (
        ("done", "Complete a task"),
        ("skip", "Pass over one occurrence of a repeating task"),
        ("cancel", "Cancel a task"),
        ("reopen", "Reopen a finished task"),
        ("archive", "Hide an entity without deleting it"),
        ("unarchive", "Restore a hidden record and keep its status"),
        ("undo", "Restore the previous state of an entity"),
        ("accept", "Accept a proposal (a person's decision)"),
        ("dismiss", "Dismiss a proposal"),
        ("release", "Give back a claim"),
        ("resume", "Stop waiting; make the task open again"),
        ("forget", "Archive a remembered fact"),
        ("unfocus", "Take a task off the short list"),
    ):
        item = add(name, help, writes=True)
        item.add_argument("id")
        if name in {"accept", "dismiss", "forget", "undo", "unarchive", "done", "release", "resume"}:
            item.add_argument("--if-version", type=int, metavar="N", help="Refuse if the record changed since this version")
        if name in {"accept", "dismiss", "forget", "undo", "unarchive", "resume"}:
            item.add_argument("--if-review", metavar="TOKEN", help="Match the exact reviewed entity revision")
        if name in {"done", "release"}:
            item.add_argument("--note", help="Closing note" if name == "done" else "Handoff note")
    claim = add("claim", "Take a task so others leave it alone", writes=True)
    claim.add_argument("id")
    claim.add_argument("--minutes", type=int, default=60, help="Claim length (default 60)")
    take = add("take", "Claim one ready task explicitly delegated to an agent", writes=True)
    take.add_argument("--minutes", type=int, default=60, help="Claim length (default 60)")
    take.add_argument("--context", metavar="ID", help="Only take tasks tagged with this context")
    take.add_argument("--project", metavar="ID", help="Only take an explicitly delegated member of this project")
    _scope_filter(take, "Only take from this scope")
    note = add("note", "Append a progress note", writes=True)
    note.add_argument("id")
    note.add_argument("text")
    ask = add("ask", "Hand a question about a task to a person or another actor", writes=True)
    ask.add_argument("id")
    ask.add_argument("question")
    ask.add_argument("--to", required=True, metavar="WHO", help="Who should answer")
    ask.add_argument("--option", action="append", default=[], metavar="TEXT", help="A choice to offer (repeat, up to 6)")
    ask.add_argument("--recommend", type=int, metavar="N", help="Number of the option you recommend")
    answer = add("answer", "Answer the open question on a task", writes=True)
    answer.add_argument("id")
    answer.add_argument("text", nargs="?")
    answer.add_argument("--if-question", metavar="KEY", help="Match the exact question from the inbox")
    answer.add_argument("--choice", type=int, metavar="N", help="Number of the chosen option")
    answer.add_argument("--heard-from", metavar="WHO", help="Relay an answer this person gave somewhere else")
    followup = add("followup", "Leave a task for an agent to find after an interval", writes=True)
    followup.add_argument("id")
    followup.add_argument("text")
    followup.add_argument("--in", dest="after", required=True, metavar="INTERVAL", help="10min, 2h or 1d")
    followup.add_argument("--for", dest="for_", metavar="WHO", help="Who it is for (default: you)")
    inbox = add("inbox", "What is addressed to one actor and needs them now")
    inbox.add_argument("--for", dest="for_", metavar="WHO", help="Exact actor name (default: --as)")
    inbox.add_argument("--after", type=_activity_cursor, metavar="CURSOR", help="Also read mentions after this cursor")
    inbox.add_argument("--count", action="store_true", help="Print counts and the cursor only, for a hook or a scheduler")
    digest = add("digest", "What needs a person and what changed since they last looked")
    digest.add_argument("--for", dest="for_", metavar="PERSON", help="The person (default: --as)")
    seen = add("seen", "Mark a person's digest read up to a cursor", writes=True)
    seen.add_argument("--for", dest="for_", metavar="PERSON", help="The person (default: --as)")
    seen.add_argument("--through", type=_activity_cursor, metavar="CURSOR", help="Default: the newest change")
    watch = add("watch", "Block until someone else changes a task, or until the timeout")
    watch.add_argument("ids", nargs="*", metavar="ID", help="Tasks to watch (default: the whole workspace)")
    watch.add_argument("--after", type=_activity_cursor, metavar="CURSOR", help="Continue from a saved cursor")
    watch.add_argument("--by", metavar="ACTOR", help="Only changes by this actor")
    watch.add_argument("--include-own", action="store_true", help="Also wake for this actor's own changes")
    watch.add_argument("--timeout", default="30min", metavar="INTERVAL", help="Give up after this long (default 30min; 0 checks once)")
    wait = add("wait", "Park a task on someone or something", writes=True)
    wait.add_argument("id")
    wait.add_argument("--on", required=True, metavar="WHO")
    wait.add_argument("--if-version", type=int, metavar="N", help="Require the reviewed task version")
    wait.add_argument("--if-review", metavar="TOKEN", help="Match the exact reviewed task revision")
    wait.add_argument("--until", metavar="D", help="Also hide it until this date")
    defer = add("defer", "Hide a task until a date; the deadline stays", writes=True)
    defer.add_argument("id")
    defer.add_argument("until", metavar="D")
    for name in ("block", "unblock"):
        item = add(name, f"{name.title()} a task on another task", writes=True)
        item.add_argument("id")
        item.add_argument("--on", required=True, metavar="ID")
    remind = add("remind", "Schedule a reminder about a task", writes=True)
    remind.add_argument("id")
    remind.add_argument("when", metavar="D")
    remind.add_argument("--to", required=True, metavar="ID", help="Person or principal entity")
    find = add("find", "Search titles and descriptions")
    find.add_argument("text", nargs="?")
    find.add_argument("--kind", default="task")
    find.add_argument("--status")
    find.add_argument("--archived", action="store_true", help="Search archived records only (implies --page)")
    find.add_argument("--claimed", action="store_true", help="Show my live task claims, including held work")
    find.add_argument("--page", action="store_true", help="Show compact search hits and a continuation ID")
    find.add_argument("--source", metavar="SYSTEM:KIND:ID", help="Match one exact source with the other filters")
    find.add_argument("--after", metavar="ID", help="Continue the same search after this ID (implies --page)")
    find.add_argument("--limit", "-n", type=int, default=50, metavar="N")
    _scope_filter(find, "Filter by scope (uses compact hits)")
    remember = add("remember", "Keep a durable fact for later sessions", writes=True)
    remember.add_argument("text")
    remember.add_argument("--scope", metavar="NAME", help="Where this preference or fact applies")
    remember.add_argument("--source", metavar="SYSTEM:KIND:ID", help="Exact source of this fact")
    policy = sub.add_parser(
        "policy", help="Show or set who may accept and dismiss proposals",
        description="Local workspace policy. A change replaces the entire list.",
        epilog=(
            "For a guarded change, read with --versioned, then supply --if-version N "
            "and --operation-id ID with --deciders or --clear. Repeat the exact write "
            "to recover its original result. Read again to see the current list."
        ),
    )
    policy_change = policy.add_mutually_exclusive_group()
    policy_change.add_argument("--deciders", metavar="NAMES", help="Comma-separated actor names")
    policy_change.add_argument("--clear", action="store_true", help="Let any writer decide again")
    policy.add_argument("--versioned", action="store_true", help="Read the list and version for a guarded change")
    policy.add_argument("--if-version", type=int, metavar="N", help="Require the version from policy --versioned")
    policy.add_argument("--operation-id", "--op", metavar="ID", help="Keep this ID and repeat the same write if its result is lost")
    focus = add("focus", "Put a task on the person's short list, in their order", writes=True)
    focus.add_argument("id")
    focus.add_argument("--rank", type=int, help="Position, lowest first (default: last)")
    project = add("project", "Create a project or change its configuration and facts")
    actions = project.add_subparsers(dest="action", required=True, metavar="ACTION")

    def action(name, help, *, writes=True):
        return actions.add_parser(name, help=help, parents=[common, *([write] if writes else [])])

    def facts(item):
        item.add_argument("-d", "--description")
        item.add_argument("--lead", metavar="WHO", help="A person's ID or name")
        item.add_argument("--start", metavar="D")
        item.add_argument("--target", metavar="D", help="Soft target date")
        item.add_argument("--due", metavar="D", help="Hard deadline")
        item.add_argument("--goal", metavar="ID")
        item.add_argument(
            "--managed-by", metavar="SYSTEM", help='A system that owns it ("" clears)'
        )

    def parts(item):
        item.add_argument("--preset", metavar="NAME", help="See `daymark project presets`")
        item.add_argument("--stages", metavar="SPEC", help="id:label:status:color,...")
        item.add_argument("--types", metavar="SPEC", help="id:label[:color][:container],...")
        item.add_argument("--fields", metavar="SPEC", help="id:label:type[:options|unit],...")

    created = action("add", "Create a project; bare unless given a preset or parts")
    created.add_argument("title")
    created.add_argument("--id", help="Choose the ID instead of a generated short one")
    facts(created)
    parts(created)
    changed = action("set", "Change a project's title and facts")
    changed.add_argument("project")
    changed.add_argument("--title")
    facts(changed)
    for name, label in (("stages", "stages"), ("types", "item types"), ("fields", "fields")):
        part = action(name, f'Replace a project\'s {label} ("" removes or restores defaults)')
        part.add_argument("project")
        part.add_argument("spec", metavar="SPEC")
    configure = action("config", "Print a project's configuration, or --apply one")
    configure.add_argument("project")
    configure.add_argument("--apply", metavar="FILE", help="JSON with stages, types, fields")
    configure.add_argument("--preset", metavar="NAME", help="Apply a preset's configuration")
    action("presets", "Starting configurations and what each sets", writes=False)
    projects = add("projects", "Projects with their roll-ups and health")
    _scope_filter(projects, "Projects belonging to this scope, including all their members in counts")
    plan = add("plan", "One project: by stage, in its order, with roll-ups and health")
    plan.add_argument("project")
    plan.add_argument(
        "--by",
        metavar="GROUP",
        help="stage (default), milestone, type, parent, assignee or a field",
    )
    plan.add_argument("--tree", action="store_true", help="Show the hierarchy instead")
    goal = add("goal", "Create, change or show a goal")
    goal_actions = goal.add_subparsers(dest="action", required=True, metavar="ACTION")
    for name in ("add", "set"):
        item = goal_actions.add_parser(
            name, help=f"{'Create' if name == 'add' else 'Change'} a goal", parents=[common, write]
        )
        item.add_argument("title" if name == "add" else "goal")
        if name == "add":
            item.add_argument("--id", help="Choose the ID instead of a generated short one")
        else:
            item.add_argument("--title")
        item.add_argument("--target", metavar="D", help="Its target date")
        item.add_argument("--measure", metavar="SPEC", help="Label:target[:current[:unit]]")
        item.add_argument("-d", "--description")
    shown = goal_actions.add_parser("show", help="A goal's roll-up", parents=[common])
    shown.add_argument("goal")
    add("goals", "Goals with their roll-ups")
    tree = add("tree", "An item and its children, in order")
    tree.add_argument("id")
    tree.add_argument("--depth", type=int, default=3)
    move = add("move", "Move a task to a stage and/or a place in its project's order", writes=True)
    move.add_argument("id")
    move.add_argument("--stage", metavar="S")
    where = move.add_mutually_exclusive_group()
    where.add_argument("--after", metavar="ID")
    where.add_argument("--before", metavar="ID")
    where.add_argument("--first", action="store_true")
    where.add_argument("--last", action="store_true")
    changes = add("changes", "Whether a project changed after an event cursor")
    changes.add_argument("project")
    changes.add_argument("--after", type=int, required=True, metavar="CURSOR")
    condition = add("condition", "Record what must be true for a goal to be reached", writes=True)
    condition.add_argument("statement")
    condition.add_argument("--goal", required=True, metavar="ID")
    condition.add_argument("--requires", action="append", metavar="ID", help="Needs this first")
    condition.add_argument("--state", choices=STATES, default="unknown")
    condition.add_argument("--id", help="Choose the ID")
    assess = add("assess", "Say how a condition stands, with evidence", writes=True)
    assess.add_argument("id")
    assess.add_argument("state", choices=STATES)
    assess.add_argument("--evidence", metavar="TEXT", help="What shows it")
    explore = add("explore", "A goal's conditions: what is met, what is next, where nothing moves")
    explore.add_argument("goal", nargs="?", help="Goal ID (default: every goal)")
    _scope_filter(explore, "Show conditions and named links only in this scope")
    sub.add_parser("workspaces", help="List the workspaces in this database")
    mcp = sub.add_parser("mcp", help="Serve these verbs to an agent harness over MCP (stdio)")
    mcp.add_argument("--read-only", action="store_true", help="Expose only prime and view; open the local database read-only")


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(
        prog="daymark", formatter_class=argparse.RawDescriptionHelpFormatter,
        description="Run daymark alone for a short guide. Use daymark COMMAND --help for one command.",
        epilog=_QUICK_START,
    )
    from . import __version__

    root.add_argument("--version", action="version", version=f"daymark {__version__}")
    root.add_argument(
        "--db",
        help="SQLite path. Default: $DAYMARK_DB, nearest .daymark/, ./daymark.sqlite3, "
        "then the per-user database",
    )
    root.add_argument(
        "--workspace",
        default=os.environ.get("DAYMARK_WORKSPACE", "default"),
        help="Workspace for local commands",
    )
    root.add_argument(
        "--as",
        dest="actor",
        default=os.environ.get("DAYMARK_ACTOR"),
        help="Who is acting, for claims and notes (default: $DAYMARK_ACTOR or 'agent')",
    )
    root.add_argument("--tz", help="IANA timezone for dates (default: $DAYMARK_TZ or system)")
    root.add_argument("--url", help="Use an HTTP server instead of local Store")
    root.add_argument(
        "--token-env",
        default="DAYMARK_TOKEN",
        help="Environment variable containing HTTP bearer credential",
    )
    sub = root.add_subparsers(dest="command", required=True, metavar="COMMAND")
    _simple_commands(sub)
    serve = sub.add_parser(
        "serve", help="Run optional authenticated API (install daymark-core[service])"
    )
    serve.add_argument("--host", default="127.0.0.1", help="Bind address; default is loopback")
    serve.add_argument("--port", type=int, default=8000)
    serve.add_argument(
        "--auth-file",
        required=True,
        help='Protected JSON mapping bearer tokens to {"workspace":...,"principal":...}',
    )
    worker = sub.add_parser(
        "worker", help="Run durable scheduler with recording sink only; sends no notifications"
    )
    worker.add_argument(
        "--once", action="store_true", help="Run one durable tick and delivery pass"
    )
    worker.add_argument("--interval", type=float, default=10, help="Seconds between passes")
    worker.add_argument("--lease-seconds", type=int, default=60)
    sub.add_parser("health", help="Read aggregate scheduler/outbox health")
    get = sub.add_parser("get", help="Get an entity in the current workspace")
    get.add_argument("id")
    listing = sub.add_parser("list", help="List entities")
    listing.add_argument("--kind")
    query = sub.add_parser("query", help="Query using a bounded filter AST JSON file")
    query.add_argument("--file", required=True)
    events = sub.add_parser("events", help="Read event cursor page")
    events.add_argument("--after", type=int, default=0)
    events.add_argument("--limit", type=int, default=100)
    events.add_argument("--project", metavar="ID", help="Only events that touch this project")
    events.add_argument("--head", action="store_true", help="Print only the latest cursor")
    export = sub.add_parser("export", help="Export current workspace snapshot to a new JSON file")
    export.add_argument("--output", required=True)
    imp = sub.add_parser(
        "import", help="Import a native Daymark JSON workspace snapshot",
        description="Merge a native workspace snapshot written by daymark export, retaining identities and history. "
        "For a project CSV from another app, review the rows and capture the tasks you select.",
    )
    imp.add_argument("--file", required=True, help="Native Daymark workspace JSON")
    imp.add_argument(
        "--operation-id", required=True, help="Stable identifier; reuse only for identical retries"
    )
    imp.add_argument(
        "--expected-version", type=int, required=True, help="Import merge precondition (use 0)"
    )
    for command in ("create", "update", "action"):
        item = sub.add_parser(
            command, help=f"{command.title()} entity with stable operation identity"
        )
        item.add_argument(
            "--operation-id",
            required=True,
            help="Stable identifier; reuse only for identical retries",
        )
        item.add_argument(
            "--file", required=True, help="JSON payload file; unknown entity fields rejected"
        )
        if command == "create":
            item.set_defaults(expected_version=0)
        else:
            item.add_argument(
                "--expected-version", type=int, required=True, help="Current entity version"
            )
        if command == "action":
            item.add_argument(
                "name",
                help="Action: complete, cancel, reopen, archive, skip, delete, undo, reminder.snooze/ack/dismiss/cancel",
            )
    for command in ("backup", "restore"):
        item = sub.add_parser(
            command,
            help=(
                "Create a consistent SQLite backup"
                if command == "backup"
                else "Restore backup into a NEW database; never overwrites"
            ),
        )
        item.add_argument(
            "--file",
            required=True,
            help="Backup destination" if command == "backup" else "Backup source",
        )
    return root


def _stored_schema(path):
    """The schema marker of an existing Daymark database, read without writing:
    None for a new or empty file, "" for Daymark tables without a marker."""
    if path == ":memory:" or not Path(path).is_file():
        return None
    try:
        with closing(sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)) as db:
            tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if "daymark_entities" not in tables:
                return None
            if "daymark_scheduler" not in tables:
                return ""
            row = db.execute("SELECT value FROM daymark_scheduler WHERE key='schema'").fetchone()
            return row[0] if row else ""
    except sqlite3.Error:
        return None


def _open_store(path, *, read_only=False, require_existing=False):
    """Open a Store the way every command does: upgrading an older database in place.
    The upgrade is one-way, so the first one is announced on standard error."""
    from . import __version__
    from .store import SCHEMA, Store

    if read_only:
        return Store(path, read_only=True, require_existing=True)
    before = _stored_schema(path)
    if not require_existing and path and path != ":memory:":
        # SQLite derives WAL and shared-memory permissions from the main file.
        # Set the mode before the first connection creates those companions.
        try:
            fd = os.open(Path(path).resolve(), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            pass
        else:
            os.close(fd)
    store = Store(path, require_existing=require_existing)
    try:
        if before is not None and before != SCHEMA:
            print(
                f"daymark: upgraded {path} from schema {before or 'unmarked'} to {SCHEMA} "
                f"(Daymark {__version__}); use a pre-upgrade backup to return to an older schema",
                file=sys.stderr,
            )
    except BaseException:
        store.close()
        raise
    return store


def _init(args) -> int:
    from .models import DomainError

    if args.url:
        raise DomainError("invalid", "init creates a local database",
                          ["To inspect the service, use daymark --url URL prime."])
    explicit = args.db or os.environ.get("DAYMARK_DB")
    target = (
        Path(explicit)
        if explicit
        else (Path.cwd() / ".daymark" if args.here else data_home()) / "daymark.sqlite3"
    )
    if args.here and target.resolve() != (Path.cwd() / ".daymark/daymark.sqlite3").resolve():
        raise DomainError("invalid", "This folder and the explicit database select different locations",
                          ["For init --here, omit --db and unset DAYMARK_DB."])
    existed = target.is_file()
    target.parent.mkdir(parents=True, exist_ok=True)

    if args.here:
        # The local database can contain private work in an otherwise public checkout.
        try:
            with (target.parent / ".gitignore").open("x", encoding="utf-8") as ignore:
                ignore.write("/daymark.sqlite3\n/daymark.sqlite3-*\n")
        except FileExistsError:
            pass
    _open_store(str(target)).close()
    print(f"{'Using existing' if existed else 'Created'} {target}")
    visible = resolve_db(None)
    command = "daymark prime" if visible and Path(visible).resolve() == target.resolve() else (
        f"daymark --db {shlex.quote(str(target.resolve()))} prime"
    )
    print(f"Next: `{command}` for this database; `daymark onboard` for project instructions.")
    return 0


def _onboard(args) -> int:
    from .render import ONBOARD

    if args.mcp_config:
        from .onboarding import mcp_config

        print(mcp_config(
            args.mcp_config, database=None if args.url else resolve_db(args.db),
            workspace=args.workspace, actor=args.actor, timezone=args.tz,
            url=args.url, token_env=args.token_env,
        ))
        return 0
    if args.write or args.check:
        from .onboarding import maintain

        return maintain(Path(args.write or args.check), check=args.check is not None)
    if args.skill:
        from .onboarding import skill_text

        print(skill_text(), end="")
    else:
        print(ONBOARD, end="")
    return 0


def _simple_write(args):
    """The parser offers --op on write verbs. Profiles and project config also allow reads."""
    if args.command == "profile":
        operations = (args.name, args.save_as, args.remove, args.undo, args.from_file)
        return bool(args.set or args.drop) or any(value is not None for value in operations)
    if args.command == "project" and args.action == "config":
        return bool(args.apply or args.preset)
    return hasattr(args, "op")


def _simple(args, engine) -> int:
    """Run one everyday verb and print a lean rendering of its result."""
    from . import render
    from . import tasks as tasks_module
    from .tasks import Tasks

    tasks = Tasks(engine, args.workspace, actor=args.actor, timezone=args.tz)
    now, name, as_json = tasks.now(), args.command, getattr(args, "json", False)
    op = getattr(args, "op", None)
    if args.url and name != "profile" and _simple_write(args):
        op = args.retry_op = tasks._op_id(op)

    def out(value, text):
        _print(value) if as_json else print(text)

    def entity(item):
        out(render.compact(item), render.line(item, now, timezone=tasks.timezone))

    scope = getattr(args, "scope", None)

    if name == "prime":
        context = render.session_context(tasks, database=args.db, url=args.url)
        if as_json and (args.brief or args.scope is not None):
            _print(render.brief_session(tasks, context, scope=args.scope))
        elif as_json:
            if args.url:
                context.update(engine.identity() or {})
            _print(
                {
                    "context": context,
                    "summary": tasks.summary(),
                    "memories": [m["title"] for m in tasks.memories()],
                    "ready": [render.compact(x) for x in tasks.ready(limit=5)],
                }
            )
        else:
            print(render.prime_text(tasks, context=context, brief=args.brief, scope=args.scope))
    elif name in {"advise", "profile", "strategies"}:
        from . import strategies
        from .strategies.profiles import PROFILES

        if name == "strategies" and args.strategy is not None:
            data = tasks.profile_catalog(strategy=args.strategy)
            out(data, render.strategy_detail(data))
        elif name == "strategies":
            listed = strategies.available()
            plugins = strategies.installed_plugins()
            out(
                {
                    "strategies": [
                        {"name": s.name, "kind": s.kind, "summary": s.summary,
                         "evidence": s.evidence,
                         "params": {k: p.default for k, p in s.params.items()}}
                        for s in listed
                    ],
                    "profiles": {k: [x["use"] for x in v["strategies"]] for k, v in PROFILES.items()},
                    **({"installed_plugins": plugins} if plugins else {}),
                },
                render.strategies_text(listed, PROFILES, plugins=plugins),
            )  # fmt: skip
        elif name == "profile":
            _profile(tasks, args, out)
        else:
            advice = tasks.advise(
                profile=args.profile, settings=args.set, drops=args.drop, scope=scope, context=args.context
            )
            lean = advice.to_dict()
            lean["picks"] = [render.compact(x) for x in advice.picks]
            session = render.session_context(tasks, database=args.db, url=args.url)
            out(lean, render.advice_text(advice, now, session=session))
    elif name == "ready":
        filters = {"limit": args.limit, "move": args.move, "scope": scope, "context": args.context, "project": args.project}
        empty = "Nothing is ready" + (" for these filters" if scope is not None or args.move or args.context or args.project else "")
        empty += ". Repeat with --explain to see what is held."
        if args.explain:
            view = tasks.explain_ready(**filters)
            out(view, render.ready_explanation(view))
        elif args.brief:
            rows = tasks.ready_summary(**filters)
            out([render.compact(row) for row in rows], render.ready_previews(rows) or empty)
        else:
            items = tasks.ready(**filters)
            out(
                [{**render.compact(x), "why": render.why(x, now)} for x in items],
                render.listing(items, now, empty),
            )
    elif name == "agenda":
        view = tasks.agenda(args.days, scope=scope, context=args.context, deadlines=args.deadlines, local_day=args.local_day)
        out(render.agenda_data(view, now), render.agenda_text(view, now))
    elif name == "activity":
        page = tasks.activity(after=args.after, limit=args.limit)
        session = render.session_context(tasks, database=args.db, url=args.url)
        out(page, render.activity_text(page, session=session, limit=args.limit))
    elif name == "show" and args.forecast:
        forecast = tasks.recurrence_forecast(args.id, action=args.forecast, source=args.source)
        out(forecast, render.recurrence_forecast_text(forecast))
    elif name == "show" and args.since_version is not None:
        comparison = tasks.changes_since(args.id, version=args.since_version, source=args.source)
        out(comparison, render.comparison_text(comparison))
    elif name == "show" and (args.brief or args.before_note is not None):
        brief = tasks.brief(args.id, before_note=args.before_note, source=args.source)
        session = render.session_context(tasks, database=args.db, url=args.url)
        out(brief, render.brief_text(brief, timezone=tasks.timezone, session=session, source=args.source))
    elif name == "show" and args.note is not None:
        note = tasks.read_note(args.id, args.note, source=args.source)
        out(note, render.note_text(note))
    elif name == "show":
        ctx = tasks.show(args.id, source=args.source)
        out(render.detail_data(ctx), render.show_text(ctx, now))
    elif name in {"add", "propose", "edit"}:
        names = ("due", "plan", "start", "target", "priority", "description", "after", "parent",
                 "project", "goal", "assignee", "labels", "contexts", "waiting_on", "move",
                 "minutes", "scope", "stage", "milestone", "is_milestone", "type",
                 "advances")  # fmt: skip
        fields = {key: getattr(args, key) for key in names if getattr(args, key) is not None}
        if args.values:
            project = args.project if args.project is not None else (
                tasks.get(args.id)["project_id"] if name == "edit" else None
            )  # fmt: skip
            fields["values"] = tasks.parse_values(project, args.values)
        if name == "edit":
            if args.title is not None:
                fields["title"] = args.title
            if args.managed_by is not None:
                fields["managed_by"] = args.managed_by
            item = tasks.edit(
                args.id, every=args.every, after_completion=args.after_completion,
                expected_version=args.if_version, review_token=args.if_review, op=op, **fields
            )
        else:
            item = tasks.add(
                args.title,
                kind=args.kind,
                every=args.every,
                after_completion=args.after_completion,
                note=args.note,
                proposed=name == "propose",
                source=args.source,
                id=args.id,
                op=op,
                **fields,
            )
        entity(item)
    elif name in {"done", "release"}:
        item = getattr(tasks, name)(args.id, note=args.note, op=op, expected_version=args.if_version)
        entity(item)
        if name == "done" and not as_json and item.get("advances"):
            # Finishing work never changes a condition; say who has to look.
            print("Reassess what this advanced: daymark assess " + "|".join(item["advances"]) + " STATE --evidence TEXT")
    elif name in {"accept", "dismiss", "forget", "undo", "unarchive", "resume"}:
        entity(getattr(tasks, name)(args.id, expected_version=args.if_version, review_token=args.if_review, op=op))
    elif name in {"skip", "cancel", "reopen", "archive", "unfocus"}:
        entity(getattr(tasks, name)(args.id, op=op))
    elif name == "condition":
        entity(
            tasks.condition(
                args.statement, goal=args.goal, requires=args.requires, state=args.state,
                id=args.id, op=op,
            )
        )  # fmt: skip
    elif name == "assess":
        entity(tasks.assess(args.id, args.state, evidence=args.evidence, op=op))
    elif name == "explore":
        view = tasks.explore(args.goal, scope=scope)
        out(view, render.explore_text(view))
    elif name == "focus":
        entity(tasks.focus(args.id, args.rank, op=op))
    elif name == "take":
        selected = tasks.take(scope=scope, context=args.context, project=args.project, minutes=args.minutes, op=op)
        if selected is None:
            out(None, render.EMPTY_TAKE)
        else:
            entity(selected)
    elif name == "claim":
        entity(tasks.claim(args.id, minutes=args.minutes, op=op))
    elif name == "note":
        entity(tasks.note(args.id, args.text, op=op))
    elif name == "ask":
        entity(tasks.ask(args.id, args.question, to=args.to, options=args.option, recommend=args.recommend, op=op))
    elif name == "answer":
        entity(tasks.answer(args.id, args.text, choice=args.choice, heard_from=args.heard_from, question_key=args.if_question, op=op))
    elif name == "followup":
        entity(tasks.followup(args.id, args.text, after=args.after, for_=args.for_, op=op))
    elif name == "inbox":
        view = tasks.inbox(args.for_, after=args.after)
        if args.count:
            counts = {k: len(view[k]) for k in ("to_answer", "answered", "followups", "mentions")}
            _print({"for": view["for"], **counts, "waking": view["waking"], "cursor": view["cursor"], "more": view["more"]})
        else:
            out(view, render.inbox_text(view))
    elif name == "digest":
        view = tasks.digest(args.for_)
        out(view, render.digest_text(view))
    elif name == "seen":
        mark = tasks.mark_seen(args.for_, through=args.through)
        out(mark, f"{mark['reader']} has seen through {mark['seq']} of {mark['head']}")
    elif name == "watch":
        from .coordination import interval

        result = tasks.watch(
            args.ids, after=args.after, by=args.by, include_own=args.include_own,
            timeout=0 if args.timeout == "0" else interval(args.timeout).total_seconds(),
            poll=5.0 if args.url else 1.0,
        )
        out(result, render.watch_text(result, ids=args.ids))
        if result["status"] == "timeout":
            return 8
    elif name == "wait":
        entity(tasks.wait(args.id, args.on, until=args.until, op=op,
                          expected_version=args.if_version, review_token=args.if_review))
    elif name == "defer":
        entity(tasks.defer(args.id, args.until, op=op))
    elif name in {"block", "unblock"}:
        entity(getattr(tasks, name)(args.id, args.on, op=op))
    elif name == "remind":
        entity(tasks.remind(args.id, args.when, recipient=args.to, op=op))
    elif name == "find":
        if not 1 <= args.limit <= 1000:
            from .models import DomainError

            raise DomainError("invalid", "Limit must be between 1 and 1000")
        if args.archived or args.claimed or args.page or args.after is not None or scope is not None or args.source is not None:
            page = tasks.search(args.text, kind=args.kind, status=args.status,
                                scope=scope, after=args.after, limit=args.limit, claimed=args.claimed,
                                archived=args.archived, source=args.source)
            out(page, render.search_text(page))
        else:
            items = tasks.find(args.text, kind=args.kind, status=args.status, limit=args.limit, brief=not as_json)
            if as_json:
                _print([render.compact(x) for x in items])
            else:
                print(render.listing(items, now, "No matches."))
    elif name == "remember":
        entity(tasks.remember(args.text, scope=args.scope, source=args.source, op=op))
    elif name == "project":
        _project(args, tasks, out, entity, op)
    elif name == "goal":
        if args.action == "show":
            view = tasks.goal_plan(args.goal)
            out({**view, "goal": render.compact(view["goal"])}, render.goal_text(view))
        else:
            attrs = {k: getattr(args, k) for k in ("description", "target")}
            attrs = {k: v for k, v in attrs.items() if v is not None}
            if args.measure is not None:
                attrs["measure"] = (
                    tasks_module.parse_measure(args.measure) if args.measure else None
                )
            if args.action == "add":
                entity(tasks.goal(args.title, id=args.id, op=op, **attrs))
            else:
                if args.title is not None:
                    attrs["title"] = args.title
                entity(tasks.edit(args.goal, op=op, **attrs))
    elif name == "goals":
        view = tasks.goals()
        out(view, render.goals_text(view))
    elif name == "tree":
        view = tasks.tree(args.id, args.depth)
        out(view, render.tree_text(view))
    elif name == "move":
        entity(tasks.move(args.id, stage=args.stage, after=args.after, before=args.before,
                          first=args.first, last=args.last, op=op))  # fmt: skip
    elif name == "projects":
        view = tasks.projects(scope=scope)
        out(view, render.projects_text(view))
    elif name == "plan":
        view = tasks.plan(args.project)
        lean = {
            **view,
            "project": render.compact(view["project"]),
            "tasks": [{**render.compact(x), "plan": x["plan"]} for x in view["tasks"]],
            "proposed": [render.compact(x) for x in view["proposed"]],
        }
        text = (
            render.tree_text(tasks.tree(args.project, 10))
            if args.tree
            else render.plan_text(view, now, by=args.by or "stage")
        )
        out(lean, text)
    elif name == "changes":
        result = tasks.changes(args.project, args.after)
        out(
            result,
            f"cursor {result['cursor']}; " + ("changed" if result["changed"] else "unchanged"),
        )
    elif name == "mcp":
        from .mcp import serve

        serve(tasks, context=render.session_context(tasks, database=args.db, url=args.url), read_only=args.read_only)
    return 0


def _profile(tasks, args, out) -> None:
    from . import render
    from .models import DomainError

    modifying = bool(args.name or args.set or args.drop or args.from_file)
    if args.saved is not None:
        if modifying or args.if_version is not None or args.op is not None:
            raise DomainError("invalid", "--saved reads profiles. Use --save-as to edit one.")
        result = tasks.saved_profiles(args.saved or None)
        out(result, render.saved_profiles_text(result))
    elif args.remove is not None:
        if modifying or args.if_version is None:
            raise DomainError("invalid", "Use --remove NAME --if-version N")
        result = tasks.remove_profile(args.remove, expected_version=args.if_version, op=args.op)
        out(result, f"Removed saved profile {args.remove}")
    elif args.save_as is not None:
        base = args.name
        if args.from_file:
            if args.name:
                raise DomainError("invalid", "Choose a profile name or --from-file as the base")
            try:
                payload = _json_file(args.from_file)
            except (OSError, ValueError) as exc:
                raise DomainError("invalid", "Supply a readable JSON profile file") from exc
            if not isinstance(payload, dict):
                raise DomainError("invalid", "A profile file must contain a JSON object")
            base = payload
        result = tasks.save_profile(
            args.save_as, profile=base, settings=args.set, drops=args.drop,
            expected_version=0 if args.if_version is None else args.if_version, op=args.op,
        )
        out(result, render.saved_profiles_text(result))
    else:
        if args.from_file:
            raise DomainError("invalid", "--from-file requires --save-as NAME")
        result = tasks.profile(args.name, settings=args.set, drops=args.drop, undo=args.undo,
                               expected_version=args.if_version, op=args.op)
        out(result, render.profile_text(result, database=args.db))


def _project(args, tasks, out, entity, op) -> None:
    """The project verb family: add, set, stages, types, fields, config, presets."""
    from . import render
    from .models import DomainError

    action = args.action
    if action == "presets":
        presets = tasks.presets()
        out(presets, render.presets_text(presets))
        return
    if action in {"add", "set"}:
        names = ("description", "lead", "start", "target", "due", "goal", "managed_by")
        attrs = {k: getattr(args, k) for k in names if getattr(args, k) is not None}
        if action == "add":
            entity(tasks.project(args.title, id=args.id, op=op, preset=args.preset,
                                 stages=args.stages, types=args.types, fields=args.fields,
                                 **attrs))  # fmt: skip
        else:
            if args.title is not None:
                attrs["title"] = args.title
            if not attrs:
                raise DomainError("invalid", "Nothing to change")
            entity(tasks.edit(args.project, op=op, **attrs))
        return
    if action in {"stages", "types", "fields"}:
        entity(tasks.configure(args.project, op=op, **{action: args.spec}))
        return
    # config
    if args.apply or args.preset:
        document = _json_file(args.apply) if args.apply else {}
        if not isinstance(document, dict) or set(document) - {"stages", "types", "fields"}:
            raise DomainError("invalid", "A configuration has only stages, types and fields")
        tasks.configure(args.project, preset=args.preset, op=op, **document)
    config = tasks.config(args.project)
    out(config, json.dumps(config, indent=2, sort_keys=True))


def _fail(args, code: str, message: str, details=(), *, retry_op=None) -> int:
    """Report a failure by stable code. Messages name rules, never stored content."""
    if code == "conflict" and args.command in {"add", "propose", "remember"} and getattr(args, "source", None):
        details = [*details, "Check the original capture with daymark show --source SYSTEM:KIND:ID --brief."]
    if code == "conflict" and args.command in {"edit", "accept", "dismiss", "undo"} and getattr(args, "if_version", None):
        details = [*details, f"Compare the current record with daymark show ID --since-version {args.if_version}."]
    if retry_op is not None:
        hint = f"Write outcome uncertain. Repeat the same command and flags with --op {shlex.quote(retry_op)}"
        details = [*details, hint]
    if getattr(args, "json", False):
        error = {"code": code, "message": message, "details": list(details)}
        if retry_op is not None:
            error["operation_id"] = retry_op
        print(
            json.dumps({"error": error}),
            file=sys.stderr,
        )
    else:
        print(f"error: {code}: {message}", file=sys.stderr)
        for detail in details:
            print(f"  {detail}", file=sys.stderr)
    return {"not_found": 3, "conflict": 4, "invalid": 5, "no_database": 6, "forbidden": 7}.get(
        code, 1
    )


def _policy(args):
    from .errors import DomainError
    from .store import Store

    if args.url:
        raise DomainError("invalid", "Policy requires local database access")
    writing = args.clear or args.deciders is not None
    guarded = args.if_version is not None or args.operation_id is not None
    if args.versioned and (writing or guarded):
        raise DomainError("invalid", "Use --versioned to read, then --if-version N --operation-id ID to write")
    if guarded and (not writing or args.if_version is None or args.operation_id is None):
        raise DomainError("invalid", "Pair --if-version N with --operation-id ID and --deciders or --clear")
    names = []
    if writing:
        names = [name.strip() for name in (args.deciders or "").split(",") if name.strip()]
        if not names and not args.clear:
            raise DomainError("invalid", "Supply at least one decision maker or use policy --clear")
        Store.validate_policy_change(args.workspace, names, args.if_version, args.operation_id)

    path = resolve_db(args.db) or "daymark.sqlite3"
    with closing(_open_store(path, require_existing=not writing and path != ":memory:")) as engine:
        if writing:
            result = engine.set_policy(
                args.workspace, deciders=names, expected_version=args.if_version, operation_id=args.operation_id
            )
        elif args.versioned:
            result = engine.policy_state(args.workspace)
        else:
            result = engine.policy(args.workspace)
    _print(result)
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if not argv:
        print(_QUICK_START)
        return 0
    args = parser().parse_args(argv)
    from .errors import ClientError, DomainError

    try:
        if args.command == "import":
            try:
                snapshot = _json_file(args.file)
            except OSError:
                raise DomainError("invalid", "Cannot read the snapshot file. Check its path and permissions.") from None
            except ValueError:
                advice = ["For a third-party CSV, review the source rows before capturing selected tasks."]
                raise DomainError(
                    "invalid", "Supply a readable native Daymark JSON snapshot from daymark export.",
                    advice if args.file.lower().endswith(".csv") else [],
                ) from None
            from .store import validate_snapshot_envelope

            validate_snapshot_envelope(snapshot, None if args.url else args.workspace)
        if args.command == "where":
            return _where(args)
        if args.command == "init":
            return _init(args)
        if args.command == "onboard":
            return _onboard(args)
        if args.command == "policy":
            return _policy(args)
        if not args.url:
            found = resolve_db(args.db)
            if found is None and (args.command in SIMPLE or args.command == "workspaces"):
                return _fail(
                    args,
                    "no_database",
                    "No Daymark database found",
                    ["Run `daymark init` (per-user) or `daymark init --here` (this folder)."],
                )
            args.db = found or "daymark.sqlite3"
        if args.command == "show" and args.forecast is not None:
            args.read_only = True
        if args.command in {"backup", "restore"}:
            if args.url:
                raise ValueError("Backup commands require a local database")
            source, destination = (
                (Path(args.db), Path(args.file))
                if args.command == "backup"
                else (Path(args.file), Path(args.db))
            )
            try:
                _copy_database(source, destination)
            except FileExistsError:
                option = "--file" if args.command == "backup" else "--db"
                return _fail(
                    args, "destination_exists", f"Destination already exists. Choose an unused path for {option}."
                )
            _print({"status": "ok"})
            return 0
        if args.command == "serve":
            import uvicorn

            from .api import create_app

            auth = _json_file(args.auth_file)
            store = _open_store(args.db)
            # Uvicorn access logs include private URL/query values; never enable them here.
            try:
                app = create_app(store, auth)
                uvicorn.run(app, host=args.host, port=args.port, access_log=False)
            finally:
                close = getattr(store, "close", None)
                if close:
                    close()
            return 0
        if args.url:
            if args.command == "worker":
                raise ValueError("Worker requires local Store access")
            from .client import Client

            engine = Client(args.url, os.environ.get(args.token_env, ""), read_only=getattr(args, "read_only", False))
        else:
            # The first explicit write may create a file. An ordinary workspace
            # read may upgrade recognized Daymark data, but must never create it.
            ordinary_read = args.command == "workspaces" or (
                args.command in SIMPLE and args.command != "mcp" and not _simple_write(args)
            )
            inspect_existing = ordinary_read or args.command in {"get", "query", "events", "export", "health"}
            engine = _open_store(args.db, read_only=getattr(args, "read_only", False),
                                 require_existing=inspect_existing and args.db != ":memory:")
        try:
            ws = [] if args.url else [args.workspace]
            if args.command == "workspaces":
                if args.url:
                    raise ValueError("Workspaces are listed on the local database")
                _print({"database": args.db, "current": args.workspace,
                        "workspaces": engine.workspaces()})  # fmt: skip
                return 0
            if args.command in SIMPLE:
                return _simple(args, engine)
            if args.command == "worker":
                from .worker import Worker

                worker = Worker(engine, lease_seconds=args.lease_seconds)
                if args.once:
                    _print(worker.run_once())
                else:
                    worker.run(interval=args.interval)
                return 0
            if args.command in {"create", "update", "action"}:
                action = args.name if args.command == "action" else args.command
                result = engine.execute(
                    *ws,
                    args.operation_id,
                    action,
                    _json_file(args.file),
                    expected_version=args.expected_version,
                )
            elif args.command == "import":
                result = engine.import_snapshot(
                    *ws,
                    snapshot,
                    operation_id=args.operation_id,
                    expected_version=args.expected_version,
                )
            elif args.command == "get":
                result = engine.get(*ws, args.id)
            elif args.command == "list":
                result = engine.list(*ws, kind=args.kind)
            elif args.command == "query":
                result = engine.query(*ws, _json_file(args.file))
            elif args.command == "events" and args.head:
                result = {"cursor": engine.head(*ws)}
            elif args.command == "events":
                extra = {"project": args.project} if args.project else {}
                result = engine.events(*ws, after=args.after, limit=args.limit, **extra)
            elif args.command == "health":
                result = engine.health()
            else:
                result = engine.export(*ws)
                write_export(Path(args.output), result)
                return 0
            _print(result)
        finally:
            close = getattr(engine, "close", None)
            if close:
                close()
        return 0
    except KeyboardInterrupt:
        return 130
    except DomainError as exc:
        return _fail(args, exc.code, str(exc), exc.details)
    except ClientError as exc:
        if exc.code == "unauthenticated":
            return _fail(args, exc.code, "Check the bearer credential configured for this Daymark endpoint.")
        retry_op = getattr(args, "retry_op", None) if exc.uncertain else None
        return _fail(args, exc.code, str(exc), [exc.recovery] if exc.recovery else (), retry_op=retry_op)
    except Exception:  # noqa: BLE001 - prevent private exception text escaping
        # Parsing/Store/network exceptions can contain private paths and payloads.
        print(
            "Daymark command failed; check configuration, payload and expected version.",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
