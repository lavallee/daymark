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
from .models import DomainError, SourceRef, Temporal, normalize_now

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

    def __init__(self, engine, workspace="default", *, actor=None, timezone=None, now=None):
        self.engine = engine
        self.workspace = workspace
        # The HTTP client is already bound to a workspace by its credential.
        remote = type(engine).__name__ == "Client"
        self._ws = () if remote else (workspace,)
        # Over HTTP the server records its authenticated principal instead.
        self._take_identity = remote or actor is not None
        self.actor = "agent" if actor is None else actor
        self._who = {} if remote else {"actor": self.actor}
        self.timezone = timezone or local_timezone()
        ZoneInfo(self.timezone)
        self._now = now

    # -- plumbing

    def now(self):
        return normalize_now(self._now)

    @staticmethod
    def _op_id(op):
        if op is None:
            return uuid.uuid4().hex
        if not isinstance(op, str) or not 1 <= len(op) <= 200:
            raise DomainError("invalid", "Operation ID must be 1 to 200 characters")
        return op

    @staticmethod
    def _part_id(base, part):
        key = f"{base}:{part}"
        return key if len(key) <= 200 else str(uuid.uuid5(uuid.NAMESPACE_URL, "daymark:" + key))

    def _retry_context(self, op, action, id=None):
        """Look up the original read, never trust an arbitrary result under the same ID."""
        lookup = getattr(self.engine, "receipt_context", None)
        if op is None or not lookup:
            return None
        return lookup(*self._ws, self._op_id(op), action=action, entity_id=id, **self._who)

    @staticmethod
    def _retry_dates(fields, result, date_keys):
        # Keep the first absolute value when the caller retries the same relative
        # expression in the same timezone. The kernel still compares the entire
        # original request, including which fields were supplied.
        out = dict(fields)
        for key in date_keys:
            value, saved = fields.get(key), result.get(key)
            if (
                isinstance(value, dict) and isinstance(saved, dict)
                and value.get("expression") and value["expression"] == saved.get("expression")
                and value["timezone"] == saved["timezone"]
            ):
                out[key] = saved
        return out

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

    def _act(self, id, action, fields=None, *, op=None, build=None, expected_version=None, date_keys=(), review_token=None):
        """Apply the caller's intent or verify it against the original kernel receipt."""
        if expected_version is not None and (type(expected_version) is not int or expected_version < 1):
            raise DomainError("invalid", "expected_version must be a positive integer")
        key = self._op_id(op)
        for attempt in range(4):
            retry = self._retry_context(op, action, id)
            current = retry["before"] if retry else self.engine.get(*self._ws, id)
            changes = build(current, retry) if build else (fields or {})
            if review_token is not None:
                changes = {**changes, "review_token": review_token}
            if retry:
                changes = self._retry_dates(changes, retry["result"], date_keys)
            version = expected_version if expected_version is not None else current["version"]
            try:
                return self.engine.execute(
                    *self._ws, key, action, {"id": id, **changes}, expected_version=version,
                    **self._who,
                )
            except Exception as exc:
                if getattr(exc, "code", None) != "conflict" or retry or attempt == 3:
                    raise
                if op and self._retry_context(op, action, id):
                    continue  # A simultaneous request recorded its result after our first read.
                if review_token is not None or expected_version is not None or self.engine.get(*self._ws, id)["version"] == version:
                    raise  # Stale explicit precondition or a domain refusal such as a live claim.

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
            elif key == "advances":
                names = [value] if isinstance(value, str) else list(value)
                out["advances"] = [x for x in names if x]  # "" clears
            elif key == "milestone" and not isinstance(value, bool):
                out["milestone_id"] = value or None  # Membership: the milestone it counts toward.
            elif key in {"milestone", "is_milestone"}:
                out["milestone"] = bool(value)  # The marker: this task is a milestone.
            elif key == "values":
                out["values"] = dict(value)
            else:
                out[key] = value
        return out

    def _recurrence(self, every, fields, after_completion, index=0, fallback=None):
        anchor = (
            fields.get("planned")
            or fields.get("deadline")
            or fallback
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
        retry = self._retry_context(op, "create", id)
        payload = {"kind": kind, "title": title, **self._fields(self._resolve(fields))}
        if retry:
            date_keys = [_DATE_FIELDS[k] for k, v in fields.items() if k in _DATE_FIELDS and isinstance(v, str)]
            payload = self._retry_dates(payload, retry["result"], date_keys)
        if every:
            original = (retry["result"].get("recurrence") or {}) if retry else {}
            payload["recurrence"] = self._recurrence(
                every, payload, after_completion, fallback=original.get("anchor"),
            )
            if not payload.get("planned") and not payload.get("deadline"):
                payload["planned"] = payload["recurrence"]["anchor"]
        if proposed:
            payload["status"] = "proposed"
        elif payload.get("waiting_on"):
            payload["status"] = "waiting"
        if source is not None:
            payload["source_refs"] = [SourceRef.parse_ref(source).model_dump()]
        if note:
            # Part of the same write, so a retry can never lose or repeat it.
            saved = next(iter(retry["result"]["notes"]), {}) if retry else {}
            payload["notes"] = [
                {
                    "id": saved.get("id") or uuid.uuid4().hex[:12],
                    "at": normalize_now(saved.get("at")).isoformat(),
                    "author": self.actor,
                    "text": note,
                }
            ]
        payload["id"] = id or (retry["result"]["id"] if retry else self._new_id())
        try:
            return self.engine.execute(
                *self._ws, self._op_id(op), "create", payload, expected_version=0, **self._who
            )
        except Exception as exc:
            if retry or not op or getattr(exc, "code", None) != "conflict":
                raise
            if not self._retry_context(op, "create", id):
                raise
            return self.add(
                title, kind=kind, every=every, after_completion=after_completion, note=note,
                proposed=proposed, source=source, id=id, op=op, **fields,
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

    def edit(self, id, *, every=None, after_completion=False, op=None, expected_version=None, review_token=None, **fields):
        """Edit fields, optionally refusing if the version observed by the caller is stale."""
        changes = self._fields(self._resolve(dict(fields)))

        def build(current, retry):
            date_keys = [_DATE_FIELDS[k] for k, v in fields.items() if k in _DATE_FIELDS and isinstance(v, str)]
            out = self._retry_dates(changes, retry["result"], date_keys) if retry else dict(changes)
            if every == "":
                out["recurrence"] = None
            elif every:
                merged = {**current, **out}
                index = (current["recurrence"] or {}).get("occurrence_index", 0)
                fallback = ((retry["result"].get("recurrence") or {}).get("anchor") if retry else None)
                out["recurrence"] = self._recurrence(every, merged, after_completion, index, fallback)
            return out

        return self._act(id, "update", op=op, build=build, expected_version=expected_version, review_token=review_token)

    def note(self, id, text, *, op=None):
        """Append a progress note: what was done, learned, or left for the next session."""
        return self._act(id, "note", {"text": text, "author": self.actor}, op=op)

    def _with_note(self, id, action, fields, note, op):
        base = self._op_id(op)
        note_op = str(uuid.uuid5(uuid.NAMESPACE_URL, "daymark:note:" + base))
        # 0.5.0 appended the closing note before completing. Validate its own
        # receipt before finishing an interrupted legacy operation.
        legacy_op = base + ":note"
        old_note = (
            op and action == "complete" and len(legacy_op) <= 200
            and self._retry_context(legacy_op, "note", id)
        )
        if not note:
            if old_note or (op and self._retry_context(note_op, "note", id)):
                raise DomainError("conflict", "Operation ID belongs to a request with a note")
            return self._act(id, action, fields, op=op)
        if old_note:
            self.note(id, note, op=legacy_op)
            return self._act(id, action, fields, op=op)
        return self._paired_actions(
            id, (note_op, "note", {"text": note, "author": self.actor}), (base, action, fields),
            retryable=op is not None,
        )

    def done(self, id, *, note=None, op=None):
        return self._with_note(id, "complete", {}, note, op)

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

    def accept(self, id, *, op=None, expected_version=None, review_token=None):
        """Accept the reviewed proposal, retaining a recorded wait."""
        return self._act(id, "accept", op=op, expected_version=expected_version, review_token=review_token)

    def dismiss(self, id, *, op=None, expected_version=None, review_token=None):
        return self._act(id, "dismiss", op=op, expected_version=expected_version, review_token=review_token)

    def claim(self, id, *, minutes=60, op=None):
        """Take a task so parallel workers do not collide. Fails if another holds it."""
        return self._act(
            id, "claim", {"owner": self.actor, "lease_seconds": int(minutes) * 60}, op=op
        )

    def take(self, *, scope=None, context=None, minutes=60, op=None):
        """Claim the first unclaimed ready task explicitly delegated to an agent.

        Selection and claim share one transaction. Returns None for an empty
        queue. Repeating an operation returns its original selection or None.
        """
        if not self._take_identity:
            raise DomainError("invalid", "Choose a distinct worker with --as NAME or DAYMARK_ACTOR")
        if type(minutes) is not int or not 1 <= minutes <= 10080:
            raise DomainError("invalid", "Claim minutes must be between 1 and 10080")
        selection = {"owner": self.actor, "scope": scope, "lease_seconds": minutes * 60}
        if context is not None:
            selection["context"] = context
        result = self.engine.execute(
            *self._ws, self._op_id(op), "take", selection,
            expected_version=0, **self._who,
        )
        return result["task"]

    def release(self, id, *, note=None, op=None):
        """Give back a claim, optionally recording a handoff note in the same transaction."""
        return self._with_note(id, "release", {"owner": self.actor}, note, op)

    def wait(self, id, on, *, until=None, op=None):
        """Park a task on someone or something else; optionally hide it until a date."""
        changes = {"status": "waiting", "waiting_on": on}
        if until:
            changes["earliest_start"] = parse_when(until, self.timezone, self._now)
        return self._act(id, "update", changes, op=op,
                         date_keys=("earliest_start",) if isinstance(until, str) else ())

    def resume(self, id, *, op=None):
        """Stop waiting and make the task actionable now, lifting a future start date."""

        def build(current, retry):
            out = {"status": "open", "waiting_on": None}
            if retry:
                if current["earliest_start"] and not retry["result"]["earliest_start"]:
                    out["earliest_start"] = None
                return out
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
            id, "update", {"earliest_start": parse_when(until, self.timezone, self._now)}, op=op,
            date_keys=("earliest_start",) if isinstance(until, str) else ()
        )

    def block(self, id, on, *, op=None):
        """Record that ``id`` cannot start until ``on`` is completed or cancelled."""
        return self._act(
            id,
            "update",
            op=op,
            build=lambda current, retry: {"depends_on": sorted({*current["depends_on"], on})},
        )

    def unblock(self, id, on, *, op=None):
        return self._act(
            id,
            "update",
            op=op,
            build=lambda current, retry: {"depends_on": [x for x in current["depends_on"] if x != on]},
        )

    def remind(self, id, when, *, recipient, channel="recording", op=None):
        """Schedule a reminder about a task for a person or principal entity."""
        at = parse_when(when, self.timezone, self._now)
        retry = self._retry_context(op, "create")
        if retry and isinstance(when, str) and at.get("expression"):
            saved = retry["result"]
            original = parse_when(when, self.timezone, saved["created_at"])
            if original.get("at") == saved.get("scheduled_at"):
                at = original
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

    def activity(self, *, after=None, limit=20):
        """Recent workspace changes, or the next page after a saved cursor."""
        return self.engine.activity(*self._ws, after=after, limit=limit)

    def show(self, id=None, history=10, *, source=None):
        return self.engine.context(
            *self._ws, id, history=history, now=self._now, **self._read_owner(),
            **({"source": source} if source is not None else {}),
        )

    def brief(self, id=None, *, before_note=None, source=None):
        """A bounded task handoff, including the latest attributed note previews."""
        return self.engine.brief(
            *self._ws, id, now=self._now, before_note=before_note, **self._read_owner(),
            **({"source": source} if source is not None else {}),
        )

    def changes_since(self, id=None, *, version, source=None):
        """Read net changes since the exact version a person reviewed."""
        return self.engine.changes_since(*self._ws, id, version=version, source=source)

    def read_note(self, id, note_id, *, source=None):
        """Read one complete note using the ID from a brief."""
        return self.engine.read_note(*self._ws, id, note_id, **({"source": source} if source is not None else {}))

    def _read_owner(self, mine=True):
        if not mine:
            return {}
        return {"owner": self.actor} if self._ws else {"mine": True, "owner": self.actor}

    def ready(self, *, limit=20, move=None, mine=True, context=None, scope=None, brief=False):
        """``scope`` narrows to one scope; ``""`` to tasks that have none."""
        return self.engine.ready(
            *self._ws,
            now=self._now,
            limit=limit,
            **self._read_owner(mine),
            **({"context": context} if context is not None else {}),
            next_move=move,
            **({"brief": True} if brief else {}),
            **({} if scope is None else {"scope": scope}),
        )

    def explain_ready(self, *, limit=20, move=None, mine=True, context=None, scope=None):
        """Count actionable and held work and name examples of each holding reason."""
        return self.engine.explain_ready(
            *self._ws, now=self._now, limit=limit, next_move=move, scope=scope, context=context,
            **self._read_owner(mine),
        )

    def ready_summary(self, *, limit=20, move=None, mine=True, context=None, scope=None):
        """Orientation-sized records, in precisely the same order as ready."""
        return self.ready(limit=limit, move=move, mine=mine, scope=scope, context=context, brief=True)

    def agenda(self, days=7, *, context=None, scope=None):
        extra = {} if scope is None else {"scope": scope}
        if context is not None:
            extra["context"] = context
        return self.engine.agenda(*self._ws, now=self._now, days=days, **extra)

    def advise(self, *, profile=None, settings=(), drops=(), scope=None, context=None):
        """Run the chosen profile with one-call adjustments, without saving them."""
        from . import strategies
        from .strategies.preferences import select_profile

        chosen, source = select_profile(self, profile, settings=settings, drops=drops)
        advice = strategies.advise(self, chosen, scope=scope, context=context)
        advice.profile_source = source
        return advice

    def profile(self, name=None, *, settings=(), drops=(), op=None, expected_version=None, undo=None):
        """Read or adjust the active workspace profile."""
        from .strategies.preferences import active_profile

        return active_profile(
            self, name, settings=settings, drops=drops, op=op, expected_version=expected_version, undo=undo,
        )

    def saved_profiles(self, name=None):
        """List named profiles, or inspect one saved configuration and version."""
        from .strategies.preferences import saved_profiles

        entries = saved_profiles(self)
        if name is None:
            return entries
        for entry in entries:
            if entry["name"] == name:
                return entry
        raise DomainError("not_found", "No saved profile with that name", [name])

    def profile_catalog(self, *, strategy=None):
        """Discover profiles and adjustment defaults, or inspect one strategy's parameters."""
        from .strategies import available, describe
        from .strategies.profiles import PROFILES

        if strategy is not None:
            return describe(strategy)
        saved = self.saved_profiles() if hasattr(self.engine, "setting") else None

        return {
            "active": self.profile(),
            "builtins": list(PROFILES),
            "saved": [{k: entry[k] for k in ("name", "version")} for entry in saved]
            if saved is not None else None,
            "adjustments": {s.name: {key: p.default for key, p in s.params.items()} for s in available()},
        }

    def save_profile(self, name, *, profile=None, settings=(), drops=(), expected_version=0, op=None):
        """Save a reusable copy. Replacing a copy requires its current version."""
        from .strategies.preferences import save_profile

        return save_profile(
            self, name, profile=profile, settings=settings, drops=drops,
            expected_version=expected_version, op=op,
        )

    def remove_profile(self, name, *, expected_version, op=None):
        """Remove a named copy at the version the caller observed."""
        from .strategies.preferences import remove_profile

        return remove_profile(self, name, expected_version=expected_version, op=op)

    def summary(self, *, context=None, scope=None):
        extra = {} if scope is None else {"scope": scope}
        if context is not None:
            extra["context"] = context
        return self.engine.summary(*self._ws, now=self._now, **self._read_owner(), **extra)

    def condition(self, statement, *, goal, requires=None, state="unknown", id=None, op=None):
        """Record something that must be true for a goal to be reached."""
        return self.add(
            statement,
            kind="condition",
            goal=goal,
            after=list(requires or []) or None,
            condition=state,
            id=id,
            op=op,
        )

    def assess(self, id, state, *, evidence=None, op=None):
        """Say how a condition stands, with the evidence for saying so."""
        retry = self._retry_context(op, "update", id)
        current = retry["before"] if retry else self.get(id)
        if current["kind"] != "condition":
            raise DomainError("invalid", "Only a condition can be assessed")
        base = self._op_id(op)
        evidence_op = self._part_id(base, "evidence")
        if evidence:
            return self._paired_actions(
                id, (base, "update", {"condition": state}),
                (evidence_op, "note", {"text": evidence, "author": self.actor}),
                retryable=op is not None,
            )
        if retry and self._retry_context(evidence_op, "note", id):
            raise DomainError("conflict", "Operation ID belongs to an assessment with evidence")
        return self._act(id, "update", {"condition": state}, op=op)

    def explore(self, goal=None, *, scope=None):
        """Conditions behind a goal, which are next, and where nothing is moving."""
        extra = {} if scope is None else {"scope": scope}
        return self.engine.explore(*self._ws, goal, now=self._now, **extra)

    def focus(self, id, rank=None, *, op=None):
        """Put a task on the person's short list. Without a rank it goes last."""
        retry = self._retry_context(op, "update", id) if rank is None else None
        if retry:
            rank = retry["result"]["focus"]
            if rank is None:
                raise DomainError("conflict", "Operation ID belongs to a different request")
        elif rank is None:
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

        def build(current, retry):
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
            if op and self._retry_context(self._part_id(op, "place"), "place", id):
                raise DomainError("conflict", "Operation ID belongs to a move with a position")
            return self._act(id, "update", {"stage": stage}, op=op)
        base = self._op_id(op)
        return self._paired_actions(
            id, (base, "update", {"stage": stage}), (self._part_id(base, "place"), "place", place),
            retryable=op is not None,
        )

    def _paired_actions(self, id, first, second, *, retryable):
        """Two ordered changes, checked against both original receipts on replay."""
        for attempt in range(4):
            retry = self._retry_context(first[0], first[1], id) if retryable else None
            next_retry = self._retry_context(second[0], second[1], id) if retryable else None
            if next_retry and not retry:
                # A simultaneous atomic batch may have committed between our reads.
                retry = self._retry_context(first[0], first[1], id)
            if bool(retry) != bool(next_retry):
                raise DomainError("conflict", "Operation ID belongs to a different request")
            current = retry["before"] if retry else self.engine.get(*self._ws, id)
            version = current["version"]
            versions = (version, next_retry["before"]["version"] if next_retry else version + 1)
            operations = [
                {
                    "operation_id": key,
                    "action": action,
                    "payload": {"id": id, **fields},
                    "expected_version": versions[index],
                }
                for index, (key, action, fields) in enumerate((first, second))
            ]
            result = self.engine.batch(*self._ws, operations, atomic=True, **self._who)
            if result["committed"]:
                return result["results"][-1]["result"]
            error = next(r["error"] for r in result["results"] if r["error"]["code"] != "aborted")
            if retry or error["code"] != "conflict" or attempt == 3:
                raise DomainError(error["code"], error["message"], error["details"])
            if retryable and self._retry_context(first[0], first[1], id):
                continue
            if self.engine.get(*self._ws, id)["version"] == version:
                raise DomainError(error["code"], error["message"], error["details"])

    def plan(self, project):
        return self.engine.plan(*self._ws, project, now=self._now)

    def projects(self):
        return self.engine.projects(*self._ws, now=self._now)

    def changes(self, project, after):
        return self.engine.changes(*self._ws, project, after)

    def search(self, text=None, *, kind="task", status=None, scope=None, after=None, limit=50, claimed=False):
        """Lean search hits, optionally limited to the current actor's active work claims."""
        if type(claimed) is not bool:
            raise DomainError("invalid", "claimed must be true or false")
        filters = {}
        if claimed:
            filters = {"claimed_by": self.actor} if self._ws else {"claimed": True}
            filters["now"] = self._now
        return self.engine.search(
            *self._ws, text, kind=kind, status=status, scope=scope, after=after, limit=limit, **filters
        )

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
