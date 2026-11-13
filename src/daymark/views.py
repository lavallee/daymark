"""Derived, read-only views: what is ready, what the days ahead hold, and why.

Every rule here is deterministic and stated once, so an item cannot be ready in
one view and missing from another. Nothing in this module writes.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from . import plans
from .models import DomainError, Temporal, normalize_now

LIVE = ("proposed", "open", "waiting")
DONE = ("completed", "cancelled")


def day_offset(temporal, now: datetime) -> int:
    """Whole local days from today to a temporal value, in that value's own zone."""
    value = Temporal.model_validate(temporal)
    return (value.local().date() - now.astimezone(ZoneInfo(value.timezone)).date()).days


def _is_past(temporal, now):
    value = Temporal.model_validate(temporal)
    return value.is_before(now)


def _started(item, now):
    """False while an earliest start is still ahead."""
    start = item["earliest_start"]
    if start is None:
        return True
    value = Temporal.model_validate(start)
    if value.date:
        return day_offset(start, now) <= 0
    return value.at.astimezone(UTC) <= now


def claim_holder(item, now) -> str | None:
    """The live claim owner, or None when unclaimed or the claim has lapsed."""
    if item["claimed_by"] and normalize_now(item["claim_until"]) > now:
        return item["claimed_by"]
    return None


def _usable(store):
    """False when there is nothing stored yet; refuse a schema this code cannot read."""
    if not store._has_schema():
        return False
    if not store._current():
        raise DomainError(
            "conflict", "Stored schema is not current; open a Store and commit its upgrade"
        )
    return True


def _statuses(store, workspace, ids):
    """Status per ID. An archived entity reads as cancelled: shelved work blocks nothing."""
    found, ids = {}, sorted(set(ids))
    for start in range(0, len(ids), 500):
        chunk = ids[start : start + 500]
        rows = store.db.execute(
            "SELECT id,CASE WHEN archived THEN 'cancelled' ELSE status END FROM daymark_entities WHERE workspace=? AND id IN ({})".format(
                ",".join("?" * len(chunk))
            ),
            (workspace, *chunk),
        )
        found.update(dict(rows))
    return found


def _parents_with_live_children(store, workspace):
    return {
        row[0]
        for row in store.db.execute(
            "SELECT DISTINCT r.target_id FROM daymark_refs r JOIN daymark_entities e "
            "ON e.workspace=r.workspace AND e.id=r.source_id WHERE r.workspace=? "
            "AND r.field='parent_id' AND e.kind='task' AND e.archived=0 "
            "AND e.status IN ('open','waiting')",
            (workspace,),
        )
    }


def urgency(item, now) -> tuple:
    """Sort key and a short human reason. Hard deadlines outrank intentions."""
    deadline = day_offset(item["deadline"], now) if item["deadline"] else None
    planned = day_offset(item["planned"], now) if item["planned"] else None
    target = day_offset(item["latest_target"], now) if item["latest_target"] else None
    if item["deadline"] and _is_past(item["deadline"], now):
        late = max(1, -deadline)
        bucket, when, why = 0, deadline, f"overdue {late}d" if deadline < 0 else "overdue"
    elif deadline == 0:
        bucket, when, why = 1, 0, "due today"
    elif planned is not None and planned <= 0:
        bucket, when = 2, planned
        why = "planned today" if planned == 0 else f"planned {-planned}d ago"
    elif deadline is not None and deadline <= 7:
        bucket, when, why = 3, deadline, f"due in {deadline}d"
    elif target is not None and target <= 7:
        bucket, when = 4, target
        why = f"target in {target}d" if target >= 0 else f"target passed {-target}d ago"
    else:
        soonest = min((x for x in (deadline, planned, target) if x is not None), default=None)
        bucket, when = 5, soonest if soonest is not None else 10**6
        why = (
            f"due in {deadline}d"
            if deadline is not None
            else f"planned in {planned}d"
            if planned is not None
            else "no date"
        )
    # The person's own ordering outranks every computed one.
    focus = item["focus"]
    key = (0 if focus else 1, focus or 0, bucket, when, item["priority"] or 5)
    return (*key, item["created_at"], item["id"]), why


