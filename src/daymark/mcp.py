"""A dependency-free Model Context Protocol server over stdio.

Six tools cover the everyday surface, so an agent harness spends few context
tokens on tool definitions. Run with `daymark mcp`.
"""

from __future__ import annotations

import json
import sys

from . import __version__, render
from .client import ClientError
from .models import DomainError

# Only advertise dated revisions for which we implement the tools/stdio surface.
_PROTOCOLS = ("2024-11-05", "2025-06-18", "2025-11-25")
_OP = {"type": "string", "minLength": 1}

_FIELDS = {
    "due": {"type": "string", "description": "Hard cutoff."},
    "plan": {"type": "string", "description": "Intended date."},
    "start": {"type": "string", "description": "Earliest start."},
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
    "scope": {"type": "string"},
    "advances": {"type": "array", "items": {"type": "string"}},
    "stage": {"type": "string"},
    "milestone": {"type": ["string", "boolean"], "description": "Milestone ID or marker."},
    "is_milestone": {"type": "boolean"},
    "type": {"type": "string"},
    "values": {"type": "object", "description": "Project fields by id."},
}
_DATES = "Dates: YYYY-MM-DD, YYYY-MM-DDTHH:MM, today, tomorrow, fri, +Nd, +Nw, +Nm."
_ACTIONS = [
    "done", "skip", "cancel", "reopen", "archive", "undo", "accept", "dismiss", "claim",
    "release", "take", "note", "wait", "resume", "defer", "block", "unblock", "forget",
    "focus", "unfocus", "move", "assess",
]  # fmt: skip

TOOLS = [
    {
        "name": "daymark_prime",
        "description": "Call first for workflow, context and memories.",
        "annotations": {"readOnlyHint": True},
        "inputSchema": {"type": "object", "properties": {"brief": {"type": "boolean"}}},
    },
    {
        "name": "daymark_view",
        "annotations": {"readOnlyHint": True},
        "description": "ready: actionable; activity: recent changes; agenda: dates and waits; show: blockers/notes/history; "
        "find: text search; plan: project board; explore: goal conditions; "
        "advise: profile preview.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "view": {
                    "type": "string",
                    "enum": [
                        "ready",
                        "advise",
                        "profiles",
                        "explore",
                        "agenda",
                        "activity",
                        "show",
                        "find",
                        "projects",
                        "plan",
                        "goals",
                        "goal",
                    ],
                },
                "id": {"type": "string"},
                "text": {"type": "string"},
                "after": {
                    "type": "integer",
                    "minimum": 0,
                    "description": "Activity cursor.",
                },
                "days": {"type": "integer", "minimum": 0, "maximum": 366},
                "limit": {"type": "integer", "minimum": 1, "maximum": 1000},
                "move": {"type": "string", "enum": ["person", "answer", "agent"]},
                "scope": {"type": "string", "description": 'Only this scope; "" for unscoped.'},
                "by": {"type": "string"},
                "profile": {"type": "string", "description": "Built-in or saved profile name."},
                "set": {"type": "array", "items": {"type": "string"}},
                "drop": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["view"],
        },
    },
    {
        "name": "daymark_add",
        "description": "Capture requested work. Use proposed=true for suggestions. " + _DATES,
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
                "op": _OP,
                **_FIELDS,
            },
            "required": ["title"],
        },
    },
    {
        "name": "daymark_edit",
        "description": "Edit fields. Empty string clears a date; expected_version guards a prior read.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "id": {"type": "string"},
                "expected_version": {"type": "integer", "minimum": 1},
                "title": {"type": "string"},
                "op": _OP,
                **_FIELDS,
            },
            "required": ["id"],
        },
    },
    {
        "name": "daymark_act",
        "description": "note/done/release: text; wait: on, until; defer: until; block/unblock: on; "
        "take: no id, scope/minutes; otherwise id. claim: minutes; move: stage/place; assess: state, text evidence.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": _ACTIONS},
                "id": {"type": "string"},
                "text": {"type": "string"},
                "on": {"type": "string"},
                "until": {"type": "string"},
                "scope": {"type": "string"},
                "minutes": {"type": "integer"},
                "state": {
                    "type": "string",
                    "description": "For assess: met, partly_met, unmet, unknown.",
                },
                "stage": {"type": "string"},
                "place": {"type": "string", "description": "after:ID, before:ID, first or last"},
                "op": _OP,
            },
            "required": ["action"],
        },
    },
    {
        "name": "daymark_remember",
        "description": "Store a fact for future sessions.",
        "inputSchema": {
            "type": "object",
            "properties": {"text": {"type": "string"}, "op": _OP},
            "required": ["text"],
        },
    },
]


for tool in TOOLS:
    tool["inputSchema"]["additionalProperties"] = False
_TOOLS = {tool["name"]: tool for tool in TOOLS}
_TYPES = {"string": str, "boolean": bool, "integer": int, "array": list, "object": dict}

