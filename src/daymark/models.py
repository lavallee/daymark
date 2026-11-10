"""Typed, application-independent work semantics."""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from datetime import date as Date
from typing import Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dateutil.rrule import rrulestr
from dateutil.tz import datetime_exists, resolve_imaginary
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class DomainError(ValueError):
    """A domain failure with a stable code.

    ``details`` names offending fields and rules only; it never echoes input values.
    """

    def __init__(self, code: str, message: str, details: list[str] | None = None):
        self.code = code
        self.details = details or []
        super().__init__(message)


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


def aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("an instant requires an explicit UTC offset")
    return value


class Temporal(Model):
    date: Date | None = None
    at: datetime | None = None
    timezone: str = "UTC"
    # The phrase a person actually used ("after the holidays"). Kept as evidence
    # beside the resolved value; Daymark never interprets it.
    expression: str | None = Field(default=None, max_length=200)

    @field_validator("timezone")
    @classmethod
    def valid_zone(cls, value):
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as e:
            raise ValueError("unknown IANA timezone") from e
        return value

    @field_validator("at")
    @classmethod
    def valid_instant(cls, value):
        return aware(value) if value else None

    @model_validator(mode="after")
    def exactly_one(self):
        if (self.date is None) == (self.at is None):
            raise ValueError("provide exactly one of date or at")
        return self

    def local(self) -> datetime:
        zone = ZoneInfo(self.timezone)
        return (
            self.at.astimezone(zone)
            if self.at
            else datetime.combine(self.date, datetime.min.time(), zone)
        )

    def is_before(self, now: datetime) -> bool:
        now = aware(now)
        return (
            self.date < now.astimezone(ZoneInfo(self.timezone)).date()
            if self.date
            else (self.at.astimezone(UTC) < now.astimezone(UTC))
        )


_WEEKDAYS = {"MO", "TU", "WE", "TH", "FR", "SA", "SU"}
_SELECTOR_RANGES = {
    "BYMONTH": (1, 12),
    "BYMONTHDAY": (-31, 31),
    "BYYEARDAY": (-366, 366),
    "BYWEEKNO": (-53, 53),
    "BYSETPOS": (-366, 366),
}
_SEARCH_SPAN = timedelta(days=146097)  # One complete 400-year Gregorian cycle.


def _until(value: str) -> datetime:
    formats = {8: "%Y%m%d", 15: "%Y%m%dT%H%M%S", 16: "%Y%m%dT%H%M%SZ"}
    if not re.fullmatch(r"[0-9]{8}(?:T[0-9]{6}Z?)?", value):
        raise ValueError("UNTIL must be a local date/time or UTC date/time")
    result = datetime.strptime(value, formats[len(value)])
    return result.replace(tzinfo=UTC) if value.endswith("Z") else result


