"""A bounded handoff read of an entity and its latest attributed notes."""

from __future__ import annotations

import json
import sqlite3

from . import outbox, views
from .models import DomainError, normalize_now, recurrence_date_field

_NOTE_LIMIT = 3
_TEXT_LIMIT = 240
_PREVIEW_BYTES = 4 * (_TEXT_LIMIT + 1)
_LINK_LIMIT = 5
_TAG_FIELDS = ("label_ids", "context_ids")
_TAG_LIMIT = 3
_FIELDS = (
    "id",
    "kind",
    "status",
    "version",
    "archived",
    "next_move",
    "scope",
    "waiting_on",
    "claimed_by",
    "claim_until",
    "depends_on",
    "recurrence",
    "deadline",
    "planned",
    "earliest_start",
    "latest_target",
    *outbox.DETAIL_FIELDS,
    "attempts",
    "retry_at",
)


def _projected_row(store, workspace, id, before_note):
    paths = ",".join(f"'$.{key}'" for key in _FIELDS)
    # Keep each list's order and total without transferring all its IDs.
    tags = []
    for key in _TAG_FIELDS:
        indices = ",".join(f"'$.{key}[{i}]'" for i in range(_TAG_LIMIT))
        tags.extend([f"json_extract(data,{indices})", f"json_array_length(data,'$.{key}')"])
    row = store.db.execute(
        f"SELECT json_extract(data,{paths}), "
        "substr(CAST(json_extract(data,'$.title') AS BLOB),1,?),"
        "substr(CAST(json_extract(data,'$.description') AS BLOB),1,?), "
        "json_array_length(data,'$.notes'), substr(CAST(json_extract(data,'$.series_id') AS BLOB),1,?), "
        f"{','.join(tags)} "
        "FROM daymark_entities WHERE workspace=? AND id=?",
        (_PREVIEW_BYTES, _PREVIEW_BYTES, _PREVIEW_BYTES, workspace, id),
    ).fetchone()
    if row is None:
        raise DomainError("not_found", "Entity not found")
    item = dict(zip(_FIELDS, json.loads(row[0])))
    for key, values, total in zip(_TAG_FIELDS, row[5::2], row[6::2], strict=True):
        item[key] = [v for v in json.loads(values) if v is not None]
        item[key + "_count"] = total or 0
    encoding = store._text_encoding()
    item.update(
        title=(row[1] or b"").decode(encoding, errors="ignore"),
        description=(row[2] or b"").decode(encoding, errors="ignore"),
        series_id=(row[4] or b"").decode(encoding, errors="ignore"),
    )
    count = row[3] or 0
    stop = count
    if before_note is not None:
        position = store.db.execute(
            "SELECT n.key FROM daymark_entities e, json_each(e.data,'$.notes') n "
            "WHERE e.workspace=? AND e.id=? AND json_extract(n.value,'$.id')=? LIMIT 1",
            (workspace, id, before_note),
        ).fetchone()
        if position is None:
            raise DomainError("not_found", "Note cursor not found on this entity")
        stop = position[0]
    # Position indexes address only the tail; the Python process never
    # receives the preceding note trail or unbounded descriptions.
    notes = []
    for index in range(max(0, stop - _NOTE_LIMIT), stop):
        prefix = f"$.notes[{index}]"
        info = store.db.execute(
            "SELECT json_extract(data,?,?,?), substr(CAST(json_extract(data,?) AS BLOB),1,?) "
            "FROM daymark_entities WHERE workspace=? AND id=?",
            (
                *[prefix + "." + key for key in ("id", "at", "author", "text")],
                _PREVIEW_BYTES,
                workspace,
                id,
            ),
        ).fetchone()
        note_id, at, author = json.loads(info[0])
        notes.append(
            {"id": note_id, "at": at, "author": author, "text": info[1].decode(encoding, errors="ignore")}
        )
    return item, stop, count - stop, notes


def _source(store, workspace, id, before_note):
    try:
        return _projected_row(store, workspace, id, before_note)
    except sqlite3.OperationalError as exc:
        if "no such function" not in str(exc) and "no such table: json_each" not in str(exc):
            raise
        item = store.get(workspace, id)
        notes = item["notes"]
        stop = len(notes)
        if before_note is not None:
            stop = next((i for i, n in enumerate(notes) if n["id"] == before_note), -1)
            if stop < 0:
                raise DomainError("not_found", "Note cursor not found on this entity") from None
        return (
            item,
            stop,
            len(notes) - stop,
            [dict(n) for n in notes[max(0, stop - _NOTE_LIMIT) : stop]],
        )


