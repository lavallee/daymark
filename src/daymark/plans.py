"""Rules for managing a project: its stages, the manual order and the plan's dates.

Pure functions over entity dictionaries. The Store applies them inside its
transactions; views and the toucan adapter read with them. Nothing here writes.
"""

from __future__ import annotations

from .models import DomainError, Temporal

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


# -- manual order: fractional-index keys over base-62 digits, compared as text

DIGITS = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"


def _midpoint(a: str, b: str | None) -> str:
    """A key strictly between ``a`` and ``b`` (None is the end); keys never end in '0'."""
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


def rank_between(low: str | None, high: str | None) -> str:
    """A rank after ``low`` and before ``high``; None means the start or the end."""
    if low is not None and high is not None and low >= high:
        raise DomainError("invalid", "Ranks are out of order")
    return _midpoint(low or "", high)


def order_key(task: dict) -> tuple:
    """Manual order: ranked tasks by rank, then unranked tasks oldest first."""
    return (task.get("rank") is None, task.get("rank") or "", task["created_at"], task["id"])


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