class Recurrence(Model):
    """A bounded calendar RRULE in its governing zone.

    V1 accepts daily through yearly frequencies and calendar BY selectors,
    excluding subday expansion. INTERVAL is 1..10,000; COUNT is 1..1,000,000.
    The materializer increments occurrence_index for each actual successor.
    """

    rule: str = Field(min_length=1, max_length=500)
    mode: Literal["fixed", "after_completion"] = "fixed"
    timezone: str = "UTC"
    anchor: Temporal
    # Store carries this actual occurrence budget across completion-based copies.
    # The original occurrence is zero; elapsed days never consume this budget.
    occurrence_index: int = Field(default=0, ge=0, le=1_000_000)

    @field_validator("timezone")
    @classmethod
    def valid_zone(cls, value):
        return Temporal.valid_zone(value)

    @field_validator("rule")
    @classmethod
    def safe_rule(cls, value):
        # One bounded RFC 5545 recurrence rule; no injected DTSTART/RDATE sets.
        text = value.removeprefix("RRULE:")
        if any(c in text for c in "\r\n") or len(text.split(";")) > 16:
            raise ValueError("provide a single bounded RRULE")
        pairs = {}
        allowed = {"FREQ", "INTERVAL", "COUNT", "UNTIL", "BYDAY", "WKST", *_SELECTOR_RANGES}
        for part in text.split(";"):
            if part.count("=") != 1:
                raise ValueError("each RRULE part requires one key and value")
            key, item = part.split("=")
            if key in pairs or key not in allowed:
                raise ValueError("duplicate or unsupported RRULE part")
            pairs[key] = item
        if pairs.get("FREQ") not in {"DAILY", "WEEKLY", "MONTHLY", "YEARLY"}:
            raise ValueError("v1 recurrence supports DAILY/WEEKLY/MONTHLY/YEARLY")
        if "COUNT" in pairs and "UNTIL" in pairs:
            raise ValueError("COUNT and UNTIL are mutually exclusive")
        for key, maximum in [("INTERVAL", 10_000), ("COUNT", 1_000_000)]:
            if key in pairs and (
                not re.fullmatch(r"[0-9]{1,7}", pairs[key]) or not 1 <= int(pairs[key]) <= maximum
            ):
                raise ValueError(f"{key} must be between 1 and {maximum}")
        for key, (minimum, maximum) in _SELECTOR_RANGES.items():
            if key in pairs:
                for item in pairs[key].split(","):
                    if (
                        not re.fullmatch(r"[+-]?[0-9]{1,3}", item)
                        or not minimum <= int(item) <= maximum
                        or int(item) == 0
                    ):
                        raise ValueError(f"invalid {key} selector")
        if "BYDAY" in pairs:
            for item in pairs["BYDAY"].split(","):
                match = re.fullmatch(r"([+-]?[0-9]{1,2})?(MO|TU|WE|TH|FR|SA|SU)", item)
                if not match or (
                    match[1]
                    and (
                        pairs["FREQ"] not in {"MONTHLY", "YEARLY"}
                        or not 1 <= abs(int(match[1])) <= 53
                    )
                ):
                    raise ValueError("invalid BYDAY selector")
        if "WKST" in pairs and pairs["WKST"] not in _WEEKDAYS:
            raise ValueError("invalid week start")
        if "BYSETPOS" in pairs:
            if not ({"BYDAY", "BYMONTH", "BYMONTHDAY", "BYYEARDAY", "BYWEEKNO"} & pairs.keys()):
                raise ValueError("BYSETPOS requires another BY selector")
            maximum = {"DAILY": 1, "WEEKLY": 7, "MONTHLY": 31, "YEARLY": 366}[pairs["FREQ"]]
            if any(abs(int(item)) > maximum for item in pairs["BYSETPOS"].split(",")):
                raise ValueError("BYSETPOS exceeds candidates per calendar period")
        if "UNTIL" in pairs:
            _until(pairs["UNTIL"])
        # Validate parser compatibility without mixing a UTC UNTIL and dummy naive DTSTART.
        body = ";".join(f"{key}={item}" for key, item in pairs.items() if key != "UNTIL")
        rrulestr(body, dtstart=datetime(2000, 1, 1))
        return text

    @model_validator(mode="after")
    def same_zone(self):
        if self.anchor.timezone != self.timezone:
            raise ValueError("anchor and recurrence timezone must match")
        return self


