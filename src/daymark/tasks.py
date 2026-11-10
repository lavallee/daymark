"""The simple surface: plain verbs over the typed engine.

``Tasks`` wraps a Store (or the HTTP Client) for callers that want to say
"add this, due Friday" and "what is ready?" without composing payloads. It
reads the current version itself, mints short IDs and operation IDs, and
resolves a small, explicit date grammar. It never guesses: an unrecognised
date or recurrence is an error that states the accepted forms.

Pass ``op`` to make a write safe to retry. A repeated ``op`` returns the first
result without writing again.
"""

from __future__ import annotations

import os
import re
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from dateutil.relativedelta import relativedelta
from dateutil.tz import datetime_exists, resolve_imaginary

from .models import DomainError, Temporal, normalize_now

_ALPHABET = "23456789abcdefghjkmnpqrstuvwxyz"  # No 0/o, 1/l/i.
_WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
_BYDAY = ["MO", "TU", "WE", "TH", "FR", "SA", "SU"]
_UNITS = {"d": "days", "w": "weeks", "m": "months", "y": "years"}
_FREQ = {"day": "DAILY", "week": "WEEKLY", "month": "MONTHLY", "year": "YEARLY"}

WHEN_FORMS = (
    "Accepted dates: YYYY-MM-DD, YYYY-MM-DDTHH:MM (local to the timezone) or with a "
    "UTC offset, today, tomorrow, a weekday name (the next one), +Nd, +Nw, +Nm, +Ny; "
    "any of the relative forms may be followed by HH:MM."
)
EVERY_FORMS = (
    "Accepted recurrences: day, week, month, year, 'N days|weeks|months|years', "
    "weekdays, a weekday list such as 'mon,thu', or an RRULE body such as "
    "'FREQ=MONTHLY;BYMONTHDAY=1'."
)


def local_timezone() -> str:
    """DAYMARK_TZ, then TZ, then the system zone, then UTC."""
    for name in (os.environ.get("DAYMARK_TZ"), os.environ.get("TZ")):
        if name:
            try:
                ZoneInfo(name)
                return name
            except (KeyError, ValueError, OSError):
                continue  # An unusable name falls through to the next source.
    try:
        target = os.path.realpath("/etc/localtime")
        if "/zoneinfo/" in target:
            name = target.split("/zoneinfo/", 1)[1]
            ZoneInfo(name)
            return name
    except (KeyError, ValueError, OSError):
        return "UTC"
    return "UTC"


def _weekday(word):
    matches = [i for i, name in enumerate(_WEEKDAYS) if len(word) >= 3 and name.startswith(word)]
    return matches[0] if len(matches) == 1 else None


def parse_when(text, timezone="UTC", now=None) -> dict:
    """Resolve the documented date grammar to a Temporal dict, or raise."""
    if isinstance(text, dict):
        return Temporal.model_validate(text).model_dump(mode="json")
    if not isinstance(text, str) or not text.strip():
        raise DomainError("invalid", "Unrecognized date", [WHEN_FORMS])
    zone, raw = ZoneInfo(timezone), text.strip()
    today = normalize_now(now).astimezone(zone).date()
    lowered = raw.lower()
    try:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw):
            return Temporal(date=raw, timezone=timezone).model_dump(mode="json")
        if re.match(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}", raw):
            at = datetime.fromisoformat(raw)
            if at.tzinfo is None:
                at = at.replace(tzinfo=zone)
            return Temporal(at=at, timezone=timezone).model_dump(mode="json")
        match = re.fullmatch(r"(\S+)(?:\s+(\d{1,2}):(\d{2}))?", lowered)
        if not match:
            raise ValueError
        word, hour, minute = match.groups()
        relative = re.fullmatch(r"\+(\d{1,4})([dwmy])", word)
        if word == "today":
            day = today
        elif word == "tomorrow":
            day = today + timedelta(days=1)
        elif relative:
            day = today + relativedelta(**{_UNITS[relative[2]]: int(relative[1])})
        elif _weekday(word) is not None:
            day = today + timedelta(days=(_weekday(word) - today.weekday() - 1) % 7 + 1)
        else:
            raise ValueError
        if hour is None:
            value = Temporal(date=day, timezone=timezone, expression=raw)
        else:
            at = datetime(day.year, day.month, day.day, int(hour), int(minute), tzinfo=zone)
            if not datetime_exists(at):
                at = resolve_imaginary(at)  # A clock time skipped by DST moves forward.
            value = Temporal(at=at, timezone=timezone, expression=raw)
        return value.model_dump(mode="json")
    except DomainError:
        raise
    except (ValueError, OverflowError):
        raise DomainError("invalid", "Unrecognized date", [WHEN_FORMS]) from None


