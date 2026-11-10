"""Token-lean text and JSON renderings, and the orientation text agents read first."""

from __future__ import annotations

from zoneinfo import ZoneInfo

from .models import Entity, Temporal, normalize_now
from .views import claim_holder, why

_DEFAULTS = Entity(id="x", workspace="x", kind="task").model_dump(mode="json")
_ALWAYS = ("id", "kind", "title", "status", "version")
_BOOKKEEPING = {"workspace", "created_at", "updated_at"}


def compact(item: dict) -> dict:
    """An entity without defaults, nulls or bookkeeping. Roughly a fifth of the full size."""
    out = {key: item[key] for key in _ALWAYS if key in item}
    for key, value in item.items():
        if key in out or key in _BOOKKEEPING or value == _DEFAULTS.get(key):
            continue
        if key in {"deadline", "planned", "earliest_start", "latest_target"}:
            value = when(value)
        elif key == "recurrence":
            key, value = "repeats", f"{value['rule']} ({value['mode']})"
        elif key in {"series_id", "occurrence_key"}:
            continue
        elif key == "notes":
            value = [f"{note['at'][:10]} {note['text']}" for note in value]
        out[key] = value
    return out


def when(temporal) -> str:
    """A civil date as YYYY-MM-DD; an instant as local wall time with its zone."""
    value = Temporal.model_validate(temporal)
    if value.date:
        return value.date.isoformat()
    return value.local().strftime("%Y-%m-%d %H:%M ") + value.timezone


def line(item: dict, now=None, reason=True) -> str:
    """One task on one line: id, priority, title, then only what is set."""
    now = normalize_now(now)
    parts = [item["id"]]
    if item["priority"]:
        parts.append(f"P{item['priority']}")
    parts.append(item["title"] or f"({item['kind']})")
    tags = []
    if item["kind"] == "task" and item["status"] in {"open", "waiting"} and reason:
        pressing = why(item, now)
        # The dates below already say when; add words only when it presses now.
        if pressing.startswith("overdue") or pressing.endswith(("today", "ago")):
            tags.append(pressing)
    if item["status"] not in {"open"}:
        tags.append(item["status"])
    if item["deadline"]:
        tags.append("due " + when(item["deadline"]))
    if item["planned"]:
        tags.append("plan " + when(item["planned"]))
    if item["earliest_start"]:
        tags.append("from " + when(item["earliest_start"]))
    if item["waiting_on"]:
        tags.append("waiting on " + item["waiting_on"])
    if item["recurrence"]:
        tags.append("repeats")
    if item["next_move"]:
        tags.append("move:" + item["next_move"])
    if item["estimate_minutes"]:
        tags.append(f"{item['estimate_minutes']}m")
    holder = claim_holder(item, now)
    if holder:
        tags.append("claimed by " + holder)
    if item["depends_on"]:
        tags.append("after " + ",".join(item["depends_on"]))
    return "  ".join(parts) + ("  [" + "; ".join(dict.fromkeys(tags)) + "]" if tags else "")


def listing(items, now=None, empty="Nothing here.") -> str:
    return "\n".join(line(item, now) for item in items) if items else empty


def agenda_text(view: dict, now=None) -> str:
    out = []
    for key, label in (("overdue", "OVERDUE"), ("today", "TODAY")):
        if view[key]:
            out.append(f"{label} ({len(view[key])})")
            out.extend("  " + line(item, now) for item in view[key])
    for day, items in view["upcoming"].items():
        out.append(day)
        out.extend("  " + line(item, now, reason=False) for item in items)
    for key, label in (("waiting", "WAITING"), ("proposed", "PROPOSED, awaiting a decision")):
        if view[key]:
            out.append(f"{label} ({len(view[key])})")
            out.extend("  " + line(item, now, reason=False) for item in view[key])
    if view.get("later_or_undated"):
        out.append(f"{view['later_or_undated']} more later or undated; see `daymark ready`.")
    return "\n".join(out) if out else "Nothing scheduled, waiting or proposed."