# The multiplexed tools have one schema each. Refuse arguments intended for a
# different verb so a filter or write instruction can never silently disappear.
_VIEW_ARGS = {
    "ready": {"limit", "move", "scope"},
    "advise": {"scope", "profile", "set", "drop"},
    "profiles": {"profile"},
    "explore": {"id"},
    "agenda": {"days", "scope"},
    "activity": {"after", "limit"},
    "show": {"id"},
    "find": {"text", "limit"},
    "plan": {"id", "by"},
    "goal": {"id"},
}
_ACT_ARGS = {
    "take": {"scope", "minutes"},
    "note": {"text"},
    "done": {"text"},
    "release": {"text"},
    "wait": {"on", "until"},
    "defer": {"until"},
    "block": {"on"},
    "unblock": {"on"},
    "assess": {"state", "text"},
    "claim": {"minutes"},
    "move": {"stage", "place"},
}


def _validate_value(key, value, schema):
    kind = schema["type"]
    kinds = [kind] if isinstance(kind, str) else kind
    if not any(type(value) is _TYPES[t] for t in kinds):
        raise DomainError("invalid", f"{key} must be a JSON {' or '.join(kinds)}")
    if "enum" in schema and value not in schema["enum"]:
        raise DomainError("invalid", f"{key} must be one of " + ", ".join(schema["enum"]))
    if kind == "array":
        for child in value:
            _validate_value(f"{key} item", child, schema["items"])
    if "minLength" in schema and len(value) < schema["minLength"]:
        raise DomainError(
            "invalid", f"{key} must contain at least {schema['minLength']} character(s)"
        )
    if "minimum" in schema and value < schema["minimum"]:
        raise DomainError("invalid", f"{key} must be at least {schema['minimum']}")
    if "maximum" in schema and value > schema["maximum"]:
        raise DomainError("invalid", f"{key} must be at most {schema['maximum']}")


def _validate_arguments(name, args):
    schema = _TOOLS[name]["inputSchema"]
    for key in schema.get("required", ()):
        if key not in args:
            raise DomainError("invalid", f"missing argument {key}")
    for key, value in args.items():
        if key not in schema["properties"]:
            raise DomainError("invalid", "Unknown argument", [key])
        _validate_value(key, value, schema["properties"][key])
    if name == "daymark_view":
        verb = args["view"]
        allowed = _VIEW_ARGS.get(verb, set()) | {"view"}
    elif name == "daymark_act":
        verb = args["action"]
        allowed = _ACT_ARGS.get(verb, set()) | {"action", "op"}
        if verb != "take":
            if "id" not in args:
                raise DomainError("invalid", "missing argument id")
            allowed.add("id")
    else:
        return
    if extra := args.keys() - allowed:
        raise DomainError("invalid", f"{verb} does not take: {', '.join(sorted(extra))}")


def _place(text):
    if not text:
        return {}
    if text in {"first", "last"}:
        return {text: True}
    side, _, ref = text.partition(":")
    if side not in {"after", "before"} or not ref:
        raise DomainError("invalid", "place is after:ID, before:ID, first or last")
    return {side: ref}


def call(tasks, name, args, *, context=None):
    """Run one tool and return the same lean text as the command line."""
    result = _invoke(tasks, name, args, context=context)
    return render.line(result, tasks.now()) if isinstance(result, dict) else result


def _invoke(tasks, name, args, *, context=None):
    """Reads produce text. Writes return the entity from the committed operation."""
    _validate_arguments(name, args)
    now = tasks.now()
    if name == "daymark_prime":
        return render.prime_text(tasks, context=context, brief=args.get("brief", False))
    if name == "daymark_view":
        view = args.get("view")
        if view == "ready":
            items = tasks.ready(
                limit=args.get("limit", 20), move=args.get("move"), scope=args.get("scope")
            )
            return render.listing(items, now, "Nothing is ready.")
        if view == "explore":
            return render.explore_text(tasks.explore(args.get("id")))
        if view == "profiles":
            return render.saved_profiles_text(tasks.saved_profiles(args.get("profile")))
        if view == "advise":
            advice = tasks.advise(
                profile=args.get("profile"),
                settings=args.get("set", []),
                drops=args.get("drop", []),
                scope=args.get("scope"),
            )
            return render.advice_text(advice, now)
        if view == "activity":
            page = tasks.activity(after=args.get("after"), limit=args.get("limit", 20))
            return render.activity_text(page)
        if view == "agenda":
            return render.agenda_text(
                tasks.agenda(args.get("days", 7), scope=args.get("scope")), now
            )
        if view == "show":
            return render.show_text(tasks.show(args["id"]), now)
        if view == "find":
            return render.listing(
                tasks.find(args.get("text"), limit=args.get("limit", 50)), now, "No matches."
            )
        if view == "projects":
            return render.projects_text(tasks.projects())
        if view == "plan":
            return render.plan_text(tasks.plan(args["id"]), now, by=args.get("by") or "stage")
        if view == "goals":
            return render.goals_text(tasks.goals())
        if view == "goal":
            return render.goal_text(tasks.goal_plan(args["id"]))
        raise DomainError(
            "invalid", "view must be ready, agenda, show, find, projects, plan, goals or goal"
        )
    if name in {"daymark_add", "daymark_edit"}:
        args = dict(args)
        if name == "daymark_add":
            return tasks.add(args.pop("title"), **args)
        return tasks.edit(args.pop("id"), **args)
    if name == "daymark_remember":
        return tasks.remember(args["text"], op=args.get("op"))
    if name == "daymark_act":
        if args["action"] == "take":
            return tasks.take(
                scope=args.get("scope"), minutes=args.get("minutes", 60), op=args.get("op")
            ) or render.EMPTY_TAKE
        action, id, op = args.get("action"), args["id"], args.get("op")
        if action not in _ACTIONS:
            raise DomainError("invalid", "Unknown action")
        if action == "note":
            item = tasks.note(id, args["text"], op=op)
        elif action in {"done", "release"}:
            item = getattr(tasks, action)(id, note=args.get("text"), op=op)
        elif action == "wait":
            item = tasks.wait(id, args["on"], until=args.get("until"), op=op)
        elif action == "defer":
            item = tasks.defer(id, args["until"], op=op)
        elif action in {"block", "unblock"}:
            item = getattr(tasks, action)(id, args["on"], op=op)
        elif action == "assess":
            item = tasks.assess(id, args["state"], evidence=args.get("text"), op=op)
        elif action == "claim":
            item = tasks.claim(id, minutes=args.get("minutes", 60), op=op)
        elif action == "move":
            item = tasks.move(id, stage=args.get("stage"), op=op, **_place(args.get("place")))
        else:
            item = getattr(tasks, action)(id, op=op)
        return item
    raise DomainError("not_found", "Unknown tool")