def why(item, now=None) -> str:
    return urgency(item, normalize_now(now))[1]


def blockers(store, workspace, item) -> list[str]:
    """Dependencies that are not yet completed or cancelled."""
    statuses = _statuses(store, workspace, item["depends_on"])
    return [ref for ref in item["depends_on"] if statuses.get(ref) not in DONE]


def in_scope(item, scope) -> bool:
    """``None`` matches everything, ``""`` only unscoped items, a name only that scope."""
    return scope is None or (item["scope"] or "") == scope


def ready(store, workspace, *, now=None, limit=50, owner=None, next_move=None, scope=None):
    """Open tasks that can be acted on now, most pressing first.

    A task is ready when it is open and unarchived, its earliest start has
    arrived (for a repeating task, also its planned day), every dependency is completed, cancelled or archived, it has no open
    or waiting child task (work the leaves, not the container), and nobody else holds a live
    claim. ``owner`` keeps that owner's own claims in the result. ``next_move``
    narrows to "person", "answer" or "agent"; unset moves match none of them.
    ``scope`` narrows to one scope, or with ``""`` to items that have none.
    Tasks the person has put in focus come first, in their order.
    """
    if type(limit) is not int or not 1 <= limit <= 1000:
        raise DomainError("invalid", "Limit must be between 1 and 1000")
    if next_move not in (None, "person", "answer", "agent"):
        raise DomainError("invalid", "next_move must be person, answer or agent")
    now = normalize_now(now)
    with store._lock:
        if not _usable(store):
            return []
        tasks = store._rows(workspace, kind="task", status=["open"])
        statuses = _statuses(store, workspace, [ref for t in tasks for ref in t["depends_on"]])
        containers = _parents_with_live_children(store, workspace)
    result = []
    for item in tasks:
        holder = claim_holder(item, now)
        if (
            item["id"] in containers
            or not _started(item, now)
            # The next round of a repeating task waits for its planned day.
            or (item["recurrence"] and item["planned"] and day_offset(item["planned"], now) > 0)
            or any(statuses.get(ref) not in DONE for ref in item["depends_on"])
            or (holder is not None and holder != owner)
            or (next_move is not None and item["next_move"] != next_move)
            or not in_scope(item, scope)
        ):
            continue
        result.append(item)
    result.sort(key=lambda item: urgency(item, now)[0])
    return result[:limit]


def agenda(store, workspace, *, now=None, days=7, scope=None):
    """The shape of the days ahead: what is late, due, planned, waiting or proposed.

    Sections do not overlap. A task appears once, in the first section it fits:
    overdue, today, upcoming (keyed by local date), waiting, proposed.
    """
    if type(days) is not int or not 0 <= days <= 366:
        raise DomainError("invalid", "Days must be between 0 and 366")
    now = normalize_now(now)
    with store._lock:
        tasks = store._rows(workspace, kind="task", status=list(LIVE)) if _usable(store) else []
    tasks = [item for item in tasks if in_scope(item, scope)]
    out = {"overdue": [], "today": [], "upcoming": {}, "waiting": [], "proposed": []}
    undated = 0
    for item in sorted(tasks, key=lambda item: urgency(item, now)[0]):
        if item["status"] == "proposed":
            out["proposed"].append(item)
            continue
        deadline = day_offset(item["deadline"], now) if item["deadline"] else None
        planned = day_offset(item["planned"], now) if item["planned"] else None
        if item["deadline"] and _is_past(item["deadline"], now):
            out["overdue"].append(item)
        elif item["status"] == "waiting":
            out["waiting"].append(item)
        elif deadline == 0 or (planned is not None and planned <= 0):
            out["today"].append(item)
        else:
            ahead = [x for x in (deadline, planned) if x is not None and 0 < x <= days]
            if ahead:
                source = item["deadline"] if deadline == min(ahead) else item["planned"]
                day = Temporal.model_validate(source).local().date().isoformat()
                out["upcoming"].setdefault(day, []).append(item)
            else:
                undated += 1
    out["upcoming"] = dict(sorted(out["upcoming"].items()))
    out["later_or_undated"] = undated
    return out


