"""toucan-source-daymark: a Daymark project as a toucan plan source.

toucan draws a plan (a board, a gantt, a timeline, a dependency graph, a table)
from a system of record and writes back what a person changes on the canvas.
This adapter is that seam for Daymark. It speaks the ``toucan.plan/1`` protocol
on standard input and output and needs only the core install:

    toucan-source-daymark list                       projects in every workspace
    toucan-source-daymark read WORKSPACE/PROJECT     the plan
    toucan-source-daymark changes REF --after CUR    {"cursor", "changed"}
    toucan-source-daymark write REF < changes.json   per-change results

It opens ``--db PATH``, else ``$DAYMARK_DB``, else the per-user database. A
failure exits non-zero with one JSON object on standard error:
``{"error": CODE, "message": TEXT}``. See docs/toucan.md for the field mapping.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from datetime import date
from pathlib import Path

from . import plans
from .models import DomainError, Temporal

CODES = {"not_found": 3, "conflict": 4, "invalid": 5, "forbidden": 7, "unavailable": 1}
_ALPHABET = "23456789abcdefghjkmnpqrstuvwxyz"
PRIORITIES = [
    {"id": "1", "label": "P1", "color": "red"},
    {"id": "2", "label": "P2", "color": "orange"},
    {"id": "3", "label": "P3", "color": "yellow"},
    {"id": "4", "label": "P4", "color": "grey"},
]
STATUSES = [
    {"id": "open", "label": "Open"},
    {"id": "waiting", "label": "Waiting"},
    {"id": "completed", "label": "Completed", "done": True},
    {"id": "cancelled", "label": "Cancelled"},
]
WRITABLE = ("title", "detail", "stage", "assignee", "start", "end", "deadline", "priority",
            "labels", "dependsOn", "parent", "milestone", "done", "estimate")  # fmt: skip


class Failure(Exception):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


def database(explicit=None):
    """--db, then $DAYMARK_DB, then the per-user database (as `daymark init` makes it)."""
    if explicit:
        return explicit
    if os.environ.get("DAYMARK_DB"):
        return os.environ["DAYMARK_DB"]
    home = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    return str(home / "daymark" / "daymark.sqlite3")


def _open(path):
    from .store import Store

    if path != ":memory:" and not Path(path).is_file():
        raise Failure("unavailable", "No Daymark database; run `daymark init`")
    return Store(path)


def _ref(ref):
    workspace, _, project = (ref or "").partition("/")
    if not workspace or not project:
        raise Failure("invalid", "A reference is WORKSPACE/PROJECT_ID")
    return workspace, project


def _project(store, workspace, project_id):
    project = store._project(workspace, project_id)
    if project is None or project["archived"]:
        raise Failure("not_found", "No such project")
    return project


# -- reading


def _item(task, board, members):
    """One task as a plan item. ``members`` are the IDs of the plan's items."""
    stage = plans.resolve_stage(task, board)
    dates = plans.plan_dates(task)
    return {
        "id": task["id"],
        "version": task["version"],
        "title": task["title"],
        "detail": task["description"] or None,
        "stage": stage["id"] if stage else None,
        "assignee": task["assignee_id"],
        "start": dates["start"],
        "end": dates["end"],
        "deadline": dates["deadline"],
        "priority": str(task["priority"]) if task["priority"] else None,
        "labels": list(task["label_ids"]),
        "dependsOn": [ref for ref in task["depends_on"] if ref in members],
        "parent": task["parent_id"] if task["parent_id"] in members else None,
        "milestone": task["milestone"],
        "done": task["status"] == "completed",
        "estimate": task["estimate_minutes"],
        "url": None,
        "status": task["status"],
    }


