"""Rules for managing a project: its stages, the manual order and the plan's dates.

Pure functions over entity dictionaries. The Store applies them inside its
transactions; views and applications read with them. Nothing here writes.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from zoneinfo import ZoneInfo

from dateutil.tz import datetime_exists, resolve_imaginary

from .models import DomainError, Temporal, normalize_now

# A project that defines no stages reads as this board, derived from status.
DEFAULT_STAGES = (
    {"id": "todo", "label": "To do", "status": "open", "color": "grey"},
    {"id": "waiting", "label": "Waiting", "status": "waiting", "color": "yellow"},
    {"id": "done", "label": "Done", "status": "completed", "color": "green"},
)


def stages(project: dict | None) -> list[dict]:
    """A project's effective stages: its own, else the defaults."""
    own = (project or {}).get("stages") or []
    return [dict(s) for s in own] if own else [dict(s) for s in DEFAULT_STAGES]


def compatible(stage: dict, status: str, board: list[dict]) -> bool:
    """Whether a task with ``status`` may sit in ``stage``.

    A stage's status must match, except that a waiting task may stay in an open
    stage when the board has no waiting stage. A proposal sits in no stage.
    """
    if stage["status"] == status:
        return True
    return (
        status == "waiting"
        and stage["status"] == "open"
        and not any(s["status"] == "waiting" for s in board)
    )


def home(status: str, board: list[dict]) -> dict | None:
    """The first stage for a status; a waiting task falls back to the first open stage.

    None for a proposal, and for a cancelled task on a board with no cancelled stage:
    that task is off the board.
    """
    for stage in board:
        if stage["status"] == status:
            return stage
    if status == "waiting":
        return home("open", board)
    return None


def resolve_stage(task: dict, board: list[dict]) -> dict | None:
    """Where a task sits: its stored stage when valid, else its status's home."""
    if task["status"] == "proposed":
        return None
    for stage in board:
        if stage["id"] == task.get("stage") and compatible(stage, task["status"], board):
            return stage
    return home(task["status"], board)


# -- manual order: fractional-index keys with an integer part, compared as text
#
# A key is an integer part (a head letter giving its length, then base-62 digits)
# and an optional fraction that never ends in "0". Appending or prepending changes
# the integer, so keys grow logarithmically; only repeated inserts at one spot
# lengthen the fraction (about one character per six inserts).

DIGITS = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
_SMALLEST = "A" + DIGITS[0] * 26


def _integer_length(head: str) -> int:
    if "a" <= head <= "z":
        return ord(head) - ord("a") + 2
    if "A" <= head <= "Z":
        return ord("Z") - ord(head) + 2
    raise ValueError("invalid order key head")


def _split(key: str) -> tuple[str, str]:
    size = _integer_length(key[0])
    if size > len(key):
        raise ValueError("invalid order key")
    return key[:size], key[size:]


def valid_rank(key) -> bool:
    """Whether ``key`` is a well-formed order key."""
    try:
        if not isinstance(key, str) or not key or key == _SMALLEST or len(key) > 1000:
            return False
        if any(c not in DIGITS for c in key[1:]):
            return False
        _, fraction = _split(key)
        return not fraction.endswith(DIGITS[0])
    except ValueError:
        return False


