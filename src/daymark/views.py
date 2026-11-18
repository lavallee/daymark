"""Derived, read-only views: what is ready, what the days ahead hold, and why.

Every rule here is deterministic and stated once, so an item cannot be ready in
one view and missing from another. Nothing in this module writes.
"""

from __future__ import annotations

import heapq
import json
import sqlite3
from collections import deque

from . import cursors, plans
from .models import DomainError, Temporal, day_offset, normalize_now, start_has_arrived
from .store import STALE

LIVE = ("proposed", "open", "waiting")
DONE = ("completed", "cancelled")


LIST_FIELDS = (
    "id",
    "kind",
    "title",
    "status",
    "version",
    "priority",
    "scope",
    "next_move",
    "planned",
    "deadline",
    "earliest_start",
    "latest_target",
    "waiting_on",
    "focus",
    "estimate_minutes",
    "project_id",
    "parent_id",
    "milestone_id",
    "milestone",
    "type",
    "stage",
    "assignee_id",
)
# Scheduling and checklist facts for a task card. Detail bodies stay with show.
CARD_FIELDS = LIST_FIELDS + (
    "created_at", "updated_at", "checklist", "advances", "depends_on", "recurrence", "context_ids",
    "claimed_by", "claim_until",
)


def task_card(item):
    return {key: item.get(key) for key in CARD_FIELDS}


def _is_past(temporal, now):
    value = Temporal.model_validate(temporal)
    return value.is_before(now)


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
        raise DomainError("conflict", STALE)
    return True


def _id_pages(store, ids):
    # Each query binds the workspace once, then one variable for each ID.
    available = store.db.getlimit(sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER) - 1
    size = max(1, min(500, available))
    for start in range(0, len(ids), size):
        yield ids[start : start + size]


def _statuses(store, workspace, ids):
    """Status per ID. An archived entity reads as cancelled: shelved work blocks nothing."""
    found, ids = {}, sorted(set(ids))
    for chunk in _id_pages(store, ids):
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
        if soonest is None:
            why = "no date"
        elif soonest == deadline:
            why = f"due in {deadline}d"
        elif soonest == planned:
            why = f"planned in {planned}d"
        else:
            why = f"target in {target}d"
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


def ready(store, workspace, *, now=None, limit=50, owner=None, next_move=None, scope=None, context=None, project=None, brief=False, projection=None):
    """Open tasks that can be acted on now, most pressing first.

    A task is ready when it is open and unarchived, its earliest start has
    arrived (for a repeating task, also its planned day), every dependency is completed, cancelled or archived, it has no open
    or waiting child task (work the leaves, not the container), and nobody else holds a live
    claim. ``owner`` keeps that owner's own claims in the result. ``next_move``
    narrows to "person", "answer" or "agent"; unset moves match none of them.
    ``scope`` narrows to one scope, or with ``""`` to items that have none.
    Tasks the person has put in focus come first, in their order.
    """
    check_task_projection(projection, brief)
    _check_ready_query(limit, next_move, scope, project)
    now = normalize_now(now)
    return _read_ready(store, workspace, now, owner, next_move, scope, context=context, project=project,
                       limit=limit, brief=brief, projection=projection)


def check_task_projection(projection, brief):
    if projection not in (None, "entity", "card") or (brief and projection is not None):
        raise DomainError("invalid", "Choose full entities, task cards, or brief summaries")


def _check_ready_query(limit, next_move, scope, project):
    if type(limit) is not int or not 1 <= limit <= 1000:
        raise DomainError("invalid", "Limit must be between 1 and 1000")
    if next_move not in (None, "person", "answer", "agent"):
        raise DomainError("invalid", "next_move must be person, answer or agent")
    if scope is not None and not isinstance(scope, str):
        raise DomainError("invalid", "Scope must be a name or an empty string")
    if project is not None and (not isinstance(project, str) or not 1 <= len(project) <= 128):
        raise DomainError("invalid", "Project must be a nonempty entity ID of at most 128 characters")


def _readiness_reasons(item, now, owner, *, blocked, has_children):
    """Facts that keep a task out of an unfiltered ready list for this actor."""
    if item["archived"]:
        yield "archived"
    if item["status"] != "open":
        yield item["status"]
    if has_children:
        yield "active_children"
    if not start_has_arrived(item["earliest_start"], now):
        yield "future_start"
    if item["recurrence"] and item["planned"] and day_offset(item["planned"], now) > 0:
        yield "future_round"
    if blocked:
        yield "dependencies"
    holder = claim_holder(item, now)
    if holder is not None and holder != owner:
        yield "claimed_elsewhere"


# Only these fields affect eligibility or order. Reading a queue of ten
# thousand tasks should not materialize ten thousand descriptions and note
# trails. The full records for the selected page are fetched at the end.
_READY_FIELDS = (
    "earliest_start",
    "planned",
    "recurrence",
    "claimed_by",
    "claim_until",
    "deadline",
    "latest_target",
    "priority",
    "focus",
    "created_at",
    "next_move",
    "scope",
)


