"""Transactional SQLite storage for Daymark entities and operation receipts."""
from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import uuid
from contextlib import contextmanager

from .models import DomainError, Entity, normalize_now


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


class Store:
    """Embedded store; supplied connections remain under the caller's transaction."""

    def __init__(self, path):
        self._owned = not isinstance(path, sqlite3.Connection)
        self.db = sqlite3.connect(path, check_same_thread=False) if self._owned else path
        self._lock = threading.RLock()
        with self._transaction():
            self.db.execute("CREATE TABLE IF NOT EXISTS daymark_entities (workspace TEXT NOT NULL,id TEXT NOT NULL,kind TEXT NOT NULL,version INTEGER NOT NULL,data TEXT NOT NULL,PRIMARY KEY(workspace,id))")
            self.db.execute("CREATE TABLE IF NOT EXISTS daymark_events (seq INTEGER PRIMARY KEY AUTOINCREMENT,workspace TEXT NOT NULL,operation_id TEXT,entity_id TEXT,action TEXT NOT NULL,before_data TEXT,after_data TEXT,created_at TEXT NOT NULL)")
            self.db.execute("CREATE TABLE IF NOT EXISTS daymark_operations (workspace TEXT NOT NULL,operation_id TEXT NOT NULL,payload_hash TEXT NOT NULL,result TEXT NOT NULL,PRIMARY KEY(workspace,operation_id))")

    @contextmanager
    def _transaction(self):
        with self._lock:
            if not self.db.in_transaction:
                self.db.execute("BEGIN IMMEDIATE" if self._owned else "BEGIN")
            name = 'daymark_' + uuid.uuid4().hex
            self.db.execute('SAVEPOINT ' + name)
            try:
                yield
                self.db.execute('RELEASE ' + name)
                if self._owned:
                    self.db.commit()
            except BaseException:
                self.db.execute('ROLLBACK TO ' + name)
                self.db.execute('RELEASE ' + name)
                if self._owned:
                    self.db.rollback()
                raise

    def close(self):
        if self._owned:
            self.db.close()

    def get(self, workspace, id):
        with self._lock:
            row = self.db.execute('SELECT data FROM daymark_entities WHERE workspace=? AND id=?', (workspace,id)).fetchone()
            if row is None:
                raise DomainError('not_found', 'Entity not found')
            return json.loads(row[0])

    def list(self, workspace, kind=None, include_archived=False):
        with self._lock:
            rows = self.db.execute('SELECT data FROM daymark_entities WHERE workspace=? ORDER BY id', (workspace,)).fetchall()
            return [item for row in rows if (item := json.loads(row[0])) and (kind is None or item['kind']==kind) and (include_archived or not item['archived'])]

    def _put(self, data):
        self.db.execute('INSERT INTO daymark_entities VALUES (?,?,?,?,?) ON CONFLICT(workspace,id) DO UPDATE SET kind=excluded.kind,version=excluded.version,data=excluded.data', (data['workspace'],data['id'],data['kind'],data['version'],canonical(data)))

    def execute(self, workspace, operation_id, action, payload, expected_version=None):
        if not operation_id or expected_version is None:
            raise DomainError('invalid', 'Operation id and expected version required')
        digest = hashlib.sha256(canonical([action,payload,expected_version]).encode()).hexdigest()
        with self._transaction():
            receipt = self.db.execute('SELECT payload_hash,result FROM daymark_operations WHERE workspace=? AND operation_id=?', (workspace,operation_id)).fetchone()
            if receipt:
                if receipt[0] != digest:
                    raise DomainError('conflict', 'Operation id reused')
                return json.loads(receipt[1])
            before = None
            if action == 'create':
                if expected_version != 0:
                    raise DomainError('conflict', 'Create requires version zero')
                raw = dict(payload)
                if raw.get('workspace',workspace) != workspace:
                    raise DomainError('invalid', 'Workspace mismatch')
                raw.update(workspace=workspace,version=1)
                raw.setdefault('id',uuid.uuid4().hex)
                if self.db.execute('SELECT 1 FROM daymark_entities WHERE workspace=? AND id=?',(workspace,raw['id'])).fetchone():
                    raise DomainError('conflict','Entity exists')
            elif action == 'update':
                before = self.get(workspace,payload['id'])
                if before['version'] != expected_version:
                    raise DomainError('conflict','Version mismatch')
                raw = {**before, **{k:v for k,v in payload.items() if k != 'id'}, 'version':before['version']+1, 'updated_at':normalize_now().isoformat()}
                if raw['workspace'] != workspace:
                    raise DomainError('invalid','Workspace mismatch')
            else:
                raise DomainError('invalid','Unsupported action')
            data = Entity.model_validate(raw).model_dump(mode='json')
            self._put(data)
            self.db.execute('INSERT INTO daymark_events(workspace,operation_id,entity_id,action,before_data,after_data,created_at) VALUES(?,?,?,?,?,?,?)',(workspace,operation_id,data['id'],action,canonical(before) if before else None,canonical(data),normalize_now().isoformat()))
            self.db.execute('INSERT INTO daymark_operations VALUES(?,?,?,?)',(workspace,operation_id,digest,canonical(data)))
            return data
