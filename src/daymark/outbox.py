"""Read the scheduling fields for reminders without loading task history."""

from __future__ import annotations

import json
import sqlite3
from typing import NamedTuple

from .models import normalize_now

DETAIL_FIELDS = ("task_id", "scheduled_at", "recipient_id", "channel", "reminder_state")
ACTIVE_STATES = ("pending", "leased", "snoozed", "failed")


def availability(status, archived, task_id, task_status):
    """An undecided task keeps its reminder dormant. Closed work cancels it."""
    if archived or status not in ("open", "waiting"):
        return "inactive"
    if task_id is None or task_status in ("open", "waiting"):
        return "eligible"
    return "dormant" if task_status == "proposed" else "inactive"


def target_status(db, workspace, task_id):
    if task_id is None:
        return None
    row = db.execute(
        "SELECT status FROM daymark_entities WHERE workspace=? AND id=? AND archived=0",
        (workspace, task_id),
    ).fetchone()
    return row[0] if row else None


class ActiveReminder(NamedTuple):
    workspace: str
    id: str
    state: str
    scheduled_at: str
    retry_at: str | None
    lease_until: str | None
    availability: str

    def due(self, now):
        if self.availability != "eligible":
            return None
        if self.state == "leased" and self.lease_until and normalize_now(self.lease_until) > now:
            return None
        scheduled = normalize_now(self.scheduled_at)
        when = max(scheduled, normalize_now(self.retry_at)) if self.retry_at else scheduled
        return when if when <= now else None


def active(db):
    """Stream active reminders across workspaces in stable workspace/ID order."""
    try:
        rows = db.execute(
            "SELECT r.workspace,r.id,r.status,r.archived,link.target_id,t.status,"
            "json_extract(r.data,'$.reminder_state','$.scheduled_at','$.retry_at','$.lease_until') "
            "FROM daymark_entities r LEFT JOIN daymark_refs link ON link.workspace=r.workspace "
            "AND link.source_id=r.id AND link.field='task_id' "
            "LEFT JOIN daymark_entities t ON t.workspace=link.workspace AND t.id=link.target_id "
            "AND t.archived=0 WHERE r.kind='reminder' "
            "AND json_extract(r.data,'$.reminder_state') IN (?,?,?,?) ORDER BY r.workspace,r.id",
            ACTIVE_STATES,
        )
    except sqlite3.OperationalError as exc:
        if "no such function: json_extract" not in str(exc):
            raise
        for (raw,) in db.execute(
            "SELECT data FROM daymark_entities WHERE kind='reminder' ORDER BY workspace,id"
        ):
            item = json.loads(raw)
            if item["reminder_state"] in ACTIVE_STATES:
                status = target_status(db, item["workspace"], item["task_id"])
                yield ActiveReminder(
                    item["workspace"],
                    item["id"],
                    item["reminder_state"],
                    item["scheduled_at"],
                    item["retry_at"],
                    item["lease_until"],
                    availability(item["status"], item["archived"], item["task_id"], status),
                )
        return
    for workspace, id, status, archived, task_id, task_status, fields in rows:
        yield ActiveReminder(
            workspace, id, *json.loads(fields), availability(status, archived, task_id, task_status)
        )


def counts(db, workspace=None):
    """Count outbox states without returning reminder bodies to Python."""
    where = " FROM daymark_entities WHERE kind='reminder'"
    parameters = ()
    if workspace is not None:
        where += " AND workspace=?"
        parameters = (workspace,)
    try:
        rows = db.execute(
            "SELECT json_extract(data,'$.reminder_state') AS state, count(*)"
            + where
            + " GROUP BY state",
            parameters,
        )
    except sqlite3.OperationalError as exc:
        if "no such function: json_extract" not in str(exc):
            raise
        rows = (
            (json.loads(raw)["reminder_state"], 1)
            for (raw,) in db.execute("SELECT data" + where, parameters)
        )
    result = dict.fromkeys(("pending", "leased", "failed", "delivered"), 0)
    for state, count in rows:
        if state == "snoozed":
            state = "pending"
        if state in result:
            result[state] += count
    return result
