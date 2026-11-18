"""A page of workspace search hits, ordered by ID and cheap to carry between sessions."""

import json
import sqlite3
import unicodedata

from .models import DomainError
from .views import _usable


def _fold(text):
    return unicodedata.normalize("NFC", text.casefold())


def _check(text, kind, status, scope, after, limit):
    for name, value, maximum in (
        ("text", text, 2000),
        ("kind", kind, 100),
        ("status", status, 30),
        ("scope", scope, 100),
        ("after", after, 128),
    ):
        if value is not None and (not isinstance(value, str) or len(value) > maximum):
            raise DomainError("invalid", f"{name} must be text of at most {maximum} characters")
    if not kind:
        raise DomainError("invalid", "A search needs an entity kind")
    if type(limit) is not int or not 1 <= limit <= 1000:
        raise DomainError("invalid", "Limit must be between 1 and 1000")


def _rows(store, workspace, kind, status, scope, after, search_description):
    sql = " FROM daymark_entities WHERE workspace=? AND kind=? AND archived=0"
    args = [workspace, kind]
    if status is not None:
        sql += " AND status=?"
        args.append(status)
    if after is not None:
        sql += " AND id>?"
        args.append(after)
    sql += " ORDER BY id"
    # Indexable filters use relational columns. Keep note trails and other large
    # JSON fields inside SQLite, even when only a few hits match the text.
    description = "json_extract(data,'$.description')" if search_description else "''"
    columns = "id,kind,status,version,json_extract(data,'$.title'),json_extract(data,'$.scope'),"
    try:
        cursor = store.db.execute("SELECT " + columns + description + sql, args)
    except sqlite3.OperationalError as error:
        if "no such function: json_extract" not in str(error):
            raise
        for (raw,) in store.db.execute("SELECT data" + sql, args):
            item = json.loads(raw)
            if scope is None or (item["scope"] or "") == scope:
                yield (
                    item["id"],
                    item["kind"],
                    item["status"],
                    item["version"],
                    item["title"],
                    item["scope"],
                    item["description"],
                )
        return
    for row in cursor:
        if scope is None or (row[5] or "") == scope:
            yield row


def _matches(rows, needle, limit):
    out = []
    for id, kind, state, version, title, scope, description in rows:
        if needle and needle not in _fold(title) and needle not in _fold(description):
            continue
        if len(out) == limit:
            return {"items": out, "next": out[-1]["id"]}
        hit = {"id": id, "kind": kind, "status": state, "version": version, "title": title}
        if scope:
            hit["scope"] = scope
        out.append(hit)
    return {"items": out, "next": None}


def legacy_page(client, text, *, kind, status, scope, after, limit):
    """An older HTTP server only supplies entity queries. Preserve the public page shape."""
    _check(text, kind, status, scope, after, limit)
    clauses = [{"field": "kind", "op": "eq", "value": kind}]
    if status is not None:
        clauses.append({"field": "status", "op": "eq", "value": status})
    items = client.query({"and": clauses})
    rows = (
        (
            item["id"],
            kind,
            item["status"],
            item["version"],
            item["title"],
            item["scope"],
            item["description"],
        )
        for item in sorted(items, key=lambda x: x["id"])
        if (after is None or item["id"] > after)
        and (scope is None or (item["scope"] or "") == scope)
    )
    return _matches(rows, _fold(text or ""), limit)


def page(
    store, workspace, text=None, *, kind="task", status=None, scope=None, after=None, limit=50
):
    _check(text, kind, status, scope, after, limit)
    needle = _fold(text or "")
    with store._lock:
        if not _usable(store):
            return {"items": [], "next": None}
        rows = _rows(store, workspace, kind, status, scope, after, bool(needle))
        return _matches(rows, needle, limit)
