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

from . import plans, ranks
from .models import (
    DomainError,
    Entity,
    Recurrence,
    Temporal,
    next_occurrence,
    normalize_now,
    plain_number,
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
SCHEMA = "5"
# The first release that writes this schema. A file at this schema needs it or newer.
RELEASE = "0.4.0"

REFS = {
    "assignee_id": {"person", "principal"},
    "beneficiary_ids": {"person", "principal"},
    "parent_id": {"task", "project", "goal"},
    "project_id": {"project"},
    "goal_id": {"goal"},
    "label_ids": {"label"},
    "context_ids": {"context"},
    "depends_on": {"task"},
    "milestone_id": {"task"},
    "task_id": {"task"},
    "recipient_id": {"person", "principal"},
}


class Store:
    def __init__(self, path, *, upgrade=True):
        """Open a database path (Store owns its commits) or a caller's connection.

        A database written by an older Daymark is upgraded in place on open, inside
        one transaction; an upgraded file cannot be opened by the older release. With
        ``upgrade=False`` an older or missing schema is refused with ``conflict``
        instead, and nothing is written. A schema newer than this code is always
        refused: Daymark never downgrades a file.
        """
        self._owned = not isinstance(path, sqlite3.Connection)
        self._upgrade = upgrade
        self.db = (
            sqlite3.connect(path, check_same_thread=False, timeout=30) if self._owned else path
        )
        self._lock = threading.RLock()
        self._actor = None  # Who is acting in the current execute; recorded on its events.
        try:
            self._check_schema(upgrading=upgrade)
            if not upgrade:
                return
            if self._owned:
                # One durable sync per commit instead of a journal's several, and readers
                # that do not wait on a writer. A caller-owned connection keeps its own mode.
                self.db.execute("PRAGMA journal_mode=WAL")
            # Individual DDL statements do not implicitly commit an existing transaction.
            # Use a root transaction even for schema so caller rollback includes schema.
            try:
                with self._transaction():
                    pass
            except sqlite3.OperationalError as exc:
                if "readonly" not in str(exc).replace(" ", "").replace("-", ""):
                    raise
                raise DomainError(
                    "conflict",
                    "Stored schema is older than this Daymark and the connection is "
                    "read-only; open it writable once to upgrade it",
                ) from None
        except BaseException:
            self.close()
            raise

    def stored_schema(self):
        """The schema marker in the database: None when it has no Daymark tables (or
        predates the marker), else a string such as "5". ``SCHEMA`` is this code's."""
        with self._lock:
            if not self._has_schema("daymark_scheduler"):
                return None
            row = self.db.execute(
                "SELECT value FROM daymark_scheduler WHERE key='schema'"
            ).fetchone()
            return row[0] if row else None

    def _check_schema(self, *, upgrading):
        """Refuse a schema this code must not touch: newer always, older or missing
        unless upgrading."""
        stored = self.stored_schema()
        if stored is not None and stored.isdigit() and int(stored) > int(SCHEMA):
            raise DomainError(
                "conflict",
                f"This database was written by a newer Daymark (schema {stored}); this is "
                f"daymark-core {RELEASE} (schema {SCHEMA}). Use a newer Daymark.",
            )
        if upgrading or self._current():
            return
        if not self._has_schema():
            raise DomainError("conflict", "No Daymark data in this database; run `daymark init`")
        raise DomainError(
            "conflict",
            f"This database needs an upgrade to schema {SCHEMA} (daymark-core {RELEASE} or "
            "newer); open it once with `daymark` to upgrade it in place",
        )

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
            "CREATE TABLE IF NOT EXISTS daymark_events (seq INTEGER PRIMARY KEY AUTOINCREMENT,workspace TEXT NOT NULL,operation_id TEXT,entity_id TEXT,action TEXT NOT NULL,before_data TEXT,after_data TEXT,created_at TEXT NOT NULL,actor TEXT)",
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
        if "actor" not in {row[1] for row in self.db.execute("PRAGMA table_info(daymark_events)")}:
            self.db.execute("ALTER TABLE daymark_events ADD COLUMN actor TEXT")
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
                    # Reads never upgrade as a side effect, and neither does a store
                    # opened with upgrade=False; writes upgrade (a caller-owned
                    # connection that rolled back the upgrade made at open).
                    self._check_schema(upgrading=write and self._upgrade)
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

    def _event(
        self,
        workspace,
        operation_id,
        action,
        before,
        after,
        *,
        seq=None,
        created_at=None,
        actor=...,
    ):
        if seq is None:
            seq = self.db.execute(
                "SELECT COALESCE(MAX(seq),0)+1 FROM daymark_event_cursors WHERE workspace=?",
                (workspace,),
            ).fetchone()[0]
        cursor = self.db.execute(
            "INSERT INTO daymark_events(workspace,operation_id,entity_id,action,before_data,after_data,created_at,actor) VALUES(?,?,?,?,?,?,?,?)",
            (
                workspace,
                operation_id,
                (after or before)["id"],
                action,
                canonical(before) if before is not None else None,
                canonical(after) if after is not None else None,
                created_at or normalize_now().isoformat(),
                self._actor if actor is ... else actor,
            ),
        )
        self.db.execute(
            "INSERT INTO daymark_event_cursors VALUES(?,?,?)", (workspace, seq, cursor.lastrowid)
        )

    def head(self, workspace):
        """The workspace's latest event cursor; 0 when nothing has happened."""
        with self._lock:
            if not self._has_schema("daymark_event_cursors"):
                return 0
            return self.db.execute(
                "SELECT COALESCE(MAX(seq),0) FROM daymark_event_cursors WHERE workspace=?",
                (workspace,),
            ).fetchone()[0]

    # Kinds a plan can name anywhere in its workspace: as owners, labels or goals.
    _NAMED = ("person", "principal", "label", "goal")

    def _related(self, workspace, project_id):
        """Entities outside a project whose changes alter its plan: what its tasks
        depend on, and its goal."""
        rows = self.db.execute(
            "SELECT DISTINCT r.target_id FROM daymark_refs r JOIN daymark_refs p ON "
            "p.workspace=r.workspace AND p.source_id=r.source_id AND p.field='project_id' "
            "AND p.target_id=? WHERE r.workspace=? AND r.field='depends_on'",
            (project_id, workspace),
        ).fetchall()
        related = {row[0] for row in rows}
        project = self._project(workspace, project_id)
        if project and project["goal_id"]:
            related.add(project["goal_id"])
        return related

    def _touches(self, project_id, related, entity_id, before, after):
        """Whether an event changes what a project's plan shows: the project itself,
        a task entering, leaving or inside it, something its tasks depend on, or a
        person, label or goal it can name."""
        if entity_id == project_id or entity_id in related:
            return True
        for state in (before, after):
            if state and (
                state.get("project_id") == project_id or state.get("kind") in self._NAMED
            ):
                return True
        return False

    def _project_events(self, workspace, project_id, after, limit):
        """Event rows after a cursor that touch a project, prefiltered in SQL."""
        related = sorted(self._related(workspace, project_id))
        needles = [
            '"project_id":' + json.dumps(project_id, ensure_ascii=False),
            *(f'"kind":"{kind}"' for kind in self._NAMED),
        ]
        clause = " OR ".join(
            f"instr(COALESCE(e.{column},''),?)>0"
            for column in ("before_data", "after_data")
            for _ in needles
        )
        ids = ",".join("?" * (len(related) + 1))
        rows = self.db.execute(
            "SELECT c.seq,e.workspace,e.operation_id,e.entity_id,e.action,e.before_data,"
            "e.after_data,e.created_at,e.actor FROM daymark_events e JOIN daymark_event_cursors c "
            f"ON c.event_seq=e.seq WHERE c.workspace=? AND c.seq>? AND (e.entity_id IN ({ids}) "
            f"OR {clause}) ORDER BY c.seq",
            (workspace, after, project_id, *related, *needles, *needles),
        )
        found, related = [], set(related)
        for row in rows:
            before = json.loads(row[5]) if row[5] else None
            after_data = json.loads(row[6]) if row[6] else None
            if self._touches(project_id, related, row[3], before, after_data):
                found.append(row)
                if len(found) >= limit:
                    break
        return found

    def changes(self, workspace, project_id, after):
        """Whether anything in a project's plan changed after an event cursor.

        Returns the workspace's head cursor and ``changed``. A cursor ahead of the
        head (a restored database) reads as changed, so the caller reads again.
        """
        if type(after) is not int or after < 0:
            invalid("Invalid event cursor")
        if not isinstance(project_id, str) or not project_id:
            invalid("Project ID required")
        with self._lock:
            head = self.head(workspace)
            if after > head:
                return {"cursor": head, "changed": True}
            if not self._has_schema():
                return {"cursor": head, "changed": False}
            from . import views

            views._read(self)
            hit = self._project_events(workspace, project_id, after, 1)
            return {"cursor": head, "changed": bool(hit)}

    def events(self, workspace, after=0, limit=100, project=None):
        """An event page after a cursor. ``project`` keeps the events that touch it."""
        if type(after) is not int or after < 0 or type(limit) is not int or not 1 <= limit <= 1000:
            invalid("Invalid event cursor or limit")
        if project is not None and (not isinstance(project, str) or not project):
            invalid("Invalid project filter")
        with self._lock:
            if not self._has_schema():
                return []
            if project is not None:
                rows = self._project_events(workspace, project, after, limit)
            else:
                rows = self.db.execute(
                    "SELECT c.seq,e.workspace,e.operation_id,e.entity_id,e.action,e.before_data,e.after_data,e.created_at,e.actor FROM daymark_events e JOIN daymark_event_cursors c ON c.event_seq=e.seq WHERE c.workspace=? AND c.seq>? ORDER BY c.seq LIMIT ?",
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
                "actor",
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
        try:
            request = canonical([action, payload, expected_version])
        except (TypeError, ValueError):
            invalid(
                "A request is plain JSON: text, finite numbers, true, false, null, lists, objects"
            )
        digest = hashlib.sha256(request.encode()).hexdigest()
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

    def policy(self, workspace):
        """Workspace policy. ``deciders`` lists who may accept or dismiss proposals;
        empty means anyone who can write."""
        with self._lock:
            if not self._has_schema("daymark_scheduler"):
                return {"deciders": []}
            row = self.db.execute(
                "SELECT value FROM daymark_scheduler WHERE key=?", ("policy:" + workspace,)
            ).fetchone()
            return json.loads(row[0]) if row else {"deciders": []}

    def set_policy(self, workspace, *, deciders):
        """Name the actors who may accept or dismiss proposals; an empty list lifts the rule.

        The check is only as strong as the actor name it is given. The HTTP
        service supplies the authenticated principal; a local caller states its own.
        """
        if (
            not isinstance(workspace, str)
            or not 1 <= len(workspace) <= 128
            or not isinstance(deciders, list)
            or len(deciders) > 100
            or any(not isinstance(x, str) or not 1 <= len(x) <= 200 for x in deciders)
        ):
            invalid("Deciders must be a list of at most 100 actor names")
        value = {"deciders": sorted(set(deciders))}
        with self._transaction():
            self.db.execute(
                "INSERT INTO daymark_scheduler VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                ("policy:" + workspace, canonical(value)),
            )
        return value

    def execute(self, workspace, operation_id, action, payload, expected_version=None, actor=None):
        """Apply one operation. ``actor`` names who is acting; it is recorded on the
        resulting events and checked against the workspace policy for decisions."""
        if not isinstance(payload, dict):
            invalid("Payload must be an object")
        if actor is not None and (not isinstance(actor, str) or not 1 <= len(actor) <= 200):
            invalid("Actor must be a short name")
        with self._transaction():
            self._actor = actor
            try:
                return self._execute(workspace, operation_id, action, payload, expected_version)
            finally:
                self._actor = None

    def batch(
        self, workspace, operations, *, atomic=True, actor=None, request_id=None, request=None
    ):
        """Apply up to 100 operations in one transaction, each with its own operation
        ID and expected version, and replayed by that ID like a single operation.

        ``atomic=True`` writes all or nothing: the first refusal rolls back the batch
        and every other operation reports ``aborted``. ``atomic=False`` rolls back only
        the refused operations and commits the rest together. Returns ``committed``
        and one result per operation, in order. A failure outside the domain rules
        (for example a database error) rolls back the batch and is raised.

        ``request_id`` makes the batch idempotent as a unit: a committed batch is
        recorded under it with a digest of ``request`` (the caller's own description
        of what it asked for, defaulting to the operations). The same ID with the same
        request returns the recorded result and writes nothing; with a different
        request it is refused with ``conflict``. Use it when the operations are derived
        from state that may change between an attempt and its retry.
        """
        if not isinstance(operations, list) or not 1 <= len(operations) <= 100:
            invalid("A batch holds 1 to 100 operations")
        if type(atomic) is not bool:
            invalid("Atomic must be a boolean")
        if actor is not None and (not isinstance(actor, str) or not 1 <= len(actor) <= 200):
            invalid("Actor must be a short name")
        keys = {"operation_id", "action", "payload", "expected_version"}
        for operation in operations:
            if (
                not isinstance(operation, dict)
                or set(operation) != keys
                or not isinstance(operation["operation_id"], str)
                or not isinstance(operation["payload"], dict)
                or not isinstance(operation["action"], str)
            ):
                invalid("Each operation needs operation_id, action, payload and expected_version")
        if len({op["operation_id"] for op in operations}) != len(operations):
            invalid("Operation IDs in a batch must differ")
        if request_id is not None:
            if request_id in {op["operation_id"] for op in operations}:
                invalid("The request ID must differ from its operation IDs")
            try:
                canonical(operations if request is None else request)
            except (TypeError, ValueError):
                invalid("The request must be plain JSON")

        class Abort(Exception):
            pass

        results = []
        try:
            with self._transaction():
                if request_id is not None:
                    digest, replay = self._receipt(
                        workspace,
                        request_id,
                        "batch",
                        operations if request is None else request,
                        0,
                    )
                    if replay is not None:
                        return {**replay, "replayed": True}
                self._actor = actor
                try:
                    for operation in operations:
                        name = "daymark_" + uuid.uuid4().hex
                        self.db.execute("SAVEPOINT " + name)
                        try:
                            result = self._execute(
                                workspace,
                                operation["operation_id"],
                                operation["action"],
                                operation["payload"],
                                operation["expected_version"],
                            )
                        except DomainError as exc:
                            self.db.execute("ROLLBACK TO " + name)
                            self.db.execute("RELEASE " + name)
                            results.append(
                                {
                                    "operation_id": operation["operation_id"],
                                    "ok": False,
                                    "error": {
                                        "code": exc.code,
                                        "message": str(exc),
                                        "details": exc.details,
                                    },
                                }
                            )
                            if atomic:
                                raise Abort from None
                            continue
                        self.db.execute("RELEASE " + name)
                        results.append(
                            {
                                "operation_id": operation["operation_id"],
                                "ok": True,
                                "result": result,
                            }
                        )
                finally:
                    self._actor = None
                outcome = {"committed": True, "results": results}
                if request_id is not None:
                    self._record(workspace, request_id, digest, outcome)
                return outcome
        except Abort:
            aborted = {"code": "aborted", "message": "Rolled back with the batch", "details": []}
            done = {r["operation_id"]: r for r in results if not r["ok"]}
            results = [
                done.get(op["operation_id"])
                or {"operation_id": op["operation_id"], "ok": False, "error": aborted}
                for op in operations
            ]
            return {"committed": False, "results": results}

    def plan(self, workspace, project_id, *, now=None):
        """A project as a plan, read in one transaction with the head event cursor.
        See views.plan."""
        from . import views

        return views.plan(self, workspace, project_id, now=now)

    def goal_plan(self, workspace, goal_id, *, now=None):
        """A goal with its roll-up across linked projects and tasks. See views.goal_plan."""
        from . import views

        return views.goal_plan(self, workspace, goal_id, now=now)

    def goals(self, workspace, *, now=None):
        """Every unarchived goal with its roll-up. See views.goals."""
        from . import views

        return views.goals(self, workspace, now=now)

    def tree(self, workspace, id, *, depth=3):
        """An entity and its child tasks in manual order, to a depth. See views.tree."""
        from . import views

        return views.tree(self, workspace, id, depth=depth)

    def projects(self, workspace, *, now=None):
        """Every unarchived project in a workspace with its roll-up. See views.projects."""
        from . import views

        return views.projects(self, workspace, now=now)

    def _execute(self, workspace, operation_id, action, payload, expected_version):
        digest, replay = self._receipt(workspace, operation_id, action, payload, expected_version)
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
            if raw.get("rank") is not None:
                invalid("A task's place in its project's order is set by the place action")
            if "created_at" in payload:
                # It orders tasks never placed; history is restored by snapshot import.
                invalid("created_at is set by Daymark; restore history through snapshot import")
            if raw.get("stage") is not None:
                self._move_to_stage(raw, None, payload)
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
                    "rank",
                }
                if any(k in protected and v != before.get(k) for k, v in payload.items()):
                    invalid(
                        "Identity, provenance, scheduler, note, claim and order fields "
                        "change only through their own actions"
                    )
                if payload.get("status", before["status"]) != before["status"] and "proposed" in {
                    payload["status"],
                    before["status"],
                }:
                    invalid("A proposal changes status only through accept or dismiss")
                raw.update({k: v for k, v in payload.items() if k not in protected})
                if isinstance(payload.get("values"), dict):
                    # Values merge per field: name only what changes; null clears one.
                    merged = {**before.get("values", {}), **payload["values"]}
                    raw["values"] = {k: v for k, v in merged.items() if v is not None}
                if payload.get("stage") is not None:
                    self._move_to_stage(raw, before, payload)
                if not {canonical(x) for x in before["source_refs"]}.issubset(
                    {canonical(x) for x in raw["source_refs"]}
                ):
                    invalid("Source references are stable")
            elif action in {"accept", "dismiss"}:
                if set(payload) != {"id"}:
                    invalid("Unexpected action fields")
                if before["status"] != "proposed":
                    invalid("Only a proposal can be accepted or dismissed")
                deciders = self.policy(workspace)["deciders"]
                if deciders and self._actor not in deciders:
                    raise DomainError(
                        "forbidden", "Only a designated decider can accept or dismiss"
                    )
                raw["status"] = "open" if action == "accept" else "cancelled"
            elif action in {"complete", "cancel", "reopen", "archive", "skip", "delete"}:
                if set(payload) - {"id", "completed_at"}:
                    invalid("Unexpected action fields")
                if before["status"] == "proposed" and action in {"complete", "skip", "reopen"}:
                    invalid("Accept or dismiss a proposal first")
                if action == "archive":
                    raw["archived"] = True
                elif action == "complete":
                    raw.update(status="completed", completed_at=payload.get("completed_at", now))
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
            elif action == "place":
                keys = set(payload) - {"id"}
                if len(keys) != 1 or keys - {"after", "before"}:
                    invalid("Place needs exactly one of after or before")
                if before["kind"] != "task" or not before["project_id"]:
                    invalid("Only a task in a project has a place in its order")
                raw["rank"] = self._place(before, payload, operation_id, now)
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
        if action != "delete":
            self._settle_stage(raw, before, payload, operation_id, now)
        if action in {"create", "update"}:
            self._check_structure(raw, before)
        data = self._validated(raw)
        if data["status"] == "completed" and not data["completed_at"]:
            data["completed_at"] = now
        elif data["status"] != "completed":
            data["completed_at"] = None
        if data["status"] in {"completed", "cancelled"} or data["archived"]:
            # Finished or shelved work holds no working claim and no place in focus.
            data.update(claimed_by=None, claim_until=None, focus=None)
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
                self._restage(before, data, operation_id, now)
                self._detach_members(before, data, operation_id, now)
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

    # -- projects: stages and manual order (rules in plans.py)

    def _project(self, workspace, id):
        if not id:
            return None
        row = self.db.execute(
            "SELECT data FROM daymark_entities WHERE workspace=? AND id=? AND kind='project'",
            (workspace, id),
        ).fetchone()
        return json.loads(row[0]) if row else None

    def _project_tasks(self, workspace, project_id):
        """Every task in a project, archived included, through the reference index."""
        rows = self.db.execute(
            "SELECT e.data FROM daymark_refs r JOIN daymark_entities e ON "
            "e.workspace=r.workspace AND e.id=r.source_id WHERE r.workspace=? AND "
            "r.target_id=? AND r.field='project_id' AND e.kind='task' ORDER BY e.id",
            (workspace, project_id),
        ).fetchall()
        return [json.loads(row[0]) for row in rows]

    def _move_to_stage(self, raw, before, payload):
        """An explicit stage sets the task's status to the stage's status."""
        project = self._project(raw["workspace"], raw.get("project_id"))
        if project is None:
            invalid("A stage requires the task to be in a project")
        stage = next((s for s in plans.stages(project) if s["id"] == raw["stage"]), None)
        if stage is None:
            invalid("Unknown stage for this project")
        if (before or raw).get("status") == "proposed":
            invalid("Accept or dismiss a proposal first")
        if "status" in payload and payload["status"] != stage["status"]:
            invalid("Stage and status disagree")
        raw["status"] = stage["status"]

    def _settle_stage(self, raw, before, payload, operation_id=None, now=None):
        """Keep a task's stored stage consistent with its status and project.

        A task that changes project loses its stage unless the same write sets one,
        and goes last in its new project's order; a stored stage that is gone or no
        longer fits the status is cleared, so the task reads in its status's home
        stage.
        """
        if raw.get("kind") != "task":
            return
        if before is not None and before["project_id"] != raw.get("project_id"):
            if "stage" not in payload:
                raw["stage"] = None
            raw["rank"] = (
                self._rank_after(
                    None, raw["workspace"], raw["project_id"], raw["id"], operation_id, now
                )
                if raw.get("project_id")
                else None
            )
        if not raw.get("stage"):
            return
        project = self._project(raw["workspace"], raw.get("project_id"))
        board = plans.stages(project)
        stage = next((s for s in board if s["id"] == raw["stage"]), None)
        status = raw.get("status", "open")
        if (
            project is None
            or stage is None
            or status == "proposed"
            or not plans.compatible(stage, status, board)
        ):
            raw["stage"] = None

    def _check_structure(self, raw, before):
        """Check what this write changes in a task's type, custom values and milestone
        against its project. Values already stored are kept even when their declaration
        has since gone: the plan reports them as warnings instead."""
        if raw.get("kind") != "task":
            return
        old = before or {}
        project = self._project(raw["workspace"], raw.get("project_id"))
        moved = before is not None and before["project_id"] != raw.get("project_id")
        if raw.get("type") is not None and raw["type"] != old.get("type"):
            declared = [t["id"] for t in (project or {}).get("types", [])]
            if raw["type"] not in declared:
                raise DomainError(
                    "invalid",
                    "Unknown type for this project"
                    if declared
                    else "This project declares no types",
                    declared,
                )
        values = {k: v for k, v in (raw.get("values") or {}).items() if v is not None}
        raw["values"] = values
        previous = old.get("values") or {}
        changed = [k for k, v in values.items() if previous.get(k) != v]
        if changed:
            declared = {f["id"]: f for f in (project or {}).get("fields", [])}
            for key in changed:
                if key not in declared:
                    raise DomainError(
                        "invalid",
                        "Unknown field for this project"
                        if declared
                        else "This project declares no fields",
                        sorted(declared),
                    )
                self._check_value(raw["workspace"], declared[key], values[key])
        if (
            raw.get("milestone")
            and raw.get("milestone_id")
            and (raw["milestone_id"] != old.get("milestone_id") or not old.get("milestone"))
        ):
            invalid("A milestone does not belong to a milestone")
        if moved and raw.get("milestone_id") == old.get("milestone_id"):
            raw["milestone_id"] = None  # Membership is part of the project left behind.
        target = raw.get("milestone_id")
        if target is not None and (target != old.get("milestone_id") or moved):
            row = self.db.execute(
                "SELECT data FROM daymark_entities WHERE workspace=? AND id=?",
                (raw["workspace"], target),
            ).fetchone()
            milestone = json.loads(row[0]) if row else None
            if (
                milestone is None
                or not milestone.get("milestone")
                or milestone["project_id"] is None
                or milestone["project_id"] != raw.get("project_id")
            ):
                invalid("A task belongs to a milestone task in its own project")

    def _check_value(self, workspace, field, value):
        kind, name = field["type"], field["id"]
        ok = {
            "text": isinstance(value, str),
            "number": plain_number(value),
            "bool": isinstance(value, bool),
            "option": isinstance(value, str) and value in {o["id"] for o in field["options"]},
            "options": isinstance(value, list)
            and len(set(value)) == len(value)
            and set(value) <= {o["id"] for o in field["options"]},
            "date": isinstance(value, str) and plans.is_civil_date(value),
            "url": isinstance(value, str)
            and re.fullmatch(r"https?://\S{1,2000}", value) is not None,
            "person": isinstance(value, str),
        }[kind]
        if ok and kind == "person":
            row = self.db.execute(
                "SELECT kind FROM daymark_entities WHERE workspace=? AND id=?", (workspace, value)
            ).fetchone()
            ok = row is not None and row[0] in {"person", "principal"}
        if not ok:
            choices = [o["id"] for o in field["options"]]
            raise DomainError("invalid", f"Field {name} takes {plans.FIELD_HINTS[kind]}", choices)

    def _restage(self, before, project, operation_id, now):
        """After a project's stages change, clear task stages that no longer fit."""
        if project["kind"] != "project" or plans.stages(before) == plans.stages(project):
            return
        board = plans.stages(project)
        for task in self._project_tasks(project["workspace"], project["id"]):
            stage = next((s for s in board if s["id"] == task["stage"]), None)
            if task["stage"] is None or (
                stage is not None and plans.compatible(stage, task["status"], board)
            ):
                continue
            updated = {**task, "stage": None, "version": task["version"] + 1, "updated_at": now}
            self._put(updated)
            self._event(project["workspace"], operation_id, "stage.reset", task, updated)

    def _ordered(self, workspace, project_id, exclude=None):
        """A project's tasks in manual order, archived ones included so that a new
        key never collides with the place an archived task will return to."""
        return sorted(
            (x for x in self._project_tasks(workspace, project_id) if x["id"] != exclude),
            key=plans.order_key,
        )

    def _rekey(self, item, key, operation_id, now):
        if item.get("rank") == key:
            return
        updated = {**item, "rank": key, "version": item["version"] + 1, "updated_at": now}
        self._put(updated)
        self._event(item["workspace"], operation_id, "rank.assign", item, updated)

    def _key_at(self, others, index, operation_id, now):
        """The key for a task inserted at ``index`` of ``others`` (an ordered list
        without it). Only that task is written, unless its neighbours share a key
        (then the tied tasks after it are re-keyed) or the key would grow past
        ``ranks.REBALANCE_AT`` (then the whole project is re-keyed, evenly)."""
        keys = [plans.effective_rank(x) for x in others]
        low = keys[index - 1] if index > 0 else None
        high = keys[index] if index < len(keys) else None
        try:
            if low is not None and high is not None and low >= high:
                end = index
                while end < len(keys) and keys[end] <= low:
                    end += 1
                upper = keys[end] if end < len(keys) else None
                fresh, previous = [], low
                for _ in range(end - index + 1):
                    previous = ranks.rank_between(previous, upper)
                    fresh.append(previous)
                if max(len(k) for k in fresh) <= ranks.REBALANCE_AT:
                    for item, key in zip(others[index:end], fresh[1:]):
                        self._rekey(item, key, operation_id, now)
                    return fresh[0]
            else:
                key = ranks.rank_between(low, high)
                if len(key) <= ranks.REBALANCE_AT:
                    return key
            spread = ranks.spaced(len(others) + 1)
        except ValueError:
            invalid("This project's order keys are damaged; place a task first or last")
        for position, item in enumerate(others):
            self._rekey(item, spread[position + (position >= index)], operation_id, now)
        return spread[index]

    def _place(self, task, payload, operation_id, now):
        """The rank that puts ``task`` directly after or before a neighbour.

        ``after: None`` is first and ``before: None`` is last. Order is total:
        by key, then by ID. Archived tasks keep their places.
        """
        others = self._ordered(task["workspace"], task["project_id"], task["id"])
        ids = [x["id"] for x in others]
        key = "after" if "after" in payload else "before"
        ref = payload[key]
        if ref is None:
            index = 0 if key == "after" else len(others)
        else:
            live = {x["id"] for x in others if not x["archived"]}
            if not isinstance(ref, str) or ref not in live:
                invalid("The neighbour must be another task in the same project")
            index = ids.index(ref) + (key == "after")
        return self._key_at(others, index, operation_id, now)

    def _rank_after(self, task, workspace, project_id, exclude, operation_id, now):
        """The key directly after ``task`` (None: last) in a project's order."""
        others = self._ordered(workspace, project_id, exclude)
        if task is None:
            return self._key_at(others, len(others), operation_id, now)
        index = next((i for i, x in enumerate(others) if x["id"] == task["id"]), len(others) - 1)
        return self._key_at(others, index + 1, operation_id, now)

    def _untie(self, task, operation_id, now):
        """After an undo restores an old key, move the task just past any task that
        now holds the same key, so no two tasks share one."""
        if task["kind"] != "task" or not task["project_id"] or not task["rank"]:
            return
        others = self._ordered(task["workspace"], task["project_id"], task["id"])
        if all(plans.effective_rank(x) != task["rank"] for x in others):
            return
        index = sum(1 for x in others if plans.effective_rank(x) <= task["rank"])
        task["rank"] = self._key_at(others, index, operation_id, now)

    def _undo(self, workspace, payload, expected_version, operation_id, now):
        if set(payload) != {"id"}:
            invalid("Undo requires entity ID")
        row = self.db.execute(
            "SELECT action,before_data,after_data,operation_id FROM daymark_events WHERE workspace=? AND entity_id=? ORDER BY seq DESC LIMIT 1",
            (workspace, payload["id"]),
        ).fetchone()
        if row is None or row[1] is None:
            invalid("No prior state to restore")
        undone = row[3]
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
        # A stage the project has since removed falls back to the status's home stage.
        self._settle_stage(restored, None, {})
        self._untie(restored, operation_id, now)
        if restored["kind"] == "reminder":
            restored.update(lease_owner=None, lease_until=None, lease_token=None)
            if restored["reminder_state"] == "leased":
                restored["reminder_state"] = "pending"
        self._put(restored)
        if current:
            self._invalidate_reminders(current, restored, operation_id, now)
            self._restage(current, restored, operation_id, now)
            if restored["kind"] == "project" and undone:
                self._restore_stages(workspace, undone, operation_id, now)
        self._event(workspace, operation_id, "undo", current, restored)
        return restored

    def _detach_members(self, before, data, operation_id, now):
        """A milestone that moves to another project leaves its members behind: each
        loses its membership, visibly, in the same transaction."""
        if data["kind"] != "task" or before["project_id"] == data["project_id"]:
            return
        rows = self.db.execute(
            "SELECT e.data FROM daymark_refs r JOIN daymark_entities e ON e.workspace=r.workspace "
            "AND e.id=r.source_id WHERE r.workspace=? AND r.target_id=? AND r.field='milestone_id'",
            (data["workspace"], data["id"]),
        ).fetchall()
        for (text,) in rows:
            member = json.loads(text)
            if member["project_id"] == data["project_id"]:
                continue
            updated = {**member, "milestone_id": None, "version": member["version"] + 1,
                       "updated_at": now}  # fmt: skip
            self._put(updated)
            self._event(data["workspace"], operation_id, "milestone.detach", member, updated)

    def _restore_stages(self, workspace, undone, operation_id, now):
        """Undoing a project's configuration change also puts back the task stages
        that change cleared, for each task nobody has written since."""
        rows = self.db.execute(
            "SELECT before_data,after_data FROM daymark_events WHERE workspace=? AND "
            "operation_id=? AND action='stage.reset' ORDER BY seq",
            (workspace, undone),
        ).fetchall()
        for before_text, after_text in rows:
            before, after = json.loads(before_text), json.loads(after_text)
            try:
                task = self.get(workspace, after["id"])
            except DomainError:
                continue
            if task["version"] != after["version"]:
                continue  # Written since: leave it as it is now.
            updated = {**task, "stage": before["stage"], "version": task["version"] + 1,
                       "updated_at": now}  # fmt: skip
            self._settle_stage(updated, None, {})
            if updated["stage"] == task["stage"]:
                continue
            self._put(updated)
            self._event(workspace, operation_id, "stage.restore", task, updated)

    def _successor(self, data, operation_id):
        now = normalize_now().isoformat()
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
            # A new round starts in its project's first open stage, directly after
            # the round it follows. Type, custom values, milestone marker and
            # milestone membership belong to the series and carry over.
            "stage": None,
            "rank": (
                self._rank_after(data, data["workspace"], data["project_id"], id, operation_id, now)
                if data["project_id"]
                else None
            ),
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
                event = {"actor": None, **event} if isinstance(event, dict) else event
                required = {
                    "actor",
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
                    or not (
                        event["actor"] is None
                        or (isinstance(event["actor"], str) and 1 <= len(event["actor"]) <= 200)
                    )
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
                        actor=event["actor"],
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
            "focus",
            "scope",
            "stage",
            "rank",
            "milestone",
            "managed_by",
            "type",
            "milestone_id",
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
                or (
                    field not in fields
                    and not re.fullmatch(r"values\.[a-z0-9][a-z0-9_-]{0,63}", field)
                )
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
                    actual = (
                        item.get("values", {}).get(field[7:])
                        if field.startswith("values.")
                        else item[field]
                    )
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

    def ready(self, workspace, *, now=None, limit=50, owner=None, next_move=None, scope=None):
        """Open tasks that can be acted on now, most pressing first. See views.ready."""
        from . import views

        return views.ready(
            self, workspace, now=now, limit=limit, owner=owner, next_move=next_move, scope=scope
        )

    def agenda(self, workspace, *, now=None, days=7, scope=None):
        """Overdue, today, upcoming, waiting and proposed tasks. See views.agenda."""
        from . import views

        return views.agenda(self, workspace, now=now, days=days, scope=scope)

    def workspaces(self):
        """Every workspace in this database with its entity count. Local callers only:
        the HTTP service never lists workspaces other than the caller's own."""
        with self._lock:
            if not self._has_schema():
                return []
            rows = self.db.execute(
                "SELECT workspace, count(*) FROM daymark_entities GROUP BY workspace ORDER BY workspace"
            ).fetchall()
            return [{"workspace": row[0], "entities": row[1]} for row in rows]

    def context(self, workspace, id, *, history=10):
        """An entity with its blockers, dependents, children, reminders and history."""
        from . import views

        return views.context(self, workspace, id, history=history)

    def summary(self, workspace, *, now=None, scope=None):
        from . import views

        return views.summary(self, workspace, now=now, scope=scope)

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