def parse_every(text) -> str:
    """Resolve the documented recurrence shorthand to an RRULE body, or raise."""
    if not isinstance(text, str) or not text.strip():
        raise DomainError("invalid", "Unrecognized recurrence", [EVERY_FORMS])
    raw = text.strip()
    if raw.upper().startswith(("FREQ=", "RRULE:")):
        return raw.upper().removeprefix("RRULE:")
    lowered = raw.lower().removeprefix("every ").strip()
    if lowered in _FREQ:
        return f"FREQ={_FREQ[lowered]}"
    if lowered == "weekdays":
        return "FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR"
    match = re.fullmatch(r"(\d{1,4})\s*(day|week|month|year)s?", lowered)
    if match and int(match[1]) >= 1:
        return f"FREQ={_FREQ[match[2]]};INTERVAL={int(match[1])}"
    days = [_weekday(part.strip()) for part in lowered.split(",")]
    if days and all(day is not None for day in days):
        return "FREQ=WEEKLY;BYDAY=" + ",".join(_BYDAY[day] for day in sorted(set(days)))
    raise DomainError("invalid", "Unrecognized recurrence", [EVERY_FORMS])


_DATE_FIELDS = {
    "due": "deadline",
    "plan": "planned",
    "start": "earliest_start",
    "target": "latest_target",
}