def _task_fields(store, workspace, statuses, fields, *, next_move=None, scope=None, context=None, project=None, deadlines=False):
    if scope is not None and not isinstance(scope, str):
        raise DomainError("invalid", "Scope must be a name or an empty string")
    marks = ",".join("?" * len(statuses))
    base = (
        " FROM daymark_entities WHERE workspace=? AND kind='task' AND archived=0 "
        f"AND status IN ({marks})"
    )
    args = [workspace, *statuses]
    for kind, field, target in (("context", "context_ids", context), ("project", "project_id", project)):
        if target is None:
            continue
        if not isinstance(target, str) or not 1 <= len(target) <= 128:
            raise DomainError("invalid", f"{kind.capitalize()} must be a nonempty entity ID")
        found = store.db.execute("SELECT kind FROM daymark_entities WHERE workspace=? AND id=?",
                                 (workspace, target)).fetchone()
        if not found or found[0] != kind:
            raise DomainError("not_found", f"{kind.capitalize()} not found in this workspace",
                              [f"List available {kind}s with daymark find --kind {kind}."])
        base += (" AND id IN (SELECT source_id FROM daymark_refs "
                 "WHERE workspace=? AND field=? AND target_id=?)")
        args.extend((workspace, field, target))
    base_args = tuple(args)
    paths = ",".join(f"'$.{field}'" for field in fields)
    query = f"SELECT id,status,json_extract(data,{paths})" + base
    if next_move is not None:
        query += " AND json_extract(data,'$.next_move')=?"
        args.append(next_move)
    if scope is not None:
        query += " AND coalesce(json_extract(data,'$.scope'),'')=?"
        args.append(scope)
    if deadlines:
        query += " AND json_extract(data,'$.deadline') IS NOT NULL"
    try:
        rows = store.db.execute(query, args)
    except sqlite3.OperationalError as exc:
        # JSON functions were optional on older SQLite builds. The ordinary
        # entity representation remains a supported source for embedded users.
        if "no such function: json_extract" not in str(exc):
            raise
        for (data,) in store.db.execute("SELECT data" + base, base_args):
            item = json.loads(data)
            if deadlines and not item.get("deadline"):
                continue
            if (next_move is None or item["next_move"] == next_move) and in_scope(item, scope):
                yield {key: item[key] for key in ("id", "status", "archived", *fields)}
        return
    for id, status, values in rows:
        yield {
            "id": id,
            "status": status,
            "archived": False,
            **dict(zip(fields, json.loads(values))),
        }


def ready_record(item, now):
    """Project one eligible task for a bounded read, including its ordering reason."""
    fields = (
        "id",
        "scope",
        "next_move",
        "deadline",
        "planned",
        "latest_target",
        "earliest_start",
        "priority",
        "claimed_by",
        "claim_until",
    )
    record = {key: item[key] for key in fields}
    if item.get("estimate_minutes") is not None:
        record["estimate_minutes"] = item["estimate_minutes"]
    contexts = item.get("context_ids") or []
    if contexts:
        record["context_ids"] = contexts[:3]
        if len(contexts) > 3:
            record["more_contexts"] = len(contexts) - 3
    title = item["title"]
    record.update(kind="task", status="open", version=item["version"],
                  title=title[:160] + ("…" if len(title) > 160 else ""), why=why(item, now))
    if not claim_holder(item, now):
        record.update(claimed_by=None, claim_until=None)
    return record


def _entity_previews(store, workspace, ids):
    """Fetch bounded titles and small indexed facts, preserving note trails inside SQLite."""
    found = {}
    encoding = store._text_encoding()
    for chunk in _id_pages(store, ids):
        marks = ",".join("?" * len(chunk))
        table = f" FROM daymark_entities WHERE workspace=? AND id IN ({marks})"
        try:
            rows = store.db.execute(
                "SELECT id,status,version,substr(CAST(json_extract(data,'$.title') AS BLOB),1,644),"
                "json_extract(data,'$.context_ids'),json_extract(data,'$.estimate_minutes')" + table,
                (workspace, *chunk),
            )
            for id, status, version, title, contexts, estimate in rows:
                found[id] = {"title": title.decode(encoding, errors="ignore")[:161], "status": status,
                             "version": version, "context_ids": json.loads(contexts or "[]"),
                             "estimate_minutes": estimate}
        except sqlite3.OperationalError as exc:
            if "no such function: json_extract" not in str(exc):
                raise
            for id, status, version, raw in store.db.execute("SELECT id,status,version,data" + table, (workspace, *chunk)):
                item = json.loads(raw)
                found[id] = {"title": item["title"][:161], "status": status, "version": version,
                             "context_ids": item.get("context_ids", []),
                             "estimate_minutes": item.get("estimate_minutes")}
    return found


def _ready_summaries(store, workspace, top, now, project=None):
    details = _entity_previews(store, workspace, [item["id"] for item in top])
    records = [ready_record({**item, **details[item["id"]]}, now) for item in top]
    if project is not None:
        for row in records:
            row["project_id"] = project
    return records


def blocker_previews(store, workspace, blocked):
    """Name the first five unresolved prerequisites in the task's own dependency order."""
    selected = blocked[:5]
    found = _entity_previews(store, workspace, selected)
    rows = []
    for id in selected:
        if id not in found:
            rows.append({"id": id, "status": "missing"})
            continue
        task = found[id]
        rows.append({"id": id, "title": task["title"][:160], "status": task["status"],
                     **({"truncated": True} if len(task["title"]) > 160 else {})})
    return rows