def _fields(view, writable):
    def stage_option(stage):
        option = {"id": stage["id"], "label": stage["label"]}
        if stage.get("color"):
            option["color"] = stage["color"]
        if stage["status"] == "completed":
            option["done"] = True
        return option

    def field(id, label, type, **extra):
        return {"id": id, "label": label, "type": type, "writable": writable, **extra}

    people = [{"id": p["id"], "label": p["title"] or p["id"]} for p in view["people"]]
    labels = [{"id": x["id"], "label": x["title"] or x["id"]} for x in view["labels"]]
    return [
        field("title", "Title", "text"),
        field("detail", "Detail", "text"),
        field("stage", "Stage", "option", options=[stage_option(s) for s in view["stages"]]),
        field("assignee", "Owner", "option", options=people),
        field("start", "Start", "date"),
        field("end", "End", "date"),
        field("deadline", "Deadline", "date"),
        field("priority", "Priority", "option", options=PRIORITIES),
        field("labels", "Labels", "options", options=labels),
        field("dependsOn", "Depends on", "options", items=True),
        field("parent", "Parent", "option", items=True),
        field("milestone", "Milestone", "bool"),
        field("done", "Done", "bool"),
        field("estimate", "Estimate (minutes)", "number"),
        {"id": "status", "label": "Status", "type": "option", "writable": False,
         "options": STATUSES},
    ]  # fmt: skip


def read(store, ref):
    workspace, project_id = _ref(ref)
    _project(store, workspace, project_id)
    view = store.plan(workspace, project_id)
    project = view["project"]
    readonly = project["managed_by"] is not None
    members = {card["id"] for card in view["tasks"]}
    source = {
        "ref": ref,
        "title": project["title"] or project["id"],
        "description": project["description"],
        "readonly": readonly,
    }
    if readonly:
        source["managedBy"] = project["managed_by"]
    return {
        "plan": 1,
        "source": source,
        "cursor": str(view["cursor"]),
        "fields": _fields(view, not readonly),
        "items": [_item(card, view["stages"], members) for card in view["tasks"]],
    }


def listing(path):
    if path != ":memory:" and not Path(path).is_file():
        return {"sources": []}
    store = _open(path)
    try:
        sources = []
        for entry in store.workspaces():
            workspace = entry["workspace"]
            for project in store.list(workspace, kind="project"):
                description = (project["description"].splitlines() or [""])[0]
                sources.append(
                    {
                        "ref": f"{workspace}/{project['id']}",
                        "title": project["title"] or project["id"],
                        "description": description or f"Daymark project in workspace {workspace}",
                        "readonly": project["managed_by"] is not None,
                    }
                )
        return {"sources": sources}
    finally:
        store.close()


def changes(store, ref, after):
    workspace, project_id = _ref(ref)
    if not isinstance(after, str) or not re.fullmatch(r"[0-9]{1,18}", after):
        raise Failure("invalid", "The cursor is the string a read or changes returned")
    _project(store, workspace, project_id)
    result = store.changes(workspace, project_id, int(after))
    return {"cursor": str(result["cursor"]), "changed": result["changed"]}


# -- writing


def _day(value, field):
    if value is None:
        return None
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise Failure("invalid", f"{field} is a YYYY-MM-DD date or null")
    try:
        date.fromisoformat(value)
    except ValueError:
        raise Failure("invalid", f"{field} is a YYYY-MM-DD date or null") from None
    return value


def _zone(task, field):
    """The zone a written date is read in: the field's own, another date's, else local."""
    from .tasks import local_timezone

    for name in (field, "planned", "latest_target", "deadline", "earliest_start"):
        if task and task.get(name):
            return task[name]["timezone"]
    return local_timezone()


