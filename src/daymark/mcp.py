"""A dependency-free Model Context Protocol server over stdio.

Six tools cover the everyday surface, so an agent harness spends few context
tokens on tool definitions. Run with `daymark mcp`.
"""

from __future__ import annotations

import json
import sys

from . import __version__, render
from .models import DomainError

_FIELDS = {
    "due": {"type": "string", "description": "Hard deadline. Date grammar below."},
    "plan": {"type": "string", "description": "Intended day or time; may move freely."},
    "start": {"type": "string", "description": "Not actionable before this."},
    "target": {"type": "string", "description": "Soft latest target."},
    "every": {
        "type": "string",
        "description": "day, week, month, year, '2 weeks', weekdays, 'mon,thu'.",
    },
    "after_completion": {"type": "boolean", "description": "Repeat from actual completion."},
    "priority": {"type": "integer", "minimum": 0, "maximum": 4, "description": "1 most important."},
    "description": {"type": "string"},
    "after": {"type": "array", "items": {"type": "string"}, "description": "IDs this depends on."},
    "parent": {"type": "string"},
    "project": {"type": "string"},
    "waiting_on": {"type": "string"},
    "move": {"type": "string", "enum": ["person", "answer", "agent"]},
    "minutes": {"type": "integer", "description": "Estimated effort."},
    "scope": {"type": "string", "description": "Part of life it belongs to, e.g. household."},
    "stage": {"type": "string", "description": "Stage on the project's board; sets status."},
    "milestone": {"type": "boolean"},
}
_DATES = (
    "Dates: YYYY-MM-DD, YYYY-MM-DDTHH:MM, today, tomorrow, a weekday name, +Nd, +Nw, +Nm. "
    "Never invent a date; a deadline is a hard cutoff and a plan is an intention."
)
_ACTIONS = [
    "done", "skip", "cancel", "reopen", "archive", "undo", "accept", "dismiss", "claim",
    "release", "note", "wait", "resume", "defer", "block", "unblock", "forget",
    "focus", "unfocus", "move",
]  # fmt: skip

TOOLS = [
    {
        "name": "daymark_prime",
        "description": "Workflow guide plus current state and remembered facts. Call first.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "daymark_view",
        "description": "Read tasks. view=ready (actionable now, most pressing first), "
        "agenda (overdue, today, days ahead, waiting, proposed), show (one task by id "
        "with blockers, notes, history), find (search by text), projects, or plan (id=project: "
        "its board by stage, in order).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "view": {
                    "type": "string",
                    "enum": ["ready", "agenda", "show", "find", "projects", "plan"],
                },
                "id": {"type": "string"},
                "text": {"type": "string"},
                "days": {"type": "integer"},
                "limit": {"type": "integer"},
                "move": {"type": "string", "enum": ["person", "answer", "agent"]},
                "scope": {"type": "string", "description": 'Only this scope; "" for unscoped.'},
            },
            "required": ["view"],
        },
    },
    {
        "name": "daymark_add",
        "description": "Capture a task. Set proposed=true to suggest work the person did "
        "not ask for; they accept or dismiss it. " + _DATES,
        "inputSchema": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "proposed": {"type": "boolean"},
                "note": {"type": "string"},
                "source": {"type": "string", "description": "system:kind:id; refuses duplicates."},
                "kind": {
                    "type": "string",
                    "description": "Default task; also project, goal, person.",
                },
                "op": {"type": "string", "description": "Repeat to retry safely."},
                **_FIELDS,
            },
            "required": ["title"],
        },
    },
    {
        "name": "daymark_edit",
        "description": "Change fields of an entity. An empty string clears a date. " + _DATES,
        "inputSchema": {
            "type": "object",
            "properties": {"id": {"type": "string"}, "title": {"type": "string"}, **_FIELDS},
            "required": ["id"],
        },
    },
    {
        "name": "daymark_act",
        "description": "Act on a task. note needs text; wait needs on (and optional until); "
        "defer needs until; block/unblock need on (another task id); claim takes minutes. "
        "done accepts text as a closing note. move takes stage and/or place.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": _ACTIONS},
                "id": {"type": "string"},
                "text": {"type": "string"},
                "on": {"type": "string"},
                "until": {"type": "string"},
                "minutes": {"type": "integer"},
                "stage": {"type": "string"},
                "place": {"type": "string", "description": "after:ID, before:ID, first or last"},
                "op": {"type": "string", "description": "Repeat to retry safely."},
            },
            "required": ["action", "id"],
        },
    },
    {
        "name": "daymark_remember",
        "description": "Keep a durable fact about the person or project for later sessions.",
        "inputSchema": {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        },
    },
]


def _place(text):
    if not text:
        return {}
    if text in {"first", "last"}:
        return {text: True}
    side, _, ref = text.partition(":")
    if side not in {"after", "before"} or not ref:
        raise DomainError("invalid", "place is after:ID, before:ID, first or last")
    return {side: ref}


