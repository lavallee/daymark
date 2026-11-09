"""Cloud-free argparse interface; HTTP service dependencies load only when selected."""

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


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description=__doc__)
    root.add_argument(
        "--db", default="daymark.sqlite3", help="Local SQLite path (default: daymark.sqlite3)"
    )
    root.add_argument("--workspace", default="default", help="Workspace for local commands")
    root.add_argument("--url", help="Use an HTTP server instead of local Store")
    root.add_argument(
        "--token-env",
        default="DAYMARK_TOKEN",
        help="Environment variable containing HTTP bearer credential",
    )
    sub = root.add_subparsers(dest="command", required=True)
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


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
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
    except Exception:  # noqa: BLE001 - prevent private exception text escaping
        # Parsing/Store/network exceptions can contain private paths and payloads.
        print(
            "Daymark command failed; check configuration, payload and expected version.",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