def context(store, workspace, id, *, history=10):
    """One entity with what surrounds it: blockers, dependents, children, reminders, history."""
    item = store.get(workspace, id)
    with store._lock:
        _usable(store)

        def linked(field):
            rows = store.db.execute(
                "SELECT e.data FROM daymark_refs r JOIN daymark_entities e ON "
                "e.workspace=r.workspace AND e.id=r.source_id WHERE r.workspace=? AND "
                "r.target_id=? AND r.field=? ORDER BY e.id",
                (workspace, id, field),
            ).fetchall()
            return [json.loads(row[0]) for row in rows]

        statuses = _statuses(store, workspace, item["depends_on"])
        events = store.db.execute(
            "SELECT e.action,e.created_at,e.operation_id,e.actor FROM daymark_events e WHERE "
            "e.workspace=? AND e.entity_id=? ORDER BY e.seq DESC LIMIT ?",
            (workspace, id, history),
        ).fetchall()
        return {
            "entity": item,
            "blocked_by": [ref for ref in item["depends_on"] if statuses.get(ref) not in DONE],
            "dependents": [x["id"] for x in linked("depends_on") if x["status"] in LIVE],
            "children": [
                {"id": x["id"], "title": x["title"], "status": x["status"]}
                for x in linked("parent_id")
            ],
            "reminders": [
                {"id": x["id"], "scheduled_at": x["scheduled_at"], "state": x["reminder_state"]}
                for x in linked("task_id")
            ],
            "history": [
                {"action": row[0], "at": row[1], "operation_id": row[2], "actor": row[3]}
                for row in events
            ],
        }


def summary(store, workspace, *, now=None, scope=None):
    """Counts for a one-glance orientation."""
    now = normalize_now(now)
    view = agenda(store, workspace, now=now, days=7, scope=scope)
    return {
        "ready": len(ready(store, workspace, now=now, limit=1000, scope=scope)),
        "overdue": len(view["overdue"]),
        "today": len(view["today"]),
        "next_7_days": sum(len(x) for x in view["upcoming"].values()),
        "waiting": len(view["waiting"]),
        "proposed": len(view["proposed"]),
    }


# -- projects and goals: one pass over preloaded records, then pure roll-ups

LIVE_WORK = ("open", "waiting")


def _status(item) -> str | None:
    """A dependency's effective status; archived reads as cancelled."""
    if item is None:
        return None
    return "cancelled" if item["archived"] else item["status"]


def _fetch(store, workspace, ids) -> dict:
    found, ids = {}, sorted(set(ids))
    for start in range(0, len(ids), 500):
        chunk = ids[start : start + 500]
        rows = store.db.execute(
            "SELECT id,data FROM daymark_entities WHERE workspace=? AND id IN ({})".format(
                ",".join("?" * len(chunk))
            ),
            (workspace, *chunk),
        )
        found.update((row[0], json.loads(row[1])) for row in rows)
    return found


def _summary(items, decls) -> dict:
    """Counts, progress, effort, span and number-field sums for a set of items."""
    counted = [x for x in items if x["status"] != "cancelled"]
    done = sum(1 for x in counted if x["status"] == "completed")
    dates = [plans.plan_dates(x) for x in items]
    starts = [d["start"] for d in dates if d["start"]]
    ends = [d["end"] for d in dates if d["end"]]
    by_status = {}
    for item in items:
        by_status[item["status"]] = by_status.get(item["status"], 0) + 1
    sums = {}
    for field in decls.values():
        if field["type"] == "number":
            numbers = [
                x["values"][field["id"]]
                for x in counted
                if isinstance(x["values"].get(field["id"]), (int, float))
                and not isinstance(x["values"][field["id"]], bool)
            ]
            if numbers:
                sums[field["id"]] = sum(numbers)
    estimates = [x["estimate_minutes"] for x in counted if x["estimate_minutes"]]
    return {
        "total": len(items),
        "by_status": by_status,
        "done": done,
        "percent_done": (100 * done) // len(counted) if counted else 0,
        "estimate_minutes": sum(estimates) if estimates else None,
        "start": min(starts, default=None),
        "end": max(ends, default=None),
        "sums": sums,
    }