def _translate(changes, task, members):
    """A ``set`` object as an entity payload. ``task`` is None for a create."""
    if not isinstance(changes, dict):
        raise Failure("invalid", "set is an object of field values")
    out = {}
    for key, value in changes.items():
        if key not in WRITABLE:
            raise Failure("invalid", f"{key} is not a writable field")
        if key == "title":
            if not isinstance(value, str):
                raise Failure("invalid", "title is text")
            out["title"] = value
        elif key == "detail":
            if value is not None and not isinstance(value, str):
                raise Failure("invalid", "detail is text or null")
            out["description"] = value or ""
        elif key in {"stage", "assignee", "parent"}:
            if value is not None and not isinstance(value, str):
                raise Failure("invalid", f"{key} is an option id or null")
            out[{"stage": "stage", "assignee": "assignee_id", "parent": "parent_id"}[key]] = value
        elif key in {"start", "end", "deadline"}:
            target = {"start": "planned", "end": "latest_target", "deadline": "deadline"}[key]
            day = _day(value, key)
            out[target] = (
                None
                if day is None
                else Temporal(date=day, timezone=_zone(task, target)).model_dump(mode="json")
            )
        elif key == "priority":
            if value is None:
                out["priority"] = 0
            elif str(value) in {"1", "2", "3", "4"} and not isinstance(value, bool):
                out["priority"] = int(value)
            else:
                raise Failure("invalid", "priority is 1, 2, 3, 4 or null")
        elif key in {"labels", "dependsOn"}:
            value = [] if value is None else value
            if not isinstance(value, list) or not all(isinstance(x, str) for x in value):
                raise Failure("invalid", f"{key} is a list of ids")
            if key == "labels":
                out["label_ids"] = list(dict.fromkeys(value))
            else:
                # The plan shows only dependencies inside the project; keep the others.
                outside = [r for r in (task or {}).get("depends_on", []) if r not in members]
                out["depends_on"] = list(dict.fromkeys([*outside, *value]))
        elif key == "milestone":
            if not isinstance(value, bool):
                raise Failure("invalid", "milestone is true or false")
            out["milestone"] = value
        elif key == "done":
            if not isinstance(value, bool):
                raise Failure("invalid", "done is true or false")
            if value:
                out["status"] = "completed"
            elif task is None or task["status"] in {"completed", "cancelled"}:
                out["status"] = "open"
        elif key == "estimate":
            if value is not None and (type(value) is not int or value < 1):
                raise Failure("invalid", "estimate is a whole number of minutes or null")
            out["estimate_minutes"] = value
    merged = plans.plan_dates({**(task or _BLANK), **out})
    if merged["start"] and merged["end"] and merged["start"] > merged["end"]:
        raise Failure("invalid", "start is after end")
    return out


_BLANK = {"planned": None, "earliest_start": None, "latest_target": None, "deadline": None}


def _short_id(workspace, project_id, mutation):
    digest = int.from_bytes(
        hashlib.sha256(f"{workspace}\x00{project_id}\x00{mutation}".encode()).digest(), "big"
    )
    chars = []
    for _ in range(8):
        digest, index = divmod(digest, len(_ALPHABET))
        chars.append(_ALPHABET[index])
    return "dm-" + "".join(chars)


def _place(change):
    place = change.get("place")
    if place is None:
        return None
    if not isinstance(place, dict) or len(place) != 1 or set(place) - {"after", "before"}:
        raise Failure("invalid", 'place is {"after": ID or null}')
    value = next(iter(place.values()))
    if value is not None and not isinstance(value, str):
        raise Failure("invalid", "place names an item id or null")
    return place


def _apply(store, workspace, project_id, change, members, actor):
    """Run one change. Returns (item id or None, error code or None, message)."""
    if not isinstance(change, dict):
        raise Failure("invalid", "Each change is an object")
    mutation = change.get("mutation")
    if not isinstance(mutation, str) or not 1 <= len(mutation) <= 180:
        raise Failure("invalid", "mutation is an idempotency key of 1 to 180 characters")
    operation = "toucan:" + mutation
    replay = store.receipt(workspace, operation)
    if replay is not None:
        return replay["id"]
    kind, place = change.get("op"), _place(change)
    if kind == "create":
        payload = {
            "id": _short_id(workspace, project_id, mutation),
            "kind": "task",
            "project_id": project_id,
            "title": "",
            **_translate(change.get("set") or {}, None, members),
        }
        operations = [
            {"operation_id": operation, "action": "create", "payload": payload,
             "expected_version": 0},
        ]  # fmt: skip
        version, id = 1, payload["id"]
    elif kind == "update":
        id, version = change.get("id"), change.get("version")
        if not isinstance(id, str) or type(version) is not int:
            raise Failure("invalid", "An update needs the item's id and version")
        try:
            task = store.get(workspace, id)
        except DomainError:
            task = None
        if task is None or task["kind"] != "task" or task["project_id"] != project_id:
            raise Failure("not_found", "No such item in this plan")
        payload = _translate(change.get("set") or {}, task, members)
        operations = []
        if payload:
            operations.append(
                {"operation_id": operation, "action": "update",
                 "payload": {"id": id, **payload}, "expected_version": version}
            )  # fmt: skip
            version += 1
        if not operations and place is None:
            if task["version"] != version:
                raise Failure("conflict", "Changed upstream")
            return id
    else:
        raise Failure("invalid", "op is update or create")
    if place is not None:
        operations.append(
            {"operation_id": operation + (":place" if operations else ""), "action": "place",
             "payload": {"id": id, **place}, "expected_version": version}
        )  # fmt: skip
    result = store.batch(workspace, operations, atomic=True, actor=actor)
    if not result["committed"]:
        error = next(r["error"] for r in result["results"] if r["error"]["code"] != "aborted")
        code = error["code"] if error["code"] in CODES else "invalid"
        failure = Failure(code, error["message"] if code != "conflict" else "Changed upstream")
        failure.item_id = id
        raise failure
    return id


