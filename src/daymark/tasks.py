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

from . import plans
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


STAGE_FORMS = (
    "Stages: comma-separated id:label:status:color, for example "
    "'todo:To do:open:grey,doing:Doing:open:blue,done:Done:completed:green'. "
    "Status is open, waiting, completed or cancelled (default open); label and colour "
    'are optional. Include at least one open and one completed stage; "" restores '
    "the defaults."
)


def parse_stages(spec) -> list[dict]:
    """A stage list from a list of dicts or the documented text form, or raise."""
    if isinstance(spec, list):
        return spec
    if not isinstance(spec, str):
        raise DomainError("invalid", "Unrecognized stages", [STAGE_FORMS])
    out = []
    for part in (p.strip() for p in spec.split(",") if p.strip()):
        bits = [b.strip() for b in part.split(":")]
        if len(bits) > 4 or not bits[0]:
            raise DomainError("invalid", "Unrecognized stages", [STAGE_FORMS])
        stage = {"id": bits[0], "label": bits[1] if len(bits) > 1 and bits[1] else bits[0]}
        stage["status"] = bits[2] if len(bits) > 2 and bits[2] else "open"
        if len(bits) > 3 and bits[3]:
            stage["color"] = bits[3]
        out.append(stage)
    return out


TYPE_FORMS = (
    "Types: comma-separated id:label[:color][:container], for example "
    "'epic:Epic:violet:container,story:Story:blue,bug:Bug:red'. \"\" removes them."
)
FIELD_FORMS = (
    "Fields: comma-separated id:label:type[:options or unit], for example "
    "'points:Points:number,risk:Risk:option:low|medium|high,spec:Spec:url'. Types are "
    "text, number, option, options, date, bool, person and url; options are separated "
    'by |; a number may name its unit. "" removes them.'
)


def parse_types(spec) -> list[dict]:
    if isinstance(spec, list):
        return spec
    if not isinstance(spec, str):
        raise DomainError("invalid", "Unrecognized types", [TYPE_FORMS])
    out = []
    for part in (p.strip() for p in spec.split(",") if p.strip()):
        bits = [b.strip() for b in part.split(":")]
        if not bits[0] or len(bits) > 4:
            raise DomainError("invalid", "Unrecognized types", [TYPE_FORMS])
        item = {"id": bits[0], "label": bits[1] if len(bits) > 1 and bits[1] else bits[0]}
        for extra in bits[2:]:
            if extra == "container":
                item["container"] = True
            elif extra:
                item["color"] = extra
        out.append(item)
    return out


def parse_fields(spec) -> list[dict]:
    if isinstance(spec, list):
        return spec
    if not isinstance(spec, str):
        raise DomainError("invalid", "Unrecognized fields", [FIELD_FORMS])
    out = []
    for part in (p.strip() for p in spec.split(",") if p.strip()):
        bits = [b.strip() for b in part.split(":")]
        if len(bits) < 3 or len(bits) > 4 or not bits[0]:
            raise DomainError("invalid", "Unrecognized fields", [FIELD_FORMS])
        item = {"id": bits[0], "label": bits[1] or bits[0], "type": bits[2]}
        if len(bits) == 4 and bits[3]:
            if bits[2] in {"option", "options"}:
                item["options"] = [
                    {"id": o.strip(), "label": o.strip()} for o in bits[3].split("|") if o.strip()
                ]
            else:
                item["unit"] = bits[3]
        out.append(item)
    return out


def parse_config(*, preset=None, stages=None, types=None, fields=None) -> dict:
    """A configuration from a preset name and/or parts; parts override the preset."""

    config = {}
    if preset is not None:
        if preset not in plans.PRESETS:
            raise DomainError("invalid", "Unknown preset", sorted(plans.PRESETS))
        config.update(plans.PRESETS[preset]["config"])
    for key, value, parse in (
        ("stages", stages, parse_stages),
        ("types", types, parse_types),
        ("fields", fields, parse_fields),
    ):
        if value is not None:
            config[key] = parse(value)
    return config


def _number(key, text):
    """A custom number from text: a whole number kept exact within ±(2**53 - 1), else
    a finite decimal. Anything else is refused, never rounded or guessed."""
    from .models import MAX_EXACT, plain_number

    hint = f"Field {key} takes {plans.FIELD_HINTS['number']}"
    if re.fullmatch(r"[+-]?\d+", text):
        value = int(text)
    else:
        try:
            value = float(text)
        except ValueError:
            raise DomainError("invalid", hint) from None
        if (
            value.is_integer()
            and abs(value) <= MAX_EXACT
            and "." not in text
            and "e" not in text.lower()
        ):
            value = int(value)
    if not plain_number(value):
        raise DomainError("invalid", hint)
    return value