def blocker_cycle(store, workspace, item, *, held):
    """Return one short, verified circular wait leading back to this live task.

    This is a bounded detail-view diagnostic. The queue's readiness rule does
    not depend on whether this search finds a witness.
    """
    if not held or item["kind"] != "task" or item["archived"] or item["status"] not in ("open", "waiting"):
        return None
    start = item["id"]
    seen = {start}
    queue = deque([(start, [])])
    # Bound candidate index entries before joining entity state, including
    # inbound references of other kinds. No entity JSON crosses this query.
    sql = """
        SELECT 'prerequisite', e.id FROM (
            SELECT target_id FROM daymark_refs
            WHERE workspace=? AND source_id=? AND field='depends_on' ORDER BY target_id LIMIT 8
        ) r JOIN daymark_entities e ON e.workspace=? AND e.id=r.target_id
        WHERE e.kind='task' AND e.archived=0 AND e.status IN ('open','waiting')
        UNION ALL
        SELECT 'child', e.id FROM (
            SELECT source_id,field FROM daymark_refs
            WHERE workspace=? AND target_id=? ORDER BY source_id,field LIMIT 8
        ) r JOIN daymark_entities e ON e.workspace=? AND e.id=r.source_id
        WHERE r.field='parent_id' AND e.kind='task' AND e.archived=0 AND e.status IN ('open','waiting')
    """
    while queue:
        node, path = queue.popleft()
        for relation, other in store.db.execute(sql, (workspace, node, workspace) * 2):
            step = {"relation": relation, "id": other}
            if other == start:
                return [*path, step]
            if len(path) < 5 and len(seen) < 20 and other not in seen:
                seen.add(other)
                queue.append((other, [*path, step]))
    return None


def _readiness_links(store, workspace):
    # An unfinished (or missing) target blocks its source. Select only the
    # affected source IDs; a dense graph need not materialize every edge.
    rows = store.db.execute(
        "SELECT DISTINCT r.source_id FROM daymark_entities source JOIN daymark_refs r "
        "ON r.workspace=source.workspace AND r.source_id=source.id AND r.field='depends_on' "
        "LEFT JOIN daymark_entities target ON target.workspace=r.workspace AND target.id=r.target_id "
        "WHERE source.workspace=? AND source.kind='task' AND source.status='open' AND source.archived=0 "
        "AND coalesce(target.archived,0)=0 AND coalesce(target.status,'') NOT IN (?,?)",
        (workspace, *DONE),
    )
    blocked = {id for (id,) in rows}
    return blocked, _parents_with_live_children(store, workspace)


def _is_ready(item, now, owner, blocked, containers):
    return not any(
        _readiness_reasons(
            item, now, owner, blocked=item["id"] in blocked, has_children=item["id"] in containers
        )
    )


def _read_ready(store, workspace, now, owner, next_move, scope, *, limit, brief=False, context=None, project=None, projection=None):
    """Rank projected fields and hydrate the selected page inside the same read snapshot."""
    with store._lock:
        if not _usable(store):
            if project is not None:
                raise DomainError("not_found", "Project not found in this workspace")
            return []
        with store._transaction(write=False):
            blocked, containers = _readiness_links(store, workspace)
            items = _task_fields(
                store, workspace, ("open",), _READY_FIELDS, next_move=next_move, scope=scope, context=context, project=project
            )
            eligible = (item for item in items if _is_ready(item, now, owner, blocked, containers))
            top = heapq.nsmallest(limit, eligible, key=lambda item: urgency(item, now)[0])
            if brief:
                return _ready_summaries(store, workspace, top, now, project)
            fields = {"fields": CARD_FIELDS} if projection == "card" else {}
            full = _fetch(store, workspace, (item["id"] for item in top), **fields)
            return [full[item["id"]] for item in top]


def explain_ready(store, workspace, *, now=None, limit=20, owner=None, next_move=None, scope=None, context=None, project=None):
    """The ready page and a census of live tasks held back within the selected filters."""
    _check_ready_query(limit, next_move, scope, project)
    now = normalize_now(now)
    result = {"ready": [], "ready_count": 0, "held_count": 0, "reasons": {},
              "filters": {"move": next_move, "scope": scope, **({"context": context} if context is not None else {})}}
    if project is not None:
        result["filters"]["project"] = project

    def eligible(items, blocked, containers):
        for item in items:
            if item["status"] != "open":
                # The person must decide or resume before other gates matter.
                reasons = [item["status"]]
            else:
                reasons = list(_readiness_reasons(
                    item, now, owner, blocked=item["id"] in blocked,
                    has_children=item["id"] in containers,
                ))
            if not reasons:
                result["ready_count"] += 1
                yield item
                continue
            result["held_count"] += 1
            for code in reasons:
                group = result["reasons"].setdefault(code, {"count": 0, "examples": []})
                group["count"] += 1
                examples = group["examples"]
                if len(examples) < 3 or item["id"] < examples[-1]:
                    examples.append(item["id"])
                    examples.sort()
                    del examples[3:]

    with store._lock:
        if not _usable(store):
            if project is not None:
                raise DomainError("not_found", "Project not found in this workspace")
            return result
        with store._transaction(write=False):
            blocked, containers = _readiness_links(store, workspace)
            items = _task_fields(store, workspace, LIVE, _READY_FIELDS,
                                 next_move=next_move, scope=scope, context=context, project=project)
            top = heapq.nsmallest(limit, eligible(items, blocked, containers),
                                  key=lambda item: urgency(item, now)[0])
            result["ready"] = _ready_summaries(store, workspace, top, now, project)
    return result


