"""A dependency-free Model Context Protocol server over stdio.

Six tools cover the everyday surface, so an agent harness spends few context
tokens on tool definitions. Run with `daymark mcp`.
"""

from __future__ import annotations

import json
import sys
import uuid

from . import __version__, render
from .client import Client, ClientError
from .cursors import cursor_number
from .mcp_views import StructuredView, read_view
from .models import DomainError

# Only advertise dated revisions for which we implement the tools/stdio surface.
_PROTOCOLS = ("2024-11-05", "2025-06-18", "2025-11-25")
_OP = {"type": "string", "minLength": 1}

_FIELDS = {
    **{key: {"type": "string"} for key in ("due", "plan", "start", "target")},
    "every": {"type": "string", "description": "day/week/month/year, weekdays, mon,thu"},
    "after_completion": {"type": "boolean"},
    "priority": {"type": "integer", "minimum": 0, "maximum": 4},
    "description": {"type": "string"},
    "after": {"type": "array", "items": {"type": "string"}, "description": "Prerequisite IDs"},
    "parent": {"type": "string"},
    "contexts": {"type": "array", "items": {"type": "string"}},
    "project": {"type": "string"},
    "waiting_on": {"type": "string"},
    "move": {"type": "string", "enum": ["person", "answer", "agent"]},
    "minutes": {"type": "integer"},
    "scope": {"type": "string"},
    "advances": {"type": "array", "items": {"type": "string"}},
    "stage": {"type": "string"},
    "milestone": {"type": ["string", "boolean"]},
    "is_milestone": {"type": "boolean"},
    "type": {"type": "string"},
    "values": {"type": "object"},
}
_DATES = "due=hard, plan=intended, start=earliest, target=soft. "
_DATE_FORMS = "YYYY-MM-DD, YYYY-MM-DDTHH:MM, today, tomorrow, fri, +Nd/+Nw/+Nm."
_ACTIONS = [
    "done", "skip", "cancel", "reopen", "archive", "undo", "accept", "dismiss", "claim",
    "release", "take", "note", "wait", "resume", "defer", "block", "unblock", "forget",
    "focus", "unfocus", "move", "assess", "use_profile", "remind",
]  # fmt: skip

TOOLS = [
    {
        "name": "daymark_prime",
        "description": "Start here.",
        "annotations": {"readOnlyHint": True},
        "inputSchema": {"type": "object", "properties": {"brief": {"type": "boolean"}}},
    },
    {
        "name": "daymark_view",
        "annotations": {"readOnlyHint": True},
        "description": "json=true for data. Known ID/source: show (brief/note/since_version). ready: explain. "
        "plan: project id. explore: goal id. advise: set/drop. profiles: strategy.",
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
                "brief": {"type": "boolean"},
                "note": {"type": "string"},
                "source": {"type": "string"},
                "explain": {"type": "boolean"},
                "claimed": {"type": "boolean"},
                "context": {"type": "string"},
                "before_note": {"type": "string"},
                "since_version": {"type": "integer", "minimum": 1},
                "after": {"type": ["integer", "string"]},
                "json": {"type": "boolean"},
                "kind": {"type": "string"},
                "status": {"type": "string"},
                "days": {"type": "integer", "minimum": 0, "maximum": 366},
                "limit": {"type": "integer", "minimum": 1, "maximum": 1000},
                "move": {"type": "string", "enum": ["person", "answer", "agent"]},
                "scope": {"type": "string", "description": '"" selects unscoped.'},
                "by": {"type": "string"},
                "profile": {"type": "string"},
                "strategy": {"type": "string"},
                "set": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "STRATEGY or STRATEGY.PARAM=VALUE",
                },
                "drop": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["view"],
        },
    },
    {
        "name": "daymark_add",
        "description": "Suggest with proposed=true. " + _DATES + _DATE_FORMS,
        "inputSchema": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "proposed": {"type": "boolean"},
                "note": {"type": "string"},
                "source": {"type": "string", "description": "system:kind:id"},
                "kind": {
                    "type": "string",
                    "default": "task",
                },
                "op": _OP,
                **_FIELDS,
            },
            "required": ["title"],
        },
    },
    {
        "name": "daymark_edit",
        "description": "Dates as in add; empty clears. Guard with review_token from show.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "id": {"type": "string"},
                "expected_version": {"type": "integer", "minimum": 1},
                "review_token": {"type": "string"},
                "title": {"type": "string"},
                "op": _OP,
                **_FIELDS,
            },
            "required": ["id"],
        },
    },
    {
        "name": "daymark_act",
        "description": "note/done/release: text. wait: on/until. defer: until. block/unblock: on. "
        "take: omit id. move: stage/place. assess: state/text. "
        "remind: when + recipient ID, default local recording. use_profile: local id=name or set/drop.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": _ACTIONS},
                "set": {"type": "array", "items": {"type": "string"}},
                "drop": {"type": "array", "items": {"type": "string"}},
                "id": {"type": "string"},
                "expected_version": {"type": "integer", "minimum": 0},
                "review_token": {"type": "string"},
                "text": {"type": "string"},
                "on": {"type": "string"},
                "until": {"type": "string"},
                "when": {"type": "string", "description": "Local date and time."},
                "recipient": {"type": "string"},
                "context": {"type": "string"},
                "scope": {"type": "string"},
                "minutes": {"type": "integer"},
                "state": {
                    "type": "string",
                    "enum": ["met", "partly_met", "unmet", "unknown"],
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
        "description": "Remember a fact.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "text": {"type": "string"},
                "scope": {"type": "string"},
                "source": {"type": "string"},
                "op": _OP,
            },
            "required": ["text"],
        },
    },
]


