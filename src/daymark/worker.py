"""Durable scheduler runner. The default sink records keys without sending anything."""

from __future__ import annotations

import logging
import threading
import uuid
from datetime import UTC, datetime
from typing import Any, Protocol

from .models import DomainError, normalize_now

LOG = logging.getLogger("daymark.worker")


class Sink(Protocol):
    def deliver(self, reminder: dict[str, Any], *, delivery_key: str) -> None: ...


class RecordingSink:
    """In-memory synthetic receipt sink; authoritative outcomes live in Store.

    No notification provider or network integration is configured. Repeated keys
    yield one recorded receipt within this sink instance. External sinks must
    persistently deduplicate the same key across process restarts.
    """

    def __init__(self) -> None:
        self.delivery_keys: set[str] = set()

    def deliver(self, reminder: dict[str, Any], *, delivery_key: str) -> None:
        if reminder.get("channel", "recording") != "recording":
            raise ValueError("Unsupported recording channel")
        self.delivery_keys.add(delivery_key)


class Worker:
    def __init__(
        self,
        store: Any,
        sink: Sink | None = None,
        *,
        owner: str | None = None,
        lease_seconds: int = 60,
        limit: int = 50,
    ) -> None:
        if lease_seconds < 1 or not 1 <= limit <= 1000:
            raise ValueError("Invalid worker limits")
        self.store = store
        self.sink = sink if sink is not None else RecordingSink()
        self.owner = owner or uuid.uuid4().hex
        self.lease_seconds = lease_seconds
        self.limit = limit

    def _ready(self, claim: dict[str, Any], now: datetime) -> dict[str, Any] | None:
        """Recheck immediately before the sink; never send a stale claim snapshot.

        This closes observable claim-to-send cancellations, not the unavoidable
        race between this check and an external side effect. Sinks still need
        durable delivery-key deduplication; lease fencing protects persistence.
        """
        try:
            current = self.store.get(claim["workspace"], claim["id"])
            if (
                current.get("reminder_state") != "leased"
                or current.get("lease_owner") != claim.get("lease_owner")
                or not claim.get("lease_token")
                or current.get("lease_token") != claim["lease_token"]
                or current.get("version") != claim.get("version")
                or current.get("delivery_key") != claim.get("delivery_key")
                or not current.get("lease_until")
                or normalize_now(current["lease_until"]) <= now
            ):
                return None
            if current.get("task_id"):
                task = self.store.get(current["workspace"], current["task_id"])
                if task["status"] in {"completed", "cancelled"} or task.get("archived"):
                    return None
            return current
        except DomainError as exc:
            if exc.code != "not_found":
                raise
            return None

    def run_once(self, now: datetime | None = None) -> dict[str, int]:
        """Run a recorder pass; use path-owned Store for committed claim sequencing.

        Caller-owned SQLite transactions remain uncommitted here. Applications
        adding external sinks must orchestrate their own committed claim/result
        transactions and persistent receiver deduplication.
        """
        fixed_now = now
        now = now or datetime.now(UTC)
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("Worker time must be timezone aware")
        self.store.tick(now=now)
        self.store.heartbeat(self.owner, now=now)
        # A new batch token fences late completion from an expired earlier pass,
        # even when the same Worker instance is reused after lease recovery.
        lease_owner = uuid.uuid4().hex
        claimed = self.store.claim_reminders(
            lease_owner, now=now, lease_seconds=self.lease_seconds, limit=self.limit
        )
        counts = {"claimed": len(claimed), "recorded": 0, "failed": 0, "suppressed": 0}
        for claim in claimed:
            checked_at = fixed_now if fixed_now is not None else datetime.now(UTC)
            reminder = self._ready(claim, checked_at)
            if reminder is None:
                counts["suppressed"] += 1
                LOG.info(
                    "delivery_suppressed",
                    extra={"event_type": "delivery_suppressed", "actor": "worker"},
                )
                continue
            success = True
            try:
                self.sink.deliver(reminder, delivery_key=reminder["delivery_key"])
            except Exception:  # noqa: BLE001 - prevent private exception text escaping
                success = False
            try:
                self.store.finish_delivery(
                    reminder["workspace"],
                    reminder["id"],
                    claim["lease_owner"],
                    success,
                    lease_token=claim["lease_token"],
                    error=None if success else "sink_failure",
                    # Production runs must not backdate completion to claim time.
                    now=fixed_now if fixed_now is not None else datetime.now(UTC),
                )
            except DomainError as exc:
                if exc.code not in {"conflict", "not_found"}:
                    raise
                counts["failed"] += 1
                LOG.warning("lease_lost", extra={"event_type": "lease_lost", "actor": "worker"})
                continue
            counts["recorded" if success else "failed"] += 1
            event = "delivery_recorded" if success else "delivery_failed"
            LOG.info(event, extra={"event_type": event, "actor": "worker"})
        return counts

    def run(self, *, interval: float = 10, stop: threading.Event | None = None) -> None:
        if interval <= 0:
            raise ValueError("Worker interval must be positive")
        stop = stop or threading.Event()
        while not stop.is_set():
            self.run_once()
            stop.wait(interval)
