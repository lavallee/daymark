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
import time
import uuid
from contextlib import contextmanager, nullcontext
from copy import copy
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from dateutil.relativedelta import relativedelta
from dateutil.tz import datetime_ambiguous, datetime_exists, resolve_imaginary
from pydantic import ValidationError

from . import coordination, plans
from .client import Client
from .cursors import cursor_number
from .models import (
    DomainError,
    Recurrence,
    SourceRef,
    Temporal,
    first_occurrence,
    normalize_now,
    require_active_round,
    require_visible_proposal,
    start_has_arrived,
)

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


def _explicit_timezone(name: str) -> str:
    try:
        ZoneInfo(name)
    except (KeyError, ValueError, OSError):
        raise DomainError(
            "invalid", "Unrecognized Daymark timezone",
            ["Use an IANA name such as America/New_York, Europe/London or UTC with --tz or DAYMARK_TZ."],
        ) from None
    return name


def local_timezone(explicit: str | None = None) -> str:
    """Honor the call's zone, then the configured zone, then the host's IANA zone."""
    if explicit is not None:
        return _explicit_timezone(explicit)
    if configured := os.environ.get("DAYMARK_TZ"):
        return _explicit_timezone(configured)
    if host_zone := os.environ.get("TZ"):
        try:
            ZoneInfo(host_zone)
            return host_zone
        except (KeyError, ValueError, OSError):
            pass  # The host may be using a POSIX TZ expression instead of an IANA key.
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


def _check_wall_time(at):
    if not datetime_exists(at):
        valid = resolve_imaginary(at).isoformat()
        raise DomainError("invalid", f"This local time does not exist in {at.tzinfo}",
                          [f"Choose an existing local time, for example {valid}."])
    if datetime_ambiguous(at):
        choices = [at.replace(fold=fold).isoformat() for fold in (0, 1)]
        raise DomainError("invalid", f"This local time occurs twice in {at.tzinfo}",
                          ["Specify the intended offset: " + " or ".join(choices)])