def _value_warning(field, value, people):
    if field["type"] in {"option", "options"}:
        allowed = {o["id"] for o in field["options"]}
        chosen = value if isinstance(value, list) else [value]
        if not all(isinstance(x, str) and x in allowed for x in chosen):
            return "option_removed"
        if (field["type"] == "options") != isinstance(value, list):
            return "value_mismatch"
        return None
    expected = {
        "text": lambda v: isinstance(v, str),
        "url": lambda v: isinstance(v, str),
        "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
        "bool": lambda v: isinstance(v, bool),
        "date": plans.is_civil_date,
        "person": lambda v: isinstance(v, str),
    }[field["type"]]
    if not expected(value):
        return "value_mismatch"
    if field["type"] == "person" and value not in people:
        return "person_missing"
    return None


def _warnings(task, types, decls, members, in_project, by_id, people):
    """Guard rails reported as facts, never enforced: structure that no longer fits."""
    out = []

    def warn(code, detail):
        out.append({"item": task["id"], "code": code, "detail": detail})

    if task["type"] and task["type"] not in types:
        warn("type_undeclared", task["type"])
    for key, value in sorted(task["values"].items()):
        if key not in decls:
            warn("value_undeclared", key)
        else:
            problem = _value_warning(decls[key], value, people)
            if problem:
                warn(problem, key)
    if task["milestone_id"]:
        target = members.get(task["milestone_id"])
        if target is None or not target["milestone"]:
            warn("milestone_invalid", task["milestone_id"])
    parent = by_id.get(task["parent_id"]) if task["parent_id"] else None
    if parent is not None and parent["kind"] == "task":
        if parent["id"] not in in_project:
            warn("parent_elsewhere", parent["id"])
        elif any(t["container"] for t in types.values()):
            kind = types.get(parent["type"]) if parent["type"] else None
            if kind is not None and not kind["container"]:
                warn("parent_not_container", parent["id"])
    return out