def call(tasks, name, args):
    """Run one tool and return its text result."""
    now = tasks.now()
    if name == "daymark_prime":
        return render.prime_text(tasks)
    if name == "daymark_view":
        view = args.get("view")
        if view == "ready":
            items = tasks.ready(
                limit=args.get("limit", 20), move=args.get("move"), scope=args.get("scope")
            )
            return render.listing(items, now, "Nothing is ready.")
        if view == "agenda":
            return render.agenda_text(
                tasks.agenda(args.get("days", 7), scope=args.get("scope")), now
            )
        if view == "show":
            return render.show_text(tasks.show(args["id"]), now)
        if view == "find":
            return render.listing(tasks.find(args.get("text")), now, "No matches.")
        if view == "projects":
            return render.projects_text(tasks.projects())
        if view == "plan":
            return render.plan_text(tasks.plan(args["id"]), now)
        raise DomainError("invalid", "view must be ready, agenda, show, find, projects or plan")
    if name in {"daymark_add", "daymark_edit"}:
        known = next(t for t in TOOLS if t["name"] == name)["inputSchema"]["properties"]
        if set(args) - set(known):
            raise DomainError("invalid", "Unknown argument", sorted(set(args) - set(known)))
        args = dict(args)
        if name == "daymark_add":
            return render.line(tasks.add(args.pop("title"), **args), now)
        return render.line(tasks.edit(args.pop("id"), **args), now)
    if name == "daymark_remember":
        return render.line(tasks.remember(args["text"]), now)
    if name == "daymark_act":
        action, id, op = args.get("action"), args["id"], args.get("op")
        if action not in _ACTIONS:
            raise DomainError("invalid", "Unknown action")
        if action == "note":
            item = tasks.note(id, args["text"], op=op)
        elif action == "done":
            item = tasks.done(id, note=args.get("text"), op=op)
        elif action == "wait":
            item = tasks.wait(id, args["on"], until=args.get("until"), op=op)
        elif action == "defer":
            item = tasks.defer(id, args["until"], op=op)
        elif action in {"block", "unblock"}:
            item = getattr(tasks, action)(id, args["on"], op=op)
        elif action == "claim":
            item = tasks.claim(id, minutes=args.get("minutes", 60), op=op)
        elif action == "move":
            item = tasks.move(id, stage=args.get("stage"), op=op, **_place(args.get("place")))
        else:
            item = getattr(tasks, action)(id, op=op)
        return render.line(item, now)
    raise DomainError("not_found", "Unknown tool")


def handle(tasks, message):
    """Answer one JSON-RPC message; notifications return None."""
    method, id = message.get("method"), message.get("id")
    if id is None:
        return None
    if not isinstance(message.get("params") or {}, dict):
        return {
            "jsonrpc": "2.0",
            "id": id,
            "error": {"code": -32602, "message": "Invalid params"},
        }

    def reply(result):
        return {"jsonrpc": "2.0", "id": id, "result": result}

    if method == "initialize":
        version = (message.get("params") or {}).get("protocolVersion") or "2024-11-05"
        return reply(
            {
                "protocolVersion": version,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "daymark", "version": __version__},
                "instructions": "Call daymark_prime first. Never invent dates or completions.",
            }
        )
    if method == "ping":
        return reply({})
    if method == "tools/list":
        return reply({"tools": TOOLS})
    if method == "tools/call":
        params = message.get("params") or {}
        try:
            arguments = params.get("arguments") or {}
            if not isinstance(arguments, dict):
                raise TypeError
            text, failed = call(tasks, params.get("name"), arguments), False
        except DomainError as exc:
            text = "\n".join([f"error: {exc.code}: {exc}", *exc.details])
            failed = True
        except KeyError as exc:
            text, failed = f"error: invalid: missing argument {exc.args[0]}", True
        except (TypeError, AttributeError):
            text, failed = "error: invalid: unexpected or missing arguments", True
        except Exception:  # noqa: BLE001 - never leak stored content through errors
            text, failed = "error: internal: the request failed", True
        return reply({"content": [{"type": "text", "text": text}], "isError": failed})
    return {
        "jsonrpc": "2.0",
        "id": id,
        "error": {"code": -32601, "message": "Method not found"},
    }


def serve(tasks, stdin=None, stdout=None):
    stdin, stdout = stdin or sys.stdin, stdout or sys.stdout
    for raw in stdin:
        if not raw.strip():
            continue
        try:
            message = json.loads(raw)
        except ValueError:
            response = {
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32700, "message": "Parse error"},
            }
        else:
            response = handle(tasks, message) if isinstance(message, dict) else None
        if response is not None:
            stdout.write(json.dumps(response) + "\n")
            stdout.flush()
