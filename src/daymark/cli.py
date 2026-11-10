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
    "prime", "ready", "agenda", "show", "add", "propose", "edit", "done", "skip", "cancel",
    "reopen", "archive", "undo", "accept", "dismiss", "claim", "release", "note", "wait",
    "resume", "defer", "block", "unblock", "remind", "find", "remember", "forget", "mcp",
}  # fmt: skip


def data_home() -> Path:
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share") / "daymark"


def resolve_db(explicit: str | None) -> str | None:
    """--db, then DAYMARK_DB, the nearest .daymark/ above the working directory,
    a legacy ./daymark.sqlite3, then the per-user database."""
    if explicit:
        return explicit
    if os.environ.get("DAYMARK_DB"):
        return os.environ["DAYMARK_DB"]
    here = Path.cwd()
    for folder in (here, *here.parents):
        candidate = folder / ".daymark" / "daymark.sqlite3"
        if candidate.is_file():
            return str(candidate)
    for candidate in (here / "daymark.sqlite3", data_home() / "daymark.sqlite3"):
        if candidate.is_file():
            return str(candidate)
    return None


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
    item.add_argument("--assignee", metavar="ID", help="A person or principal entity")
    item.add_argument("--label", action="append", dest="labels", metavar="ID")
    item.add_argument("--context", action="append", dest="contexts", metavar="ID")
    item.add_argument("--waiting-on", metavar="WHO")
    item.add_argument("--move", choices=["person", "answer", "agent"], help="Whose move it is")
    item.add_argument("--minutes", type=int, help="Estimated effort")
    if not editing:
        item.add_argument("--kind", default="task", help="task, project, goal, person, label, ...")
        item.add_argument("--note", help="First progress note")
        item.add_argument("--source", metavar="SYSTEM:KIND:ID", help="Origin; refuses duplicates")
        item.add_argument("--id", help="Choose the ID instead of a generated short one")