def _build(project, tasks, by_id, people, labels, goals, now):
    """A project as a plan from records already read. Pure: no database access."""
    board = plans.stages(project)
    types = {t["id"]: t for t in project["types"]}
    decls = {f["id"]: f for f in project["fields"]}
    # Archived tasks in the project are not on the plan, but they still answer for
    # what refers to them: an archived dependency releases its dependents (A02).
    in_project = {t["id"]: t for t in tasks}
    tasks = sorted((t for t in tasks if not t["archived"]), key=plans.order_key)
    members = {t["id"]: t for t in tasks}
    lookup = {**by_id, **in_project}
    person_ids = {p["id"] for p in people}
    cards, proposed, off_board = [], [], 0
    for task in tasks:
        if task["status"] == "proposed":
            proposed.append(task)
            continue
        stage = plans.resolve_stage(task, board)
        if stage is None:
            off_board += 1  # Cancelled, on a board with no cancelled stage.
            continue
        live = task["status"] in LIVE_WORK
        cards.append(
            {
                **task,
                "plan": {
                    "stage": stage["id"],
                    **plans.plan_dates(task),
                    "done": task["status"] == "completed",
                    "overdue": bool(live and task["deadline"] and _is_past(task["deadline"], now)),
                    "blocked_by": [
                        r for r in task["depends_on"] if _status(lookup.get(r)) not in DONE
                    ],
                },
            }
        )
    edges = [
        {"from": t["id"], "to": ref} for t in tasks for ref in t["depends_on"] if ref in members
    ]
    external = [
        {
            "id": ref,
            "title": (lookup.get(ref) or {}).get("title"),
            "project_id": (lookup.get(ref) or {}).get("project_id"),
            "status": _status(lookup.get(ref)),
            "dependents": [t["id"] for t in tasks if ref in t["depends_on"]],
        }
        for ref in sorted({r for t in tasks for r in t["depends_on"] if r not in in_project})
    ]
    conflicts = []
    for card in (c for c in cards if c["status"] in LIVE_WORK):  # Finished work has none.
        for ref in card["depends_on"]:
            blocker = lookup.get(ref)
            if blocker is None or _status(blocker) == "cancelled":
                continue
            ends = plans.plan_dates(blocker)["end"]
            if card["plan"]["start"] and ends and card["plan"]["start"] < ends:
                conflicts.append(
                    {
                        "item": card["id"],
                        "blocker": ref,
                        "start": card["plan"]["start"],
                        "blocker_end": ends,
                    }
                )
    warnings = [
        w
        for t in tasks
        for w in _warnings(t, types, decls, members, in_project, lookup, person_ids)
    ]
    milestones = []
    # Every unarchived milestone is listed with its status, including a proposed or
    # cancelled one that is not on the board, so its members are never orphaned.
    for marker in (t for t in tasks if t["milestone"]):
        belongs = [c for c in cards if c["milestone_id"] == marker["id"]]
        dated = plans.plan_dates(marker)
        when = dated["end"] or dated["start"]
        late = [
            c["id"]
            for c in belongs
            if c["status"] in LIVE_WORK and when and c["plan"]["end"] and c["plan"]["end"] > when
        ]
        counted = [c for c in belongs if c["status"] != "cancelled"]
        milestones.append(
            {
                "id": marker["id"],
                "title": marker["title"],
                "status": marker["status"],
                "date": when,
                "reached": marker["status"] == "completed",
                "items_done": bool(counted) and all(c["status"] == "completed" for c in counted),
                "late": late,
                **_summary(belongs, decls),
            }
        )
    children = {}
    for card in cards:
        if card["parent_id"] in members:
            children.setdefault(card["parent_id"], []).append(card)
    # Every parent in the project rolls up its children, whatever its own status: a
    # cancelled epic's children are still work. The parent's status says which it is.
    parents = {
        pid: {
            "status": members[pid]["status"],
            "children": [c["id"] for c in kids],
            **_summary(kids, decls),
        }
        for pid, kids in children.items()
    }
    summary = _summary(cards, decls)
    live = [c for c in cards if c["status"] in LIVE_WORK]
    late_for_milestone = {i for m in milestones for i in m["late"]}
    target = plans.plan_dates(project)
    rollup = {
        "stages": {s["id"]: 0 for s in board},
        "total": summary["total"],
        "done": summary["done"],
        "percent_done": summary["percent_done"],
        "earliest_start": summary["start"],
        "latest_end": summary["end"],
        "overdue": sum(1 for c in cards if c["plan"]["overdue"]),
        "milestones": len(milestones),
        "proposed": len(proposed),
        "off_board": off_board,
        "estimate_minutes": summary["estimate_minutes"],
        "sums": summary["sums"],
    }
    for card in cards:
        rollup["stages"][card["plan"]["stage"]] += 1
    health = {
        "percent_done": summary["percent_done"],
        "overdue": rollup["overdue"],
        "blocked": sum(1 for c in live if c["plan"]["blocked_by"]),
        "unowned": sum(1 for c in live if not c["assignee_id"]),
        "past_milestone": len(late_for_milestone),
        "dependency_conflicts": len(conflicts),
        "warnings": len(warnings),
        "span": {"start": summary["start"], "end": summary["end"]},
        "beyond_target": sum(
            1
            for c in live
            if target["end"] and c["plan"]["end"] and c["plan"]["end"] > target["end"]
        ),
    }
    goal = by_id.get(project["goal_id"]) if project["goal_id"] else None
    return {
        "project": project,
        "facts": {
            **target,
            "lead": project["assignee_id"],
            "status": project["status"],
            "goal": {"id": goal["id"], "title": goal["title"]} if goal else None,
        },
        "stages": board,
        "default_stages": not project["stages"],
        "types": project["types"],
        "fields": project["fields"],
        "tasks": cards,
        "proposed": proposed,
        "dependencies": edges,
        "external_dependencies": external,
        "dependency_conflicts": conflicts,
        "milestones": milestones,
        "parents": parents,
        "warnings": warnings,
        "people": people,
        "labels": labels,
        "goals": goals,
        "rollup": rollup,
        "health": health,
    }