def next_occurrence(recurrence: Recurrence, current: Temporal, completed_at: datetime):
    """Next local slot; gaps advance, overlaps use first fold, endings stay finite.

    Search is limited to 400 Gregorian years each of catch-up and lookahead.
    Reaching a search limit raises recurrence_limit; it does not end the series.
    Completion-based COUNT uses occurrence_index, persisted by the materializer.
    """
    zone = ZoneInfo(recurrence.timezone)
    anchor = recurrence.anchor.local().replace(tzinfo=None)
    cursor = current.local().astimezone(zone).replace(tzinfo=None)
    parts = dict(part.split("=", 1) for part in recurrence.rule.split(";"))
    count = int(parts.pop("COUNT")) if "COUNT" in parts else None
    ending = _until(parts.pop("UNTIL")) if "UNTIL" in parts else None
    if recurrence.mode == "after_completion":
        if count is not None and recurrence.occurrence_index + 1 >= count:
            return None
        anchor = aware(completed_at).astimezone(zone).replace(tzinfo=None)
        cursor = anchor
    if abs(cursor - anchor) > _SEARCH_SPAN:
        raise DomainError("recurrence_limit", "recurrence catch-up exceeds 400 years")
    limit = cursor + min(_SEARCH_SPAN, datetime.max - cursor)
    artificial_limit = True
    if ending is not None:
        local_end = ending.astimezone(zone).replace(tzinfo=None) if ending.tzinfo else ending
        # UTC endings inside the second fold must retain an earlier first-fold slot.
        if ending.tzinfo:
            local_end += min(timedelta(days=1), datetime.max - local_end)
        if local_end <= limit:
            limit, artificial_limit = local_end, False
    body = ";".join(f"{key}={item}" for key, item in parts.items())
    rule = rrulestr(body, dtstart=anchor).replace(until=limit)
    following = None
    instant_cursor = completed_at if recurrence.mode == "after_completion" else current.at
    for ordinal, candidate in enumerate(rule, start=1):
        if recurrence.mode == "fixed" and count is not None and ordinal > count:
            return None
        if candidate > cursor:
            local = candidate.replace(tzinfo=zone, fold=0)
            if not datetime_exists(local):
                local = resolve_imaginary(local)
            # A later wall time can still be an earlier instant in the second fold.
            if instant_cursor is None or local.astimezone(UTC) > aware(instant_cursor).astimezone(
                UTC
            ):
                following = local
                break
        if recurrence.mode == "fixed" and count == ordinal:
            return None
    if following is None:
        if artificial_limit and limit < datetime.max:
            raise DomainError("recurrence_limit", "no recurrence found within 400-year lookahead")
        return None
    local = following
    if ending is not None:
        comparable = local.astimezone(UTC) if ending.tzinfo else local.replace(tzinfo=None)
        if comparable > ending:
            return None
    return (
        Temporal(date=local.date(), timezone=recurrence.timezone)
        if current.date
        else Temporal(at=local, timezone=recurrence.timezone)
    )


class SourceRef(Model):
    system: str = Field(min_length=1, max_length=100)
    kind: str = Field(min_length=1, max_length=100)
    id: str = Field(min_length=1, max_length=500)


class CheckItem(Model):
    id: str = Field(min_length=1, max_length=128)
    text: str = Field(min_length=1, max_length=2000)
    completed: bool = False


class Note(Model):
    """One append-only progress or context entry; the working memory of a task."""

    id: str = Field(min_length=1, max_length=128)
    at: datetime
    author: str | None = Field(default=None, max_length=200)
    text: str = Field(min_length=1, max_length=10000)

    @field_validator("at")
    @classmethod
    def valid_instant(cls, value):
        return aware(value)