def _rpc_error(id, code, message):
    return {"jsonrpc": "2.0", "id": id, "error": {"code": code, "message": message}}


def handle(tasks, message, *, context=None, protocol=_PROTOCOLS[-1]):
    """Answer one JSON-RPC request. Valid notifications never get a response."""
    if not isinstance(message, dict):
        return _rpc_error(None, -32600, "Invalid request")
    id = message.get("id")
    if "id" in message and type(id) not in (str, int):
        return _rpc_error(None, -32600, "Invalid request id")
    method = message.get("method")
    if message.get("jsonrpc") != "2.0" or not isinstance(method, str):
        return _rpc_error(id, -32600, "Invalid request")
    if "id" not in message:
        return None
    params = message.get("params", {})
    if not isinstance(params, dict):
        return _rpc_error(id, -32602, "params must be an object")

    def reply(result):
        return {"jsonrpc": "2.0", "id": id, "result": result}

    if method == "initialize":
        version = params.get("protocolVersion")
        if not isinstance(version, str) or not version:
            return _rpc_error(id, -32602, "initialize requires protocolVersion")
        return reply(
            {
                "protocolVersion": version if version in _PROTOCOLS else _PROTOCOLS[-1],
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "daymark", "version": __version__},
                "instructions": "Call daymark_prime first. For each write, choose a fresh op "
                "and reuse it if that same write needs a retry. Never invent dates or completions.",
            }
        )
    if method == "ping":
        return reply({})
    if method == "tools/list":
        return reply({"tools": TOOLS})
    if method != "tools/call":
        return _rpc_error(id, -32601, "Method not found")
    name = params.get("name")
    arguments = params.get("arguments", {})
    if not isinstance(name, str) or not isinstance(arguments, dict):
        return _rpc_error(id, -32602, "tools/call requires a name and an arguments object")
    if name not in _TOOLS:
        return _rpc_error(id, -32602, "Unknown tool. Use tools/list for the available names.")
    try:
        value = _invoke(tasks, name, arguments, context=context)
        result = {"isError": False}
        if isinstance(value, dict):
            text = render.line(value, tasks.now())
            if protocol >= "2025-06-18":
                result["structuredContent"] = {
                    key: value[key] for key in ("id", "kind", "status", "version")
                }
        else:
            text = value
        result["content"] = [{"type": "text", "text": text}]
    except DomainError as exc:
        text = "\n".join([f"error: {exc.code}: {exc}", *exc.details])
    except KeyError as exc:
        text = f"error: invalid: missing argument {exc.args[0]}"
    except ClientError as exc:
        text = f"error: {exc.code}: {exc}"
    except Exception:  # noqa: BLE001 - never leak stored content through errors
        return _rpc_error(id, -32603, "The request failed")
    else:
        return reply(result)
    return reply({"content": [{"type": "text", "text": text}], "isError": True})


def serve(tasks, stdin=None, stdout=None, *, context=None):
    stdin, stdout = stdin or sys.stdin, stdout or sys.stdout
    protocol = _PROTOCOLS[0]
    for raw in stdin:
        if not raw.strip():
            continue
        try:
            message = json.loads(raw)
        except ValueError:
            response = _rpc_error(None, -32700, "Parse error")
        else:
            response = handle(tasks, message, context=context, protocol=protocol)
            if response and "protocolVersion" in response.get("result", {}):
                protocol = response["result"]["protocolVersion"]
        if response is not None:
            stdout.write(json.dumps(response) + "\n")
            stdout.flush()