def read(store, workspace, id, *, now=None, owner=None, before_note=None, actor=None):
    """The current state and a short note trail, with exact references to read further."""
    now = normalize_now(now)
    if before_note is not None and (
        not isinstance(before_note, str) or not 1 <= len(before_note) <= 128
    ):
        raise DomainError("invalid", "A note cursor must contain 1 to 128 characters")
    with store._lock:
        if not views._usable(store):
            raise DomainError("not_found", "Entity not found")
    with store._transaction(write=False):
        item, note_count, newer, notes = _source(store, workspace, id, before_note)
        review_token = store._review_token(workspace, id)
        access = store.decision_access(workspace, actor) if item["status"] == "proposed" and actor is not None else None
        reminder = {}
        if item["kind"] == "reminder":
            reminder = {key: item[key] for key in outbox.DETAIL_FIELDS}
            if item.get("attempts"):
                reminder["attempts"] = item["attempts"]
            if item.get("retry_at"):
                reminder["retry_at"] = item["retry_at"]
            target = outbox.target_status(store.db, workspace, item["task_id"])
            if item["reminder_state"] in outbox.ACTIVE_STATES and outbox.availability(
                item["status"], item["archived"], item["task_id"], target
            ) == "dormant":
                reminder["held_for_decision"] = True
        blocked = views.blockers(store, workspace, item)
        named_blockers = views.blocker_previews(store, workspace, blocked)
        sources = store.db.execute(
            "SELECT system,kind,ref_id FROM daymark_sources WHERE workspace=? AND entity_id=? "
            "ORDER BY system,kind,ref_id",
            (workspace, id),
        )
        refs, source_count = [], 0
        for system, kind, ref_id in sources:
            if source_count < 3:
                refs.append({"system": system, "kind": kind, "id": ref_id})
            source_count += 1
        active = store.db.execute(
            "SELECT e.id FROM daymark_refs r JOIN daymark_entities e ON "
            "e.workspace=r.workspace AND e.id=r.source_id WHERE r.workspace=? AND "
            "r.target_id=? AND r.field='parent_id' AND e.kind='task' AND e.archived=0 "
            "AND e.status IN ('open','waiting') ORDER BY e.id",
            (workspace, id),
        )
        children, child_count = [], 0
        for (child_id,) in active:
            if child_count < _LINK_LIMIT:
                children.append(child_id)
            child_count += 1
        cycle = views.blocker_cycle(store, workspace, item, held=blocked or child_count)
    out = {key: item[key] for key in ("id", "kind", "status", "version")}
    out.update(reminder)
    if item["archived"]:
        out["archived"] = True
    out["review_token"] = review_token
    if access:
        out["decision_access"] = access
    clipped = []
    for key in ("title", "description", "waiting_on"):
        value = item.get(key) or ""
        if key == "title" or value:
            out[key] = value[:_TEXT_LIMIT]
            if len(value) > _TEXT_LIMIT:
                clipped.append(key)
    if rule := item.get("recurrence"):
        out["recurrence"] = {k: rule[k] for k in ("rule", "mode", "timezone", "occurrence_index")}
        out["recurrence"]["advances"] = recurrence_date_field(item)
        if series := item.get("series_id"):
            if len(series) <= _TEXT_LIMIT:
                out["series_id"] = series
            else:
                clipped.append("series_id")
    if clipped:
        out["truncated"] = clipped
    for key in ("next_move", "scope", "deadline", "planned", "earliest_start", "latest_target"):
        if item[key] is not None:
            out[key] = item[key]
    if holder := views.claim_holder(item, now):
        out["claim"] = {"owner": holder, "until": item["claim_until"]}
    if item["kind"] == "task":
        reasons = list(
            views._readiness_reasons(item, now, owner, blocked=blocked, has_children=child_count)
        )
        out["readiness"] = {"ready": not reasons, "reasons": reasons}
        if cycle:
            out["readiness"]["cycle"] = cycle
    out["blocked_by"] = blocked[:_LINK_LIMIT]
    if named_blockers:
        out["blockers"] = named_blockers
    out["active_children"] = children
    for note in notes:
        if len(note["text"]) > _TEXT_LIMIT:
            note["text"] = note["text"][:_TEXT_LIMIT]
            note["truncated"] = True
    out["notes"] = notes
    out["omitted"] = {
        "notes": note_count - len(notes),
        "newer_notes": newer,
        "blocked_by": len(blocked) - len(out["blocked_by"]),
        "active_children": child_count - len(children),
    }
    for key in _TAG_FIELDS:
        if values := item.get(key):
            out[key] = values[:_TAG_LIMIT]
            total = item.get(key + "_count", len(values))
            if total > len(out[key]):
                out["omitted"][key] = total - len(out[key])
    if refs:
        out["source_refs"] = refs
    if source_count > len(refs):
        out["omitted"]["source_refs"] = source_count - len(refs)
    if out["omitted"]["notes"]:
        out["next_before_note"] = notes[0]["id"]
    return out


def note(store, workspace, id, note_id):
    """Return one complete, attributed append-only note by its exact ID."""
    if not isinstance(note_id, str) or not 1 <= len(note_id) <= 128:
        raise DomainError("invalid", "A note ID must contain 1 to 128 characters")
    with store._lock:
        if not views._usable(store):
            raise DomainError("not_found", "Entity not found")
    with store._transaction(write=False):
        try:
            row = store.db.execute(
                "SELECT n.value FROM daymark_entities e, json_each(e.data,'$.notes') n "
                "WHERE e.workspace=? AND e.id=? AND json_extract(n.value,'$.id')=? LIMIT 1",
                (workspace, id, note_id),
            ).fetchone()
        except sqlite3.OperationalError as exc:
            if "no such function" not in str(exc) and "no such table: json_each" not in str(exc):
                raise
            for saved in store.get(workspace, id)["notes"]:
                if saved["id"] == note_id:
                    return saved
        else:
            if row:
                return json.loads(row[0])
    raise DomainError("not_found", "Note not found on this entity")