def _agenda_bucket(item, now, days, *, deadlines=False):
    if item["status"] == "proposed":
        return "proposed", None
    deadline = day_offset(item["deadline"], now) if item["deadline"] else None
    planned = day_offset(item["planned"], now) if not deadlines and item["planned"] else None
    if item["deadline"] and _is_past(item["deadline"], now):
        return "overdue", None
    if not deadlines and item["status"] == "waiting":
        return "waiting", None
    if deadline == 0 or (planned is not None and planned <= 0):
        return "today", None
    ahead = [x for x in (deadline, planned) if x is not None and 0 < x <= days]
    if ahead:
        source = item["deadline"] if deadline == min(ahead) else item["planned"]
        return "upcoming", Temporal.model_validate(source).local().date().isoformat()
    return None, None


def agenda(store, workspace, *, now=None, days=7, scope=None, context=None, deadlines=False):
    """The shape of the days ahead: what is late, due, planned, waiting or proposed.

    Each task appears at most once. With deadlines=True, only open and waiting
    tasks with hard deadlines qualify for overdue, today or upcoming (keyed by
    the deadline's local date), independently of readiness.
    """
    if type(days) is not int or not 0 <= days <= 366:
        raise DomainError("invalid", "Days must be between 0 and 366")
    now = normalize_now(now)
    out = {"overdue": [], "today": [], "upcoming": {}}
    if not deadlines:
        out.update(waiting=[], proposed=[], later_or_undated=0)
    statuses = ("open", "waiting") if deadlines else LIVE
    fields = ("deadline", "created_at") if deadlines else _READY_FIELDS
    with store._lock:
        if not _usable(store):
            return out
        with store._transaction(write=False):
            shown = []
            for item in _task_fields(
                store, workspace, statuses, fields, scope=scope, context=context, deadlines=deadlines
            ):
                bucket, day = _agenda_bucket(item, now, days, deadlines=deadlines)
                if bucket is None:
                    if not deadlines:
                        out["later_or_undated"] += 1
                    continue
                if deadlines:
                    order = (day_offset(item["deadline"], now), item["created_at"], item["id"])
                else:
                    order = urgency(item, now)[0]
                shown.append((order, item["id"], bucket, day))
            full = _fetch(store, workspace, (id for _, id, _, _ in shown))
            for _, id, bucket, day in sorted(shown):
                if bucket == "upcoming":
                    out[bucket].setdefault(day, []).append(full[id])
                else:
                    out[bucket].append(full[id])
    out["upcoming"] = dict(sorted(out["upcoming"].items()))
    return out


def activity(store, workspace, *, after=None, limit=20):
    """Return a bounded page of workspace changes in chronological order.

    Without a cursor, start at the most recent page. With a cursor, return only
    later events. The page and its head share one SQLite read transaction.
    """
    if type(limit) is not int or not 1 <= limit <= 100:
        raise DomainError("invalid", "Activity limit must be between 1 and 100")
    saved = after if isinstance(after, str) else None
    if after is not None:
        after = cursors.cursor_number(after)
    with store._lock:
        if not _usable(store):
            if saved:
                if after or saved != cursors.checkpoint(store, workspace, 0):
                    raise cursors.mismatch()
                return {"events": [], "cursor": 0, "head": 0, "checkpoint": saved}
            if after:
                raise DomainError("conflict", "Cursor is ahead of this empty workspace")
            return {"events": [], "cursor": 0, "head": 0}
    with store._transaction(write=False):
        head = store.head(workspace)
        if saved and saved != cursors.checkpoint(store, workspace, after):
            raise cursors.mismatch()
        if after is not None and after > head:
            raise DomainError(
                "conflict",
                "Cursor is ahead of this workspace. Read activity again without a cursor.",
            )
        if after is None:
            # Imported histories may have gaps, so counting backwards from the
            # head cursor is not equivalent to selecting the last N events.
            previous = store.db.execute(
                "SELECT seq FROM daymark_event_cursors WHERE workspace=? "
                "ORDER BY seq DESC LIMIT 1 OFFSET ?",
                (workspace, limit),
            ).fetchone()
            after = previous[0] if previous else 0
        items = _activity_items(store, workspace, after, limit)
        cursor = items[-1]["seq"] if items else after
        page = {"events": items, "cursor": cursor, "head": head}
        if saved:
            page["checkpoint"] = cursors.checkpoint(store, workspace, cursor) if items else saved
        return page


def _activity_items(store, workspace, after, limit):
    # Compare inside SQLite. A page of small edits on a task with several
    # megabytes of notes must not send every pair of full snapshots to Python.
    try:
        rows = store.db.execute(
            """SELECT c.seq,e.created_at,e.action,e.entity_id,e.actor,
                    json_extract(coalesce(e.after_data,e.before_data),'$.kind','$.title'),
                    CASE WHEN e.action='note' AND json_array_length(e.after_data,'$.notes')>0
                         THEN json_extract(e.after_data,
                             '$.notes['||(json_array_length(e.after_data,'$.notes')-1)||']') END,
                    CASE WHEN e.before_data IS NOT NULL AND e.after_data IS NOT NULL
                          AND (e.action!='note' OR coalesce(json_array_length(e.after_data,'$.notes'),0)=0)
                         THEN (SELECT json_group_array(k.key) FROM json_each(e.after_data) k
                               WHERE k.key NOT IN ('version','updated_at')
                               AND k.value IS NOT json_extract(e.before_data,'$.'||k.key)) END
               FROM daymark_event_cursors c JOIN daymark_events e ON e.seq=c.event_seq
               WHERE c.workspace=? AND c.seq>? ORDER BY c.seq LIMIT ?""",
            (workspace, after, limit),
        )
    except sqlite3.OperationalError as exc:
        if "no such function" not in str(exc) and "no such table: json_each" not in str(exc):
            raise
        return _activity_without_json(store, workspace, after, limit)
    result = []
    for seq, at, action, id, actor, entity, note, changed in rows:
        kind, title = json.loads(entity)
        item = {
            "seq": seq,
            "at": at,
            "action": action,
            "id": id,
            "kind": kind,
            "title": title or "",
            "actor": actor,
        }
        if note is not None:
            item["note"] = json.loads(note)
        if changed is not None:
            item["changed"] = sorted(json.loads(changed))
        result.append(item)
    return result