class Entity(Model):
    id: str = Field(min_length=1, max_length=128)
    workspace: str = Field(min_length=1, max_length=128)
    kind: Literal[
        "task",
        "project",
        "goal",
        "person",
        "principal",
        "label",
        "context",
        "filter",
        "reminder",
        "memory",
    ]
    version: int = Field(default=1, ge=1)
    title: str = Field(default="", max_length=2000)
    # "proposed" is a suggestion awaiting a person's accept or dismiss; it is
    # never ready work and never fires reminders.
    status: Literal["proposed", "open", "waiting", "completed", "cancelled"] = "open"
    archived: bool = False
    # 0 is unset; 1 is most important and 4 least (the iCalendar convention).
    priority: int = Field(default=0, ge=0, le=4)
    description: str = Field(default="", max_length=100000)
    assignee_id: str | None = None
    beneficiary_ids: list[str] = Field(default_factory=list, max_length=100)
    parent_id: str | None = None
    project_id: str | None = None
    goal_id: str | None = None
    label_ids: list[str] = Field(default_factory=list, max_length=100)
    context_ids: list[str] = Field(default_factory=list, max_length=100)
    depends_on: list[str] = Field(default_factory=list, max_length=100)
    checklist: list[CheckItem] = Field(default_factory=list, max_length=1000)
    notes: list[Note] = Field(default_factory=list, max_length=1000)
    # Who or what an open item is waiting on; free text, never a permission grant.
    waiting_on: str | None = Field(default=None, max_length=500)
    estimate_minutes: int | None = Field(default=None, ge=1, le=525_600)
    # Whose move it is: the person does it, the person must answer or decide,
    # or an agent may carry it out. Unset means not yet decided; never inferred.
    next_move: Literal["person", "answer", "agent"] | None = None
    # An expiring working claim. It is advisory coordination, not authorization.
    claimed_by: str | None = Field(default=None, min_length=1, max_length=200)
    claim_until: datetime | None = None
    deadline: Temporal | None = None
    planned: Temporal | None = None
    earliest_start: Temporal | None = None
    latest_target: Temporal | None = None
    recurrence: Recurrence | None = None
    series_id: str | None = None
    occurrence_key: str | None = None
    source_refs: list[SourceRef] = Field(default_factory=list, max_length=100)
    metadata: dict[str, Any] = Field(default_factory=dict)
    filter: dict[str, Any] | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    completed_at: datetime | None = None
    task_id: str | None = None
    scheduled_at: datetime | None = None
    channel: str = Field(default="recording", max_length=100)
    recipient_id: str | None = None
    reminder_state: Literal[
        "pending",
        "leased",
        "delivered",
        "snoozed",
        "dismissed",
        "acknowledged",
        "cancelled",
        "failed",
    ] = "pending"
    attempts: int = Field(default=0, ge=0)
    retry_at: datetime | None = None
    lease_owner: str | None = None
    lease_token: str | None = None
    lease_until: datetime | None = None
    delivery_key: str | None = None
    error: str | None = None

    @field_validator(
        "created_at",
        "updated_at",
        "completed_at",
        "scheduled_at",
        "retry_at",
        "lease_until",
        "claim_until",
    )
    @classmethod
    def valid_instant(cls, value):
        return aware(value) if value else None

    @model_validator(mode="after")
    def valid_semantics(self):
        if self.kind == "reminder" and (not self.scheduled_at or not self.recipient_id):
            raise ValueError("reminder requires scheduled_at and recipient_id")
        if self.kind != "reminder" and (self.task_id or self.scheduled_at):
            raise ValueError("delivery fields require kind=reminder")
        # A civil earliest start begins its local day; a civil latest target
        # includes its whole local day. Exact latest instants remain inclusive.
        # Reuse civil lateness rather than turning a target date into midnight.
        earliest, latest = self.earliest_start, self.latest_target
        if earliest and latest and latest.is_before(earliest.local()):
            raise ValueError("earliest_start exceeds latest_target")
        if len({i.id for i in self.checklist}) != len(self.checklist):
            raise ValueError("checklist ids must be unique")
        if len({i.id for i in self.notes}) != len(self.notes):
            raise ValueError("note ids must be unique")
        if (self.claimed_by is None) != (self.claim_until is None):
            raise ValueError("claimed_by and claim_until are set together")
        if self.kind == "filter" and self.filter is None:
            raise ValueError("saved filter requires filter AST")
        return self


def utcnow():
    return datetime.now(UTC)


def normalize_now(now=None):
    if isinstance(now, str):
        now = datetime.fromisoformat(now)
    return aware(now or utcnow()).astimezone(UTC)


def retry_delay(attempts: int):
    return timedelta(seconds=min(3600, 2 ** min(attempts, 12)))