def _named(store, workspace, preloaded=None):
    rows = (
        preloaded
        if preloaded is not None
        else [
            x
            for kind in ("person", "principal", "label", "goal")
            for x in store._rows(workspace, kind=kind)
        ]
    )
    rows = [x for x in rows if not x["archived"]]
    people = [
        {"id": p["id"], "kind": p["kind"], "title": p["title"]}
        for kind in ("person", "principal")
        for p in rows
        if p["kind"] == kind
    ]
    labels = [{"id": x["id"], "title": x["title"]} for x in rows if x["kind"] == "label"]
    goals = [{"id": x["id"], "title": x["title"]} for x in rows if x["kind"] == "goal"]
    return people, labels, goals


def _read(store):
    """Refuse a schema this code cannot read rather than upgrading during a read."""
    if store._has_schema() and not store._current():
        raise DomainError(
            "conflict", "Stored schema is not current; open a Store and commit its upgrade"
        )


def plan(store, workspace, project_id, *, now=None):
    """A project as a plan, read in one transaction.

    Its stages (the defaults when it defines none), types and field declarations;
    its tasks in manual order, each with ``plan``: the resolved stage, start, end
    and deadline as dates, done, overdue and open blockers. Proposed tasks are
    listed apart: they are not work yet. A cancelled task appears only when the
    board has a cancelled stage. Milestones and parents carry roll-ups; ``health``
    and ``facts`` are counts and dates, never a score. ``dependency_conflicts`` are
    items that start before a blocker ends; ``warnings`` are structure that no
    longer fits the project's declarations. ``cursor`` is the head event cursor at
    the moment of the read, so a reader can follow ``changes`` with no gap.
    """
    now = normalize_now(now)
    with store._transaction(write=False):
        _read(store)
        project = store._project(workspace, project_id) if isinstance(project_id, str) else None
        if project is None:
            raise DomainError("not_found", "Project not found")
        tasks = store._project_tasks(workspace, project_id)
        members = {t["id"] for t in tasks}
        wanted = {r for t in tasks for r in [*t["depends_on"], t["parent_id"], t["milestone_id"]]}
        wanted = {r for r in wanted if r and r not in members}
        if project["goal_id"]:
            wanted.add(project["goal_id"])
        by_id = _fetch(store, workspace, wanted)
        people, labels, goals = _named(store, workspace)
        result = _build(project, tasks, by_id, people, labels, goals, now)
        result["cursor"] = store.head(workspace)
    return result


def _everything(store, workspace):
    rows = store._rows(workspace, include_archived=True)
    by_id = {x["id"]: x for x in rows}
    tasks = {}
    for item in rows:
        if item["kind"] == "task" and item["project_id"]:
            tasks.setdefault(item["project_id"], []).append(item)
    return rows, by_id, tasks


def projects(store, workspace, *, now=None):
    """Every unarchived project with its facts, stages, roll-up and health, read in
    one pass over the workspace."""
    now = normalize_now(now)
    with store._transaction(write=False):
        _read(store)
        rows, by_id, tasks = _everything(store, workspace)
        people, labels, goals = _named(store, workspace, rows)
        out = []
        for project in (x for x in rows if x["kind"] == "project" and not x["archived"]):
            view = _build(project, tasks.get(project["id"], []), by_id, people, labels, goals, now)
            out.append(
                {
                    "id": project["id"],
                    "title": project["title"],
                    "description": project["description"],
                    "status": project["status"],
                    "managed_by": project["managed_by"],
                    "facts": view["facts"],
                    "stages": view["stages"],
                    "rollup": view["rollup"],
                    "health": view["health"],
                }
            )
        return {"projects": out, "cursor": store.head(workspace)}