def _simple_commands(sub) -> None:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--json", action="store_true", help="Compact JSON instead of text")
    write = argparse.ArgumentParser(add_help=False)
    write.add_argument("--op", help="Operation ID; repeat it to retry a write safely")

    def add(name, help, *, writes=False):
        return sub.add_parser(name, help=help, parents=[common, *([write] if writes else [])])

    init = sub.add_parser("init", help="Create a database: per-user, or --here for this folder")
    init.add_argument("--here", action="store_true", help="Use ./.daymark/ in this directory")
    add("prime", "Print the workflow guide and current state; read this first")
    onboard = sub.add_parser("onboard", help="Print (or --write) the snippet for AGENTS.md")
    onboard.add_argument("--write", metavar="FILE", help="Insert or refresh the snippet in FILE")
    ready = add("ready", "Tasks that can be done now, most pressing first")
    ready.add_argument("-n", "--limit", type=int, default=20)
    ready.add_argument("--move", choices=["person", "answer", "agent"], help="Only this next move")
    agenda = add("agenda", "Overdue, today, the days ahead, waiting and proposed")
    agenda.add_argument("--days", type=int, default=7)
    show = add("show", "One entity with blockers, notes, reminders and history")
    show.add_argument("id")
    for name, help in (("add", "Capture a task"), ("propose", "Suggest a task for a person")):
        item = add(name, help, writes=True)
        item.add_argument("title")
        _field_options(item, editing=False)
    edit = add("edit", 'Change fields; pass "" to clear a date or link', writes=True)
    edit.add_argument("id")
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
    ):
        item = add(name, help, writes=True)
        item.add_argument("id")
        if name == "done":
            item.add_argument("--note", help="Closing note")
    claim = add("claim", "Take a task so others leave it alone", writes=True)
    claim.add_argument("id")
    claim.add_argument("--minutes", type=int, default=60, help="Claim length (default 60)")
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
    remember = add("remember", "Keep a durable fact for later sessions", writes=True)
    remember.add_argument("text")
    sub.add_parser("mcp", help="Serve these verbs to an agent harness over MCP (stdio)")


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(
        prog="daymark", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
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
        default=os.environ.get("DAYMARK_ACTOR", "agent"),
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


def _init(args) -> int:
    explicit = args.db or os.environ.get("DAYMARK_DB")
    target = (
        Path(explicit)
        if explicit
        else (Path.cwd() / ".daymark" if args.here else data_home()) / "daymark.sqlite3"
    )
    existed = target.is_file()
    target.parent.mkdir(parents=True, exist_ok=True)
    from .store import Store

    Store(str(target)).close()
    os.chmod(target, 0o600)
    print(f"{'Using existing' if existed else 'Created'} {target}")
    print("Next: `daymark prime` for the workflow; `daymark onboard` for an AGENTS.md snippet.")
    return 0


def _onboard(args) -> int:
    from .render import ONBOARD

    if not args.write:
        print(ONBOARD, end="")
        return 0
    path = Path(args.write)
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    begin, end = "<!-- daymark:begin -->", "<!-- daymark:end -->"
    if begin in text and end in text:
        text = text[: text.index(begin)] + ONBOARD.rstrip("\n") + text[text.index(end) + len(end) :]
    else:
        text = (text.rstrip("\n") + "\n\n" if text.strip() else "") + ONBOARD
    path.write_text(text, encoding="utf-8")
    print(f"Wrote the Daymark section to {path}")
    return 0


def _simple(args, engine) -> int:
    """Run one everyday verb and print a lean rendering of its result."""
    from . import render
    from .tasks import Tasks

    tasks = Tasks(engine, args.workspace, actor=args.actor, timezone=args.tz)
    now, name, as_json = tasks.now(), args.command, getattr(args, "json", False)
    op = getattr(args, "op", None)

    def out(value, text):
        _print(value) if as_json else print(text)

    def entity(item):
        out(render.compact(item), render.line(item, now))

    if name == "prime":
        if as_json:
            _print(
                {
                    "summary": tasks.summary(),
                    "memories": [m["title"] for m in tasks.memories()],
                    "ready": [render.compact(x) for x in tasks.ready(limit=5)],
                }
            )
        else:
            print(render.prime_text(tasks))
    elif name == "ready":
        items = tasks.ready(limit=args.limit, move=args.move)
        out(
            [{**render.compact(x), "why": render.why(x, now)} for x in items],
            render.listing(items, now, "Nothing is ready. Try `daymark agenda`."),
        )
    elif name == "agenda":
        view = tasks.agenda(args.days)
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
    elif name == "show":
        ctx = tasks.show(args.id)
        out({**ctx, "entity": render.compact(ctx["entity"])}, render.show_text(ctx, now))
    elif name in {"add", "propose", "edit"}:
        names = ("due", "plan", "start", "target", "priority", "description", "after", "parent",
                 "project", "goal", "assignee", "labels", "contexts", "waiting_on", "move",
                 "minutes")  # fmt: skip
        fields = {key: getattr(args, key) for key in names if getattr(args, key) is not None}
        if name == "edit":
            if args.title is not None:
                fields["title"] = args.title
            item = tasks.edit(
                args.id, every=args.every, after_completion=args.after_completion, op=op, **fields
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
    elif name == "done":
        entity(tasks.done(args.id, note=args.note, op=op))
    elif name in {"skip", "cancel", "reopen", "archive", "undo", "accept", "dismiss", "release",
                  "resume", "forget"}:  # fmt: skip
        entity(getattr(tasks, name)(args.id, op=op))
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
        items = tasks.find(args.text, kind=args.kind, status=args.status)
        out([render.compact(x) for x in items], render.listing(items, now, "No matches."))
    elif name == "remember":
        entity(tasks.remember(args.text, op=op))
    elif name == "mcp":
        from .mcp import serve

        serve(tasks)
    return 0


def _fail(args, code: str, message: str, details=()) -> int:
    """Report a failure by stable code. Messages name rules, never stored content."""
    if getattr(args, "json", False):
        print(
            json.dumps({"error": {"code": code, "message": message, "details": list(details)}}),
            file=sys.stderr,
        )
    else:
        print(f"error: {code}: {message}", file=sys.stderr)
        for detail in details:
            print(f"  {detail}", file=sys.stderr)
    return {"not_found": 3, "conflict": 4, "invalid": 5, "no_database": 6}.get(code, 1)


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    from .models import DomainError

    try:
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
            from .store import Store

            store = Store(args.db)
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

            engine = Client(args.url, os.environ.get(args.token_env, ""))
        else:
            from .store import Store

            engine = Store(args.db)
        try:
            ws = [] if args.url else [args.workspace]
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
            elif args.command == "events":
                result = engine.events(*ws, after=args.after, limit=args.limit)
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
        if type(exc).__name__ == "ClientError" and status in {404, 409, 422}:
            code = {404: "not_found", 409: "conflict", 422: "invalid"}[status]
            return _fail(args, code, "The server refused the request")
        # Parsing/Store/network exceptions can contain private paths and payloads.
        print(
            "Daymark command failed; check configuration, payload and expected version.",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
