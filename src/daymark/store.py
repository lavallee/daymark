"""SQLite entity storage, replay receipts, and a fenced reminder outbox.

An application-supplied connection owns its root transaction. Store never commits
or closes it. Path-backed stores serialize writes and commit each operation.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import timedelta

from pydantic import ValidationError

from .models import (
    DomainError,
    Entity,
    Recurrence,
    Temporal,
    next_occurrence,
    normalize_now,
    retry_delay,
)


def canonical(value):
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )


def invalid(message):
    raise DomainError("invalid", message)


# These are workspace-local entity references, not arbitrary metadata links.
# Bumped whenever stored entities or derived tables need an in-place upgrade.
SCHEMA = "2"

REFS = {
    "assignee_id": {"person", "principal"},
    "beneficiary_ids": {"person", "principal"},
    "parent_id": {"task", "project", "goal"},
    "project_id": {"project"},
    "goal_id": {"goal"},
    "label_ids": {"label"},
    "context_ids": {"context"},
    "depends_on": {"task"},
    "task_id": {"task"},
    "recipient_id": {"person", "principal"},
}


class Store:
    def __init__(self, path):
        self._owned = not isinstance(path, sqlite3.Connection)
        self.db = (
            sqlite3.connect(path, check_same_thread=False, timeout=30) if self._owned else path
        )
        self._lock = threading.RLock()
        if self._owned:
            # One durable sync per commit instead of a journal's several, and readers
            # that do not wait on a writer. A caller-owned connection keeps its own mode.
            self.db.execute("PRAGMA journal_mode=WAL")
        # Individual DDL statements do not implicitly commit an existing transaction.
        # Use a root transaction even for schema so caller rollback includes schema.
        with self._transaction():
            pass

    def _ensure_schema(self):
        """Create or upgrade Daymark's tables inside the current transaction."""

        def exists(name):
            return (
                self.db.execute("SELECT 1 FROM sqlite_master WHERE name=?", (name,)).fetchone()
                is not None
            )

        upgrade = exists("daymark_entities")
        for statement in (
            "CREATE TABLE IF NOT EXISTS daymark_entities (workspace TEXT NOT NULL,id TEXT NOT NULL,kind TEXT NOT NULL,version INTEGER NOT NULL,data TEXT NOT NULL,status TEXT,archived INTEGER NOT NULL DEFAULT 0,PRIMARY KEY(workspace,id))",
            "CREATE TABLE IF NOT EXISTS daymark_events (seq INTEGER PRIMARY KEY AUTOINCREMENT,workspace TEXT NOT NULL,operation_id TEXT,entity_id TEXT,action TEXT NOT NULL,before_data TEXT,after_data TEXT,created_at TEXT NOT NULL)",
            "CREATE TABLE IF NOT EXISTS daymark_event_cursors (workspace TEXT NOT NULL,seq INTEGER NOT NULL,event_seq INTEGER NOT NULL UNIQUE,PRIMARY KEY(workspace,seq))",
            "CREATE TABLE IF NOT EXISTS daymark_operations (workspace TEXT NOT NULL,operation_id TEXT NOT NULL,payload_hash TEXT NOT NULL,result TEXT NOT NULL,PRIMARY KEY(workspace,operation_id))",
            "CREATE TABLE IF NOT EXISTS daymark_scheduler (key TEXT PRIMARY KEY,value TEXT NOT NULL)",
            # Derived lookups. Entity JSON stays the authority; these make each write
            # validate against its own references instead of the whole workspace.
            "CREATE TABLE IF NOT EXISTS daymark_refs (workspace TEXT NOT NULL,source_id TEXT NOT NULL,field TEXT NOT NULL,target_id TEXT NOT NULL,PRIMARY KEY(workspace,source_id,field,target_id)) WITHOUT ROWID",
            "CREATE TABLE IF NOT EXISTS daymark_sources (workspace TEXT NOT NULL,system TEXT NOT NULL,kind TEXT NOT NULL,ref_id TEXT NOT NULL,entity_id TEXT NOT NULL,PRIMARY KEY(workspace,system,kind,ref_id)) WITHOUT ROWID",
        ):
            self.db.execute(statement)
        columns = {row[1] for row in self.db.execute("PRAGMA table_info(daymark_entities)")}
        if "status" not in columns:
            self.db.execute("ALTER TABLE daymark_entities ADD COLUMN status TEXT")
            self.db.execute(
                "ALTER TABLE daymark_entities ADD COLUMN archived INTEGER NOT NULL DEFAULT 0"
            )
        if upgrade:
            # Rewrite stored entities in the current shape (new fields take their
            # defaults; versions and history are untouched) and rebuild lookups.
            for (data,) in self.db.execute("SELECT data FROM daymark_entities").fetchall():
                self._put(self._validated(json.loads(data)))
        for statement in (
            "CREATE INDEX IF NOT EXISTS daymark_refs_target ON daymark_refs(workspace,target_id)",
            "CREATE INDEX IF NOT EXISTS daymark_sources_entity ON daymark_sources(workspace,entity_id)",
            "CREATE INDEX IF NOT EXISTS daymark_events_entity ON daymark_events(workspace,entity_id,seq)",
            "CREATE INDEX IF NOT EXISTS daymark_entities_kind ON daymark_entities(kind,workspace,status)",
        ):
            self.db.execute(statement)
        # Written last: its value marks a current schema.
        self.db.execute(
            "INSERT INTO daymark_scheduler VALUES('schema',?) ON CONFLICT(key) "
            "DO UPDATE SET value=excluded.value",
            (SCHEMA,),
        )

    def _current(self):
        if not self._has_schema("daymark_refs"):
            return False
        row = self.db.execute("SELECT value FROM daymark_scheduler WHERE key='schema'").fetchone()
        return row is not None and row[0] == SCHEMA

    def _has_schema(self, name="daymark_entities"):
        return (
            self.db.execute("SELECT 1 FROM sqlite_master WHERE name=?", (name,)).fetchone()
            is not None
        )

    @contextmanager
    def _transaction(self, *, write=True):
        with self._lock:
            if not self.db.in_transaction:
                self.db.execute("BEGIN IMMEDIATE" if self._owned and write else "BEGIN")
            name = "daymark_" + uuid.uuid4().hex
            self.db.execute("SAVEPOINT " + name)
            released = False
            try:
                if not self._current():
                    self._ensure_schema()
                yield
                self.db.execute("RELEASE " + name)
                released = True
                if self._owned:
                    self.db.commit()
            except BaseException:
                if not released:
                    self.db.execute("ROLLBACK TO " + name)
                    self.db.execute("RELEASE " + name)
                if self._owned:
                    self.db.rollback()
                raise

    def close(self):
        if self._owned:
            self.db.close()

    def get(self, workspace, id):
        with self._lock:
            if not self._has_schema():
                raise DomainError("not_found", "Entity not found")
            row = self.db.execute(
                "SELECT data FROM daymark_entities WHERE workspace=? AND id=?", (workspace, id)
            ).fetchone()
            if row is None:
                raise DomainError("not_found", "Entity not found")
            return json.loads(row[0])

    def list(self, workspace, kind=None, include_archived=False):
        with self._lock:
            if not self._has_schema():
                return []
            return self._rows(workspace, kind=kind, include_archived=include_archived)

    def _rows(self, workspace, kind=None, status=None, include_archived=False):
        """Entities by indexed kind and status. ``status`` needs a current schema."""
        sql, args = "SELECT data FROM daymark_entities WHERE workspace=?", [workspace]
        if kind is not None:
            sql, args = sql + " AND kind=?", [*args, kind]
        if status is not None:
            sql += " AND status IN ({})".format(",".join("?" * len(status)))
            args.extend(status)
        items = [json.loads(row[0]) for row in self.db.execute(sql + " ORDER BY id", args)]
        return items if include_archived else [x for x in items if not x["archived"]]

    def _put(self, data):
        self.db.execute(
            "INSERT INTO daymark_entities(workspace,id,kind,version,data,status,archived) "
            "VALUES (?,?,?,?,?,?,?) ON CONFLICT(workspace,id) DO UPDATE SET kind=excluded.kind,"
            "version=excluded.version,data=excluded.data,status=excluded.status,"
            "archived=excluded.archived",
            (
                data["workspace"],
                data["id"],
                data["kind"],
                data["version"],
                canonical(data),
                data["status"],
                int(data["archived"]),
            ),
        )
        self._index(data)

    @staticmethod
    def _targets(item, field):
        value = item[field]
        return value if isinstance(value, list) else ([] if value is None else [value])

    def _unindex(self, workspace, id):
        self.db.execute(
            "DELETE FROM daymark_refs WHERE workspace=? AND source_id=?", (workspace, id)
        )
        self.db.execute(
            "DELETE FROM daymark_sources WHERE workspace=? AND entity_id=?", (workspace, id)
        )

    def _index(self, data):
        workspace, id = data["workspace"], data["id"]
        self._unindex(workspace, id)
        self.db.executemany(
            "INSERT OR IGNORE INTO daymark_refs VALUES(?,?,?,?)",
            [(workspace, id, field, ref) for field in REFS for ref in self._targets(data, field)],
        )
        # A source identity already owned elsewhere keeps its owner; validation reports it.
        self.db.executemany(
            "INSERT OR IGNORE INTO daymark_sources VALUES(?,?,?,?,?)",
            [(workspace, x["system"], x["kind"], x["id"], id) for x in data["source_refs"]],
        )

    def _validated(self, raw):
        try:
            return Entity.model_validate(raw).model_dump(mode="json")
        except ValidationError as exc:
            # Field paths and rule text only: validation input can be a whole private entity.
            details = [
                ".".join(str(part) for part in error["loc"]) + ": " + error["msg"]
                for error in exc.errors(include_input=False, include_url=False)[:20]
            ]
            raise DomainError("invalid", "Invalid entity", details) from exc
        except ValueError as exc:
            raise DomainError("invalid", "Invalid entity") from exc

    def _validate_entity(self, data):
        """Check one changed entity against the workspace using indexed lookups.

        Kinds and IDs are immutable and deletion checks inbound references, so a
        valid workspace stays valid when each change validates only itself.
        """
        workspace, id = data["workspace"], data["id"]
        for field, kinds in REFS.items():
            for ref in self._targets(data, field):
                row = self.db.execute(
                    "SELECT kind FROM daymark_entities WHERE workspace=? AND id=?",
                    (workspace, ref),
                ).fetchone()
                if row is None or row[0] not in kinds:
                    invalid("Reference is missing or has the wrong kind")
                if ref == id:
                    invalid("Self references are not allowed")
        for source in data["source_refs"]:
            row = self.db.execute(
                "SELECT entity_id FROM daymark_sources WHERE workspace=? AND system=? AND kind=? AND ref_id=?",
                (workspace, source["system"], source["kind"], source["id"]),
            ).fetchone()
            if row and row[0] != id:
                raise DomainError("conflict", "Source identity already assigned")
        if data["kind"] == "filter":
            self._compile_filter(data["filter"])
        for field in ("parent_id", "depends_on"):
            seen, stack = set(), list(self._targets(data, field))
            while stack:
                node = stack.pop()
                if node == id:
                    invalid("Cyclic hierarchy or dependencies")
                if node in seen:
                    continue
                seen.add(node)
                stack.extend(
                    row[0]
                    for row in self.db.execute(
                        "SELECT target_id FROM daymark_refs WHERE workspace=? AND source_id=? AND field=?",
                        (workspace, node, field),
                    )
                )

    def _validate_graph(self, workspace):
        """Whole-workspace validation, used when a snapshot arrives in bulk."""
        items = {x["id"]: x for x in self.list(workspace, include_archived=True)}
        if len(items) > 100_000:
            invalid("Workspace validation bound exceeded")
        sources = {}
        for item in items.values():
            for field, kinds in REFS.items():
                value = item[field]
                refs = value if isinstance(value, list) else ([] if value is None else [value])
                for ref in refs:
                    target = items.get(ref)
                    if target is None or target["kind"] not in kinds:
                        invalid("Reference is missing or has the wrong kind")
                    if ref == item["id"]:
                        invalid("Self references are not allowed")
            for source in item["source_refs"]:
                key = (source["system"], source["kind"], source["id"])
                if key in sources and sources[key] != item["id"]:
                    raise DomainError("conflict", "Source identity already assigned")
                sources[key] = item["id"]
            if item["kind"] == "filter":
                self._compile_filter(item["filter"])
        # Iterative three-colour traversal; bounded by entity and reference limits.
        for field in ("parent_id", "depends_on"):
            colors = {}
            for root in items:
                stack = [(root, False)]
                while stack:
                    node, leaving = stack.pop()
                    if leaving:
                        colors[node] = 2
                        continue
                    if colors.get(node) == 1:
                        invalid("Cyclic hierarchy or dependencies")
                    if colors.get(node) == 2:
                        continue
                    colors[node] = 1
                    stack.append((node, True))
                    value = items[node][field]
                    for target in value if isinstance(value, list) else ([value] if value else []):
                        stack.append((target, False))

    def _event(self, workspace, operation_id, action, before, after, *, seq=None, created_at=None):
        if seq is None:
            seq = self.db.execute(
                "SELECT COALESCE(MAX(seq),0)+1 FROM daymark_event_cursors WHERE workspace=?",
                (workspace,),
            ).fetchone()[0]
        cursor = self.db.execute(
            "INSERT INTO daymark_events(workspace,operation_id,entity_id,action,before_data,after_data,created_at) VALUES(?,?,?,?,?,?,?)",
            (
                workspace,
                operation_id,
                (after or before)["id"],
                action,
                canonical(before) if before is not None else None,
                canonical(after) if after is not None else None,
                created_at or normalize_now().isoformat(),
            ),
        )
        self.db.execute(
            "INSERT INTO daymark_event_cursors VALUES(?,?,?)", (workspace, seq, cursor.lastrowid)
        )

    def events(self, workspace, after=0, limit=100):
        if type(after) is not int or after < 0 or type(limit) is not int or not 1 <= limit <= 1000:
            invalid("Invalid event cursor or limit")
        with self._lock:
            if not self._has_schema():
                return []
            rows = self.db.execute(
                "SELECT c.seq,e.workspace,e.operation_id,e.entity_id,e.action,e.before_data,e.after_data,e.created_at FROM daymark_events e JOIN daymark_event_cursors c ON c.event_seq=e.seq WHERE c.workspace=? AND c.seq>? ORDER BY c.seq LIMIT ?",
                (workspace, after, limit),
            ).fetchall()
            keys = (
                "seq",
                "workspace",
                "operation_id",
                "entity_id",
                "action",
                "before_data",
                "after_data",
                "created_at",
            )
            return [
                {
                    **dict(zip(keys, row)),
                    "before_data": json.loads(row[5]) if row[5] else None,
                    "after_data": json.loads(row[6]) if row[6] else None,
                }
                for row in rows
            ]

    def _receipt(self, workspace, operation_id, action, payload, expected_version):
        if (
            not isinstance(workspace, str)
            or not 1 <= len(workspace) <= 128
            or not isinstance(operation_id, str)
            or not 1 <= len(operation_id) <= 200
            or type(expected_version) is not int
            or expected_version < 0
        ):
            invalid("Workspace, operation ID and expected version required")
        digest = hashlib.sha256(canonical([action, payload, expected_version]).encode()).hexdigest()
        row = self.db.execute(
            "SELECT payload_hash,result FROM daymark_operations WHERE workspace=? AND operation_id=?",
            (workspace, operation_id),
        ).fetchone()
        if row and row[0] != digest:
            raise DomainError("conflict", "Operation ID reused with a different request")
        return digest, json.loads(row[1]) if row else None

    def _record(self, workspace, operation_id, digest, result):
        self.db.execute(
            "INSERT INTO daymark_operations VALUES(?,?,?,?)",
            (workspace, operation_id, digest, canonical(result)),
        )

    def execute(self, workspace, operation_id, action, payload, expected_version=None):
        if not isinstance(payload, dict):
            invalid("Payload must be an object")
        with self._transaction():
            digest, replay = self._receipt(
                workspace, operation_id, action, payload, expected_version
            )
            if replay is not None:
                return replay
            now = normalize_now().isoformat()
            before = None
            if action == "create":
                if expected_version != 0:
                    raise DomainError("conflict", "Create requires version zero")
                raw = dict(payload)
                for field, default in {
                    "lease_owner": None,
                    "lease_until": None,
                    "lease_token": None,
                    "attempts": 0,
                    "retry_at": None,
                    "delivery_key": None,
                    "reminder_state": "pending",
                    "error": None,
                }.items():
                    if raw.get(field, default) != default:
                        invalid("Scheduler state must be restored through snapshot import")
                if raw.get("workspace", workspace) != workspace or raw.get("version", 1) != 1:
                    invalid("Invalid creation identity or version")
                raw.update(workspace=workspace, version=1)
                raw.setdefault("id", uuid.uuid4().hex)
                if self.db.execute(
                    "SELECT 1 FROM daymark_entities WHERE workspace=? AND id=?",
                    (workspace, raw["id"]),
                ).fetchone():
                    raise DomainError("conflict", "Entity exists")
                if raw.get("recurrence"):
                    raw.setdefault("series_id", raw["id"])
                    raw.setdefault("occurrence_key", raw["id"] + ":0")
            else:
                if not isinstance(payload.get("id"), str):
                    invalid("Entity ID required")
                if action == "undo":
                    data = self._undo(workspace, payload, expected_version, operation_id, now)
                    self._validate_entity(data)
                    self._record(workspace, operation_id, digest, data)
                    return data
                before = self.get(workspace, payload["id"])
                if before["version"] != expected_version:
                    raise DomainError("conflict", "Version mismatch")
                raw = {**before, "version": before["version"] + 1, "updated_at": now}
                if action == "update":
                    protected = {
                        "id",
                        "workspace",
                        "kind",
                        "version",
                        "created_at",
                        "series_id",
                        "occurrence_key",
                        "lease_owner",
                        "lease_until",
                        "delivery_key",
                        "attempts",
                        "retry_at",
                        "reminder_state",
                        "lease_token",
                        "error",
                        "notes",
                        "claimed_by",
                        "claim_until",
                    }
                    if any(k in protected and v != before.get(k) for k, v in payload.items()):
                        invalid(
                            "Identity, provenance, scheduler, note and claim fields change "
                            "only through their own actions"
                        )
                    if payload.get("status", before["status"]) != before[
                        "status"
                    ] and "proposed" in {
                        payload["status"],
                        before["status"],
                    }:
                        invalid("A proposal changes status only through accept or dismiss")
                    raw.update({k: v for k, v in payload.items() if k not in protected})
                    if not {canonical(x) for x in before["source_refs"]}.issubset(
                        {canonical(x) for x in raw["source_refs"]}
                    ):
                        invalid("Source references are stable")
                elif action in {"accept", "dismiss"}:
                    if set(payload) != {"id"}:
                        invalid("Unexpected action fields")
                    if before["status"] != "proposed":
                        invalid("Only a proposal can be accepted or dismissed")
                    raw["status"] = "open" if action == "accept" else "cancelled"
                elif action in {"complete", "cancel", "reopen", "archive", "skip", "delete"}:
                    if set(payload) - {"id", "completed_at"}:
                        invalid("Unexpected action fields")
                    if before["status"] == "proposed" and action in {"complete", "skip", "reopen"}:
                        invalid("Accept or dismiss a proposal first")
                    if action == "archive":
                        raw["archived"] = True
                    elif action == "complete":
                        raw.update(
                            status="completed", completed_at=payload.get("completed_at", now)
                        )
                    elif action in {"cancel", "skip"}:
                        raw.update(status="cancelled", completed_at=None)
                    elif action == "reopen":
                        raw.update(status="open", completed_at=None, archived=False)
                elif action == "note":
                    if set(payload) - {"id", "text", "author"}:
                        invalid("Unexpected note fields")
                    note_id = uuid.uuid5(
                        uuid.NAMESPACE_URL, canonical([workspace, raw["id"], "note", operation_id])
                    ).hex[:12]
                    raw["notes"] = [
                        *before["notes"],
                        {
                            "id": note_id,
                            "at": now,
                            "author": payload.get("author"),
                            "text": payload.get("text"),
                        },
                    ]
                elif action in {"claim", "release"}:
                    self._claim_action(raw, before, action, payload, now)
                elif action.startswith("reminder."):
                    self._reminder_action(raw, action, payload)
                    if action == "reminder.snooze":
                        raw["delivery_key"] = uuid.uuid5(
                            uuid.NAMESPACE_URL,
                            canonical([workspace, raw["id"], "snooze", operation_id]),
                        ).hex
                        raw["attempts"] = 0
                        raw["error"] = None
                else:
                    invalid("Unsupported action")
            data = self._validated(raw)
            if data["status"] == "completed" and not data["completed_at"]:
                data["completed_at"] = now
            elif data["status"] != "completed":
                data["completed_at"] = None
            if data["status"] in {"completed", "cancelled"} or data["archived"]:
                # Finished or shelved work holds no working claim.
                data.update(claimed_by=None, claim_until=None)
            if action == "delete":
                self._cancel_reminders(workspace, data["id"], operation_id, now, detach=True)
                self.db.execute(
                    "DELETE FROM daymark_entities WHERE workspace=? AND id=?",
                    (workspace, data["id"]),
                )
                self._unindex(workspace, data["id"])
                if self.db.execute(
                    "SELECT 1 FROM daymark_refs WHERE workspace=? AND target_id=? LIMIT 1",
                    (workspace, data["id"]),
                ).fetchone():
                    invalid("Reference is missing or has the wrong kind")
                self._event(workspace, operation_id, action, before, None)
            else:
                self._put(data)
                if before:
                    self._invalidate_reminders(before, data, operation_id, now)
                self._validate_entity(data)
                self._event(workspace, operation_id, action, before, data)
                if data["recurrence"] and (
                    action == "skip"
                    or (
                        data["status"] == "completed"
                        and (before is None or before["status"] != "completed")
                    )
                ):
                    self._successor(data, operation_id)
            self._record(workspace, operation_id, digest, data)
            return data

    def _claim_action(self, raw, before, action, payload, now):
        """Take or give back an expiring working claim; one live holder at a time."""
        if set(payload) - {"id", "owner", "lease_seconds"}:
            invalid("Unexpected claim fields")
        owner, lease = payload.get("owner"), payload.get("lease_seconds", 3600)
        held = before["claimed_by"] is not None and normalize_now(
            before["claim_until"]
        ) > normalize_now(now)
        if action == "release":
            if "lease_seconds" in payload:
                invalid("Unexpected claim fields")
            if owner is not None and held and before["claimed_by"] != owner:
                raise DomainError("conflict", "Task is claimed by another owner")
            raw.update(claimed_by=None, claim_until=None)
            return
        if (
            not isinstance(owner, str)
            or not 1 <= len(owner) <= 200
            or type(lease) is not int
            or not 1 <= lease <= 604_800
        ):
            invalid("Claim requires an owner and a lease of at most seven days")
        if before["kind"] != "task" or before["archived"] or before["status"] != "open":
            invalid("Only open tasks can be claimed")
        if held and before["claimed_by"] != owner:
            raise DomainError("conflict", "Task is claimed by another owner")
        raw.update(
            claimed_by=owner,
            claim_until=(normalize_now(now) + timedelta(seconds=lease)).isoformat(),
        )

    def _undo(self, workspace, payload, expected_version, operation_id, now):
        if set(payload) != {"id"}:
            invalid("Undo requires entity ID")
        row = self.db.execute(
            "SELECT action,before_data,after_data FROM daymark_events WHERE workspace=? AND entity_id=? ORDER BY seq DESC LIMIT 1",
            (workspace, payload["id"]),
        ).fetchone()
        if row is None or row[1] is None:
            invalid("No prior state to restore")
        previous = json.loads(row[1])
        current = self.get(workspace, payload["id"]) if row[2] is not None else None
        version = current["version"] if current else previous["version"] + 1
        if version != expected_version:
            raise DomainError("conflict", "Version mismatch")
        if current and previous["kind"] != current["kind"]:
            # Kinds are immutable; imported history cannot be used to change one.
            invalid("History does not match this entity")
        restored = self._validated({**previous, "version": version + 1, "updated_at": now})
        if current:
            # Notes are append-only and a claim belongs to its holder: undo keeps both.
            restored.update(
                notes=current["notes"],
                claimed_by=current["claimed_by"],
                claim_until=current["claim_until"],
            )
        if restored["status"] in {"completed", "cancelled"} or restored["archived"]:
            restored.update(claimed_by=None, claim_until=None)
        if restored["kind"] == "reminder":
            restored.update(lease_owner=None, lease_until=None, lease_token=None)
            if restored["reminder_state"] == "leased":
                restored["reminder_state"] = "pending"
        self._put(restored)
        if current:
            self._invalidate_reminders(current, restored, operation_id, now)
        self._event(workspace, operation_id, "undo", current, restored)
        return restored

    def _successor(self, data, operation_id):
        recurrence = Recurrence.model_validate(data["recurrence"])
        index = recurrence.occurrence_index + 1
        series = data["series_id"] or data["id"]
        # Identity follows the original slot, independent of later date edits.
        workspace, key = data["workspace"], f"{series}:{index}"
        hashed = uuid.uuid5(uuid.NAMESPACE_URL, canonical([workspace, series, index])).hex

        def slot(candidate):
            """ "ours" if this ID ever held this occurrence, "other" if only
            something unrelated did, None if it has never been used."""
            states = [
                json.loads(text)
                for row in self.db.execute(
                    "SELECT before_data,after_data FROM daymark_events WHERE workspace=? AND entity_id=?",
                    (workspace, candidate),
                )
                for text in row
                if text
            ]
            live = self.db.execute(
                "SELECT data FROM daymark_entities WHERE workspace=? AND id=?",
                (workspace, candidate),
            ).fetchone()
            if live:
                states.append(json.loads(live[0]))
            if any(state.get("occurrence_key") == key for state in states):
                return "ours"
            return "other" if states else None

        # Prefer a readable "<series>.<n>"; a name some unrelated entity uses or
        # used, or an over-long one, leaves the hashed identity. A slot that was
        # ever materialized, even if since deleted, is never materialized again.
        readable = f"{series}.{index}"
        state = slot(readable) if len(readable) <= 128 else "other"
        if state == "ours" or slot(hashed) is not None:
            return
        id = readable if state is None else hashed
        original_row = self.db.execute(
            "SELECT after_data FROM daymark_events WHERE workspace=? AND entity_id=? AND after_data IS NOT NULL AND action IN ('create','recurrence.create') ORDER BY seq DESC LIMIT 1",
            (data["workspace"], data["id"]),
        ).fetchone()
        original = json.loads(original_row[0]) if original_row else data
        if not original.get("recurrence"):
            original = data
        current = Temporal.model_validate(
            original["planned"] or original["deadline"] or original["recurrence"]["anchor"]
        )
        # Skip advances the series at its transition time without recording activity.
        following = next_occurrence(
            recurrence, current, normalize_now(data["completed_at"] or data["updated_at"])
        )
        if following is None:
            return
        raw = {
            **data,
            "id": id,
            "version": 1,
            "archived": False,
            "completed_at": None,
            "created_at": normalize_now().isoformat(),
            "updated_at": normalize_now().isoformat(),
            "series_id": series,
            "occurrence_key": f"{series}:{index}",
            "recurrence": {**data["recurrence"], "occurrence_index": index},
            "source_refs": [],
            "checklist": [{**x, "completed": False} for x in data["checklist"]],
            # Progress notes and waiting state describe one occurrence, not the series.
            "notes": [],
            "status": "open",
            "waiting_on": None,
            "claimed_by": None,
            "claim_until": None,
        }
        field = "planned" if data["planned"] else "deadline" if data["deadline"] else "planned"
        raw[field] = following.model_dump(mode="json")
        child = self._validated(raw)
        self._put(child)
        self._validate_entity(child)
        self._event(data["workspace"], operation_id, "recurrence.create", None, child)

    def _reminder_action(self, raw, action, payload):
        if raw["kind"] != "reminder":
            invalid("Reminder action requires reminder")
        states = {
            "reminder.ack": "acknowledged",
            "reminder.dismiss": "dismissed",
            "reminder.cancel": "cancelled",
            "reminder.snooze": "snoozed",
        }
        if action not in states:
            invalid("Unknown reminder action")
        if action == "reminder.snooze":
            if set(payload) - {"id", "until", "scheduled_at"}:
                invalid("Unexpected snooze fields")
            until = payload.get("until", payload.get("scheduled_at"))
            if until is None:
                invalid("Snooze requires until")
            raw["scheduled_at"] = normalize_now(until).isoformat()
            raw["retry_at"] = None
        elif set(payload) != {"id"}:
            invalid("Unexpected reminder action fields")
        raw.update(
            reminder_state=states[action], lease_owner=None, lease_until=None, lease_token=None
        )

    def _cancel_reminders(self, workspace, task_id, operation_id, now, *, detach=False):
        rows = self.db.execute(
            "SELECT e.data FROM daymark_refs r JOIN daymark_entities e ON e.workspace=r.workspace "
            "AND e.id=r.source_id WHERE r.workspace=? AND r.target_id=? AND r.field='task_id' "
            "ORDER BY e.id",
            (workspace, task_id),
        ).fetchall()
        for item in (json.loads(row[0]) for row in rows):
            active = item["reminder_state"] in {"pending", "leased", "snoozed", "failed"}
            if active or detach:
                updated = {
                    **item,
                    "reminder_state": "cancelled" if active else item["reminder_state"],
                    "task_id": None if detach else item["task_id"],
                    "lease_owner": None,
                    "lease_until": None,
                    "lease_token": None,
                    "version": item["version"] + 1,
                    "updated_at": now,
                }
                self._put(updated)
                # Preserve the deleted task reference in immutable event history.
                self._event(workspace, operation_id, "reminder.invalidate", item, updated)

    def _invalidate_reminders(self, before, after, operation_id, now):
        if before["kind"] == "reminder":
            if after["archived"] or after["status"] not in {"open", "waiting"}:
                after.update(
                    reminder_state="cancelled", lease_owner=None, lease_until=None, lease_token=None
                )
                self._put(after)
            elif after["reminder_state"] == "leased":
                after.update(
                    reminder_state="pending", lease_owner=None, lease_until=None, lease_token=None
                )
                self._put(after)
        elif (
            after["status"] in {"completed", "cancelled"}
            or after["archived"]
            or any(
                before[field] != after[field]
                for field in (
                    "deadline",
                    "planned",
                    "earliest_start",
                    "latest_target",
                    "recurrence",
                )
            )
        ):
            # Explicit reminder times survive harmless task text/provenance edits.
            self._cancel_reminders(before["workspace"], before["id"], operation_id, now)

    def export(self, workspace):
        with self._transaction(write=False):
            events, after = [], 0
            while batch := self.events(workspace, after=after, limit=1000):
                events.extend(batch)
                after = batch[-1]["seq"]
            operations = [
                {
                    "workspace": workspace,
                    "operation_id": row[0],
                    "payload_hash": row[1],
                    "result": json.loads(row[2]),
                }
                for row in self.db.execute(
                    "SELECT operation_id,payload_hash,result FROM daymark_operations WHERE workspace=? ORDER BY operation_id",
                    (workspace,),
                )
            ]
            return {
                "schema_version": 1,
                "workspace": workspace,
                "entities": self.list(workspace, include_archived=True),
                "events": events,
                "operations": operations,
            }

    def import_snapshot(self, workspace, snapshot, *, operation_id, expected_version=None):
        with self._transaction():
            digest, replay = self._receipt(
                workspace, operation_id, "import", snapshot, expected_version
            )
            if replay is not None:
                return replay
            if expected_version != 0:
                raise DomainError("conflict", "Import merge requires version zero")
            if (
                not isinstance(snapshot, dict)
                or snapshot.get("schema_version") != 1
                or snapshot.get("workspace", workspace) != workspace
                or set(snapshot)
                - {"schema_version", "workspace", "entities", "events", "operations"}
            ):
                invalid("Invalid snapshot envelope")
            for field in ("entities", "events", "operations"):
                if (
                    not isinstance(snapshot.get(field, []), list)
                    or len(snapshot.get(field, [])) > 100_000
                ):
                    invalid("Invalid snapshot collection")
            seen = set()
            for raw in snapshot.get("entities", []):
                data = self._validated(raw)
                if data["workspace"] != workspace:
                    invalid("Snapshot cannot reassign workspace")
                if data["id"] in seen:
                    invalid("Duplicate entity ID")
                seen.add(data["id"])
                existing = self.db.execute(
                    "SELECT data FROM daymark_entities WHERE workspace=? AND id=?",
                    (workspace, data["id"]),
                ).fetchone()
                if existing and json.loads(existing[0]) != data:
                    raise DomainError("conflict", "Entity collision")
                if not existing:
                    self._put(data)
            self._validate_graph(workspace)
            seen_events = set()
            for event in snapshot.get("events", []):
                required = {
                    "seq",
                    "workspace",
                    "operation_id",
                    "entity_id",
                    "action",
                    "before_data",
                    "after_data",
                    "created_at",
                }
                if not isinstance(event, dict) or set(event) != required:
                    invalid("Invalid event")
                if (
                    event["workspace"] != workspace
                    or type(event["seq"]) is not int
                    or event["seq"] < 1
                    or event["seq"] in seen_events
                ):
                    invalid("Invalid or duplicate event cursor")
                seen_events.add(event["seq"])
                normalize_now(event["created_at"])
                for key in ("before_data", "after_data"):
                    if event[key] is not None:
                        data = self._validated(event[key])
                        if data["workspace"] != workspace or data["id"] != event["entity_id"]:
                            invalid("Foreign event data")
                if event["before_data"] is None and event["after_data"] is None:
                    invalid("Event requires entity data")
                existing = self.events(workspace, after=event["seq"] - 1, limit=1)
                if existing and existing[0]["seq"] == event["seq"]:
                    if existing[0] != event:
                        raise DomainError("conflict", "Event cursor collision")
                else:
                    self._event(
                        workspace,
                        event["operation_id"],
                        event["action"],
                        event["before_data"],
                        event["after_data"],
                        seq=event["seq"],
                        created_at=event["created_at"],
                    )
            seen_ops = set()
            for operation in snapshot.get("operations", []):
                if (
                    not isinstance(operation, dict)
                    or set(operation) != {"workspace", "operation_id", "payload_hash", "result"}
                    or operation["workspace"] != workspace
                    or not isinstance(operation["operation_id"], str)
                    or not 1 <= len(operation["operation_id"]) <= 200
                    or operation["operation_id"] in seen_ops
                    or not isinstance(operation["payload_hash"], str)
                    or re.fullmatch("[0-9a-f]{64}", operation["payload_hash"]) is None
                ):
                    invalid("Invalid or duplicate operation receipt")
                seen_ops.add(operation["operation_id"])
                result = operation["result"]
                if not isinstance(result, dict):
                    invalid("Invalid operation result")
                if "workspace" in result and result["workspace"] != workspace:
                    invalid("Foreign operation result")
                if "kind" in result:
                    self._validated(result)
                existing = self.db.execute(
                    "SELECT payload_hash,result FROM daymark_operations WHERE workspace=? AND operation_id=?",
                    (workspace, operation["operation_id"]),
                ).fetchone()
                if existing:
                    if (
                        existing[0] != operation["payload_hash"]
                        or json.loads(existing[1]) != result
                    ):
                        raise DomainError("conflict", "Operation receipt collision")
                elif operation["operation_id"] == operation_id:
                    raise DomainError("conflict", "Import ID collides with snapshot receipt")
                else:
                    self._record(
                        workspace, operation["operation_id"], operation["payload_hash"], result
                    )
            result = {
                "entities": len(snapshot.get("entities", [])),
                "events": len(snapshot.get("events", [])),
                "operations": len(snapshot.get("operations", [])),
            }
            self._record(workspace, operation_id, digest, result)
            return result

    def _compile_filter(self, ast):
        """Validate before scanning, with no SQL or user-provided attribute traversal."""
        budget = [0]
        fields = {
            "id",
            "kind",
            "title",
            "description",
            "status",
            "archived",
            "priority",
            "next_move",
            "waiting_on",
            "claimed_by",
            "estimate_minutes",
            "assignee_id",
            "beneficiary_ids",
            "parent_id",
            "project_id",
            "goal_id",
            "label_ids",
            "context_ids",
            "depends_on",
            "task_id",
            "recipient_id",
            "reminder_state",
            "channel",
            "series_id",
            "occurrence_key",
            "version",
            "deadline",
            "planned",
            "earliest_start",
            "latest_target",
            "overdue",
        }
        now = normalize_now()

        def compile_node(node, depth):
            budget[0] += 1
            if depth > 10 or budget[0] > 100 or not isinstance(node, dict):
                invalid("Filter complexity bound exceeded")
            if node == {}:
                return lambda item: True
            if len(node) == 1 and next(iter(node)) in {"and", "or", "not"}:
                key = next(iter(node))
                if key == "not":
                    child = compile_node(node[key], depth + 1)
                    return lambda item: not child(item)
                if not isinstance(node[key], list) or not 1 <= len(node[key]) <= 50:
                    invalid("Logical filters require bounded nonempty lists")
                children = [compile_node(x, depth + 1) for x in node[key]]
                return (
                    (lambda item: all(f(item) for f in children))
                    if key == "and"
                    else (lambda item: any(f(item) for f in children))
                )
            if set(node) != {"field", "op", "value"}:
                invalid("Invalid filter expression")
            field, op, value = node["field"], node["op"], node["value"]
            if (
                not isinstance(field, str)
                or field not in fields
                or not isinstance(op, str)
                or op not in {"eq", "ne", "in", "contains", "lt", "lte", "gt", "gte", "exists"}
            ):
                invalid("Unsupported filter field or operator")
            if op == "in" and (not isinstance(value, list) or len(value) > 100):
                invalid("Membership requires bounded list")
            if op == "exists" and type(value) is not bool:
                invalid("Exists requires boolean")
            if isinstance(value, (dict, list)) and len(canonical(value)) > 10_000:
                invalid("Filter value exceeds bound")

            def test(item):
                if field == "overdue":
                    actual = (
                        item["kind"] == "task"
                        and item["status"] in {"open", "waiting"}
                        and not item["archived"]
                        and item["deadline"] is not None
                        and Temporal.model_validate(item["deadline"]).is_before(now)
                    )
                else:
                    actual = item[field]
                if op == "eq":
                    return actual == value
                if op == "ne":
                    return actual != value
                if op == "in":
                    return actual in value
                if op == "exists":
                    return (actual is not None) == value
                if op == "contains":
                    return (
                        isinstance(actual, (str, list))
                        and isinstance(value, str)
                        and value in actual
                    )
                if actual is None or value is None:
                    return False
                try:
                    if field in {"deadline", "planned", "earliest_start", "latest_target"}:
                        left = Temporal.model_validate(actual).local()
                        right = Temporal.model_validate(value).local()
                    else:
                        left, right = actual, value
                    return {
                        "lt": lambda: left < right,
                        "lte": lambda: left <= right,
                        "gt": lambda: left > right,
                        "gte": lambda: left >= right,
                    }[op]()
                except (TypeError, ValueError):
                    return False

            return test

        return compile_node(ast, 0)

    def query(self, workspace, ast):
        test = self._compile_filter(ast)
        # Top-level kind/status equality narrows the indexed scan; the compiled
        # filter still decides every row, so this changes cost, never results.
        narrowed = {}
        for node in ast.get("and", [ast]) if set(ast) <= {"and"} or "field" in ast else []:
            if (
                isinstance(node, dict)
                and node.get("op") == "eq"
                and node.get("field") in {"kind", "status"}
                and isinstance(node.get("value"), str)
            ):
                narrowed.setdefault(node["field"], node["value"])
        with self._lock:
            if not self._current():
                return [item for item in self.list(workspace) if test(item)]
            status = [narrowed["status"]] if "status" in narrowed else None
            rows = self._rows(workspace, kind=narrowed.get("kind"), status=status)
        return [item for item in rows if test(item)]

    def ready(self, workspace, *, now=None, limit=50, owner=None, next_move=None):
        """Open tasks that can be acted on now, most pressing first. See views.ready."""
        from . import views

        return views.ready(self, workspace, now=now, limit=limit, owner=owner, next_move=next_move)

    def agenda(self, workspace, *, now=None, days=7):
        """Overdue, today, upcoming, waiting and proposed tasks. See views.agenda."""
        from . import views

        return views.agenda(self, workspace, now=now, days=days)

    def context(self, workspace, id, *, history=10):
        """An entity with its blockers, dependents, children, reminders and history."""
        from . import views

        return views.context(self, workspace, id, history=history)

    def summary(self, workspace, *, now=None):
        from . import views

        return views.summary(self, workspace, now=now)

    def receipt(self, workspace, operation_id):
        """The stored result of a completed operation ID, or None."""
        with self._lock:
            if not self._has_schema():
                return None
            row = self.db.execute(
                "SELECT result FROM daymark_operations WHERE workspace=? AND operation_id=?",
                (workspace, operation_id),
            ).fetchone()
            return json.loads(row[0]) if row else None

    def tick(self, now=None):
        now = normalize_now(now)
        with self._transaction():
            self.db.execute(
                "INSERT INTO daymark_scheduler VALUES('tick',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (now.isoformat(),),
            )
            workspaces = [
                row[0]
                for row in self.db.execute(
                    "SELECT DISTINCT workspace FROM daymark_entities WHERE kind='reminder'"
                )
            ]
            cancelled = 0
            for workspace in workspaces:
                for item in self.list(workspace, kind="reminder", include_archived=True):
                    if item["reminder_state"] not in {"pending", "leased", "snoozed", "failed"}:
                        continue
                    if not self._dispatchable(item, dormant=True):
                        before = dict(item)
                        item.update(
                            reminder_state="cancelled",
                            lease_owner=None,
                            lease_until=None,
                            lease_token=None,
                            version=item["version"] + 1,
                            updated_at=now.isoformat(),
                        )
                        self._put(item)
                        self._event(
                            workspace,
                            None,
                            "reminder.invalidate",
                            before,
                            item,
                            created_at=now.isoformat(),
                        )
                        cancelled += 1
            return {"cancelled": cancelled}

    def heartbeat(self, owner, now=None):
        if not isinstance(owner, str) or not 1 <= len(owner) <= 200:
            invalid("Worker owner required")
        now = normalize_now(now)
        with self._transaction():
            # Aggregate timestamp only: no private actor identifiers in health.
            self.db.execute(
                "INSERT INTO daymark_scheduler VALUES('heartbeat',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (now.isoformat(),),
            )
        return {"last_heartbeat": now.isoformat()}

    def _dispatchable(self, reminder, *, dormant=False):
        """Whether a reminder may fire. ``dormant`` also passes one that is only
        waiting for its proposed task to be accepted, so a tick does not cancel it."""
        if reminder["archived"] or reminder["status"] not in {"open", "waiting"}:
            return False
        if reminder["task_id"]:
            try:
                task = self.get(reminder["workspace"], reminder["task_id"])
            except DomainError as exc:
                if exc.code != "not_found":
                    raise
                return False
            if dormant and task["status"] == "proposed" and not task["archived"]:
                return True
            if task["archived"] or task["status"] not in {"open", "waiting"}:
                return False
        return True

    def claim_reminders(self, owner, now=None, lease_seconds=60, limit=50):
        if (
            not isinstance(owner, str)
            or not 1 <= len(owner) <= 200
            or type(lease_seconds) is not int
            or not 1 <= lease_seconds <= 86400
            or type(limit) is not int
            or not 1 <= limit <= 1000
        ):
            invalid("Invalid claim limits or owner")
        now = normalize_now(now)
        with self._transaction():
            reminders = [
                json.loads(row[0])
                for row in self.db.execute(
                    "SELECT data FROM daymark_entities WHERE kind='reminder' ORDER BY workspace,id"
                )
            ]
            candidates = []
            for item in reminders:
                if not self._dispatchable(item):
                    continue
                state = item["reminder_state"]
                if state == "leased":
                    if item["lease_until"] and normalize_now(item["lease_until"]) > now:
                        continue
                elif state not in {"pending", "snoozed", "failed"}:
                    continue
                due = max(
                    normalize_now(item["scheduled_at"]),
                    normalize_now(item["retry_at"])
                    if item["retry_at"]
                    else normalize_now(item["scheduled_at"]),
                )
                if due <= now:
                    candidates.append((due, item["workspace"], item["id"], item))
            claimed = []
            for _, workspace, id, item in sorted(candidates, key=lambda x: x[:3])[:limit]:
                token = uuid.uuid4().hex
                updated = {
                    **item,
                    "reminder_state": "leased",
                    "lease_owner": owner,
                    "lease_token": token,
                    "lease_until": (now + timedelta(seconds=lease_seconds)).isoformat(),
                    "delivery_key": item["delivery_key"]
                    or uuid.uuid5(uuid.NAMESPACE_URL, canonical([workspace, id, "delivery"])).hex,
                    "attempts": item["attempts"] + 1,
                    "version": item["version"] + 1,
                    "updated_at": now.isoformat(),
                }
                self._put(updated)
                self._event(
                    workspace,
                    "lease:" + token,
                    "reminder.claim",
                    item,
                    updated,
                    created_at=now.isoformat(),
                )
                claimed.append(updated)
            return claimed

    def finish_delivery(
        self, workspace, id, owner, success, error=None, now=None, lease_token=None
    ):
        if type(success) is not bool:
            invalid("Delivery success must be boolean")
        now = normalize_now(now)
        with self._transaction():
            if not isinstance(lease_token, str) or not 1 <= len(lease_token) <= 128:
                raise DomainError("conflict", "Lease token required")
            operation_id = "delivery:" + lease_token
            digest, replay = self._receipt(
                workspace,
                operation_id,
                "delivery",
                {"id": id, "owner": owner, "success": success, "error": error},
                0,
            )
            if replay is not None:
                return replay
            item = self.get(workspace, id)
            if (
                not lease_token
                or item["kind"] != "reminder"
                or item["reminder_state"] != "leased"
                or item["lease_owner"] != owner
                or item["lease_token"] != lease_token
                or not item["lease_until"]
                or normalize_now(item["lease_until"]) <= now
                or not self._dispatchable(item)
            ):
                raise DomainError("conflict", "Lease is stale or cancelled")
            if success and item["channel"] != "recording":
                invalid("Unsupported delivery channel")
            if error is not None and (not isinstance(error, str) or len(error) > 2000):
                invalid("Invalid delivery error")
            updated = {
                **item,
                "reminder_state": "delivered" if success else "failed",
                "lease_owner": None,
                "lease_token": None,
                "lease_until": None,
                "error": None if success else error,
                "retry_at": None if success else (now + retry_delay(item["attempts"])).isoformat(),
                "updated_at": now.isoformat(),
                "version": item["version"] + 1,
            }
            self._put(updated)
            self._event(
                workspace,
                "delivery:" + lease_token,
                "reminder.delivered" if success else "reminder.failed",
                item,
                updated,
                created_at=now.isoformat(),
            )
            self._record(workspace, operation_id, digest, updated)
            return updated

    def health(self, workspace=None):
        with self._lock:
            rows = self.db.execute(
                "SELECT data FROM daymark_entities WHERE kind='reminder'"
                + (" AND workspace=?" if workspace is not None else ""),
                (workspace,) if workspace is not None else (),
            ).fetchall()
            counts = {key: 0 for key in ("pending", "leased", "failed", "delivered")}
            for row in rows:
                state = json.loads(row[0])["reminder_state"]
                if state == "snoozed":
                    state = "pending"
                if state in counts:
                    counts[state] += 1
            scheduler = dict(self.db.execute("SELECT key,value FROM daymark_scheduler"))
            return {
                "outbox": counts,
                **{key: counts[key] for key in ("pending", "leased", "failed")},
                "last_tick": scheduler.get("tick"),
                "last_heartbeat": scheduler.get("heartbeat"),
            }
