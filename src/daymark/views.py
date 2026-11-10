"""Derived, read-only views: what is ready, what the days ahead hold, and why.

Every rule here is deterministic and stated once, so an item cannot be ready in
one view and missing from another. Nothing in this module writes.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

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