def parse_when(text, timezone="UTC", now=None, *, _replay=False) -> dict:
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
                if not _replay:
                    _check_wall_time(at)
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
            if _replay:
                # Match the original result on receipts from earlier versions.
                at = resolve_imaginary(at)
            else:
                _check_wall_time(at)
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
        remote = isinstance(engine, Client)
        self._ws = () if remote else (workspace,)
        # Over HTTP the server records its authenticated principal instead.
        self._take_identity = remote or actor is not None
        self.actor = "agent" if actor is None else actor
        self._who = {} if remote else {"actor": self.actor}
        self.timezone = local_timezone(timezone)
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

    @staticmethod
    def _check_expected_version(expected_version):
        if expected_version is not None and (type(expected_version) is not int or expected_version < 1):
            raise DomainError("invalid", "expected_version must be a positive integer")

    def _act(self, id, action, fields=None, *, op=None, build=None, expected_version=None, date_keys=(), review_token=None):
        """Apply the caller's intent or verify it against the original kernel receipt."""
        self._check_expected_version(expected_version)
        key = self._op_id(op)
        for attempt in range(4):
            retry = self._retry_context(op, action, id)
            current = retry["before"] if retry else self.engine.get(*self._ws, id)
            if not retry and not self._ws and action in ("skip", "complete") and current["status"] != "proposed":
                require_active_round(current, action)  # Older services lack this guard.
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
                if action == "undo" or review_token is not None or expected_version is not None or self.engine.get(*self._ws, id)["version"] == version:
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
        from .search import fold_text

        wanted = fold_text(value.strip())
        matches = [
            item["id"]
            for kind in ("person", "principal")
            for item in self.engine.list(*self._ws, kind=kind)
            if fold_text(item["title"].strip()) == wanted
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

    def _resolve(self, fields, *, retry=None):
        if "lead" in fields:  # A project's lead is its assignee.
            fields = {
                **{k: v for k, v in fields.items() if k != "lead"},
                "assignee": fields["lead"],
            }
        if requested := fields.get("assignee"):
            saved = retry["result"].get("assignee_id") if retry else None
            if requested == saved:
                return fields
            try:
                person = self._person(requested)
            except DomainError as exc:
                if not saved or exc.code != "invalid":
                    raise
                person = None
            if saved and person != saved:
                raise DomainError(
                    "conflict", "The assignee does not resolve to the person on this receipt",
                    [f"The recorded assignee ID is {saved}. Use that ID to retry the original assignment."],
                )
            fields = {**fields, "assignee": person}
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

    def _fields(self, values, *, replay=False):
        """Translate friendly keyword names into entity fields."""
        out = {}
        for key, value in values.items():
            if value is None:
                continue
            if key in _DATE_FIELDS:
                out[_DATE_FIELDS[key]] = (
                    None if value == "" else parse_when(value, self.timezone, self._now, _replay=replay)
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
        supplied = fields.get("planned") or fields.get("deadline") or fallback
        anchor = {**(supplied or parse_when("today", self.timezone, self._now)), "expression": None}
        result = {
            "rule": parse_every(every),
            "mode": "after_completion" if after_completion else "fixed",
            "timezone": anchor["timezone"],
            "anchor": anchor,
            # Changing the rule mid-series keeps this round's place in it.
            "occurrence_index": index,
        }
        if not supplied:
            try:
                schedule = Recurrence.model_validate(result)
            except ValidationError as exc:
                raise DomainError("invalid", "Invalid recurrence rule", [EVERY_FORMS]) from exc
            first = first_occurrence(schedule)
            if first is None:
                raise DomainError("invalid", "This recurrence has no starting date on or after today",
                                  ["Review the rule ending or provide an explicit first plan date."])
            result["anchor"] = first.model_dump(mode="json")
        return result

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
        payload = {"kind": kind, "title": title, **self._fields(self._resolve(fields, retry=retry), replay=bool(retry))}
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
        capture_options = {}
        if note:
            # Part of the same write, so a retry can never lose or repeat it.
            saved = next(iter(retry["result"]["notes"]), {}) if retry else {}
            author = self.actor
            if not self._ws:
                # An old receipt can contain a historical supplied author.
                # Fresh capture on a current service is stamped inside the write.
                # A 0.5 service has no trustworthy way to name its principal here.
                author = saved.get("author")
                if not retry and self.engine.identity() is not None:
                    capture_options["attribute_capture_note"] = True
            payload["notes"] = [
                {
                    "id": saved.get("id") or uuid.uuid4().hex[:12],
                    "at": normalize_now(saved.get("at")).isoformat(),
                    "author": author,
                    "text": note,
                }
            ]
        payload["id"] = id or (retry["result"]["id"] if retry else self._new_id())
        try:
            return self.engine.execute(
                *self._ws, self._op_id(op), "create", payload, expected_version=0, **self._who, **capture_options
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

    def remember(self, text, *, scope=None, source=None, op=None):
        """Keep an explicitly supplied fact for later sessions in this workspace."""
        if not isinstance(text, str) or not text.strip():
            raise DomainError("invalid", "A remembered fact needs text")
        return self.add(text, kind="memory", scope=scope, source=source, op=op)

    def memories(self):
        return self.engine.list(*self._ws, kind="memory")

    def forget(self, id, *, op=None, expected_version=None, review_token=None):
        """Retire a remembered fact while retaining its archived record and history."""
        return self._act(id, "forget", op=op, expected_version=expected_version, review_token=review_token)

    # -- change

    def edit(self, id, *, every=None, after_completion=False, op=None, expected_version=None, review_token=None, **fields):
        """Edit fields, optionally refusing if the version observed by the caller is stale."""
        resolved = None

        def build(current, retry):
            nonlocal resolved
            if resolved is None:
                resolved = self._resolve(fields, retry=retry)
            changes = self._fields(resolved, replay=bool(retry))
            date_keys = [_DATE_FIELDS[k] for k, v in fields.items() if k in _DATE_FIELDS and isinstance(v, str)]
            out = self._retry_dates(changes, retry["result"], date_keys) if retry else dict(changes)
            if every == "":
                out["recurrence"] = None
            elif every:
                merged = {**current, **out}
                index = (current["recurrence"] or {}).get("occurrence_index", 0)
                fallback = ((retry["result"].get("recurrence") or {}).get("anchor") if retry else None)
                out["recurrence"] = self._recurrence(every, merged, after_completion, index, fallback)
                if (not current["recurrence"] and not merged.get("planned") and not merged.get("deadline")
                        and "plan" not in fields and "due" not in fields
                        and (not retry or retry["result"]["planned"])):
                    # Enrolling an undated task uses the same first round as a
                    # fresh capture. Earlier receipts had no inferred plan.
                    out["planned"] = out["recurrence"]["anchor"]
            return out

        return self._act(id, "update", op=op, build=build, expected_version=expected_version, review_token=review_token)

    def note(self, id, text, *, op=None):
        """Append a progress note: what was done, learned, or left for the next session."""
        return self._act(id, "note", {"text": text, "author": self.actor}, op=op)

    def _with_note(self, id, action, fields, note, op, expected_version):
        self._check_expected_version(expected_version)
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
            return self._act(id, action, fields, op=op, expected_version=expected_version)
        if old_note:
            if expected_version is not None and old_note["before"]["version"] != expected_version:
                raise DomainError("conflict", "Version mismatch")
            self.note(id, note, op=legacy_op)
            completion_version = None if expected_version is None else expected_version + 1
            return self._act(id, action, fields, op=op, expected_version=completion_version)
        return self._paired_actions(
            id, (note_op, "note", {"text": note, "author": self.actor}), (base, action, fields),
            retryable=op is not None, expected_version=expected_version,
        )

    def done(self, id, *, note=None, op=None, expected_version=None):
        return self._with_note(id, "complete", {}, note, op, expected_version)

    def skip(self, id, *, op=None):
        """Pass over one occurrence of a recurring task without recording completion."""
        return self._act(id, "skip", op=op)

    def cancel(self, id, *, op=None):
        return self._act(id, "cancel", op=op)

    def reopen(self, id, *, op=None):
        return self._act(id, "reopen", op=op)

    def archive(self, id, *, op=None):
        return self._act(id, "archive", op=op)

    def unarchive(self, id, *, op=None, expected_version=None, review_token=None):
        """Restore an archived record's visibility, retaining its current status."""
        def restore(current, retry):
            if not current["archived"]:
                raise DomainError("conflict", "This record is already visible")
            return {"archived": False}

        return self._act(id, "update", build=restore, op=op,
                         expected_version=expected_version, review_token=review_token)

    def undo(self, id, *, op=None, expected_version=None, review_token=None):
        """Reverse the latest change only if the observed revision still matches."""
        return self._act(id, "undo", op=op, expected_version=expected_version, review_token=review_token)

    def accept(self, id, *, op=None, expected_version=None, review_token=None):
        """Accept the reviewed proposal, retaining a recorded wait."""
        def check(current, retry):
            # Older services permit a hidden acceptance. A versioned write
            # ensures a concurrent archive forces a new read before accepting.
            if not retry and not self._ws:
                require_visible_proposal(current)
            return {}

        return self._act(id, "accept", build=check, op=op,
                         expected_version=expected_version, review_token=review_token)

    def dismiss(self, id, *, op=None, expected_version=None, review_token=None):
        return self._act(id, "dismiss", op=op, expected_version=expected_version, review_token=review_token)

    def claim(self, id, *, minutes=60, op=None):
        """Take a task so parallel workers do not collide. Fails if another holds it."""
        return self._act(
            id, "claim", {"owner": self.actor, "lease_seconds": int(minutes) * 60}, op=op
        )

    def take(self, *, scope=None, context=None, project=None, minutes=60, op=None):
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
        if project is not None:
            selection["project"] = project
        result = self.engine.execute(
            *self._ws, self._op_id(op), "take", selection,
            expected_version=0, **self._who,
        )
        return result["task"]

    def release(self, id, *, note=None, op=None, expected_version=None):
        """Give back a claim, optionally recording a handoff note in the same transaction."""
        return self._with_note(id, "release", {"owner": self.actor}, note, op, expected_version)

    # -- shared work: ask and answer, follow up, watch

    def _me(self):
        """The name this session's writes are recorded under, or None when an
        older service cannot say which principal its credential belongs to."""
        if self._ws:
            return self.actor
        if not hasattr(self, "_service_actor"):
            found = self.engine.identity()
            self._service_actor = found["actor"] if found else None
        return self._service_actor

    def _sequence(self, id, request_id, request, build, *, replay):
        """Several ordered changes to one record, all or nothing.

        ``build`` receives the current record and returns (action, fields)
        steps. The batch is recorded under ``request_id`` with the caller's own
        ``request``, so a retry returns the first result even though the steps
        were derived from a record that has since changed.
        """
        for attempt in range(4):
            # Every sequence opens with a note. Its receipt holds the record as
            # the first attempt read it, so a retry rebuilds the same steps.
            retry = self._retry_context(self._part_id(request_id, "0:note"), "note", id) if replay else None
            current = retry["before"] if retry else self.engine.get(*self._ws, id)
            steps = build(current)
            operations = [
                {
                    "operation_id": self._part_id(request_id, f"{index}:{action}"),
                    "action": action,
                    "payload": {"id": id, **fields},
                    "expected_version": current["version"] + index,
                }
                for index, (action, fields) in enumerate(steps)
            ]
            result = self.engine.batch(
                *self._ws, operations, atomic=True, request_id=request_id, request=request,
                **self._who,
            )
            if result["committed"]:
                return result["results"][-1]["result"]
            error = next(r["error"] for r in result["results"] if r["error"]["code"] != "aborted")
            changed = self.engine.get(*self._ws, id)["version"] != current["version"]
            if retry or error["code"] != "conflict" or attempt == 3 or not changed:
                raise DomainError(error["code"], error["message"], error["details"])

    def ask(self, id, question, *, to, options=(), recommend=None, op=None):
        """Put a question to a person or another actor and hand the task to them.

        The question joins the notes, the task becomes theirs to answer
        (``next_move`` answer) and the asker's claim is given back, so the task
        shows in the decision queue instead of hiding behind a lease.
        """
        text, to, options = coordination.clean_question(question, to, options, recommend)
        request = {"ask": text, "to": to, "options": options, "recommend": recommend, "id": id}
        base, me = self._op_id(op), self._me()

        def build(current):
            if current["kind"] != "task" or current["archived"] or current["status"] != "open":
                raise DomainError("conflict", "Ask about an open task. Reopen or unarchive it first.")
            if coordination.open_question(current):
                raise DomainError(
                    "conflict", "This task already has an unanswered question",
                    ["Read it with show ID. One question at a time keeps the answer unambiguous."],
                )
            holder = current["claimed_by"]
            live = holder is not None and normalize_now(current["claim_until"]) > self.now()
            if live and me is not None and holder != me:
                raise DomainError("conflict", "Task is claimed by another owner")
            record = {
                "id": uuid.uuid5(uuid.NAMESPACE_URL, "daymark:question:" + base).hex[:12],
                "text": text, "to": to, "by": me or self.actor, "options": options,
                "recommend": recommend, "resume": {"next_move": current["next_move"]},
            }
            steps = [("note", {"text": coordination.question_note(record), "author": self.actor})]
            if live:
                steps.append(("release", {"owner": self.actor}))
            steps.append(("update", {
                "next_move": "answer",
                "metadata": {**current["metadata"], coordination.QUESTION: record},
            }))
            return steps

        return self._sequence(id, base, request, build, replay=op is not None)

    def answer(self, id, text=None, *, choice=None, heard_from=None, question_key=None, op=None):
        """Answer the open question on a task and return it to whoever moves next.

        The actor who asked may only relay an answer it was given elsewhere,
        naming who gave it (``heard_from``). The record then says it was relayed.
        """
        if heard_from is not None and (not isinstance(heard_from, str) or not 1 <= len(heard_from.strip()) <= 200):
            raise DomainError("invalid", "Name who gave the answer, in 1 to 200 characters")
        request = {"answer": text, "choice": choice, "id": id, "heard_from": heard_from}
        if question_key is not None:
            if not isinstance(question_key, str) or not question_key:
                raise DomainError("invalid", "Use the question key from the inbox")
            request["question_key"] = question_key
        base, me = self._op_id(op), self._me()

        def build(current):
            question = coordination.open_question(current)
            if not question:
                raise DomainError("conflict", "This task has no unanswered question")
            if question_key is not None and question_key != coordination.inbox_question_key(self.workspace, id, question):
                raise DomainError("conflict", "The open question changed. Read the current question before answering.")
            if me is not None and question.get("by") == me and not heard_from:
                raise DomainError(
                    "forbidden", "The actor who asked cannot answer its own question",
                    [
                        "Leave it for whoever it was addressed to.",
                        "If they answered somewhere else, relay it and name them: answer ID --heard-from WHO.",
                    ],
                )
            said, picked = coordination.resolve_answer(question, text, choice)
            source = heard_from.strip() if heard_from else me or self.actor
            given = {"text": said, "choice": picked, "by": source}
            if heard_from:
                given["relayed_by"] = me or self.actor
            note = coordination.answer_note(source, said, question, picked)
            if heard_from:
                note += f" (relayed by {me or self.actor})"
            return [
                ("note", {"text": note, "author": self.actor}),
                ("update", {
                    "next_move": (question.get("resume") or {}).get("next_move"),
                    "metadata": {**current["metadata"], coordination.QUESTION: {**question, "answer": given}},
                }),
            ]

        return self._sequence(id, base, request, build, replay=op is not None)

    def followup(self, id, text, *, after, for_=None, op=None):
        """Leave a task for an agent to pick up after an interval, about ``id``.

        It is an ordinary task that becomes ready when the interval has passed,
        so a later session finds it in ``ready`` whether or not this one is
        still running.
        """
        delay = coordination.interval(after)
        if for_ is not None and (not isinstance(for_, str) or not 1 <= len(for_.strip()) <= 200):
            raise DomainError("invalid", "Name who the follow-up is for, in 1 to 200 characters")
        me = self._me() or self.actor
        subject = self.engine.get(*self._ws, id)
        retry = self._retry_context(op, "create")
        start = (retry["result"]["earliest_start"] or {}).get("at") if retry else None
        if not start:
            start = (self.now() + delay).astimezone(UTC).isoformat(timespec="seconds")
        return self.add(
            text, start=start, move="agent", op=op,
            description=f"Follow-up on {subject['id']}: {subject['title']}",
            metadata={coordination.FOLLOWUP: {
                "of": subject["id"], "by": me, "for": for_.strip() if for_ else me, "after": after.strip().lower(),
            }},
        )

    def _addressed(self, name):
        found = getattr(self.engine, "addressed", None)
        if found:
            return found(*self._ws, name)
        from .store import _names  # An HTTP service lists tasks; filter the same way.

        return [x for x in self.engine.list(*self._ws, "task") if x["status"] in {"open", "waiting"} and _names(x, name)]

    def _scan(self, after):
        """Events after a cursor, at most ``SCAN_LIMIT``. Returns (events, cursor, head, more)."""
        events, cursor = [], after
        while len(events) < coordination.SCAN_LIMIT:
            page = self.activity(after=cursor, limit=100)
            events.extend(page["events"])
            cursor, head = page["cursor"], page["head"]
            if cursor >= head or not page["events"]:
                return events, cursor, head, False
        return events, cursor, head, cursor < head

    def inbox(self, name=None, *, after=None):
        """What is addressed to one actor and needs them now, by exact name.

        Standing items need no cursor: questions put to ``name``, answers to
        questions ``name`` asked that nobody has acted on, and follow-ups for
        ``name`` that are ready. Mentions of ``@name`` in notes are read from
        the event log after ``after``; without it they are not scanned and the
        returned cursor is the newest event, to save for the next read.
        """
        name = name or self._me() or self.actor
        now = self.now()
        to_answer, answered, followups = [], [], []
        for item in self._addressed(name):
            meta = item.get("metadata") or {}
            question, follow = meta.get(coordination.QUESTION) or {}, meta.get(coordination.FOLLOWUP) or {}
            if coordination.open_question(item) and question.get("to") == name:
                to_answer.append(item)
            elif coordination.answered_waiting(item, name):
                answered.append(item)
            if (follow.get("for") or follow.get("by")) == name and item["status"] == "open" and not item["claimed_by"] \
                    and start_has_arrived(item["earliest_start"], now):
                followups.append(item)
        mentioned, more = [], False
        if after is None:
            cursor = self.activity(limit=1)["head"]
        else:
            events, cursor, _head, more = self._scan(after)
            mentioned = [e for e in events if e.get("note") and e.get("actor") != name
                         and coordination.mentions(e["note"]["text"], name)]
        lean = lambda items: [{k: x[k] for k in ("id", "title", "status", "version")} for x in items]
        def asked(item):
            return item["metadata"][coordination.QUESTION]

        # A key names one thing to act on and stays the same while it waits, so a
        # consumer that records handled keys delivers each item once, even after
        # the task's version moves on.
        result = {
            "for": name,
            "to_answer": [{**{k: x[k] for k in ("id", "title", "version")},
                           "key": coordination.inbox_question_key(self.workspace, x["id"], asked(x)),
                           "question": asked(x)["text"], "options": asked(x).get("options", []),
                           "from": asked(x).get("by")} for x in to_answer],
            "answered": [{**{k: x[k] for k in ("id", "title", "version")},
                          "key": f"a:{self.workspace}:{x['id']}:{asked(x).get('id', '')}",
                          "question": asked(x)["text"], "answer": asked(x)["answer"]} for x in answered],
            "followups": [{**f, "key": f"f:{self.workspace}:{f['id']}"} for f in lean(followups)],
            "mentions": [{"seq": e["seq"], "id": e["id"], "title": e["title"], "by": e["actor"],
                          "key": f"m:{self.workspace}:{e['seq']}",
                          "text": coordination.preview(e["note"]["text"])} for e in mentioned],
            "cursor": cursor,
            "more": more,
        }
        result["waking"] = sum(len(result[k]) for k in ("to_answer", "answered", "followups", "mentions"))
        return result

    def seen(self, reader=None):
        """The cursor ``reader`` last marked as seen here; 0 if never."""
        if not self._ws:
            raise DomainError("invalid", "Seen marks need local database access")
        return self.engine.seen_mark(self.workspace, reader or self._me() or self.actor)

    def mark_seen(self, reader=None, *, through=None):
        """Move a reader's mark to ``through`` (default: the newest event). It only moves forward."""
        if not self._ws:
            raise DomainError("invalid", "Seen marks need local database access")
        reader = reader or self.actor
        through = self.activity(limit=1)["head"] if through is None else cursor_number(through)
        return self.engine.mark_seen(self.workspace, reader, through)

    def digest(self, reader=None):
        """What a person should know, without marking anything seen.

        Questions waiting on them, conditions to reassess, and changes by
        others since their mark, one capped line per task.
        """
        reader = reader or self.actor
        mark = self.seen(reader) if self._ws else 0
        events, cursor, head, more = self._scan(mark)
        changes, held = coordination.summarise(events, reader)
        asked = self.inbox(reader)
        explored = self.explore()
        return {
            "for": reader,
            "since": mark,
            "through": cursor,
            "head": head,
            "more": more,
            "to_answer": asked["to_answer"],
            "answered": asked["answered"],
            "to_reassess": explored.get("to_reassess", []),
            "changes": changes,
            "changes_not_shown": held,
            "unseen": sum(1 for e in events if e.get("actor") != reader),
        }

    def watch(self, ids=(), *, after=None, by=None, include_own=False, timeout=1800.0,
              poll=1.0, sleep=time.sleep, clock=time.monotonic):
        """Block until the next matching change after a cursor, or until the timeout.

        Starts at the newest event unless ``after`` is given. The returned
        cursor is the last event examined, matched or not, so passing it back
        never rescans and never skips. A timeout is not an answer.
        """
        if type(timeout) not in (int, float) or not 0 <= timeout <= 86400:
            raise DomainError("invalid", "Watch for at most 24 hours (86400 seconds)")
        if type(poll) not in (int, float) or not 0.05 <= poll <= 300:
            raise DomainError("invalid", "Poll interval must be between 0.05 and 300 seconds")
        ids, me = set(ids), self._me() or self.actor
        cursor = self.activity(limit=1)["head"] if after is None else after
        deadline = clock() + timeout
        while True:
            page = self.activity(after=cursor, limit=100)
            for event in page["events"]:
                if coordination.matches(event, ids=ids, actor=me, by=by, include_own=include_own):
                    return {"status": "event", "event": event, "cursor": event["seq"], "head": page["head"]}
            cursor = page["cursor"]
            if cursor < page["head"]:
                continue
            remaining = deadline - clock()
            if remaining <= 0:
                return {"status": "timeout", "cursor": cursor, "head": page["head"]}
            sleep(min(poll, remaining))

    @staticmethod
    def _check_wait_state(current, retry):
        # A legacy operation may have restarted closed work. Its committed
        # receipt still replays, without applying that transition again.
        if retry:
            return
        if current["archived"]:
            raise DomainError("conflict", "This record is archived. Unarchive it before changing its wait.")
        if current["status"] in {"completed", "cancelled"}:
            raise DomainError("conflict", f"This record is {current['status']}. Reopen it to restart work.")

    def wait(self, id, on, *, until=None, op=None, expected_version=None, review_token=None):
        """Park a task on someone or something else; optionally hide it until a date."""
        def build(current, retry):
            self._check_wait_state(current, retry)
            changes = {"status": "waiting", "waiting_on": on}
            if until:
                changes["earliest_start"] = parse_when(until, self.timezone, self._now, _replay=bool(retry))
            return changes

        return self._act(
            id, "update", build=build, op=op, expected_version=expected_version,
            review_token=review_token, date_keys=("earliest_start",) if isinstance(until, str) else (),
        )

    def resume(self, id, *, op=None, expected_version=None, review_token=None):
        """Stop waiting and make the task actionable now, lifting a future start date."""

        def build(current, retry):
            self._check_wait_state(current, retry)
            out = {"status": "open", "waiting_on": None}
            if retry:
                if current["earliest_start"] and not retry["result"]["earliest_start"]:
                    out["earliest_start"] = None
                return out
            if not start_has_arrived(current["earliest_start"], self.now()):
                out["earliest_start"] = None
            return out

        return self._act(id, "update", op=op, build=build, expected_version=expected_version, review_token=review_token)

    def defer(self, id, until, *, op=None):
        """Hide a task from ready work until a date. Its deadline does not move."""
        return self._act(
            id, "update", op=op, build=lambda _current, retry: {
                "earliest_start": parse_when(until, self.timezone, self._now, _replay=bool(retry))
            },
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

    def _reminder_schedule(self, when, retry):
        """Resolve a new request or recover the civil-time intent in its creation receipt."""
        saved = retry["result"] if retry else {}
        expression = when.strip() if isinstance(when, str) else None
        intent = {"expression": expression, "timezone": self.timezone}
        metadata = {"daymark.remind": intent}
        previous = saved.get("metadata", {}).get("daymark.remind")
        if expression and previous == intent:
            return normalize_now(saved["scheduled_at"]), metadata

        value = Temporal.model_validate(parse_when(when, self.timezone, self._now, _replay=bool(retry)))
        if value.at is None:
            raise DomainError("invalid", "A reminder needs a time", [WHEN_FORMS])
        if not expression or not value.expression:
            return value.at, None
        if not retry or previous is not None:
            return value.at, metadata

        # Pre-upgrade receipts kept only the UTC instant. Recover it when the
        # original creation day corroborates the relative expression.
        original = parse_when(when, self.timezone, saved["created_at"], _replay=True)
        if original.get("at") and saved.get("scheduled_at"):
            scheduled = normalize_now(saved["scheduled_at"])
            if normalize_now(original["at"]) == scheduled:
                return scheduled, None
        return value.at, None

    def remind(self, id, when, *, recipient, channel="recording", op=None):
        """Schedule a reminder about a task for a person or principal entity."""
        retry = self._retry_context(op, "create")
        instant, metadata = self._reminder_schedule(when, retry)
        try:
            return self.add(
                "", kind="reminder", task_id=id, scheduled_at=instant.astimezone(UTC).isoformat(),
                recipient_id=recipient, channel=channel, op=op, metadata=metadata,
            )
        except Exception as exc:
            if retry or not op or getattr(exc, "code", None) != "conflict":
                raise
            if self._retry_context(op, "create") is None:
                raise
            # Another session may have resolved this request across a day boundary.
            return self.remind(id, when, recipient=recipient, channel=channel, op=op)

    # -- read

    def get(self, id):
        return self.engine.get(*self._ws, id)

    def activity(self, *, after=None, limit=20):
        """Recent workspace changes, or the next page after a saved cursor."""
        return self.engine.activity(*self._ws, after=after, limit=limit)

    def show(self, id=None, history=10, *, source=None):
        return self.engine.context(
            *self._ws, id, history=history, now=self._now, **self._read_owner(), **self._who,
            **({"source": source} if source is not None else {}),
        )

    def recurrence_forecast(self, id=None, *, action, source=None):
        """Read the calendar effect of skipping or finishing one recurring round."""
        return self.engine.recurrence_forecast(
            *self._ws, id, action=action, source=source, now=self._now
        )

    def brief(self, id=None, *, before_note=None, source=None):
        """A bounded task handoff, including the latest attributed note previews."""
        return self.engine.brief(
            *self._ws, id, now=self._now, before_note=before_note, **self._read_owner(), **self._who,
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

    def ready(self, *, limit=20, move=None, mine=True, context=None, scope=None, project=None, brief=False, projection=None):
        """Select eligible tasks. A card projection retains planning and checklist fields."""
        return self.engine.ready(
            *self._ws,
            now=self._now,
            limit=limit,
            **self._read_owner(mine),
            **({"context": context} if context is not None else {}),
            **({"project": project} if project is not None else {}),
            next_move=move,
            **({"brief": True} if brief else {}),
            **({"projection": projection} if projection is not None else {}),
            **({} if scope is None else {"scope": scope}),
        )

    def explain_ready(self, *, limit=20, move=None, mine=True, context=None, scope=None, project=None):
        """Count actionable and held work and name examples of each holding reason."""
        return self.engine.explain_ready(
            *self._ws, now=self._now, limit=limit, next_move=move, scope=scope, context=context,
            **({"project": project} if project is not None else {}),
            **self._read_owner(mine),
        )

    def ready_summary(self, *, limit=20, move=None, mine=True, context=None, scope=None, project=None):
        """Orientation-sized records, in precisely the same order as ready."""
        return self.ready(limit=limit, move=move, mine=mine, scope=scope, context=context, project=project, brief=True)

    def agenda(self, days=7, *, context=None, scope=None, deadlines=False, local_day=False):
        if type(local_day) is not bool:
            raise DomainError("invalid", "local_day must be a boolean; set the viewer timezone on Tasks")
        extra = {} if scope is None else {"scope": scope}
        if context is not None:
            extra["context"] = context
        if deadlines:
            extra["deadlines"] = True
        if local_day:
            extra["day_zone"] = self.timezone
        return self.engine.agenda(*self._ws, now=self._now, days=days, **extra)

    @contextmanager
    def read_snapshot(self):
        """Group reads at one instant; local Store readers share a SQLite snapshot."""
        reader = copy(self)
        reader._now = self.now()
        snapshot = getattr(self.engine, "read_snapshot", nullcontext)
        with snapshot():
            yield reader

    @property
    def supports_consistent_reads(self):
        """Whether multiple factual reads can share a single engine snapshot."""
        return callable(getattr(self.engine, "read_snapshot", None))

    def advise(self, *, profile=None, settings=(), drops=(), scope=None, context=None, brief=False):
        """Run the chosen profile with one-call adjustments, without saving them."""
        from .store import Store
        from .strategies import selected_advice
        from .strategies.remote import advice as service_advice

        if type(brief) is not bool:
            raise DomainError("invalid", "Brief advice must be true or false")
        options = {"settings": settings, "drops": drops, "scope": scope, "context": context}
        if isinstance(self.engine, Client):
            result = service_advice(self, profile, brief=brief, **options)
        else:
            result = selected_advice(self, profile, brief=brief and isinstance(self.engine, Store), **options)
        if brief:
            from .render import list_record

            now = result.as_of or self.now()
            result.picks = [list_record(item, now, line_fields=True, context=context) for item in result.picks]
        return result

    def profile(self, name=None, *, settings=(), drops=(), op=None, expected_version=None, undo=None):
        """Read or adjust the active workspace profile."""
        from .strategies import has_adjustments
        from .strategies.preferences import active_profile

        if (isinstance(self.engine, Client) and name is None and undo is None
                and op is None and expected_version is None and not has_adjustments(settings, drops)):
            current = self.engine.profile_read("active")
            if current is not None:
                return current
            return {"name": "plain", "strategies": [], "execution": "legacy-client"}

        return active_profile(
            self, name, settings=settings, drops=drops, op=op, expected_version=expected_version, undo=undo,
        )

    def saved_profiles(self, name=None):
        """List named profiles, or inspect one saved configuration and version."""
        from .strategies.preferences import saved_profiles

        if isinstance(self.engine, Client):
            result = self.engine.profile_read("saved", name)
            if result is not None:
                return result
            raise DomainError("invalid", "Upgrade the service to read saved profiles")
        entries = saved_profiles(self)
        if name is None:
            return entries
        for entry in entries:
            if entry["name"] == name:
                return entry
        raise DomainError("not_found", "No saved profile with that name", [name])

    def profile_catalog(self, *, strategy=None):
        """Discover profiles and adjustment defaults, or inspect one strategy's parameters."""
        from .strategies import available, describe, installed_plugins
        from .strategies.profiles import PROFILES

        if isinstance(self.engine, Client):
            from .strategies.builtin import BUILTINS

            if strategy is None or (isinstance(strategy, str) and strategy in BUILTINS):
                data = self.engine.profile_read("catalog" if strategy is None else "strategy", strategy)
                if data is not None:
                    if strategy is None and (plugins := installed_plugins()):
                        data["client_plugins"] = plugins
                    return data
        if strategy is not None:
            return describe(strategy)
        saved = self.saved_profiles() if hasattr(self.engine, "setting") else None
        plugins = installed_plugins()
        return {
            **({"installed_plugins": plugins} if plugins else {}),
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

    def orientation(self, *, scope=None):
        """Read the facts for a returning worker, using one clock and one database snapshot."""
        return self.engine.orientation(*self._ws, now=self._now, owner=self.actor,
                                       **({"scope": scope} if scope is not None else {}))

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

    def _paired_actions(self, id, first, second, *, retryable, expected_version=None):
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
            version = current["version"] if expected_version is None else expected_version
            if not retry and not self._ws and second[1] == "complete" and current["status"] != "proposed":
                require_active_round(current, "complete")
            next_version = next_retry["before"]["version"] if next_retry and expected_version is None else version + 1
            versions = (version, next_version)
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
            if expected_version is not None or self.engine.get(*self._ws, id)["version"] == version:
                raise DomainError(error["code"], error["message"], error["details"])

    def plan(self, project):
        return self.engine.plan(*self._ws, project, now=self._now)

    def projects(self, *, scope=None):
        """Projects in the selected scope, with rollups across all of each project's members."""
        return self.engine.projects(*self._ws, now=self._now, **({"scope": scope} if scope is not None else {}))

    def changes(self, project, after):
        return self.engine.changes(*self._ws, project, after)

    def search(self, text=None, *, kind="task", status=None, scope=None, after=None, limit=50,
               claimed=False, archived=False, source=None):
        """Lean search hits for live records, archived records, or the actor's live claims."""
        if type(claimed) is not bool:
            raise DomainError("invalid", "claimed must be true or false")
        filters = {}
        if claimed:
            filters = {"claimed_by": self.actor} if self._ws else {"claimed": True}
            filters["now"] = self._now
        if source is not None:
            filters["source"] = source
        if archived is not False:
            filters["archived"] = archived
        return self.engine.search(
            *self._ws, text, kind=kind, status=status, scope=scope, after=after, limit=limit, **filters
        )

    def find(self, text=None, *, kind="task", status=None, limit=50, scope=None, context=None, brief=False, projection=None):
        """Find full records by title or description. A brief retains the fields for a listing."""
        from .search import entity_filters, fold_text, line_record, matches_text
        from .store import Store
        from .views import check_task_projection, task_card

        check_task_projection(projection, brief)
        filters = entity_filters(kind, status, scope=scope, context=context)
        # Keep Python slice semantics for unbounded reads (also used by caller
        # strategies). A service with only the original query API still gets
        # all scope and context predicates before it returns any records.
        searchable = text is None or len(text) <= 2000
        bounded = type(limit) is int and 1 <= limit <= 1000 and searchable
        if projection == "card":
            if kind != "task":
                raise DomainError("invalid", "A task card requires kind=task")
            if isinstance(self.engine, Store) and searchable and (bounded or limit is None):
                found, after = [], None
                with self.engine.read_snapshot():
                    while True:
                        page = self.engine.search(*self._ws, text, kind=kind, status=status, scope=scope,
                                                  context=context, projection="card", limit=limit or 1000, after=after)
                        found.extend(page["items"])
                        after = page["next"]
                        if limit is not None or after is None:
                            return found
            rows = self.find(text, kind=kind, status=status, limit=limit, scope=scope, context=context)
            return [task_card(item) for item in rows]
        if bounded and isinstance(self.engine, (Store, Client)):
            options = {"projection": "line" if brief else "entity"}
            if context is not None:
                options["context"] = context
            if self._ws or self.engine.supports_search_options(*options):
                return self.engine.search(*self._ws, text, kind=kind, status=status, scope=scope,
                                          limit=limit, **options)["items"]
        items = self.engine.query(*self._ws, filters)
        if scope is not None:
            items = [item for item in items if (item["scope"] or "") == scope]
        if context is not None:
            items = [item for item in items if context in item["context_ids"]]
        if text:
            needle = fold_text(text)
            items = [x for x in items if matches_text(needle, x["title"], x["description"])]
        if brief:
            return [line_record(item) for item in items[:limit]]
        return items[:limit]