def _goal_view(goal, rows, by_id, tasks_by_project, now):
    linked = [
        x
        for x in rows
        if x["kind"] == "project" and x["goal_id"] == goal["id"] and not x["archived"]
    ]
    seen, items = set(), []
    for task in [t for p in linked for t in tasks_by_project.get(p["id"], [])] + [
        x for x in rows if x["kind"] == "task" and x["goal_id"] == goal["id"]
    ]:
        if task["id"] in seen or task["archived"] or task["status"] == "proposed":
            continue
        seen.add(task["id"])
        items.append(task)
    target = plans.plan_dates(goal)
    at_risk = []
    for task in items:
        if task["status"] not in LIVE_WORK:
            continue
        end = plans.plan_dates(task)["end"]
        overdue = bool(task["deadline"] and _is_past(task["deadline"], now))
        if overdue or (target["end"] and end and end > target["end"]):
            at_risk.append(task["id"])
    measure = goal["measure"]
    if measure:
        progress = (
            round(100 * measure["current"] / measure["target"])
            if measure["current"] is not None and measure["target"]
            else None
        )
        measure = {**measure, "percent": progress}
    return {
        "goal": goal,
        "target": target,
        "status": goal["status"],
        "measure": measure,
        "projects": [
            {
                "id": p["id"],
                "title": p["title"],
                **_summary(
                    [
                        t
                        for t in tasks_by_project.get(p["id"], [])
                        if not t["archived"] and t["status"] != "proposed"
                    ],
                    {},
                ),
            }
            for p in linked
        ],
        "tasks": [t["id"] for t in items if t["goal_id"] == goal["id"]],
        "rollup": {**_summary(items, {}), "at_risk": len(at_risk)},
        "at_risk": at_risk,
    }


def goal_plan(store, workspace, goal_id, *, now=None):
    """A goal with its target date, status, optional measure, and a roll-up across
    the projects and tasks linked to it: progress, counts and the live items at
    risk (overdue, or ending after the goal's target). Read in one transaction."""
    now = normalize_now(now)
    with store._transaction(write=False):
        _read(store)
        rows, by_id, tasks = _everything(store, workspace)
        goal = by_id.get(goal_id) if isinstance(goal_id, str) else None
        if goal is None or goal["kind"] != "goal":
            raise DomainError("not_found", "Goal not found")
        return {**_goal_view(goal, rows, by_id, tasks, now), "cursor": store.head(workspace)}


def goals(store, workspace, *, now=None):
    """Every unarchived goal with its roll-up."""
    now = normalize_now(now)
    with store._transaction(write=False):
        _read(store)
        rows, by_id, tasks = _everything(store, workspace)
        out = []
        for goal in (x for x in rows if x["kind"] == "goal" and not x["archived"]):
            view = _goal_view(goal, rows, by_id, tasks, now)
            out.append(
                {
                    "id": goal["id"],
                    "title": goal["title"],
                    "status": goal["status"],
                    "target": view["target"],
                    "measure": view["measure"],
                    "projects": [p["id"] for p in view["projects"]],
                    "rollup": view["rollup"],
                }
            )
        return {"goals": out, "cursor": store.head(workspace)}


def tree(store, workspace, root_id, *, depth=3):
    """An entity and its unarchived child tasks, in manual order, to ``depth`` levels.
    A node cut off by the depth says how many children it has in ``more``."""
    if type(depth) is not int or not 1 <= depth <= 10:
        raise DomainError("invalid", "Depth must be between 1 and 10")
    with store._transaction(write=False):
        _read(store)
        root = store.get(workspace, root_id)

        def kids(id):
            if id == root["id"] and root["kind"] == "project":
                # A project's top level: its tasks whose parent is not one of them.
                tasks = [t for t in store._project_tasks(workspace, id) if not t["archived"]]
                inside = {t["id"] for t in tasks}
                tops = [t for t in tasks if t["parent_id"] not in inside]
                return sorted(tops, key=plans.order_key)
            rows = store.db.execute(
                "SELECT e.data FROM daymark_refs r JOIN daymark_entities e ON "
                "e.workspace=r.workspace AND e.id=r.source_id WHERE r.workspace=? AND "
                "r.target_id=? AND r.field='parent_id' AND e.kind='task' AND e.archived=0",
                (workspace, id),
            ).fetchall()
            return sorted((json.loads(row[0]) for row in rows), key=plans.order_key)

        def node(item, level):
            out = {
                "id": item["id"],
                "title": item["title"],
                "kind": item["kind"],
                "status": item["status"],
                "type": item.get("type"),
            }
            below = kids(item["id"])
            if level >= depth:
                out["more"] = len(below)
            else:
                out["children"] = [node(child, level + 1) for child in below]
            return out

        return node(root, 0)