def write(store, ref, document, actor):
    workspace, project_id = _ref(ref)
    project = _project(store, workspace, project_id)
    if project["managed_by"] is not None:
        raise Failure("forbidden", f"This project is managed by {project['managed_by']}")
    if not isinstance(document, dict) or not isinstance(document.get("changes"), list):
        raise Failure("invalid", 'The input is {"changes": [...]}')
    if len(document["changes"]) > 500:
        raise Failure("invalid", "At most 500 changes per write")
    members = {card["id"] for card in store.plan(workspace, project_id)["tasks"]}
    outcomes = []
    for change in document["changes"]:
        mutation = change.get("mutation") if isinstance(change, dict) else None
        try:
            id = _apply(store, workspace, project_id, change, members, actor)
            outcomes.append((mutation, id, None))
        except Failure as failure:
            fallback = change.get("id") if isinstance(change, dict) else None
            outcomes.append((mutation, getattr(failure, "item_id", fallback), failure))
        except DomainError as exc:
            code = exc.code if exc.code in CODES else "invalid"
            fallback = change.get("id") if isinstance(change, dict) else None
            outcomes.append((mutation, fallback, Failure(code, str(exc))))
    view = store.plan(workspace, project_id)
    members = {card["id"] for card in view["tasks"]}
    results = []
    for mutation, id, failure in outcomes:
        result = {"mutation": mutation, "ok": failure is None}
        if failure is not None:
            result.update(error=failure.code, message=str(failure))
        task = None
        if isinstance(id, str):
            try:
                task = store.get(workspace, id)
            except DomainError:
                task = None
        if task is not None and task["kind"] == "task" and task["project_id"] == project_id:
            result["item"] = _item(task, view["stages"], members)
        results.append(result)
    return {"cursor": str(view["cursor"]), "results": results}


# -- entry point


def _parse(argv):
    args, options = [], {}
    items = iter(argv)
    for arg in items:
        if arg in {"--db", "--after"}:
            options[arg[2:]] = next(items, None)
        elif arg.startswith(("--db=", "--after=")):
            name, _, value = arg[2:].partition("=")
            options[name] = value
        elif arg in {"-h", "--help"}:
            options["help"] = True
        else:
            args.append(arg)
    return args, options


def run(argv, stdin):
    args, options = _parse(argv)
    if options.get("help") or not args:
        return __doc__
    command, rest = args[0], args[1:]
    path = database(options.get("db"))
    if command == "list" and not rest:
        return listing(path)
    if command not in {"read", "changes", "write"} or len(rest) != 1:
        raise Failure("invalid", "Usage: list | read REF | changes REF --after CUR | write REF")
    store = _open(path)
    try:
        if command == "read":
            return read(store, rest[0])
        if command == "changes":
            return changes(store, rest[0], options.get("after"))
        try:
            document = json.loads(stdin.read() or "null")
        except ValueError:
            raise Failure("invalid", "The input is not JSON") from None
        actor = os.environ.get("DAYMARK_ACTOR") or "toucan"
        return write(store, rest[0], document, actor)
    finally:
        store.close()


def main(argv=None, stdin=None, stdout=None, stderr=None):
    stdin, stdout, stderr = stdin or sys.stdin, stdout or sys.stdout, stderr or sys.stderr
    try:
        result = run(sys.argv[1:] if argv is None else argv, stdin)
    except Failure as failure:
        stderr.write(json.dumps({"error": failure.code, "message": str(failure)}) + "\n")
        return CODES[failure.code]
    except DomainError as exc:
        code = exc.code if exc.code in CODES else "invalid"
        stderr.write(json.dumps({"error": code, "message": str(exc)}) + "\n")
        return CODES[code]
    except Exception:  # noqa: BLE001 - stored content must not reach the caller's logs
        stderr.write(json.dumps({"error": "unavailable", "message": "Daymark failed"}) + "\n")
        return CODES["unavailable"]
    if isinstance(result, str):
        stdout.write(result)
    else:
        stdout.write(json.dumps(result, ensure_ascii=False) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