def _activity_without_json(store, workspace, after, limit):
    # Older SQLite builds can still read the identical page. Decode a single
    # before/after pair at a time to keep memory independent of page length.
    items = []
    for _ in range(limit):
        page = store.events(workspace, after=after, limit=1)
        if not page:
            break
        event = page[0]
        before, end = event["before_data"], event["after_data"]
        entity = end or before
        after = event["seq"]
        item = {
            "seq": after,
            "at": event["created_at"],
            "action": event["action"],
            "id": event["entity_id"],
            "kind": entity["kind"],
            "title": entity.get("title", ""),
            "actor": event["actor"],
        }
        if event["action"] == "note" and end and end.get("notes"):
            item["note"] = end["notes"][-1]
        elif before and end:
            item["changed"] = sorted(
                key
                for key in end
                if key not in {"version", "updated_at"} and before.get(key) != end[key]
            )
        items.append(item)
    return items


def _context_links(store, workspace, id, field, *json_fields):
    """Read only the fields used in a related record's context summary."""
    base = (
        " FROM daymark_refs r JOIN daymark_entities e ON "
        "e.workspace=r.workspace AND e.id=r.source_id WHERE r.workspace=? AND "
        "r.target_id=? AND r.field=? ORDER BY e.id"
    )
    base_fields = ("id", "kind", "status", "archived")
    columns = ",".join("e." + name for name in base_fields)
    if json_fields:
        columns += "," + ",".join(f"json_extract(e.data,'$.{name}')" for name in json_fields)
    args = (workspace, id, field)
    fields = (*base_fields, *json_fields)
    try:
        rows = store.db.execute("SELECT " + columns + base, args)
    except sqlite3.OperationalError as exc:
        if "no such function: json_extract" not in str(exc):
            raise
        for (raw,) in store.db.execute("SELECT e.data" + base, args):
            item = json.loads(raw)
            yield {name: item[name] for name in fields}
        return
    for row in rows:
        yield dict(zip(fields, row))


def resume_effects(item, now):
    """Calendar consequences of resuming this visible waiting task at the read instant."""
    if item["kind"] != "task" or item["status"] != "waiting" or item["archived"]:
        return {}
    lifts_start = not start_has_arrived(item["earliest_start"], now)
    return {"if_resumed_now": {
        "earliest_start": "clear" if lifts_start else "preserve",
        "active_reminders": "cancel" if lifts_start else "preserve",
        "deadline": "preserve",
    }}


def context(store, workspace, id, *, history=10, now=None, owner=None, actor=None):
    """One entity with what surrounds it: blockers, dependents, children, reminders, history."""
    now = normalize_now(now)
    with store._lock:
        if not _usable(store):
            raise DomainError("not_found", "Entity not found")
    with store._transaction(write=False):
        item = store.get(workspace, id)

        statuses = _statuses(store, workspace, item["depends_on"])
        events = store._entity_history(
            workspace, id, "e.action,e.created_at,e.operation_id,e.actor", history
        ).fetchall()
        blocked = [ref for ref in item["depends_on"] if statuses.get(ref) not in DONE]
        children = list(_context_links(store, workspace, id, "parent_id", "title"))
        readiness = {}
        if item["kind"] == "task":
            active = [x["id"] for x in children if x["kind"] == "task"
                      and not x["archived"] and x["status"] in ("open", "waiting")]
            reasons = list(_readiness_reasons(item, now, owner, blocked=blocked, has_children=active))
            readiness = {"readiness": {"ready": not reasons, "reasons": reasons, "active_children": active}}
            if cycle := blocker_cycle(store, workspace, item, held=blocked or active):
                readiness["readiness"]["cycle"] = cycle
        return {
            **({"decision_access": store.decision_access(workspace, actor)}
               if actor is not None and item["status"] == "proposed" else {}),
            "entity": item,
            "review_token": store._review_token(workspace, id),
            **readiness,
            **resume_effects(item, now),
            "blocked_by": blocked,
            **({"blockers": blocker_previews(store, workspace, blocked)} if blocked else {}),
            "dependents": [x["id"] for x in _context_links(store, workspace, id, "depends_on") if x["status"] in LIVE],
            "children": [
                {"id": x["id"], "title": x["title"], "status": x["status"]}
                for x in children
            ],
            "reminders": [
                {"id": x["id"], "scheduled_at": x["scheduled_at"], "state": x["reminder_state"]}
                for x in _context_links(store, workspace, id, "task_id", "scheduled_at", "reminder_state")
            ],
            "history": [
                {"action": row[0], "at": row[1], "operation_id": row[2], "actor": row[3]}
                for row in events
            ],
        }


_COUNT_FIELDS = ("ready", "overdue", "today", "next_7_days", "waiting", "proposed")


