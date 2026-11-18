"""A page of workspace search hits, ordered by ID and cheap to carry between sessions."""

import json
import sqlite3
import unicodedata

from .models import DomainError, normalize_now
from .views import _usable, claim_holder


def _fold(text):
    return unicodedata.normalize("NFC", text.casefold())


def validate_filters(text, kind, status, scope, after, limit):
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


def _rows(store, workspace, kind, status, scope, after, search_description, claimed_by, now, *, archived):
    sql = " FROM daymark_entities WHERE workspace=? AND kind=? AND archived=?"
    args = [workspace, kind, int(archived)]
    if status is not None:
        sql += " AND status=?"
        args.append(status)
    if after is not None:
        sql += " AND id>?"
        args.append(after)
    # Indexable filters use relational columns. Keep note trails and other large
    # JSON fields inside SQLite, even when only a few hits match the text.
    description = "json_extract(data,'$.description')" if search_description else "''"
    columns = "id,kind,status,version,json_extract(data,'$.title'),json_extract(data,'$.scope'),"
    try:
        claim_fields = (
            ",json_extract(data,'$.claimed_by'),json_extract(data,'$.claim_until')"
            if claimed_by
            else ""
        )
        claim_filter = " AND json_extract(data,'$.claimed_by')=?" if claimed_by else ""
        cursor = store.db.execute(
            "SELECT " + columns + description + claim_fields + sql + claim_filter + " ORDER BY id",
            [*args, claimed_by] if claimed_by else args,
        )
    except sqlite3.OperationalError as error:
        if "no such function: json_extract" not in str(error):
            raise
        for (raw,) in store.db.execute("SELECT data" + sql + " ORDER BY id", args):
            item = json.loads(raw)
            if claimed_by and claim_holder(item, now) != claimed_by:
                continue
            if scope is None or (item["scope"] or "") == scope:
                yield (
                    item["id"],
                    item["kind"],
                    item["status"],
                    item["version"],
                    item["title"],
                    item["scope"],
                    item["description"],
                    *((claimed_by, item["claim_until"]) if claimed_by else ()),
                )
        return
    for row in cursor:
        if claimed_by and claim_holder({"claimed_by": row[7], "claim_until": row[8]}, now) is None:
            continue
        if scope is None or (row[5] or "") == scope:
            yield row


def _matches(rows, needle, limit, *, archived=False):
    out = []
    for id, kind, state, version, title, scope, description, *claim in rows:
        if needle and needle not in _fold(title) and needle not in _fold(description):
            continue
        if len(out) == limit:
            return {"items": out, "next": out[-1]["id"]}
        hit = {"id": id, "kind": kind, "status": state, "version": version, "title": title}
        if archived:
            hit["archived"] = True
        if claim:
            hit.update(claimed_by=claim[0], claim_until=claim[1])
        if scope:
            hit["scope"] = scope
        out.append(hit)
    return {"items": out, "next": None}


def legacy_page(client, text, *, kind, status, scope, after, limit):
    """An older HTTP server only supplies entity queries. Preserve the public page shape."""
    validate_filters(text, kind, status, scope, after, limit)
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
    store,
    workspace,
    text=None,
    *,
    kind="task",
    status=None,
    scope=None,
    after=None,
    limit=50,
    archived=False,
    claimed_by=None,
    now=None,
):
    validate_filters(text, kind, status, scope, after, limit)
    if claimed_by is not None and (
        not isinstance(claimed_by, str) or not 1 <= len(claimed_by) <= 200
    ):
        raise DomainError("invalid", "A claim owner must contain 1 to 200 characters")
    if type(archived) is not bool:
        raise DomainError("invalid", "archived must be true or false")
    if archived and claimed_by is not None:
        raise DomainError("invalid", "Choose either archived records or live claims")
    if claimed_by is not None and kind != "task":
        raise DomainError("invalid", "Only tasks have work claims")
    instant = normalize_now(now) if claimed_by else None
    needle = _fold(text or "")
    with store._lock:
        if _usable(store):
            rows = _rows(
                store, workspace, kind, status, scope, after, bool(needle), claimed_by, instant,
                archived=archived,
            )
            result = _matches(rows, needle, limit, archived=archived)
        else:
            result = {"items": [], "next": None}
    if archived:
        # Even an empty remote page must confirm it searched the archive.
        result["archived"] = True
    return result