class Tasks:
    """Plain verbs for one workspace. ``actor`` names who is acting, for claims and notes."""

    def __init__(self, engine, workspace="default", *, actor="agent", timezone=None, now=None):
        self.engine = engine
        # The HTTP client is already bound to a workspace by its credential.
        remote = type(engine).__name__ == "Client"
        self._ws = () if remote else (workspace,)
        # Over HTTP the server records its authenticated principal instead.
        self._who = {} if remote else {"actor": actor}
        self.actor = actor
        self.timezone = timezone or local_timezone()
        ZoneInfo(self.timezone)
        self._now = now

    # -- plumbing

    def now(self):
        return normalize_now(self._now)

    def _replayed(self, op):
        receipt = getattr(self.engine, "receipt", None)
        return receipt(*self._ws, op) if op and receipt else None

    def _new_id(self):
        for attempt in range(40):
            size = 4 + attempt // 4
            candidate = "dm-" + "".join(secrets.choice(_ALPHABET) for _ in range(size))
            try:
                self.engine.get(*self._ws, candidate)
            except Exception as exc:  # client and store raise different types
                if getattr(exc, "code", None) == "not_found" or (
                    getattr(exc, "status_code", None) == 404
                ):
                    return candidate
                raise
        return "dm-" + uuid.uuid4().hex

    def _act(self, id, action, fields=None, *, op=None, build=None):
        """Run an action against the current version, retrying if someone else wrote first."""
        replay = self._replayed(op)
        if replay is not None:
            return replay
        for attempt in range(4):
            current = self.engine.get(*self._ws, id)
            payload = {"id": id, **(build(current) if build else (fields or {}))}
            try:
                return self.engine.execute(
                    *self._ws,
                    op or uuid.uuid4().hex,
                    action,
                    payload,
                    expected_version=current["version"],
                    **self._who,
                )
            except Exception as exc:
                conflict = getattr(exc, "code", None) == "conflict" or (
                    getattr(exc, "status_code", None) == 409
                )
                if not conflict or attempt == 3:
                    raise
                if self.engine.get(*self._ws, id)["version"] == current["version"]:
                    raise  # A real refusal (for example a live claim), not a stale read.

    def _fields(self, values):
        """Translate friendly keyword names into entity fields."""
        out = {}
        for key, value in values.items():
            if value is None:
                continue
            if key in _DATE_FIELDS:
                out[_DATE_FIELDS[key]] = (
                    None if value == "" else parse_when(value, self.timezone, self._now)
                )
            elif key == "after":
                out["depends_on"] = [value] if isinstance(value, str) else list(value)
            elif key == "project":
                out["project_id"] = value or None
            elif key == "parent":
                out["parent_id"] = value or None
            elif key == "goal":
                out["goal_id"] = value or None
            elif key == "assignee":
                out["assignee_id"] = value or None
            elif key == "labels":
                out["label_ids"] = list(value)
            elif key == "contexts":
                out["context_ids"] = list(value)
            elif key == "minutes":
                out["estimate_minutes"] = value
            elif key == "move":
                out["next_move"] = value or None
            elif key == "scope":
                out["scope"] = value or None
            else:
                out[key] = value
        return out

    def _recurrence(self, every, fields, after_completion, index=0):
        anchor = (
            fields.get("planned")
            or fields.get("deadline")
            or parse_when("today", self.timezone, self._now)
        )
        anchor = {**anchor, "expression": None}
        return {
            "rule": parse_every(every),
            "mode": "after_completion" if after_completion else "fixed",
            "timezone": anchor["timezone"],
            "anchor": anchor,
            # Changing the rule mid-series keeps this round's place in it.
            "occurrence_index": index,
        }

    # -- capture

    def add(
        self,
        title,
        *,
        kind="task",
        every=None,
        after_completion=False,
        note=None,
        proposed=False,
        source=None,
        id=None,
        op=None,
        **fields,
    ):
        """Create an entity. Dates: due, plan, start, target. Links: project, parent,
        goal, after (dependencies). Also priority, description, minutes, move,
        waiting_on, labels, contexts, metadata. ``source`` is "system:kind:id"; a
        second add with the same source is refused, so captures are not duplicated.
        """
        replay = self._replayed(op)
        if replay is not None:
            return replay
        payload = {"kind": kind, "title": title, **self._fields(fields)}
        if every:
            payload["recurrence"] = self._recurrence(every, payload, after_completion)
            if not payload.get("planned") and not payload.get("deadline"):
                payload["planned"] = payload["recurrence"]["anchor"]
        if proposed:
            payload["status"] = "proposed"
        elif payload.get("waiting_on"):
            payload["status"] = "waiting"
        if source:
            parts = source.split(":", 2)
            if len(parts) != 3 or not all(parts):
                raise DomainError("invalid", "Source must be system:kind:id")
            payload["source_refs"] = [dict(zip(("system", "kind", "id"), parts))]
        if note:
            # Part of the same write, so a retry can never lose or repeat it.
            payload["notes"] = [
                {
                    "id": uuid.uuid4().hex[:12],
                    "at": normalize_now().isoformat(),
                    "author": self.actor,
                    "text": note,
                }
            ]
        payload["id"] = id or self._new_id()
        return self.engine.execute(
            *self._ws, op or uuid.uuid4().hex, "create", payload, expected_version=0, **self._who
        )

    def propose(self, title, **fields):
        """Suggest a task for a person to accept or dismiss. It is not ready work."""
        return self.add(title, proposed=True, **fields)

    def remember(self, text, *, op=None):
        """Keep a durable fact that ``prime`` hands to every later session."""
        return self.add(text, kind="memory", op=op)

    def memories(self):
        return self.engine.list(*self._ws, kind="memory")

    def forget(self, id, *, op=None):
        return self._act(id, "archive", op=op)

    # -- change

    def edit(self, id, *, every=None, after_completion=False, op=None, **fields):
        """Change fields. Pass an empty string to clear a date or link."""
        changes = self._fields({k: v for k, v in fields.items()})

        def build(current):
            out = dict(changes)
            if every == "":
                out["recurrence"] = None
            elif every:
                merged = {**current, **out}
                index = (current["recurrence"] or {}).get("occurrence_index", 0)
                out["recurrence"] = self._recurrence(every, merged, after_completion, index)
            return out

        return self._act(id, "update", op=op, build=build)

    def note(self, id, text, *, op=None):
        """Append a progress note: what was done, learned, or left for the next session."""
        return self._act(id, "note", {"text": text, "author": self.actor}, op=op)

    def done(self, id, *, note=None, op=None):
        if note:
            self.note(id, note, op=(op + ":note") if op else None)
        return self._act(id, "complete", op=op)

    def skip(self, id, *, op=None):
        """Pass over one occurrence of a recurring task without recording completion."""
        return self._act(id, "skip", op=op)

    def cancel(self, id, *, op=None):
        return self._act(id, "cancel", op=op)

    def reopen(self, id, *, op=None):
        return self._act(id, "reopen", op=op)

    def archive(self, id, *, op=None):
        return self._act(id, "archive", op=op)

    def undo(self, id, *, op=None):
        return self._act(id, "undo", op=op)

    def accept(self, id, *, op=None):
        """Turn a proposal into an open task. A person's decision, not an agent's."""
        return self._act(id, "accept", op=op)

    def dismiss(self, id, *, op=None):
        return self._act(id, "dismiss", op=op)

    def claim(self, id, *, minutes=60, op=None):
        """Take a task so parallel workers do not collide. Fails if another holds it."""
        return self._act(
            id, "claim", {"owner": self.actor, "lease_seconds": int(minutes) * 60}, op=op
        )

    def release(self, id, *, op=None):
        return self._act(id, "release", {"owner": self.actor}, op=op)

    def wait(self, id, on, *, until=None, op=None):
        """Park a task on someone or something else; optionally hide it until a date."""
        changes = {"status": "waiting", "waiting_on": on}
        if until:
            changes["earliest_start"] = parse_when(until, self.timezone, self._now)
        return self._act(id, "update", changes, op=op)

    def resume(self, id, *, op=None):
        """Stop waiting and make the task actionable now, lifting a future start date."""

        def build(current):
            out = {"status": "open", "waiting_on": None}
            if current["earliest_start"]:
                start, now = Temporal.model_validate(current["earliest_start"]), self.now()
                today = now.astimezone(ZoneInfo(start.timezone)).date()
                if (start.date and start.date > today) or (start.at and start.at > now):
                    out["earliest_start"] = None
            return out

        return self._act(id, "update", op=op, build=build)

    def defer(self, id, until, *, op=None):
        """Hide a task from ready work until a date. Its deadline does not move."""
        return self._act(
            id, "update", {"earliest_start": parse_when(until, self.timezone, self._now)}, op=op
        )

    def block(self, id, on, *, op=None):
        """Record that ``id`` cannot start until ``on`` is completed or cancelled."""
        return self._act(
            id,
            "update",
            op=op,
            build=lambda current: {"depends_on": sorted({*current["depends_on"], on})},
        )

    def unblock(self, id, on, *, op=None):
        return self._act(
            id,
            "update",
            op=op,
            build=lambda current: {"depends_on": [x for x in current["depends_on"] if x != on]},
        )

    def remind(self, id, when, *, recipient, channel="recording", op=None):
        """Schedule a reminder about a task for a person or principal entity."""
        at = parse_when(when, self.timezone, self._now)
        value = Temporal.model_validate(at)
        if value.at is None:
            raise DomainError("invalid", "A reminder needs a time", [WHEN_FORMS])
        return self.add(
            "",
            kind="reminder",
            task_id=id,
            scheduled_at=value.at.astimezone(UTC).isoformat(),
            recipient_id=recipient,
            channel=channel,
            op=op,
        )

    # -- read

    def get(self, id):
        return self.engine.get(*self._ws, id)

    def show(self, id, history=10):
        return self.engine.context(*self._ws, id, history=history)

    def ready(self, *, limit=20, move=None, mine=True, scope=None):
        """``scope`` narrows to one scope; ``""`` to tasks that have none."""
        return self.engine.ready(
            *self._ws,
            now=self._now,
            limit=limit,
            owner=self.actor if mine else None,
            next_move=move,
            **({} if scope is None else {"scope": scope}),
        )

    def agenda(self, days=7, *, scope=None):
        extra = {} if scope is None else {"scope": scope}
        return self.engine.agenda(*self._ws, now=self._now, days=days, **extra)

    def summary(self, *, scope=None):
        extra = {} if scope is None else {"scope": scope}
        return self.engine.summary(*self._ws, now=self._now, **extra)

    def focus(self, id, rank=None, *, op=None):
        """Put a task on the person's short list. Without a rank it goes last."""
        if rank is None:
            placed = [x["focus"] for x in self.find(status="open", limit=10**6) if x["focus"]]
            rank = max(placed, default=0) + 1
        return self._act(id, "update", {"focus": rank}, op=op)

    def unfocus(self, id, *, op=None):
        return self._act(id, "update", {"focus": None}, op=op)

    def find(self, text=None, *, kind="task", status=None, limit=50):
        """Case-insensitive title or description search within one kind."""
        clauses = [{"field": "kind", "op": "eq", "value": kind}]
        if status:
            clauses.append({"field": "status", "op": "eq", "value": status})
        items = self.engine.query(*self._ws, {"and": clauses})
        if text:
            needle = text.lower()
            items = [
                x
                for x in items
                if needle in x["title"].lower() or needle in x["description"].lower()
            ]
        return items[:limit]