def _count_for_summary(item, counts, now, owner, links):
    """Count this task's agenda bucket and return its canonical readiness."""
    bucket, _ = _agenda_bucket(item, now, 7)
    if bucket:
        counts["next_7_days" if bucket == "upcoming" else bucket] += 1
    ready = _is_ready(item, now, owner, *links)
    if ready:
        counts["ready"] += 1
    return ready


def summary(store, workspace, *, now=None, scope=None, owner=None, context=None):
    """Counts for a one-glance orientation."""
    now = normalize_now(now)
    counts = dict.fromkeys(_COUNT_FIELDS, 0)
    fields = ("earliest_start", "planned", "recurrence", "claimed_by", "claim_until", "deadline")
    with store._lock:
        if not _usable(store):
            return counts
        with store._transaction(write=False):
            links = _readiness_links(store, workspace)
            for item in _task_fields(store, workspace, LIVE, fields, scope=scope, context=context):
                _count_for_summary(item, counts, now, owner, links)
    return counts


def memory_preview(id, title, scope):
    fact = {"id": id, "title": title[:160] + ("…" if len(title) > 160 else "")}
    if scope:
        fact["scope"] = scope
    return fact


def _orientation_memories(store, workspace, scope=None):
    # Apply the same exact scope and ID order as find before taking the page.
    base = " FROM daymark_entities WHERE workspace=? AND kind='memory' AND archived=0"
    where = " AND coalesce(json_extract(data,'$.scope'),'')=?" if scope is not None else ""
    args = (workspace, scope) if scope is not None else (workspace,)
    try:
        rows = store.db.execute(
            "SELECT id,substr(CAST(json_extract(data,'$.title') AS BLOB),1,644),"
            "json_extract(data,'$.scope')" + base + where + " ORDER BY id LIMIT 6", args,
        )
        encoding = store._text_encoding()
        facts = [memory_preview(id, title.decode(encoding, errors="ignore"), saved_scope)
                 for id, title, saved_scope in rows]
    except sqlite3.OperationalError as exc:
        if "no such function: json_extract" not in str(exc):
            raise
        facts = []
        for (raw,) in store.db.execute("SELECT data" + base + " ORDER BY id", (workspace,)):
            item = json.loads(raw)
            if in_scope(item, scope):
                facts.append(memory_preview(item["id"], item["title"], item["scope"]))
                if len(facts) == 6:
                    break
    return facts[:5], facts[4]["id"] if len(facts) > 5 else None


def orientation(store, workspace, *, now=None, owner=None, scope=None):
    """Five ready records, five fact previews and exact counts from a single snapshot."""
    if scope is not None and not isinstance(scope, str):
        raise DomainError("invalid", "Scope must be a name or an empty string")
    now = normalize_now(now)
    counts = dict.fromkeys(_COUNT_FIELDS, 0)
    result = {"summary": counts, "ready": [], "memories": [], "next_memory": None,
              "as_of": now.isoformat()}
    if scope is None:
        result.update(activity_cursor=0, activity_checkpoint=cursors.checkpoint(store, workspace, 0))
    else:
        result["scope"] = scope
    with store._lock:
        if not _usable(store):
            return result
        with store._transaction(write=False):
            links = _readiness_links(store, workspace)
            items = _task_fields(store, workspace, LIVE, _READY_FIELDS, scope=scope)
            eligible = (item for item in items if _count_for_summary(item, counts, now, owner, links))
            top = heapq.nsmallest(5, eligible, key=lambda item: urgency(item, now)[0])
            result["ready"] = _ready_summaries(store, workspace, top, now)
            result["memories"], result["next_memory"] = _orientation_memories(store, workspace, scope)
            if scope is None:
                result["activity_cursor"] = store.head(workspace)
                result["activity_checkpoint"] = cursors.checkpoint(store, workspace, result["activity_cursor"])
    return result


# -- projects and goals: one pass over preloaded records, then pure roll-ups

LIVE_WORK = ("open", "waiting")


def _status(item) -> str | None:
    """A dependency's effective status; archived reads as cancelled."""
    if item is None:
        return None
    return "cancelled" if item["archived"] else item["status"]


def _fetch(store, workspace, ids, *, fields=()) -> dict:
    found, ids = {}, sorted(set(ids))
    for chunk in _id_pages(store, ids):
        marks = ",".join("?" * len(chunk))
        table = f" FROM daymark_entities WHERE workspace=? AND id IN ({marks})"
        args = (workspace, *chunk)
        if fields:
            paths = ",".join(f"'$.{key}'" for key in fields)
            try:
                rows = store.db.execute(f"SELECT id,json_extract(data,{paths})" + table, args)
                found.update((id, dict(zip(fields, json.loads(raw), strict=True))) for id, raw in rows)
                continue
            except sqlite3.OperationalError as error:
                if "no such function: json_extract" not in str(error):
                    raise
        for id, raw in store.db.execute("SELECT id,data" + table, args):
            item = json.loads(raw)
            found[id] = {key: item.get(key) for key in fields} if fields else item
    return found