for tool in TOOLS:
    tool["inputSchema"]["additionalProperties"] = False
_TOOLS = {tool["name"]: tool for tool in TOOLS}
_READ_NAMES = frozenset({"daymark_prime", "daymark_view"})
_READ_GUIDE = (
    "Read-only Daymark session. Use daymark_prime for current work and daymark_view for detail. "
    "Use ready for eligible tasks, show for blockers and notes, find for records, and activity for changes."
)
_TYPES = {"string": str, "boolean": bool, "integer": int, "array": list, "object": dict}

# The multiplexed tools have one schema each. Refuse arguments intended for a
# different verb so a filter or write instruction can never silently disappear.
_VIEW_ARGS = {
    "ready": {"limit", "move", "scope", "explain", "context"},
    "advise": {"context", "scope", "profile", "set", "drop"},
    "profiles": {"profile", "strategy"},
    "explore": {"id"},
    "agenda": {"days", "scope", "context"},
    "activity": {"after", "limit"},
    "show": {"id", "source", "brief", "note", "before_note", "since_version"},
    "find": {"text", "limit", "after", "kind", "status", "scope", "claimed"},
    "plan": {"id", "by"},
    "goal": {"id"},
}
_ACT_ARGS = {
    "accept": {"expected_version", "review_token"},
    "dismiss": {"expected_version", "review_token"},
    "forget": {"expected_version", "review_token"},
    "use_profile": {"id", "set", "drop", "expected_version"},
    "take": {"scope", "context", "minutes"},
    "note": {"text"},
    "done": {"text"},
    "release": {"text"},
    "wait": {"on", "until"},
    "defer": {"until"},
    "remind": {"when", "recipient"},
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
    if name not in _TOOLS:
        raise DomainError("not_found", "Unknown tool")
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
        required = ("id",) if verb in {"plan", "goal"} else ()
        allowed = _VIEW_ARGS.get(verb, set()) | {"view", "json"}
        if verb == "activity" and "after" in args:
            cursor_number(args["after"])
        if verb == "find" and "after" in args:
            _validate_value("after", args["after"], {"type": "string"})
    elif name == "daymark_act":
        verb = args["action"]
        required = {
            "note": ("text",), "wait": ("on",), "remind": ("when", "recipient"),
            "defer": ("until",), "block": ("on",), "unblock": ("on",), "assess": ("state",),
        }.get(verb, ())
        allowed = _ACT_ARGS.get(verb, set()) | {"action", "op"}
        if verb in {"accept", "dismiss"} and "expected_version" in args:
            _validate_value(
                "expected_version", args["expected_version"], {"type": "integer", "minimum": 1}
            )
        if verb == "use_profile":
            if not any(args.get(key) for key in ("id", "set", "drop")):
                raise DomainError("invalid", "use_profile requires a name in id, set or drop")
        elif verb != "take":
            if "id" not in args:
                raise DomainError("invalid", "missing argument id")
            allowed.add("id")
    else:
        return
    for key in required:
        if key not in args:
            raise DomainError("invalid", f"missing argument {key}")
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


def call(tasks, name, args, *, context=None, read_only=False):
    """Run one tool and return the same lean text as the command line."""
    result = _invoke(tasks, name, args, context=context, read_only=read_only)
    if isinstance(result, StructuredView):
        return result.text or json.dumps(result.data, separators=(",", ":"), ensure_ascii=False)
    return render.line(result, tasks.now(), timezone=tasks.timezone) if isinstance(result, dict) else result


def _invoke(tasks, name, args, *, context=None, read_only=False):
    """Run one validated tool. Keep entity receipts intact for versioned replies."""
    if read_only and name not in _READ_NAMES:
        raise DomainError(
            "forbidden", "This read-only session exposes only daymark_prime and daymark_view"
        )
    _validate_arguments(name, args)
    if name == "daymark_prime" and read_only:
        state = render.prime_text(tasks, context=context, brief=True, mcp=True)
        return (
            "Read-only Daymark session.\n" + state
            if args.get("brief")
            else _READ_GUIDE + "\n" + state
        )
    if name == "daymark_prime":
        return render.prime_text(tasks, context=context, brief=args.get("brief", False), mcp=True)
    if name == "daymark_view":
        return read_view(tasks, args)
    if name in {"daymark_add", "daymark_edit"}:
        args = dict(args)
        if name == "daymark_add":
            return tasks.add(args.pop("title"), **args)
        return tasks.edit(args.pop("id"), **args)
    if name == "daymark_remember":
        return tasks.remember(**args)
    if name == "daymark_act":
        if args["action"] == "use_profile":
            data = tasks.profile(
                args.get("id"),
                settings=args.get("set", ()),
                drops=args.get("drop", ()),
                op=args.get("op"),
                expected_version=args.get("expected_version"),
            )
            return StructuredView(
                data, text=render.profile_text(data, database=(context or {}).get("database"))
            )
        if args["action"] == "take":
            return (
                tasks.take(
                    scope=args.get("scope"),
                    context=args.get("context"),
                    minutes=args.get("minutes", 60),
                    op=args.get("op"),
                )
                or render.EMPTY_TAKE
            )
        action, id, op = args.get("action"), args["id"], args.get("op")
        if action == "note":
            item = tasks.note(id, args["text"], op=op)
        elif action in {"accept", "dismiss", "forget"}:
            item = getattr(tasks, action)(
                id,
                expected_version=args.get("expected_version"),
                review_token=args.get("review_token"),
                op=op,
            )
        elif action in {"done", "release"}:
            item = getattr(tasks, action)(id, note=args.get("text"), op=op)
        elif action == "wait":
            item = tasks.wait(id, args["on"], until=args.get("until"), op=op)
        elif action == "remind":
            item = tasks.remind(id, args["when"], recipient=args["recipient"], op=op)
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


def _tool_error(code, message, details, protocol, name, arguments, *, retry_op=None):
    recovery = {
        "conflict": "Read current state and compare it with the original request before retrying.",
        "forbidden": "Ask the authorized person to decide.",
        "not_found": "Check the workspace and look up the record again.",
        "remote": "Check the service connection.",
    }.get(code)
    if (
        code == "invalid" and name == "daymark_view" and arguments.get("view") == "advise"
        and (message == "Unknown argument" or message.startswith("advise does not take"))
    ):
        recovery = (
            "Use scope or context to narrow advice. For temporary ranking or time limits, "
            'use set, for example ["time-fit.minutes=15","decisions-first","shortlist.size=1"]. '
            "Read view=profiles for available adjustments."
        )
    if code == "conflict" and name == "daymark_act":
        if arguments.get("action") in {"accept", "dismiss"}:
            recovery = "Read the current proposal; ask the person to review any changed terms."
            if "expected_version" in arguments:
                recovery += " Use daymark_view show with since_version set to the reviewed version."
        elif arguments.get("action") == "use_profile":
            recovery = "Read daymark_view profiles and compare the current workspace settings."
    error = {"code": code, "message": message}
    if retry_op is not None:
        error["operation_id"] = retry_op
        recovery = (
            "The service may have committed the write. To retry that same request, "
            f"keep its arguments and use op={json.dumps(retry_op, ensure_ascii=False)}."
        )
    if details:
        error["details"] = details
    if recovery:
        error["recovery"] = recovery
    text = "\n".join([f"error: {code}: {message}", *details, *([recovery] if recovery else [])])
    result = {"content": [{"type": "text", "text": text}], "isError": True}
    if protocol >= "2025-06-18":
        result["structuredContent"] = {"error": error}
    return result


def _rpc_error(id, code, message):
    return {"jsonrpc": "2.0", "id": id, "error": {"code": code, "message": message}}


def handle(tasks, message, *, context=None, protocol=_PROTOCOLS[-1], read_only=False):
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
                "instructions": _READ_GUIDE
                if read_only
                else "Call daymark_prime first. For each write, choose a fresh op "
                "and reuse it if that same write needs a retry. Never invent dates or completions.",
            }
        )
    if method == "ping":
        return reply({})
    if method == "tools/list":
        return reply(
            {"tools": [t for t in TOOLS if t["name"] in _READ_NAMES] if read_only else TOOLS}
        )
    if method != "tools/call":
        return _rpc_error(id, -32601, "Method not found")
    name = params.get("name")
    arguments = params.get("arguments", {})
    if not isinstance(name, str) or not isinstance(arguments, dict):
        return _rpc_error(id, -32602, "tools/call requires a name and an arguments object")
    if name not in _TOOLS:
        return _rpc_error(id, -32602, "Unknown tool. Use tools/list for the available names.")
    if not read_only and name not in _READ_NAMES and "op" not in arguments and isinstance(tasks.engine, Client):
        # Keep the receipt key here in case the HTTP service commits the write
        # but its reply fails before Tasks can return a result.
        arguments = {**arguments, "op": uuid.uuid4().hex}
    try:
        value = _invoke(tasks, name, arguments, context=context, read_only=read_only)
        result = {"isError": False}
        if isinstance(value, StructuredView):
            if protocol >= "2025-06-18":
                result["structuredContent"] = value.data
                text = value.text or "Read structuredContent or omit json for text."
            else:
                text = json.dumps(value.data, separators=(",", ":"), ensure_ascii=False)
        elif isinstance(value, dict):
            text = render.line(value, tasks.now(), timezone=tasks.timezone)
            if protocol >= "2025-06-18":
                result["structuredContent"] = {
                    key: value[key] for key in ("id", "kind", "title", "status", "version")
                }
                if value["kind"] == "reminder":
                    result["structuredContent"].update(render.reminder_fields(value))
                if value.get("archived"):
                    result["structuredContent"]["archived"] = True
                if name == "daymark_act" and arguments["action"] in {"take", "claim", "release"}:
                    result["structuredContent"].update(
                        claimed_by=value.get("claimed_by"), claim_until=value.get("claim_until")
                    )
        else:
            text = value
            if name == "daymark_act" and arguments["action"] == "take" and protocol >= "2025-06-18":
                result["structuredContent"] = {"task": None}
        result["content"] = [{"type": "text", "text": text}]
    except DomainError as exc:
        error = _tool_error(exc.code, str(exc), exc.details, protocol, name, arguments)
    except ClientError as exc:
        ambiguous = exc.status_code is None or exc.status_code >= 500
        error = _tool_error(
            exc.code, str(exc), [], protocol, name, arguments,
            retry_op=arguments.get("op") if ambiguous and name not in _READ_NAMES else None,
        )
    except Exception:  # noqa: BLE001 - never leak stored content through errors
        return _rpc_error(id, -32603, "The request failed")
    else:
        return reply(result)
    return reply(error)


def serve(tasks, stdin=None, stdout=None, *, context=None, read_only=False):
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
            response = handle(
                tasks, message, context=context, protocol=protocol, read_only=read_only
            )
            if response and "protocolVersion" in response.get("result", {}):
                protocol = response["result"]["protocolVersion"]
        if response is not None:
            stdout.write(json.dumps(response) + "\n")
            stdout.flush()
