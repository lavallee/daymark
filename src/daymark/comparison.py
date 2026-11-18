"""Compare the current entity to an exact recorded version in the same workspace."""

from __future__ import annotations

import json
import sqlite3

from .models import DomainError

_PREVIEW = 160
_NOTE_LIMIT = 3
_BOOKKEEPING = {"id", "workspace", "created_at", "updated_at", "version", "notes"}
_OPAQUE = {"lease_token", "delivery_key"}


def _candidates(store, workspace, id, version):
    # An imported native snapshot can retain either side of an event. The
    # entity index narrows both arms of this lookup to this workspace and ID.
    where = "workspace=? AND entity_id=?"
    try:
        rows = store.db.execute(
            f"SELECT before_data FROM daymark_events WHERE {where} AND json_extract(before_data,'$.version')=? "
            f"UNION ALL SELECT after_data FROM daymark_events WHERE {where} AND json_extract(after_data,'$.version')=?",
            (workspace, id, version, workspace, id, version),
        )
        for (data,) in rows:
            yield json.loads(data)
    except sqlite3.OperationalError as exc:
        if "no such function: json_extract" not in str(exc):
            raise
        rows = store.db.execute(
            f"SELECT before_data,after_data FROM daymark_events WHERE {where}", (workspace, id)
        )
        for pair in rows:
            for raw in pair:
                if raw:
                    item = json.loads(raw)
                    if item["version"] == version:
                        yield item


def _same_value(left, right):
    # JSON true and 1 compare equal in Python, but can be different reviewed terms.
    if type(left) is not type(right):
        return False
    if isinstance(left, dict):
        return left.keys() == right.keys() and all(
            _same_value(value, right[key]) for key, value in left.items()
        )
    if isinstance(left, list):
        return len(left) == len(right) and all(_same_value(a, b) for a, b in zip(left, right))
    return left == right


def _display(value):
    if isinstance(value, (list, dict)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return value if isinstance(value, str) else str(value)


def _change(field, before, after):
    if field in _OPAQUE:
        return {"field": field, "values_hidden": True}
    old, new = _display(before), _display(after)
    change = {"field": field, "before": before, "after": after}
    if max(len(old), len(new)) <= _PREVIEW:
        return change
    # Show the first differing characters even if they follow a long common prefix.
    first = next((i for i, (a, b) in enumerate(zip(old, new)) if a != b), min(len(old), len(new)))
    start = max(0, first - 30)
    for key, text in (("before", old), ("after", new)):
        if len(text) > _PREVIEW:
            change[key] = text[start : start + _PREVIEW]
            change.setdefault("previews", {})[key] = {"offset": start, "length": len(text)}
    return change


def _note_changes(before, after):
    previous = {n["id"]: n for n in before}
    current = {n["id"]: n for n in after}
    added = [n for n in after if n["id"] not in previous]
    removed = [n for n in before if n["id"] not in current]
    edited = [n for n in after if n["id"] in previous and previous[n["id"]] != n]
    common = previous.keys() & current
    changed = {
        "added": len(added),
        "removed": len(removed),
        "edited": len(edited),
        "reordered": [k for k in previous if k in common] != [k for k in current if k in common],
    }
    for label, notes in (
        ("added_notes", added),
        ("removed_notes", removed),
        ("edited_notes", edited),
    ):
        if notes:
            changed[label] = [
                {
                    **n,
                    "text": n["text"][:_PREVIEW],
                    **({"truncated": True} if len(n["text"]) > _PREVIEW else {}),
                }
                for n in notes[-_NOTE_LIMIT:]
            ]
    return changed


def read(store, workspace, id, version):
    """Report endpoint differences. Missing or ambiguous historical evidence stays explicit."""
    if type(version) is not int or version < 1:
        raise DomainError("invalid", "The reviewed version must be a positive integer")
    with store._lock:
        if not store._has_schema():
            raise DomainError("not_found", "Entity not found")
        with store._transaction(write=False):
            current = store.get(workspace, id)
            if version > current["version"]:
                raise DomainError(
                    "invalid", "The reviewed version is newer than the current version"
                )
            result = {
                "id": id,
                "kind": current["kind"],
                "from_version": version,
                "to_version": current["version"],
            }
            original = current if version == current["version"] else None
            for raw in _candidates(store, workspace, id, version):
                item = store._validated(raw)  # Historical releases may lack today's default fields.
                if item["created_at"] != current["created_at"] or (
                    original is not None and not _same_value(item, original)
                ):
                    return {**result, "available": False, "reason": "ambiguous_history"}
                original = item
    if original is None:
        return {**result, "available": False, "reason": "missing_history"}
    changes = [
        _change(key, original.get(key), current.get(key))
        for key in sorted((current.keys() | original.keys()) - _BOOKKEEPING)
        if not _same_value(original.get(key), current.get(key))
    ]
    result.update(available=True, fields=changes)
    if original["notes"] != current["notes"]:
        result["notes"] = _note_changes(original["notes"], current["notes"])
    return result