def _summary(items, decls) -> dict:
    """Counts, progress, effort, span and number-field sums for a set of items."""
    counted = [x for x in items if x["status"] != "cancelled"]
    done = sum(1 for x in counted if x["status"] == "completed")
    dates = [x.get("plan") or plans.plan_dates(x) for x in items]
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
                        r for r in task["depends_on"] if _status(by_id.get(r)) not in DONE
                    ],
                },
            }
        )
    edges, external_dependents = [], {}
    for task in tasks:
        for ref in task["depends_on"]:
            if ref in members:
                edges.append({"from": task["id"], "to": ref})
            elif ref not in in_project:
                dependents = external_dependents.setdefault(ref, [])
                if not dependents or dependents[-1] != task["id"]:
                    dependents.append(task["id"])
    external = [
        {
            "id": ref,
            "title": (by_id.get(ref) or {}).get("title"),
            "project_id": (by_id.get(ref) or {}).get("project_id"),
            "status": _status(by_id.get(ref)),
            "dependents": external_dependents[ref],
        }
        for ref in sorted(external_dependents)
    ]
    conflicts = []
    for card in (c for c in cards if c["status"] in LIVE_WORK):  # Finished work has none.
        for ref in card["depends_on"]:
            blocker = by_id.get(ref)
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
        for w in _warnings(t, types, decls, members, in_project, by_id, person_ids)
    ]
    milestones = []
    # Every unarchived milestone is listed with its status, including a proposed or
    # cancelled one that is not on the board, so its members are never orphaned.
    milestone_members = {}
    for card in cards:
        if card["milestone_id"]:
            milestone_members.setdefault(card["milestone_id"], []).append(card)
    for marker in (t for t in tasks if t["milestone"]):
        belongs = milestone_members.get(marker["id"], [])
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
        raise DomainError("conflict", STALE)


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
        by_id.update((task["id"], task) for task in tasks)
        people, labels, goals = _named(store, workspace)
        result = _build(project, tasks, by_id, people, labels, goals, now)
        result["cursor"] = store.head(workspace)
    return result


# Portfolio summaries need project declarations and a task's planning fields.
# Notes, descriptions and recurrence histories can dwarf the summaries they produce.
_PORTFOLIO_FIELDS = (
    "title", "scope", "project_id", "created_at", "rank", "goal_id", "assignee_id", "parent_id",
    "milestone_id", "milestone", "type", "stage", "values", "depends_on", "estimate_minutes",
    "earliest_start", "planned", "latest_target", "deadline",
)


def _portfolio_rows(store, workspace, scope):
    paths = ",".join(f"'$.{key}'" for key in _PORTFOLIO_FIELDS)
    query = (
        "SELECT id,kind,status,archived,CASE WHEN kind='project' THEN data "
        f"ELSE json_extract(data,{paths}) END FROM daymark_entities WHERE workspace=:workspace"
    )
    if scope is not None:
        # A project's members and their prerequisites may live in other scopes.
        # Person-valued custom fields also need the workspace's live person IDs.
        query = """
            WITH selected(id) AS (
                SELECT id FROM daymark_entities WHERE workspace=:workspace
                  AND kind='project' AND archived=0 AND coalesce(json_extract(data,'$.scope'),'')=:scope
            ), members(id) AS (
                SELECT source_id FROM daymark_refs WHERE workspace=:workspace AND field='project_id'
                  AND target_id IN (SELECT id FROM selected)
            ), records(id) AS (
                SELECT id FROM selected UNION ALL SELECT id FROM members
            ), related(id) AS (
                SELECT target_id FROM daymark_refs WHERE workspace=:workspace
                  AND source_id IN (SELECT id FROM records)
                  AND field IN ('depends_on','parent_id','assignee_id','goal_id')
            )
        """ + query + """
            AND (id IN (SELECT id FROM records) OR id IN (SELECT id FROM related)
                 OR (kind IN ('person','principal') AND archived=0))
        """
    try:
        records = store.db.execute(
            query + " ORDER BY id", {"workspace": workspace, "scope": scope}
        )
    except sqlite3.OperationalError as exc:
        if "no such function: json_extract" not in str(exc):
            raise
        return store._rows(workspace, include_archived=True)
    rows = []
    for id, kind, status, archived, value in records:
        fields = json.loads(value)
        if kind == "project":
            rows.append(fields)
        else:
            rows.append({"id": id, "kind": kind, "status": status, "archived": bool(archived),
                         **dict(zip(_PORTFOLIO_FIELDS, fields))})
    return rows


def _everything(store, workspace, *, portfolio=False, scope=None):
    rows = _portfolio_rows(store, workspace, scope) if portfolio else store._rows(workspace, include_archived=True)
    by_id = {x["id"]: x for x in rows}
    tasks = {}
    for item in rows:
        if item["kind"] == "task" and item["project_id"]:
            tasks.setdefault(item["project_id"], []).append(item)
    return rows, by_id, tasks


def projects(store, workspace, *, now=None, scope=None):
    """Unarchived projects with their facts, stages, roll-up and health in one snapshot."""
    now = normalize_now(now)
    if scope is not None and (not isinstance(scope, str) or len(scope) > 100):
        raise DomainError("invalid", "Scope must be text of at most 100 characters")
    with store._transaction(write=False):
        _read(store)
        rows, by_id, tasks = _everything(store, workspace, portfolio=True, scope=scope)
        people, labels, goals = _named(store, workspace, rows)
        out = []
        for project in (x for x in rows if x["kind"] == "project" and not x["archived"] and in_scope(x, scope)):
            view = _build(project, tasks.get(project["id"], []), by_id, people, labels, goals, now)
            facts = view["facts"]
            scoped = {}
            if scope is not None:
                scoped["scope"] = project["scope"]
                outside = []
                for key, ref in (("goal", project["goal_id"]), ("lead", project["assignee_id"])):
                    if ref and (ref not in by_id or not in_scope(by_id[ref], scope)):
                        facts[key] = None
                        outside.append(key)
                if outside:
                    scoped["outside_scope"] = outside
            out.append(
                {
                    "id": project["id"],
                    "title": project["title"],
                    "description": project["description"],
                    "status": project["status"],
                    "managed_by": project["managed_by"],
                    "facts": facts,
                    "stages": view["stages"],
                    "rollup": view["rollup"],
                    "health": view["health"],
                    **scoped,
                }
            )
        if scope is not None:
            return {"projects": out, "scope": scope, "member_scope": "whole_project"}
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


