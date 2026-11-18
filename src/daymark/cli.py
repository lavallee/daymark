"""Daymark: tasks, plans and time for work and life.

Start with `daymark init`, then `daymark prime`. Everyday verbs: add, ready,
agenda, show, claim, note, done. The lower-level commands (create, update,
action, query, events, export, import) expose the engine's explicit operation
IDs and expected versions.
"""

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


def _json_file(path: str) -> Any:
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def _print(value: Any) -> None:
    print(json.dumps(value, default=str, sort_keys=True))


SIMPLE = {
    "prime", "activity", "ready", "agenda", "show", "add", "propose", "edit", "done", "skip", "cancel",
    "reopen", "archive", "undo", "accept", "dismiss", "claim", "take", "release", "note", "wait",
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
    from .render import endpoint_origin

    if args.url:
        result = {"source": "--url", "endpoint": endpoint_origin(args.url),
                  "identity_source": "server credential"}
        message = f"Endpoint {result['endpoint']} (--url). The server credential selects the workspace and actor."
    else:
        file, source = _location(args.db)
        database = str(Path(file).absolute()) if file and file != ":memory:" else file
        result = {"database": database, "source": source, "workspace": args.workspace,
                  "actor": args.actor, "file_exists": bool(file and Path(file).is_file())}
        message = f"Workspace {args.workspace}, actor {args.actor}."
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
    prime.add_argument("--brief", action="store_true", help="Skip the workflow guide")
    onboard = sub.add_parser("onboard", help="Print (or --write) the snippet for AGENTS.md")
    mode = onboard.add_mutually_exclusive_group()
    mode.add_argument("--write", metavar="FILE", help="Insert or refresh the snippet in FILE")
    mode.add_argument("--check", metavar="FILE", help="Check the snippet without writing (1 if missing or outdated)")
    advise = add("advise", "What to offer and what to mention, by the workspace's profile")
    advise.add_argument("--profile", metavar="NAME", help="Try a profile without adopting it")
    advise.add_argument("--scope", metavar="NAME", help="Only this scope")
    advise.add_argument("--context", metavar="ID", help="Only tasks tagged with this context")
    advise.add_argument("--unscoped", action="store_true", help="Only tasks with no scope")
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
    ready.add_argument("--context", metavar="ID", help="Only tasks tagged with this context")
    ready.add_argument("--explain", action="store_true", help="Show why live work is held back within these filters")
    ready.add_argument("-n", "--limit", type=int, default=20)
    ready.add_argument("--move", choices=["person", "answer", "agent"], help="Only this next move")
    for view in (ready,):
        view.add_argument("--scope", metavar="NAME", help="Only this scope")
        view.add_argument("--unscoped", action="store_true", help="Only tasks with no scope")
    agenda = add("agenda", "Overdue, today, the days ahead, waiting and proposed")
    agenda.add_argument("--context", metavar="ID", help="Only tasks tagged with this context")
    agenda.add_argument("--days", type=int, default=7)
    agenda.add_argument("--scope", metavar="NAME", help="Only this scope")
    agenda.add_argument("--unscoped", action="store_true", help="Only tasks with no scope")
    activity = add("activity", "Recent workspace changes and handoff notes")
    activity.add_argument("--after", type=int, metavar="CURSOR", help="Read on from a saved cursor")
    activity.add_argument("-n", "--limit", type=int, default=20, help="Events per page (1–100)")
    show = add("show", "One entity with blockers, notes, reminders and history")
    selector = show.add_mutually_exclusive_group(required=True)
    selector.add_argument("id", nargs="?")
    selector.add_argument("--source", metavar="SYSTEM:KIND:ID", help="Inspect the entity captured from this source")
    detail = show.add_mutually_exclusive_group()
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
        if name in {"accept", "dismiss"}:
            item.add_argument("--if-version", type=int, metavar="N", help="Decide only the reviewed version")
            item.add_argument("--if-review", metavar="TOKEN", help="Match the exact reviewed entity revision")
        if name in {"done", "release"}:
            item.add_argument("--note", help="Closing note" if name == "done" else "Handoff note")
    claim = add("claim", "Take a task so others leave it alone", writes=True)
    claim.add_argument("id")
    claim.add_argument("--minutes", type=int, default=60, help="Claim length (default 60)")
    take = add("take", "Claim one ready task explicitly delegated to an agent", writes=True)
    take.add_argument("--minutes", type=int, default=60, help="Claim length (default 60)")
    take.add_argument("--context", metavar="ID", help="Only take tasks tagged with this context")
    take_scope = take.add_mutually_exclusive_group()
    take_scope.add_argument("--scope", metavar="NAME", help="Only take from this scope")
    take_scope.add_argument("--unscoped", action="store_true", help="Only tasks with no scope")
    note = add("note", "Append a progress note", writes=True)
    note.add_argument("id")
    note.add_argument("text")
    wait = add("wait", "Park a task on someone or something", writes=True)
    wait.add_argument("id")
    wait.add_argument("--on", required=True, metavar="WHO")
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
    find.add_argument("--claimed", action="store_true", help="Show my live task claims, including held work")
    find.add_argument("--page", action="store_true", help="Show compact search hits and a continuation ID")
    find.add_argument("--after", metavar="ID", help="Continue the same search after this ID (implies --page)")
    find.add_argument("--limit", "-n", type=int, default=50, metavar="N")
    find.add_argument("--scope", metavar="NAME", help="Filter by scope (uses compact hits)")
    find.add_argument("--unscoped", action="store_true", help="Only unscoped hits")
    remember = add("remember", "Keep a durable fact for later sessions", writes=True)
    remember.add_argument("text")
    policy = sub.add_parser("policy", help="Show or set who may accept and dismiss proposals")
    policy.add_argument("--deciders", metavar="NAMES", help="Comma-separated actor names")
    policy.add_argument("--clear", action="store_true", help="Let any writer decide again")
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
    add("projects", "Projects with their roll-ups and health")
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
    sub.add_parser("workspaces", help="List the workspaces in this database")
    mcp = sub.add_parser("mcp", help="Serve these verbs to an agent harness over MCP (stdio)")
    mcp.add_argument("--read-only", action="store_true", help="Expose only prime and view; open the local database read-only")


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(
        prog="daymark", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
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
        "import", help="Import a validated workspace snapshot; retains identities/history"
    )
    imp.add_argument("--file", required=True)
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


def _copy_database(source: Path, destination: Path) -> None:
    """SQLite online backup API includes committed WAL; refuses destination overwrite."""
    if not source.is_file():
        raise ValueError("Source database missing")
    # Validate source before creating the destination.
    with closing(sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True)) as reader:
        if reader.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("Database integrity check failed")
        fd = os.open(destination, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(fd)
        try:
            with closing(sqlite3.connect(destination)) as writer:
                reader.backup(writer)
        except BaseException:
            destination.unlink(missing_ok=True)
            raise


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


def _open_store(path, *, read_only=False):
    """Open a Store the way every command does: upgrading an older database in place.
    The upgrade is one-way, so the first one is announced on standard error."""
    from .store import RELEASE, SCHEMA, Store

    if read_only:
        return Store(path, read_only=True)
    before = _stored_schema(path)
    store = Store(path)
    if before is not None and before != SCHEMA:
        print(
            f"daymark: upgraded {path} from schema {before or 'unmarked'} to {SCHEMA} "
            f"(Daymark {RELEASE}); use a pre-upgrade backup to return to an older schema",
            file=sys.stderr,
        )
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
    os.chmod(target, 0o600)
    print(f"{'Using existing' if existed else 'Created'} {target}")
    visible = resolve_db(None)
    command = "daymark prime" if visible and Path(visible).resolve() == target.resolve() else (
        f"daymark --db {shlex.quote(str(target.resolve()))} prime"
    )
    print(f"Next: `{command}` for this database; `daymark onboard` for project instructions.")
    return 0


def _onboard(args) -> int:
    from .render import ONBOARD

    if args.write or args.check:
        from .onboarding import maintain

        return maintain(Path(args.write or args.check), check=args.check is not None)
    print(ONBOARD, end="")
    return 0


def _simple(args, engine) -> int:
    """Run one everyday verb and print a lean rendering of its result."""
    from . import render
    from . import tasks as tasks_module
    from .tasks import Tasks

    tasks = Tasks(engine, args.workspace, actor=args.actor, timezone=args.tz)
    now, name, as_json = tasks.now(), args.command, getattr(args, "json", False)
    op = getattr(args, "op", None)

    def out(value, text):
        _print(value) if as_json else print(text)

    def entity(item):
        out(render.compact(item), render.line(item, now))

    scope = "" if getattr(args, "unscoped", False) else getattr(args, "scope", None)

    if name == "prime":
        context = render.session_context(tasks, database=args.db, url=args.url)
        if as_json and args.brief:
            _print(render.brief_session(tasks, context))
        elif as_json:
            _print(
                {
                    "context": context,
                    "summary": tasks.summary(),
                    "memories": [m["title"] for m in tasks.memories()],
                    "ready": [render.compact(x) for x in tasks.ready(limit=5)],
                }
            )
        else:
            print(render.prime_text(tasks, context=context, brief=args.brief))
    elif name in {"advise", "profile", "strategies"}:
        from . import strategies
        from .strategies.profiles import PROFILES

        if name == "strategies" and args.strategy is not None:
            data = tasks.profile_catalog(strategy=args.strategy)
            out(data, render.strategy_detail(data))
        elif name == "strategies":
            listed = strategies.available()
            out(
                {
                    "strategies": [
                        {"name": s.name, "kind": s.kind, "summary": s.summary,
                         "evidence": s.evidence,
                         "params": {k: p.default for k, p in s.params.items()}}
                        for s in listed
                    ],
                    "profiles": {k: [x["use"] for x in v["strategies"]] for k, v in PROFILES.items()},
                },
                render.strategies_text(listed, PROFILES),
            )  # fmt: skip
        elif name == "profile":
            _profile(tasks, args, out)
        else:
            advice = tasks.advise(
                profile=args.profile, settings=args.set, drops=args.drop, scope=scope, context=args.context
            )
            lean = advice.to_dict()
            lean["picks"] = [render.compact(x) for x in advice.picks]
            out(lean, render.advice_text(advice, now))
    elif name == "ready":
        if args.explain:
            view = tasks.explain_ready(limit=args.limit, move=args.move, scope=scope, context=args.context)
            out(view, render.ready_explanation(view))
        else:
            items = tasks.ready(limit=args.limit, move=args.move, scope=scope, context=args.context)
            empty = "Nothing is ready" + (" for these filters" if scope is not None or args.move or args.context else "")
            out(
                [{**render.compact(x), "why": render.why(x, now)} for x in items],
                render.listing(items, now, empty + ". Repeat with --explain to see what is held."),
            )
    elif name == "agenda":
        view = tasks.agenda(args.days, scope=scope, context=args.context)
        lean = {
            key: (
                {day: [render.compact(x) for x in items] for day, items in value.items()}
                if key == "upcoming"
                else [render.compact(x) for x in value]
                if isinstance(value, list)
                else value
            )
            for key, value in view.items()
        }
        out(lean, render.agenda_text(view, now))
    elif name == "activity":
        page = tasks.activity(after=args.after, limit=args.limit)
        out(page, render.activity_text(page))
    elif name == "show" and args.since_version is not None:
        comparison = tasks.changes_since(args.id, version=args.since_version, source=args.source)
        out(comparison, render.comparison_text(comparison))
    elif name == "show" and (args.brief or args.before_note is not None):
        brief = tasks.brief(args.id, before_note=args.before_note, source=args.source)
        out(brief, render.brief_text(brief))
    elif name == "show" and args.note is not None:
        note = tasks.read_note(args.id, args.note, source=args.source)
        out(note, render.note_text(note))
    elif name == "show":
        ctx = tasks.show(args.id, source=args.source)
        out({**ctx, "entity": render.compact(ctx["entity"])}, render.show_text(ctx, now))
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
        entity(getattr(tasks, name)(args.id, note=args.note, op=op))
    elif name in {"accept", "dismiss"}:
        entity(getattr(tasks, name)(args.id, expected_version=args.if_version, review_token=args.if_review, op=op))
    elif name in {"skip", "cancel", "reopen", "archive", "undo",
                  "resume", "forget", "unfocus"}:  # fmt: skip
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
        view = tasks.explore(args.goal)
        out(view, render.explore_text(view))
    elif name == "focus":
        entity(tasks.focus(args.id, args.rank, op=op))
    elif name == "take":
        selected = tasks.take(scope=scope, context=args.context, minutes=args.minutes, op=op)
        if selected is None:
            out(None, render.EMPTY_TAKE)
        else:
            entity(selected)
    elif name == "claim":
        entity(tasks.claim(args.id, minutes=args.minutes, op=op))
    elif name == "note":
        entity(tasks.note(args.id, args.text, op=op))
    elif name == "wait":
        entity(tasks.wait(args.id, args.on, until=args.until, op=op))
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
        if args.claimed or args.page or args.after is not None or scope is not None:
            page = tasks.search(args.text, kind=args.kind, status=args.status,
                                scope=scope, after=args.after, limit=args.limit, claimed=args.claimed)
            out(page, render.search_text(page))
        else:
            items = tasks.find(args.text, kind=args.kind, status=args.status, limit=args.limit)
            out([render.compact(x) for x in items], render.listing(items, now, "No matches."))
    elif name == "remember":
        entity(tasks.remember(args.text, op=op))
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
        view = tasks.projects()
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
            base = payload.get("profile", payload)
            if not isinstance(base, dict):
                raise DomainError("invalid", "A profile file must contain a profile configuration")
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


def _fail(args, code: str, message: str, details=()) -> int:
    """Report a failure by stable code. Messages name rules, never stored content."""
    if code == "conflict" and args.command in {"add", "propose"} and getattr(args, "source", None):
        details = [*details, "Check the original capture with daymark show --source SYSTEM:KIND:ID --brief."]
    if code == "conflict" and args.command in {"edit", "accept", "dismiss"} and getattr(args, "if_version", None):
        details = [*details, f"Compare the current record with daymark show ID --since-version {args.if_version}."]
    if getattr(args, "json", False):
        print(
            json.dumps({"error": {"code": code, "message": message, "details": list(details)}}),
            file=sys.stderr,
        )
    else:
        print(f"error: {code}: {message}", file=sys.stderr)
        for detail in details:
            print(f"  {detail}", file=sys.stderr)
    return {"not_found": 3, "conflict": 4, "invalid": 5, "no_database": 6, "forbidden": 7}.get(
        code, 1
    )


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    from .models import DomainError

    try:
        if args.command == "where":
            return _where(args)
        if args.command == "init":
            return _init(args)
        if args.command == "onboard":
            return _onboard(args)
        if not args.url:
            found = resolve_db(args.db)
            if found is None and args.command in SIMPLE:
                return _fail(
                    args,
                    "no_database",
                    "No Daymark database found",
                    ["Run `daymark init` (per-user) or `daymark init --here` (this folder)."],
                )
            args.db = found or "daymark.sqlite3"
        if args.command in {"backup", "restore"}:
            if args.url:
                raise ValueError("Backup commands require a local database")
            source, destination = (
                (Path(args.db), Path(args.file))
                if args.command == "backup"
                else (Path(args.file), Path(args.db))
            )
            _copy_database(source, destination)
            _print({"status": "ok"})
            return 0
        if args.command == "serve":
            import uvicorn

            from .api import create_app

            store = _open_store(args.db)
            app = create_app(store, _json_file(args.auth_file))
            # Uvicorn access logs include private URL/query values; never enable them here.
            try:
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
            fresh = args.db != ":memory:" and not Path(args.db).exists()
            engine = _open_store(args.db, read_only=getattr(args, "read_only", False))
            if fresh:
                os.chmod(args.db, 0o600)  # Task data is private by default.
        try:
            ws = [] if args.url else [args.workspace]
            if args.command == "workspaces":
                if args.url:
                    raise ValueError("Workspaces are listed on the local database")
                _print({"database": args.db, "current": args.workspace,
                        "workspaces": engine.workspaces()})  # fmt: skip
                return 0
            if args.command == "policy":
                if args.url:
                    raise ValueError("Policy is set on the local database")
                if args.clear or args.deciders is not None:
                    names = [] if args.clear else [x.strip() for x in args.deciders.split(",")]
                    engine.set_policy(args.workspace, deciders=[x for x in names if x])
                _print(engine.policy(args.workspace))
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
                    _json_file(args.file),
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
                with open(
                    args.output, "x", encoding="utf-8", opener=lambda p, f: os.open(p, f, 0o600)
                ) as handle:
                    json.dump(result, handle, default=str, sort_keys=True)
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
    except Exception as exc:  # noqa: BLE001 - prevent private exception text escaping
        status = getattr(exc, "status_code", None)
        if type(exc).__name__ == "ClientError" and status in {403, 404, 409, 422}:
            code = {403: "forbidden", 404: "not_found", 409: "conflict", 422: "invalid"}[status]
            return _fail(args, code, "The server refused the request")
        # Parsing/Store/network exceptions can contain private paths and payloads.
        print(
            "Daymark command failed; check configuration, payload and expected version.",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