def show_text(ctx: dict, now=None) -> str:
    item = ctx["entity"]
    out = [line(item, now)]
    if item["description"]:
        out.append(item["description"])
    for key, label in (
        ("deadline", "deadline (hard)"),
        ("planned", "planned (intention)"),
        ("earliest_start", "not before"),
        ("latest_target", "target (soft)"),
    ):
        if item[key]:
            said = item[key].get("expression")
            out.append(f"{label}: {when(item[key])}" + (f'  (said: "{said}")' if said else ""))
    if item["recurrence"]:
        rule = item["recurrence"]
        out.append(f"repeats: {rule['rule']} ({rule['mode']}, {rule['timezone']})")
    for key in ("project_id", "parent_id", "goal_id", "assignee_id"):
        if item[key]:
            out.append(f"{key.removesuffix('_id')}: {item[key]}")
    for check in item["checklist"]:
        out.append(f"  [{'x' if check['completed'] else ' '}] {check['text']}")
    if ctx["blocked_by"]:
        out.append("blocked by: " + ", ".join(ctx["blocked_by"]))
    if ctx["dependents"]:
        out.append("unblocks: " + ", ".join(ctx["dependents"]))
    for child in ctx["children"]:
        out.append(f"  child {child['id']}  {child['title']}  [{child['status']}]")
    for reminder in ctx["reminders"]:
        out.append(
            f"  reminder {reminder['id']} at {reminder['scheduled_at']} [{reminder['state']}]"
        )
    for note in item["notes"]:
        author = f" {note['author']}" if note["author"] else ""
        out.append(f"note {note['at'][:16]}{author}: {note['text']}")
    if ctx["history"]:
        trail = ", ".join(f"{e['action']} {e['at'][:10]}" for e in reversed(ctx["history"]))
        out.append(f"history: {trail}")
    out.append(f"version {item['version']}")
    return "\n".join(out)


GUIDE = """\
Daymark keeps tasks, plans and time for a person's work and life. Use it instead
of ad-hoc TODO lists so nothing is lost between sessions.

Rules
- Never invent a date, priority or completion. Record only what is known.
- A deadline is a hard cutoff (--due). A plan is an intention that may move
  (--plan). "Not before" hides work until it can start (--start). Do not put an
  intention in --due.
- If the person did not ask for a task, propose it; they accept or dismiss.
- Claim before working when others may be working too. Leave a note when you stop.

Commands (add --json to any for structured output)
  daymark ready                     what can be done now, most pressing first
  daymark agenda [--days N]         overdue, today, the days ahead, waiting, proposed
  daymark show ID                   one task with blockers, notes and history
  daymark add "Title" [--due D] [--plan D] [--start D] [--every R] [-p 1-4]
              [--after ID] [--parent ID] [--project ID] [--waiting-on WHO]
              [--move person|answer|agent] [--minutes N] [--note TEXT]
  daymark propose "Title" [...]     suggest; a person runs accept/dismiss
  daymark claim ID / release ID     take or give back a task
  daymark note ID "text"            progress or context for the next session
  daymark done ID [--note TEXT]     complete (a repeating task spawns its next)
  daymark skip ID                   pass over one occurrence without completing it
  daymark wait ID --on WHO [--until D] / resume ID
  daymark defer ID D                hide until a date; the deadline does not move
  daymark edit ID [--title T] [--due D] ...   (--due "" clears)
  daymark block ID --on OTHER / unblock ID --on OTHER
  daymark find TEXT                 search titles and descriptions
  daymark remember "fact"           durable context shown by `daymark prime`
  daymark undo ID                   restore the previous state
Dates D: 2030-04-03, 2030-04-03T09:00, today, tomorrow, fri, +3d, +2w, +1m ("fri 09:00").
Repeats R: day, week, month, year, "2 weeks", weekdays, "mon,thu"; --after-completion
  counts from when it was actually done.
Priority: 1 is most important, 4 least."""


ONBOARD = """\
<!-- daymark:begin -->
## Tasks: Daymark

This project tracks tasks, plans and deadlines in Daymark.

- Run `daymark prime` at the start of a session for the workflow and current state.
- Use `daymark ready`, `daymark show ID`, `daymark claim ID`, `daymark note ID "..."`
  and `daymark done ID`. Add `--json` for structured output.
- Capture new work with `daymark add "Title" --due DATE` (hard deadline) or
  `--plan DATE` (intention). Never invent dates.
- Suggest work the person did not ask for with `daymark propose`; they decide.
- Do not keep task lists in markdown files.
<!-- daymark:end -->
"""


def prime_text(tasks) -> str:
    """Orientation for a new session: the guide, then the live state."""
    now = tasks.now()
    counts = tasks.summary()
    out = [
        GUIDE,
        "",
        "Now ("
        + now.astimezone(ZoneInfo(tasks.timezone)).strftime("%Y-%m-%d %a")
        + ", "
        + tasks.timezone
        + ")",
    ]
    out.append(
        "  " + ", ".join(f"{value} {key.replace('_', ' ')}" for key, value in counts.items())
    )
    memories = tasks.memories()
    if memories:
        out.append("Remembered")
        out.extend(f"  - {memory['title']}  ({memory['id']})" for memory in memories[:50])
    top = tasks.ready(limit=5)
    if top:
        out.append("Most pressing")
        out.extend("  " + line(item, now) for item in top)
    return "\n".join(out)