def _explore_refs(store, workspace, ids, scope):
    """Retain global eligibility facts while limiting which link IDs may be shown."""
    statuses = _statuses(store, workspace, ids)
    if scope is None:
        return statuses, set(ids)
    visible = set()
    for chunk in _id_pages(store, sorted(statuses)):
        where = " FROM daymark_entities WHERE workspace=? AND id IN ({})".format(",".join("?" * len(chunk)))
        args = (workspace, *chunk)
        try:
            rows = store.db.execute("SELECT id,json_extract(data,'$.scope')" + where, args)
            visible.update(id for id, name in rows if (name or "") == scope)
        except sqlite3.OperationalError as exc:
            if "no such function: json_extract" not in str(exc):
                raise
            rows = store.db.execute("SELECT id,data" + where, args)
            visible.update(id for id, data in rows if in_scope(json.loads(data), scope))
    return statuses, visible


def explore(store, workspace, goal_id=None, *, now=None, scope=None):
    """Conditions and the work moving them, for one goal or the whole workspace.

    A condition is on the **frontier** when it is not met and everything it
    requires is met: it is where progress is possible now. A frontier condition
    with no open, waiting or proposed task advancing it is a **gap**. Conditions
    of a finished or shelved goal are on no frontier. Which frontier
    condition is the real bottleneck is a judgement; this only states the facts.
    """
    if scope is not None and not isinstance(scope, str):
        raise DomainError("invalid", "Scope must be a name or an empty string")
    selected_scope = {"scope": scope} if scope is not None else {}
    with store.read_snapshot():
        if not _usable(store):
            return {"goal": None, "conditions": [], "frontier": [], "gaps": [], "unassessed": [], **selected_scope}
        goal = None
        if goal_id is not None:
            goal = store.get(workspace, goal_id)
            if goal["kind"] != "goal":
                raise DomainError("invalid", "Explore starts from a goal")
        conditions = [
            c
            for c in store._rows(workspace, kind="condition")
            if (goal_id is None or c["goal_id"] == goal_id) and in_scope(c, scope)
        ]
        requires = {c["id"]: list(c["depends_on"]) for c in conditions}
        links = {id for refs in requires.values() for id in refs} | {c["goal_id"] for c in conditions}
        statuses, visible = _explore_refs(store, workspace, links, scope)
        advancing = {c["id"]: [] for c in conditions}
        outside_advancing = {c["id"]: {} for c in conditions}
        for chunk in _id_pages(store, sorted(advancing)):
            rows = store.db.execute(
                "SELECT r.target_id,e.data FROM daymark_refs r JOIN daymark_entities e ON "
                "e.workspace=r.workspace AND e.id=r.source_id WHERE r.workspace=? AND "
                "r.field='advances' AND e.archived=0 AND r.target_id IN ({}) ORDER BY e.id".format(
                    ",".join("?" * len(chunk))
                ), (workspace, *chunk),
            )
            for target, data in rows:
                task = json.loads(data)
                if in_scope(task, scope):
                    advancing[target].append({key: task[key] for key in ("id", "title", "status")})
                else:
                    counts = outside_advancing[target]
                    counts[task["status"]] = counts.get(task["status"], 0) + 1
    out, frontier, gaps, unassessed = [], [], [], []
    for item in sorted(conditions, key=lambda c: (c["created_at"], c["id"])):
        live = item["status"] == "open" and statuses.get(item["goal_id"]) not in DONE
        waiting_on = [r for r in requires[item["id"]] if statuses.get(r) not in DONE]
        on_frontier = live and not waiting_on
        # A proposed move counts: someone has already suggested what to do here.
        other = outside_advancing[item["id"]]
        moving = any(t["status"] in LIVE for t in advancing[item["id"]]) or any(status in LIVE for status in other)
        dependencies = requires[item["id"]]
        outside = {
            key: value for key, value in {
                "goal": item["goal_id"] not in visible,
                "requires": sum(id not in visible for id in dependencies),
                "waiting_on": sum(id not in visible for id in waiting_on),
                "advancing": other,
            }.items() if value
        }
        if on_frontier:
            frontier.append(item["id"])
            if not moving:
                gaps.append(item["id"])
        if live and item["condition"] == "unknown":
            unassessed.append(item["id"])
        out.append(
            {
                "id": item["id"],
                "title": item["title"],
                "state": "dropped" if item["status"] == "cancelled" else item["condition"],
                "goal_id": item["goal_id"] if item["goal_id"] in visible else None,
                "requires": [id for id in dependencies if id in visible],
                "waiting_on": [id for id in waiting_on if id in visible],
                **({"outside_scope": outside} if outside else {}),
                "frontier": on_frontier,
                "advancing": advancing[item["id"]],
                "evidence": [n["text"] for n in item["notes"][-3:]],
            }
        )
    lean = {k: goal[k] for k in ("id", "title", "status")} if goal and in_scope(goal, scope) else None
    return {
        **selected_scope,
        "goal": lean,
        "conditions": out,
        "frontier": frontier,
        "gaps": gaps,
        "unassessed": unassessed,
    }
