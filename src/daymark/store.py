"""SQLite entity storage, replay receipts, and a fenced reminder outbox.

An application-supplied connection owns its root transaction. Store never commits
or closes it. Path-backed stores serialize writes and commit each operation.
"""

from __future__ import annotations

import hashlib
import heapq
import json
import re
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import timedelta
from pathlib import Path

from pydantic import ValidationError

from . import __version__, cursors, outbox, plans, project_feed, ranks
from .models import (
    DomainError,
    Entity,
    Recurrence,
    SourceRef,
    Temporal,
    next_occurrence,
    normalize_now,
    plain_number,
    recurrence_date_field,
    require_active_round,
    require_visible_proposal,
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
SCHEMA = "6"
# The first release that writes this schema. A file at this schema needs it or newer.
RELEASE = "0.5.0"
STALE = (
    f"This database needs an upgrade to schema {SCHEMA}: open it once with daymark-core "
    f"{RELEASE} or newer (for example, `daymark --db FILE init`), and commit the upgrade if you own "
    "the connection"
)
TRACKING_STALE = (
    "This database needs setting revision tracking: open it with this Daymark build "
    "in writable mode, and commit the upgrade if you own the connection"
)

# v0.5.0 also writes schema 6. Both versions write the same scheduler rows,
# so the database must own the counter once revision tracking is installed.
SETTING_TRIGGERS = (
    ("daymark_setting_insert", "INSERT", "NEW", "1", ""),
    ("daymark_setting_update", "UPDATE OF value", "NEW", "2", " AND OLD.value <> NEW.value"),
    ("daymark_setting_delete", "DELETE", "OLD", "2", ""),
)

REFS = {
    "assignee_id": {"person", "principal"},
    "beneficiary_ids": {"person", "principal"},
    "parent_id": {"task", "project", "goal"},
    "project_id": {"project"},
    "goal_id": {"goal"},
    "label_ids": {"label"},
    "context_ids": {"context"},
    # A task or condition can wait on tasks and on conditions being met.
    "depends_on": {"task", "condition"},
    "advances": {"condition"},
    "milestone_id": {"task"},
    "task_id": {"task"},
    "recipient_id": {"person", "principal"},
}


class Store:
    def __init__(self, path, *, upgrade=True, read_only=False):
        """Open a database path (Store owns its commits) or a caller's connection.

        A database written by an older Daymark is upgraded in place on open, inside
        one transaction. Older releases may refuse the resulting schema. With
        ``upgrade=False`` an older or missing schema is refused with ``conflict``
        instead, and nothing is written. A schema newer than this code is always
        refused: Daymark never downgrades a file. With `read_only=True`, a path
        opens via SQLite mode=ro and local reads see committed updates.
        """
        self._owned = not isinstance(path, sqlite3.Connection)
        if read_only:
            if not self._owned:
                raise DomainError("invalid", "Open a caller-owned connection with SQLite mode=ro and upgrade=False")
            if not Path(path).is_file():
                raise DomainError("no_database", "A read-only session needs an existing database")
            path = Path(path).resolve().as_uri() + "?mode=ro"
            upgrade = False
        self._upgrade = upgrade
        self.db = (
            sqlite3.connect(path, check_same_thread=False, timeout=30, uri=read_only) if self._owned else path
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
                    self._require_setting_tracking(install=True)
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
                f"daymark-core {__version__} (schema {SCHEMA}). Use a newer Daymark.",
            )
        if upgrading or self._current():
            return
        if not self._has_schema():
            raise DomainError("conflict", "No Daymark data in this database; run `daymark init`")
        raise DomainError("conflict", STALE)

    def _refuse_stale(self):
        """Reads never interpret an older stored shape; they refuse with the one
        message every surface uses. An old schema holding no entities has nothing to
        misread and lists as empty."""
        if (
            self._has_schema()
            and not self._current()
            and self.db.execute("SELECT 1 FROM daymark_entities LIMIT 1").fetchone()
        ):
            self._check_schema(upgrading=False)

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

    def _require_setting_tracking(self, *, install=False):
        # Entity-only reads can open a v0.5 file without changing its schema.
        tracked = self.db.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='trigger' AND name IN (?, ?, ?)",
            tuple(name for name, *_ in SETTING_TRIGGERS),
        ).fetchone()[0]
        if tracked == len(SETTING_TRIGGERS):
            return
        if not install or not self._upgrade:
            raise DomainError("conflict", TRACKING_STALE)
        for name, action, row, initial, condition in SETTING_TRIGGERS:
            # A pre-tracking row without a revision already reads as version one.
            # Keep deletion revisions so an absent value cannot return to version zero.
            self.db.execute(
                f"CREATE TRIGGER IF NOT EXISTS {name} AFTER {action} ON daymark_scheduler "
                f"WHEN substr({row}.key, 1, 8) = 'setting:'{condition} BEGIN "
                f"INSERT INTO daymark_scheduler VALUES('revision:' || {row}.key, {initial}) "
                "ON CONFLICT(key) DO UPDATE SET value=CAST(value AS INTEGER) + 1; END"
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
            started = not self.db.in_transaction
            if started:
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
                if self._owned and started:
                    self.db.commit()
            except BaseException as exc:
                if not released:
                    self.db.execute("ROLLBACK TO " + name)
                    self.db.execute("RELEASE " + name)
                if self._owned and started:
                    self.db.rollback()
                if isinstance(exc, sqlite3.OperationalError) and getattr(exc, "sqlite_errorname", "") == "SQLITE_BUSY_SNAPSHOT":
                    raise DomainError(
                        "conflict", "Another writer committed after this SQLite snapshot began",
                        ["End the outer transaction, read current state, then retry the original operation."],
                    ) from None
                raise

    @contextmanager
    def read_snapshot(self):
        """Group public reads without committing a caller's transaction or upgrading a database."""
        with self._lock:
            # A caller may roll back its initial schema. Its empty reads remain nonwriting.
            if not self._has_schema():
                yield
            else:
                with self._transaction(write=False):
                    yield

    def close(self):
        if self._owned:
            self.db.close()

    def get(self, workspace, id):
        with self._lock:
            if not self._has_schema():
                raise DomainError("not_found", "Entity not found")
            self._refuse_stale()
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
            self._refuse_stale()
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
            head = self.db.execute(
                "SELECT COALESCE(MAX(seq),0) FROM daymark_event_cursors WHERE workspace=?",
                (workspace,),
            ).fetchone()[0]
            if type(head) is not int or head >= cursors.MAX_CURSOR:
                raise DomainError("conflict", "Workspace event cursor range is exhausted")
            seq = head + 1
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

    def _text_encoding(self):
        """SQLite uses the database text encoding when casting TEXT to BLOB."""
        return self.db.execute("PRAGMA encoding").fetchone()[0].lower()

    def _entity_history(self, workspace, id, columns, limit, *, creations=False):
        """Select entity events by native cursor, including out-of-order restores.

        Sort only event keys before fetching potentially large snapshots. The
        columns and optional creation filter are supplied by internal callers.
        """
        predicate = " AND e.action IN ('create','recurrence.create') AND e.after_data IS NOT NULL" if creations else ""
        return self.db.execute(
            "SELECT " + columns + " FROM ("
            "SELECT e.seq AS physical,c.seq AS cursor FROM daymark_events e "
            "JOIN daymark_event_cursors c ON c.event_seq=e.seq WHERE e.workspace=? AND e.entity_id=?"
            + predicate + " ORDER BY c.seq DESC LIMIT ?) h "
            "JOIN daymark_events e ON e.seq=h.physical ORDER BY h.cursor DESC",
            (workspace, id, limit),
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
            if not self._has_schema():
                return {"cursor": 0, "changed": after > 0}
            self._refuse_stale()
            with self._transaction(write=False):
                head = self.head(workspace)
                if after >= head:
                    return {"cursor": head, "changed": after > head}
                hit = project_feed.read(self, workspace, project_id, after, 1, cursor_only=True)
                return {"cursor": head, "changed": bool(hit)}

    def events(self, workspace, after=0, limit=100, project=None):
        """An event page after a cursor. ``project`` keeps the events that touch it."""
        if type(after) is not int or not 0 <= after <= cursors.MAX_CURSOR or type(limit) is not int or not 1 <= limit <= 1000:
            invalid("Invalid event cursor or limit")
        if project is not None and (not isinstance(project, str) or not project):
            invalid("Invalid project filter")
        with self._lock:
            if not self._has_schema():
                return []
            if project is not None:
                self._refuse_stale()
                with self._transaction(write=False):
                    rows = project_feed.read(self, workspace, project, after, limit)
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

    def activity(self, workspace, *, after=None, limit=20):
        """A compact page of workspace activity, with a cursor for the next session."""
        from . import views

        return views.activity(self, workspace, after=after, limit=limit)

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

    def decision_access(self, workspace, actor):
        """Whether the workspace proposal policy admits this actor at the time of the read."""
        deciders = self.policy(workspace)["deciders"]
        return {"actor": actor, "policy_allows": not deciders or actor in deciders,
                "rule": "named_deciders" if deciders else "any_writer"}

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

    @staticmethod
    def _setting_key(workspace, name):
        if (
            not isinstance(workspace, str)
            or not 1 <= len(workspace) <= 128
            or not isinstance(name, str)
            or re.fullmatch(r"[a-z][a-z0-9_.-]{0,63}", name) is None
        ):
            invalid("Setting needs a workspace and a short lowercase name")
        try:
            workspace.encode("utf-8")
        except UnicodeEncodeError:
            invalid("A setting workspace must be UTF-8 text")
        return f"setting:{workspace}:{name}"

    def setting_state(self, workspace, name):
        """Read a local configuration value and its revision in one snapshot."""
        key = self._setting_key(workspace, name)
        with self._lock:
            if not self._has_schema("daymark_scheduler"):
                return {"value": None, "version": 0}
            self._check_schema(upgrading=False)
            self._require_setting_tracking()
            data, revision = self.db.execute(
                "SELECT (SELECT value FROM daymark_scheduler WHERE key=?),"
                "(SELECT value FROM daymark_scheduler WHERE key=?)",
                (key, "revision:" + key),
            ).fetchone()
        # Older releases stored the value alone. Its first observed version is one.
        return {
            "value": json.loads(data) if data is not None else None,
            "version": int(revision) if revision is not None else int(data is not None),
        }

    def setting(self, workspace, name):
        """A named workspace setting (a small JSON value), or None."""
        key = self._setting_key(workspace, name)
        with self._lock:
            if not self._has_schema("daymark_scheduler"):
                return None
            self._check_schema(upgrading=False)
            row = self.db.execute("SELECT value FROM daymark_scheduler WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else None

    def set_setting(self, workspace, name, value, *, expected_version=None):
        """Store or remove a small JSON setting. An observed version guards against stale edits."""
        key = self._setting_key(workspace, name)
        if expected_version is not None and (
            type(expected_version) is not int or expected_version < 0
        ):
            invalid("Expected configuration version must be a nonnegative integer")
        try:
            text = None if value is None else canonical(value)
            if text is not None:
                text.encode("utf-8")
        except (TypeError, ValueError, RecursionError):
            invalid("A setting is plain UTF-8 JSON")
        if text is not None and len(text) > 65_536:
            invalid("Setting exceeds 65,536 JSON characters")
        with self._transaction():
            self._require_setting_tracking(install=True)
            state = self.setting_state(workspace, name)
            if expected_version is not None and expected_version != state["version"]:
                raise DomainError("conflict", "Workspace setting changed; read it again")
            if canonical(state["value"]) == canonical(value):
                return value
            if text is None:
                self.db.execute("DELETE FROM daymark_scheduler WHERE key=?", (key,))
            else:
                self.db.execute(
                    "INSERT INTO daymark_scheduler VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (key, text),
                )
        return value

    @staticmethod
    def _setting_op_key(workspace, operation_id):
        if not isinstance(operation_id, str) or not 1 <= len(operation_id) <= 200:
            invalid("Configuration operation ID must be 1 to 200 characters")
        try:
            operation_id.encode("utf-8")
        except UnicodeEncodeError:
            invalid("A configuration operation ID must be UTF-8 text")
        # Workspace and operation IDs can contain separators. JSON preserves the pair.
        return "setting-op:" + canonical([workspace, operation_id])

    def setting_operation(self, workspace, name, operation_id, request, apply, *, reversible=False):
        """Commit a local settings write and its replay receipt in one transaction.

        The callback receives the setting's observed state. Settings and their
        receipts live outside entity exports. A reversible write keeps its prior
        value, including the distinction between an unset value and a default.
        """
        self._setting_key(workspace, name)
        key = self._setting_op_key(workspace, operation_id)
        try:
            digest = hashlib.sha256(canonical([name, request]).encode()).hexdigest()
        except (TypeError, ValueError, RecursionError):
            invalid("A configuration request must be plain UTF-8 JSON")
        with self._transaction():
            row = self.db.execute(
                "SELECT value FROM daymark_scheduler WHERE key=?", (key,)
            ).fetchone()
            if row:
                receipt = json.loads(row[0])
                if receipt["digest"] != digest:
                    raise DomainError(
                        "conflict", "Configuration operation ID reused with a different request"
                    )
                return receipt["result"]
            self._require_setting_tracking(install=True)
            before = self.setting_state(workspace, name)
            result = apply(before)
            receipt = {"name": name, "digest": digest, "result": result}
            if reversible:
                receipt["before"] = before["value"]
            self.db.execute("INSERT INTO daymark_scheduler VALUES(?,?)", (key, canonical(receipt)))
        return result

    def setting_before(self, workspace, name, operation_id):
        """The prior value and resulting version of a reversible configuration operation."""
        self._setting_key(workspace, name)
        key = self._setting_op_key(workspace, operation_id)
        with self._lock:
            row = (
                self.db.execute(
                    "SELECT value FROM daymark_scheduler WHERE key=?", (key,)
                ).fetchone()
                if self._has_schema("daymark_scheduler")
                else None
            )
        receipt = json.loads(row[0]) if row else {}
        if receipt.get("name") != name or "before" not in receipt:
            raise DomainError("not_found", "No reversible configuration operation with that ID")
        return {"value": receipt["before"], "version": receipt["result"]["version"]}

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

    def projects(self, workspace, *, now=None, scope=None):
        """Every unarchived project in a workspace with its roll-up. See views.projects."""
        from . import views

        return views.projects(self, workspace, now=now, scope=scope)

    def _execute(self, workspace, operation_id, action, payload, expected_version):
        if action == "take" and payload.get("owner") != self._actor:
            invalid("Take requires the owner to match the acting worker")
        forgetting = action == "forget"
        if forgetting:
            if "id" not in payload or set(payload) - {"id", "review_token"}:
                invalid("Forget requires a memory ID")
            # Forget has always recorded an archive event and receipt. Keep
            # matching those requests, with a memory-kind check on both paths.
            action = "archive"
        digest, replay = self._receipt(workspace, operation_id, action, payload, expected_version)
        if replay is not None:
            if forgetting and replay.get("kind") != "memory":
                invalid("Forget requires a remembered fact; use a memory ID")
            return replay
        if "review_token" in payload:
            if not forgetting and action not in {"update", "accept", "dismiss", "undo"}:
                invalid("This operation does not support a review token")
            payload = dict(payload)
            token = payload.pop("review_token")
            if not isinstance(token, str) or not re.fullmatch(r"[0-9a-f]{32}", token):
                invalid("Use the review token returned by show or brief")
            if not isinstance(payload.get("id"), str):
                invalid("Entity ID required")
            if self._review_token(workspace, payload["id"]) != token:
                raise DomainError("conflict", "The reviewed entity has changed or was replaced")
        now = normalize_now().isoformat()
        taking = action == "take"
        if taking:
            if expected_version != 0:
                raise DomainError("conflict", "Selecting work requires version zero")
            candidate = self._take_candidate(workspace, payload, now)
            if candidate is None:
                empty = {"task": None}
                self._record(workspace, operation_id, digest, empty)
                return empty
            # Continue through the ordinary versioned claim transition and
            # attribute its event to the caller's actual operation ID.
            action = "claim"
            expected_version = candidate["version"]
            payload = {key: value for key, value in payload.items() if key not in {"scope", "context"}}
            payload["id"] = candidate["id"]
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
            if forgetting and before["kind"] != "memory":
                invalid("Forget requires a remembered fact; use a memory ID")
            if before["version"] != expected_version:
                raise DomainError("conflict", "Version mismatch")
            raw = {**before, "version": before["version"] + 1, "updated_at": now}
            if action == "update":
                if (payload.get("waiting_on") and "status" not in payload
                        and before["kind"] == "task" and before["status"] == "open" and not before["archived"]):
                    # Receiving a waiting reason parks live work. Keep the
                    # caller's original fields in the operation digest above.
                    payload = {**payload, "status": "waiting"}
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
                if raw.get("recurrence") and not before["recurrence"] and not raw["occurrence_key"]:
                    raw["series_id"] = raw["series_id"] or raw["id"]
                    raw["occurrence_key"] = f"{raw['series_id']}:0"
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
                if not self.decision_access(workspace, self._actor)["policy_allows"]:
                    raise DomainError(
                        "forbidden", "Only a designated decider can accept or dismiss"
                    )
                raw["status"] = "cancelled"
                if action == "accept":
                    require_visible_proposal(before)
                    raw["status"] = "waiting" if before["waiting_on"] else "open"
            elif action in {"complete", "cancel", "reopen", "archive", "skip", "delete"}:
                if set(payload) - {"id", "completed_at"}:
                    invalid("Unexpected action fields")
                if before["status"] == "proposed" and action in {"complete", "skip", "reopen"}:
                    invalid("Accept or dismiss a proposal first")
                if action in {"skip", "complete"}:
                    require_active_round(before, action)
                if action == "archive":
                    raw["archived"] = True
                elif action == "complete":
                    raw.update(status="completed", completed_at=payload.get("completed_at", now))
                    if before["kind"] == "condition":
                        raw["condition"] = "met"
                elif action in {"cancel", "skip"}:
                    raw.update(status="cancelled", completed_at=None)
                elif action == "reopen":
                    raw.update(status="open", completed_at=None, archived=False)
                    # Reopening a met condition says it no longer holds. Bringing back an
                    # archived or dropped one keeps the last assessment.
                    if (
                        before["kind"] == "condition"
                        and before["status"] == "completed"
                        and not before["archived"]
                    ):
                        raw["condition"] = "unmet"
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
        if raw.get("kind") == "condition":
            # A condition's status is never set directly: it follows its state, unless
            # the condition has been cancelled as no longer necessary.
            raw["condition"] = raw.get("condition") or "unknown"
            follows = "completed" if raw["condition"] == "met" else "open"
            asked = payload.get("status") if action in {"create", "update"} else None
            if asked not in (None, follows, "cancelled"):
                # Refuse, not ignore: a condition is assessed, never ticked off or parked.
                invalid("A condition's status follows its state; assess it instead")
            if raw.get("status") != "cancelled":
                raw["status"] = follows
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
                # Edits and stage moves can also produce a completion. Keep
                # a closed round from starting new work through those routes.
                if data["archived"]:
                    require_active_round(data, "complete")
                if action == "update":
                    require_active_round({**before, "recurrence": data["recurrence"]}, "complete")
                self._successor(data, operation_id)
        result = {"task": data} if taking else data
        self._record(workspace, operation_id, digest, result)
        return result

    def _take_candidate(self, workspace, payload, now):
        """Selection uses the same readiness and ordering that the worker sees."""
        if set(payload) - {"owner", "scope", "context", "lease_seconds"}:
            invalid("Unexpected take fields")
        owner, scope = payload.get("owner"), payload.get("scope")
        if not isinstance(owner, str) or not owner.strip() or len(owner) > 200:
            invalid("Take requires a named acting worker")
        lease = payload.get("lease_seconds", 3600)
        if type(lease) is not int or not 1 <= lease <= 604_800:
            invalid("Claim requires a lease of at most seven days")
        if scope not in (None, "") and (
            not isinstance(scope, str) or len(scope) > 100 or not re.fullmatch(r"[a-z0-9][a-z0-9._-]*", scope)
        ):
            invalid("Invalid scope name")
        context = payload.get("context")
        if context is not None and (not isinstance(context, str) or not 1 <= len(context) <= 128):
            invalid("Context must be an entity ID")
        ready = self.ready(workspace, now=now, limit=1, next_move="agent", scope=scope,
                           **({"context": context} if context is not None else {}))
        return ready[0] if ready else None

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

    def _same_materialized_state(self, recorded, live):
        """Compare canonical JSON, supplying defaults absent in older event snapshots."""
        return recorded == live or (
            recorded is not None and live is not None
            and canonical(self._validated(json.loads(recorded))) == live
        )

    def _undo(self, workspace, payload, expected_version, operation_id, now):
        if set(payload) != {"id"}:
            invalid("Undo requires entity ID")
        row = self._entity_history(
            workspace, payload["id"], "e.action,e.before_data,e.after_data,e.operation_id", 1
        ).fetchone()
        live_row = self.db.execute(
            "SELECT data FROM daymark_entities WHERE workspace=? AND id=?", (workspace, payload["id"])
        ).fetchone()
        live = live_row[0] if live_row else None
        if row is None and live is None:
            invalid("No prior state to restore")
        if row is None or not self._same_materialized_state(row[2], live) or (
            live is None and row[0] != "delete"
        ):
            raise DomainError("conflict", "Current state lacks matching event history",
                              ["Inspect the current record and its history before choosing a change."])
        if row[1] is None:
            invalid("No prior state to restore")
        undone = row[3]
        previous = json.loads(row[1])
        current = json.loads(live) if live is not None else None
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
            "SELECT e.before_data,e.after_data FROM daymark_events e JOIN daymark_event_cursors c "
            "ON c.event_seq=e.seq WHERE e.workspace=? AND e.operation_id=? "
            "AND e.action='stage.reset' ORDER BY c.seq",
            (workspace, undone),
        ).fetchall()
        for before_text, after_text in rows:
            before, after = json.loads(before_text), json.loads(after_text)
            try:
                task = self.get(workspace, after["id"])
            except DomainError:
                continue
            if not self._same_materialized_state(after_text, canonical(task)):
                continue  # The task changed or was replaced since this stage was reset.
            updated = {**task, "stage": before["stage"], "version": task["version"] + 1,
                       "updated_at": now}  # fmt: skip
            self._settle_stage(updated, None, {})
            if updated["stage"] == task["stage"]:
                continue
            self._put(updated)
            self._event(workspace, operation_id, "stage.restore", task, updated)

    def _occurrence_slot(self, workspace, candidate, key):
        """Return ours, other or None from current and historical slot evidence."""
        # The usual correction finds its already generated successor in the
        # first row. A deleted or reused ID still reserves its historical slot.
        sql = (
            "SELECT NULL,{} FROM daymark_entities WHERE workspace=? AND id=? UNION ALL "
            "SELECT {},{} FROM daymark_events WHERE workspace=? AND entity_id=?"
        )
        fields = ("data", "before_data", "after_data")
        params = (workspace, candidate, workspace, candidate)
        try:
            rows = self.db.execute(sql.format(*(f"json_extract({f},'$.occurrence_key')" for f in fields)), params)
        except sqlite3.OperationalError as exc:
            if "no such function" not in str(exc):
                raise
            rows = (
                tuple(json.loads(value).get("occurrence_key") if value else None for value in row)
                for row in self.db.execute(sql.format(*fields), params)
            )
        state = None
        for previous, following in rows:
            if key in (previous, following):
                return "ours"
            state = "other"
        return state

    def _successor(self, data, operation_id):
        now = normalize_now().isoformat()
        recurrence = Recurrence.model_validate(data["recurrence"])
        index = recurrence.occurrence_index + 1
        series = data["series_id"] or data["id"]
        # Identity follows the original slot, independent of later date edits.
        workspace, key = data["workspace"], f"{series}:{index}"
        hashed = uuid.uuid5(uuid.NAMESPACE_URL, canonical([workspace, series, index])).hex

        # Prefer a readable "<series>.<n>"; a name some unrelated entity uses or
        # used, or an over-long one, leaves the hashed identity. A slot that was
        # ever materialized, even if since deleted, is never materialized again.
        readable = f"{series}.{index}"
        state = self._occurrence_slot(workspace, readable, key) if len(readable) <= 128 else "other"
        if state == "ours" or self._occurrence_slot(workspace, hashed, key) is not None:
            return
        id = readable if state is None else hashed
        original_row = self._entity_history(
            workspace, data["id"], "e.after_data", 1, creations=True
        ).fetchone()
        original = json.loads(original_row[0]) if original_row else data
        if original.get("recurrence"):
            nominal = original["planned"] or original["deadline"] or original["recurrence"]["anchor"]
        else:
            # This task joined its series after creation. Its enrollment
            # anchor survives rescheduling of the first individual occurrence.
            nominal = data["recurrence"]["anchor"]
        current = Temporal.model_validate(nominal)
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
        field = recurrence_date_field(data)
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
            active = item["reminder_state"] in outbox.ACTIVE_STATES
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
            if after["reminder_state"] not in outbox.ACTIVE_STATES:
                return  # Archiving a delivered or acknowledged reminder preserves its outcome.
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
                # Scheduler writes and older records may spell equivalent UTC
                # instants differently. Compare model values, leaving live bytes intact.
                if existing and not self._same_materialized_state(existing[0], canonical(data)):
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
                    or not 1 <= event["seq"] <= cursors.MAX_CURSOR
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
            "condition",
            "advances",
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

    def search(self, workspace, text=None, *, kind="task", status=None, scope=None, after=None, limit=50,
               archived=False, source=None, claimed_by=None, now=None):
        """Search titles and descriptions. Continue from the last ID with the same filters."""
        from .search import page

        return page(self, workspace, text, kind=kind, status=status, scope=scope, after=after, limit=limit,
                    archived=archived, source=source, claimed_by=claimed_by, now=now)

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
                return [item for item in self.list(workspace) if test(item)]  # Empty or refused.
            status = [narrowed["status"]] if "status" in narrowed else None
            rows = self._rows(workspace, kind=narrowed.get("kind"), status=status)
        return [item for item in rows if test(item)]

    def ready(self, workspace, *, now=None, limit=50, owner=None, next_move=None, context=None, scope=None, brief=False):
        """Open tasks that can be acted on now, most pressing first. See views.ready."""
        from . import views

        return views.ready(
            self, workspace, now=now, limit=limit, owner=owner, next_move=next_move, scope=scope, context=context, brief=brief
        )

    def explain_ready(self, workspace, *, now=None, limit=20, owner=None, next_move=None, context=None, scope=None):
        from . import views

        return views.explain_ready(self, workspace, now=now, limit=limit,
                                   owner=owner, next_move=next_move, scope=scope, context=context)

    def agenda(self, workspace, *, now=None, days=7, context=None, scope=None, deadlines=False):
        """Overdue, today, upcoming, waiting and proposed tasks. See views.agenda."""
        from . import views

        return views.agenda(self, workspace, now=now, days=days, scope=scope, context=context, deadlines=deadlines)

    def explore(self, workspace, goal_id=None, *, now=None, scope=None):
        """The conditions behind a goal (or every goal): what is met, what is next,
        and where nothing is moving. See views.explore."""
        from . import views

        return views.explore(self, workspace, goal_id, now=now, scope=scope)

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

    def _read_entity(self, read, workspace, id, source, *args, **kwargs):
        """Resolve a source and inspect its owner within the very same snapshot."""
        if source is None:
            if id is None:
                invalid("An entity ID or source reference is required")
            return read(self, workspace, id, *args, **kwargs)
        if id is not None:
            invalid("Choose an entity ID or a source reference")
        ref = SourceRef.parse_ref(source)
        with self._lock:
            if not self._has_schema():
                raise DomainError("not_found", "No entity owns this source")
            with self._transaction(write=False):
                row = self.db.execute(
                    "SELECT entity_id FROM daymark_sources "
                    "WHERE workspace=? AND system=? AND kind=? AND ref_id=?",
                    (workspace, ref.system, ref.kind, ref.id),
                ).fetchone()
                if row is None:
                    raise DomainError("not_found", "No entity owns this source")
                return read(self, workspace, row[0], *args, **kwargs)

    def brief(self, workspace, id=None, *, now=None, owner=None, before_note=None, source=None, actor=None):
        from . import brief

        return self._read_entity(brief.read, workspace, id, source, now=now, owner=owner, before_note=before_note, actor=actor)

    def changes_since(self, workspace, id=None, *, version, source=None):
        from . import comparison

        return self._read_entity(comparison.read, workspace, id, source, version)

    def read_note(self, workspace, id, note_id, *, source=None):
        from . import brief

        return self._read_entity(brief.note, workspace, id, source, note_id)

    def context(self, workspace, id=None, *, history=10, now=None, owner=None, source=None, actor=None):
        """An entity with its blockers, dependents, children, reminders and history."""
        from . import views

        return self._read_entity(views.context, workspace, id, source, history=history, now=now, owner=owner, actor=actor)

    def orientation(self, workspace, *, now=None, owner=None, scope=None):
        """An atomic startup snapshot including active work and shared memory previews."""
        from . import views

        return views.orientation(self, workspace, now=now, owner=owner, scope=scope)

    def summary(self, workspace, *, now=None, context=None, scope=None, owner=None):
        from . import views

        return views.summary(self, workspace, now=now, scope=scope, context=context, owner=owner)

    def _review_token(self, workspace, id):
        """Identify this entity's latest recorded state inside the caller's transaction."""
        row = self._entity_history(
            workspace, id,
            "h.cursor,e.after_data=(SELECT i.data FROM daymark_entities i "
            "WHERE i.workspace=e.workspace AND i.id=e.entity_id),e.seq", 1
        ).fetchone()
        if row is None:
            return None
        if not row[1]:
            # A schema upgrade may have added defaults to today's entity while
            # preserving the event as written. A sparse import may have no
            # evidence at its current version. Only the first case gets a token.
            raw = self.db.execute(
                "SELECT after_data FROM daymark_events WHERE seq=?", (row[2],)
            ).fetchone()[0]
            if not self._same_materialized_state(raw, canonical(self.get(workspace, id))):
                return None
        evidence = ["daymark-review-v2", cursors.event_fingerprint(self, workspace, row[0])]
        return hashlib.sha256(canonical(evidence).encode()).hexdigest()[:32]

    def receipt_context(self, workspace, operation_id, *, action, entity_id=None, actor=None):
        """Recover the original read for a plain-verb retry. Reject unrelated receipts."""
        with self._lock:
            result = self.receipt(workspace, operation_id)
            if result is None:
                return None
            forgetting = action == "forget"
            if forgetting:
                action = "archive"
            if not isinstance(result.get("id"), str) or (
                entity_id is not None and result["id"] != entity_id
            ):
                raise DomainError("conflict", "Operation ID belongs to a different request")
            row = self.db.execute(
                "SELECT before_data,actor FROM daymark_events "
                "WHERE workspace=? AND entity_id=? AND operation_id=? AND action=? LIMIT 1",
                (workspace, result["id"], operation_id, action),
            ).fetchone()
            if not row or (actor is not None and row[1] is not None and row[1] != actor):
                raise DomainError("conflict", "Operation ID belongs to a different request")
            if forgetting and result.get("kind") != "memory":
                invalid("Forget requires a remembered fact; use a memory ID")
            return {"before": json.loads(row[0]) if row[0] else None, "result": result}

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
            expired = [(r.workspace, r.id) for r in outbox.active(self.db) if r.availability == "inactive"]
            for workspace, id in expired:
                before = self.get(workspace, id)
                updated = {
                    **before, "reminder_state": "cancelled", "lease_owner": None, "lease_until": None,
                    "lease_token": None, "version": before["version"] + 1, "updated_at": now.isoformat(),
                }
                self._put(updated)
                self._event(workspace, None, "reminder.invalidate", before, updated, created_at=now.isoformat())
            return {"cancelled": len(expired)}

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

    def _dispatchable(self, reminder):
        status = outbox.target_status(self.db, reminder["workspace"], reminder["task_id"])
        return outbox.availability(reminder["status"], reminder["archived"], reminder["task_id"], status) == "eligible"

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
            candidates = (
                (due, row.workspace, row.id) for row in outbox.active(self.db)
                if (due := row.due(now)) is not None
            )
            claimed = []
            for _, workspace, id in heapq.nsmallest(limit, candidates):
                item = self.get(workspace, id)
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
            counts = outbox.counts(self.db, workspace)
            scheduler = dict(self.db.execute(
                "SELECT key,value FROM daymark_scheduler WHERE key IN ('tick','heartbeat')"
            ))
            return {
                "outbox": counts,
                **{key: counts[key] for key in ("pending", "leased", "failed")},
                "last_tick": scheduler.get("tick"),
                "last_heartbeat": scheduler.get("heartbeat"),
            }