def _midpoint(a: str, b: str | None) -> str:
    """A fraction strictly between fractions ``a`` and ``b`` (None is the end)."""
    if b is not None:
        n = 0
        while n < len(b) and (a[n] if n < len(a) else "0") == b[n]:
            n += 1
        if n:
            return b[:n] + _midpoint(a[n:], b[n:])
    low = DIGITS.index(a[0]) if a else 0
    high = DIGITS.index(b[0]) if b is not None else len(DIGITS)
    if high - low > 1:
        return DIGITS[(low + high + 1) // 2]
    if b is not None and len(b) > 1:
        return b[:1]
    return DIGITS[low] + _midpoint(a[1:], None)


def _increment(integer: str) -> str | None:
    head, digits = integer[0], list(integer[1:])
    for i in range(len(digits) - 1, -1, -1):
        d = DIGITS.index(digits[i]) + 1
        if d < len(DIGITS):
            digits[i] = DIGITS[d]
            return head + "".join(digits)
        digits[i] = DIGITS[0]
    if head == "Z":
        return "a" + DIGITS[0]
    if head == "z":
        return None
    head = chr(ord(head) + 1)
    if head > "a":
        digits.append(DIGITS[0])
    else:
        digits.pop()
    return head + "".join(digits)


def _decrement(integer: str) -> str | None:
    head, digits = integer[0], list(integer[1:])
    for i in range(len(digits) - 1, -1, -1):
        d = DIGITS.index(digits[i]) - 1
        if d >= 0:
            digits[i] = DIGITS[d]
            return head + "".join(digits)
        digits[i] = DIGITS[-1]
    if head == "a":
        return "Z" + DIGITS[-1]
    if head == "A":
        return None
    head = chr(ord(head) - 1)
    if head < "Z":
        digits.append(DIGITS[-1])
    else:
        digits.pop()
    return head + "".join(digits)


def rank_between(low: str | None, high: str | None) -> str:
    """A key after ``low`` and before ``high``; None means the start or the end.

    Equal or reversed bounds (possible only in damaged or imported data) give a
    key directly after ``low``.
    """
    if low is not None and high is not None and low >= high:
        high = None
    if low is None:
        if high is None:
            return "a" + DIGITS[0]
        integer, fraction = _split(high)
        if integer == _SMALLEST:
            return integer + _midpoint("", fraction)
        if integer < high:
            return integer
        result = _decrement(integer)
        if result is None:
            raise DomainError("invalid", "No room before the first rank")
        return result
    integer, fraction = _split(low)
    if high is None:
        result = _increment(integer)
        return integer + _midpoint(fraction, None) if result is None else result
    high_integer, high_fraction = _split(high)
    if integer == high_integer:
        return integer + _midpoint(fraction, high_fraction)
    result = _increment(integer)
    if result is not None and result < high:
        return result
    return integer + _midpoint(fraction, None)


def time_rank(stamp: str) -> str:
    """The order key a task without a rank reads as: its creation time, in
    microseconds, as an integer key. New tasks therefore sort last."""
    instant = normalize_now(stamp)
    micros = max(0, int(instant.timestamp() * 1_000_000))
    digits = ""
    while True:
        micros, digit = divmod(micros, len(DIGITS))
        digits = DIGITS[digit] + digits
        if not micros:
            break
    return chr(ord("a") + len(digits) - 1) + digits


def effective_rank(task: dict) -> str:
    return task.get("rank") or time_rank(task["created_at"])


def order_key(task: dict) -> tuple:
    """Manual order: by rank, where a task never placed reads as its creation time."""
    return (effective_rank(task), task["id"])


# -- dates: how four temporal fields read as a plan's start and end


def civil(temporal) -> str | None:
    """A temporal value as a YYYY-MM-DD date in its own zone."""
    if temporal is None:
        return None
    return Temporal.model_validate(temporal).local().date().isoformat()


def plan_dates(task: dict) -> dict:
    """start = planned, else earliest_start; end = latest_target, else deadline.

    The deadline is also given on its own so a view can mark it. A plan write
    sets planned and latest_target and never moves a deadline.
    """
    return {
        "start": civil(task["planned"] or task["earliest_start"]),
        "end": civil(task["latest_target"] or task["deadline"]),
        "deadline": civil(task["deadline"]),
    }


FIELD_HINTS = {
    "text": "text",
    "number": "a number",
    "bool": "true or false",
    "option": "one of its options",
    "options": "a list of its options, each once",
    "date": "a YYYY-MM-DD date",
    "url": "an http or https URL",
    "person": "the ID of a person in this workspace",
}


def is_civil_date(value) -> bool:
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return False
    try:
        date.fromisoformat(value)
    except ValueError:
        return False
    return True


_DATE_TARGETS = {"start": "planned", "end": "latest_target", "deadline": "deadline"}
_TEMPORALS = ("planned", "latest_target", "deadline", "earliest_start")


def _moved(current, day: date, zone: str) -> dict:
    """``current`` moved to civil ``day``, keeping its precision: an all-day value
    stays all-day in its zone, a timed one keeps its local clock time."""
    if current is None:
        return Temporal(date=day, timezone=zone).model_dump(mode="json")
    value = Temporal.model_validate(current)
    if value.date is not None:
        return Temporal(date=day, timezone=value.timezone).model_dump(mode="json")
    local = value.local()
    moved = datetime.combine(day, local.timetz().replace(tzinfo=None), ZoneInfo(value.timezone))
    if not datetime_exists(moved):
        moved = resolve_imaginary(moved)  # A clock time skipped by DST moves forward.
    return Temporal(at=moved, timezone=value.timezone).model_dump(mode="json")


def schedule(task: dict | None, *, timezone: str = "UTC", **dates) -> dict:
    """The entity changes that put a task's plan dates where a plan view says.

    ``dates`` may carry ``start`` (written to planned), ``end`` (latest_target) and
    ``deadline``, each a ``YYYY-MM-DD`` string or None to clear. A date equal to the
    current one changes nothing, so a timed value keeps its time; a moved timed value
    keeps its local clock time; a new value takes the zone of another date on the
    task, else ``timezone``. Only the dates given are checked: a start after an end
    is refused. ``task`` is None for a task not yet created. Never infers a date.
    """
    unknown = set(dates) - set(_DATE_TARGETS)
    if unknown:
        raise DomainError("invalid", "Plan dates are start, end and deadline", sorted(unknown))
    task = task or {name: None for name in _TEMPORALS}
    out = {}
    for name, value in dates.items():
        field = _DATE_TARGETS[name]
        if value is None:
            if task[field] is not None:
                out[field] = None
            continue
        if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            raise DomainError("invalid", f"{name} is a YYYY-MM-DD date or null")
        try:
            day = date.fromisoformat(value)
        except ValueError:
            raise DomainError("invalid", f"{name} is a YYYY-MM-DD date or null") from None
        if civil(task[field]) == value:
            continue
        zone = next((task[f]["timezone"] for f in (field, *_TEMPORALS) if task.get(f)), timezone)
        out[field] = _moved(task[field], day, zone)
    if {"start", "end"} & set(dates):
        merged = plan_dates({**task, **out})
        if merged["start"] and merged["end"] and merged["start"] > merged["end"]:
            raise DomainError("invalid", "The start is after the end")
    return out


# -- presets: starting configurations a project does not remember

PRESETS = {
    "simple": {
        "description": "A plain list on To do, Waiting and Done. Nothing else to learn.",
        "config": {"stages": [], "types": [], "fields": []},
    },
    "kanban": {
        "description": "A flow board: Backlog, Ready, Doing, Review, Blocked, Done.",
        "config": {
            "stages": [
                {"id": "backlog", "label": "Backlog", "status": "open", "color": "grey"},
                {"id": "ready", "label": "Ready", "status": "open", "color": "light-blue"},
                {"id": "doing", "label": "Doing", "status": "open", "color": "blue"},
                {"id": "review", "label": "Review", "status": "open", "color": "violet"},
                {"id": "blocked", "label": "Blocked", "status": "waiting", "color": "red"},
                {"id": "done", "label": "Done", "status": "completed", "color": "green"},
            ],
            "types": [],
            "fields": [],
        },
    },
    "milestones": {
        "description": "Phased delivery: work grouped under dated milestones, with a risk field.",
        "config": {
            "stages": [
                {"id": "todo", "label": "Not started", "status": "open", "color": "grey"},
                {"id": "doing", "label": "In progress", "status": "open", "color": "blue"},
                {"id": "blocked", "label": "Blocked", "status": "waiting", "color": "red"},
                {"id": "done", "label": "Done", "status": "completed", "color": "green"},
            ],
            "types": [],
            "fields": [
                {"id": "risk", "label": "Risk", "type": "option", "options": [
                    {"id": "low", "label": "Low", "color": "green"},
                    {"id": "medium", "label": "Medium", "color": "yellow"},
                    {"id": "high", "label": "High", "color": "red"},
                ]},
            ],
        },
    },
    "epics": {
        "description": "Software delivery: epics hold stories, bugs and spikes; points and component.",
        "config": {
            "stages": [
                {"id": "todo", "label": "To do", "status": "open", "color": "grey"},
                {"id": "doing", "label": "Doing", "status": "open", "color": "blue"},
                {"id": "review", "label": "Review", "status": "open", "color": "violet"},
                {"id": "done", "label": "Done", "status": "completed", "color": "green"},
            ],
            "types": [
                {"id": "epic", "label": "Epic", "color": "violet", "container": True},
                {"id": "story", "label": "Story", "color": "blue"},
                {"id": "bug", "label": "Bug", "color": "red"},
                {"id": "spike", "label": "Spike", "color": "grey"},
            ],
            "fields": [
                {"id": "points", "label": "Points", "type": "number"},
                {"id": "component", "label": "Component", "type": "text"},
            ],
        },
    },
    "event": {
        "description": "An event or a move: phases as stages, milestones as dates, costs; no types.",
        "config": {
            "stages": [
                {"id": "plan", "label": "Plan", "status": "open", "color": "grey"},
                {"id": "book", "label": "Book", "status": "open", "color": "light-blue"},
                {"id": "prepare", "label": "Prepare", "status": "open", "color": "blue"},
                {"id": "day", "label": "On the day", "status": "open", "color": "orange"},
                {"id": "done", "label": "Done", "status": "completed", "color": "green"},
                {"id": "dropped", "label": "Dropped", "status": "cancelled", "color": "grey"},
            ],
            "types": [],
            "fields": [{"id": "cost", "label": "Cost", "type": "number"}],
        },
    },
}  # fmt: skip

CONFIG_KEYS = ("stages", "types", "fields")


def config(project: dict) -> dict:
    """A project's configuration as one document: its stored stages (empty means
    the defaults), types and field declarations. Copyable between projects."""
    return {key: project[key] for key in CONFIG_KEYS}