def parse_measure(spec):
    if isinstance(spec, dict) or spec is None:
        return spec
    bits = [b.strip() for b in str(spec).split(":")]
    if len(bits) < 2 or len(bits) > 4 or not bits[0]:
        raise DomainError("invalid", "A measure is Label:target[:current[:unit]]")
    try:
        numbers = [float(b) for b in bits[1:3] if b != ""]
    except ValueError:
        raise DomainError("invalid", "A measure is Label:target[:current[:unit]]") from None
    out = {"label": bits[0], "target": numbers[0]}
    if len(numbers) > 1:
        out["current"] = numbers[1]
    if len(bits) == 4 and bits[3]:
        out["unit"] = bits[3]
    return out


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

    @staticmethod
    def _missing(exc):
        return getattr(exc, "code", None) == "not_found" or (
            getattr(exc, "status_code", None) == 404
        )

    def _person(self, value):
        """A person's ID from an ID or a name (exact, ignoring case). An unknown name is
        refused rather than guessed or created."""
        try:
            found = self.engine.get(*self._ws, value)
            if found["kind"] in {"person", "principal"}:
                return found["id"]
        except Exception as exc:  # client and store raise different types
            if not self._missing(exc):
                raise
        wanted = value.strip().casefold()
        matches = [
            item["id"]
            for kind in ("person", "principal")
            for item in self.engine.list(*self._ws, kind=kind)
            if item["title"].strip().casefold() == wanted
        ]
        if len(matches) > 1:
            raise DomainError(
                "invalid", "Several people have that name; use an ID", sorted(matches)
            )
        if matches:
            return matches[0]
        raise DomainError(
            "invalid",
            "No person in this workspace has that ID or name",
            ['Add them first: daymark add "NAME" --kind person'],
        )

    def _resolve(self, fields):
        if "lead" in fields:  # A project's lead is its assignee.
            fields = {
                **{k: v for k, v in fields.items() if k != "lead"},
                "assignee": fields["lead"],
            }
        if fields.get("assignee"):
            fields = {**fields, "assignee": self._person(fields["assignee"])}
        return fields

    def parse_values(self, project, pairs):
        """Custom field values from ``id=value`` text, typed by the project's fields:
        numbers, yes/no, comma lists for multi-option fields, a person's name or ID.
        An empty value clears the field. Unknown fields are left for the engine to
        refuse with the list of declared ones."""
        declared = {}
        if project:
            declared = {f["id"]: f for f in self.engine.get(*self._ws, project)["fields"]}
        out = {}
        for pair in pairs or []:
            key, sep, text = pair.partition("=")
            key = key.strip()
            if not sep or not key:
                raise DomainError("invalid", "Set a field as id=value", sorted(declared))
            kind = declared.get(key, {}).get("type")
            text = text.strip()
            if text == "":
                out[key] = None
            elif kind == "number":
                out[key] = _number(key, text)
            elif kind == "bool":
                lowered = text.lower()
                if lowered not in {"yes", "no", "true", "false"}:
                    raise DomainError("invalid", f"Field {key} takes yes or no")
                out[key] = lowered in {"yes", "true"}
            elif kind == "options":
                out[key] = [part.strip() for part in text.split(",") if part.strip()]
            elif kind == "person":
                out[key] = self._person(text)
            else:
                out[key] = text
        return out

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
            elif key in {"scope", "stage", "managed_by", "type"}:
                out[key] = value or None
            elif key == "milestone" and not isinstance(value, bool):
                out["milestone_id"] = value or None  # Membership: the milestone it counts toward.
            elif key in {"milestone", "is_milestone"}:
                out["milestone"] = bool(value)  # The marker: this task is a milestone.
            elif key == "values":
                out["values"] = dict(value)
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
        payload = {"kind": kind, "title": title, **self._fields(self._resolve(fields))}
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
        replay = self._replayed(op)
        if replay is not None:
            return replay
        changes = self._fields(self._resolve(dict(fields)))

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

    # -- projects

    def project(
        self, title, *, preset=None, stages=None, types=None, fields=None, op=None, **attrs
    ):
        """Create a project. A bare project is a list on To do, Waiting and Done.

        ``preset`` (see ``presets``) writes a starting configuration and nothing
        else; ``stages``, ``types`` and ``fields`` (lists or text specs) override
        it. ``lead``, ``start``, ``target``, ``due`` and ``goal`` set its facts.
        ``managed_by`` names a system that owns and rewrites it.
        """
        config = parse_config(preset=preset, stages=stages, types=types, fields=fields)
        return self.add(title, kind="project", op=op, **attrs, **config)

    @staticmethod
    def presets():
        return {
            name: {"description": p["description"], **p["config"]}
            for name, p in plans.PRESETS.items()
        }

    def config(self, project):
        """A project's stages, types and fields as one copyable document."""
        return plans.config(self.engine.get(*self._ws, project))

    def configure(self, project, *, preset=None, stages=None, types=None, fields=None, op=None):
        """Replace parts of a project's configuration in one write: the parts given
        (or a preset's) replace what is there; the rest stays. Tasks keep their data:
        a stage that is gone sends its tasks to their status's first stage, and a type
        or field that is gone is reported by the plan as a warning, never dropped."""
        config = parse_config(preset=preset, stages=stages, types=types, fields=fields)
        if not config:
            raise DomainError("invalid", "Give a preset, stages, types or fields")
        return self._act(project, "update", config, op=op)

    def set_stages(self, project, stages, *, op=None):
        """Replace a project's stages; an empty list or "" restores the defaults."""
        return self.configure(project, stages=stages, op=op)

    def goal(self, title, *, target=None, measure=None, op=None, **attrs):
        """Create a goal. ``target`` is its date; ``measure`` an optional single number
        it is judged by ("Label:target[:current[:unit]]" or a dict)."""
        if measure is not None:
            attrs["measure"] = parse_measure(measure)
        return self.add(title, kind="goal", target=target, op=op, **attrs)

    def goal_plan(self, goal):
        return self.engine.goal_plan(*self._ws, goal, now=self._now)

    def goals(self):
        return self.engine.goals(*self._ws, now=self._now)

    def tree(self, id, depth=3):
        return self.engine.tree(*self._ws, id, depth=depth)

    def schedule(self, id, *, op=None, **dates):
        """Move a task's plan dates: ``start``, ``end``, ``deadline`` as YYYY-MM-DD
        (None clears). Writes planned and latest_target for start and end, keeps a
        timed value's clock time, and moves a deadline only when given one."""

        def build(current):
            return plans.schedule(current, timezone=self.timezone, **dates)

        return self._act(id, "update", op=op, build=build)

    def move(self, id, *, stage=None, after=None, before=None, first=False, last=False, op=None):
        """Put a task in a stage of its project's board, and/or at a place in its order.

        A stage sets the task's status to the stage's status, as the matching verb
        would. ``after``/``before`` name a neighbour; ``first``/``last`` the ends. Both
        parts are one transaction.
        """
        places = [
            ({"after": after} if after else None),
            ({"before": before} if before else None),
            ({"after": None} if first else None),
            ({"before": None} if last else None),
        ]
        places = [p for p in places if p is not None]
        if len(places) > 1:
            raise DomainError("invalid", "Give at most one of after, before, first or last")
        if stage is None and not places:
            raise DomainError("invalid", "Give a stage, a place, or both")
        place = places[0] if places else None
        if stage is None:
            return self._act(id, "place", place, op=op)
        if place is None:
            return self._act(id, "update", {"stage": stage}, op=op)
        replay = self._replayed(op)
        if replay is not None:
            return self._replayed(op + ":place") or replay
        base = op or uuid.uuid4().hex
        for attempt in range(4):
            version = self.engine.get(*self._ws, id)["version"]
            operations = [
                {
                    "operation_id": base,
                    "action": "update",
                    "payload": {"id": id, "stage": stage},
                    "expected_version": version,
                },
                {
                    "operation_id": base + ":place",
                    "action": "place",
                    "payload": {"id": id, **place},
                    "expected_version": version + 1,
                },
            ]
            result = self.engine.batch(*self._ws, operations, atomic=True, **self._who)
            if result["committed"]:
                return result["results"][-1]["result"]
            error = next(r["error"] for r in result["results"] if r["error"]["code"] != "aborted")
            stale = error["code"] == "conflict" and (
                self.engine.get(*self._ws, id)["version"] != version
            )
            if not stale or attempt == 3:
                raise DomainError(error["code"], error["message"], error["details"])

    def plan(self, project):
        return self.engine.plan(*self._ws, project, now=self._now)

    def projects(self):
        return self.engine.projects(*self._ws, now=self._now)

    def changes(self, project, after):
        return self.engine.changes(*self._ws, project, after)

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
