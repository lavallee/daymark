"""Token-lean text and JSON renderings, and the orientation text agents read first."""

from __future__ import annotations

from zoneinfo import ZoneInfo

from .models import DomainError, Entity, Temporal, normalize_now
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
    if item["focus"]:
        tags.insert(0, f"focus {item['focus']}")
    if item["scope"]:
        tags.append("scope:" + item["scope"])
    if item.get("stage"):
        tags.append("stage:" + item["stage"])
    if item.get("milestone"):
        tags.append("milestone")
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
        trail = ", ".join(
            f"{e['action']} {e['at'][:10]}" + (f" by {e['actor']}" if e.get("actor") else "")
            for e in reversed(ctx["history"])
        )
        out.append(f"history: {trail}")
    out.append(f"version {item['version']}")
    return "\n".join(out)


def _plural(count: int, word: str) -> str:
    return f"{count} {word}" + ("" if count == 1 else "s")


def _rollup_text(rollup: dict, health: dict | None = None) -> str:
    parts = [f"{rollup['done']}/{rollup['total']} done ({rollup['percent_done']}%)"]
    if rollup["overdue"]:
        parts.append(f"{rollup['overdue']} overdue")
    if health:
        for key, word in (
            ("blocked", "blocked"),
            ("past_milestone", "past their milestone"),
            ("dependency_conflicts", "date conflicts"),
            ("warnings", "warnings"),
        ):
            if health[key]:
                parts.append(f"{health[key]} {word}")
    if rollup["earliest_start"] or rollup["latest_end"]:
        parts.append(f"{rollup['earliest_start'] or '?'} to {rollup['latest_end'] or '?'}")
    if rollup["milestones"]:
        parts.append(_plural(rollup["milestones"], "milestone"))
    for key, total in sorted(rollup.get("sums", {}).items()):
        parts.append(f"{key} {total:g}")
    if rollup["proposed"]:
        parts.append(f"{rollup['proposed']} proposed")
    return ", ".join(parts)


def _card(card: dict, view: dict, now, *, skip=()) -> str:
    names = {p["id"]: p["title"] or p["id"] for p in view["people"]}
    tags = []
    if card.get("type") and "type" not in skip:
        tags.append(card["type"])
    dates = card["plan"]
    if dates["start"] or dates["end"]:
        tags.append(f"{dates['start'] or '?'}..{dates['end'] or '?'}")
    if card["assignee_id"] and "assignee" not in skip:
        tags.append("owner " + names.get(card["assignee_id"], card["assignee_id"]))
    if card["milestone_id"] and "milestone" not in skip:
        tags.append("for " + card["milestone_id"])
    if card["parent_id"] and "parent" not in skip:
        tags.append("in " + card["parent_id"])
    for key, value in sorted(card["values"].items()):
        if key not in skip:
            shown = ",".join(value) if isinstance(value, list) else value
            tags.append(f"{key}={shown}")
    base = line({**card, "stage": None}, now)
    return base + ("  " + "  ".join(tags) if tags else "")


def _groups(view: dict, by: str) -> list[tuple[str, list]]:
    cards = view["tasks"]
    if by == "stage":
        return [
            (
                f"{s['label'].upper()} ({{n}})  [{s['id']}: {s['status']}]",
                [c for c in cards if c["plan"]["stage"] == s["id"]],
            )
            for s in view["stages"]
        ]
    if by == "milestone":
        groups = [
            (
                f"MILESTONE {m['id']} {m['title']} ({{n}})  [{m['date'] or 'undated'}; "
                f"{m['done']}/{m['total']} done" + ("; reached" if m["reached"] else "") + "]",
                [c for c in cards if c["milestone_id"] == m["id"]],
            )
            for m in view["milestones"]
        ]
        known = {m["id"] for m in view["milestones"]}
        rest = [c for c in cards if c["milestone_id"] not in known and not c["milestone"]]
        return [*groups, ("NO MILESTONE ({n})", rest)]
    if by in {"type", "parent", "assignee"}:
        key = {"type": "type", "parent": "parent_id", "assignee": "assignee_id"}[by]
        titles = {c["id"]: c["title"] for c in cards}
        titles.update({p["id"]: p["title"] for p in view["people"]})
        titles.update({t["id"]: t["label"] for t in view["types"]})
        order = list(dict.fromkeys(c[key] for c in cards))
        return [
            (
                (f"{titles.get(v, v)} [{v}]" if v else f"NO {by.upper()}") + " ({n})",
                [c for c in cards if c[key] == v],
            )
            for v in sorted(order, key=lambda v: (v is None, str(v)))
        ]
    field = next((f for f in view["fields"] if f["id"] == by), None)
    if field is None:
        raise DomainError(
            "invalid",
            "Group by stage, milestone, type, parent, assignee or a field",
            [f["id"] for f in view["fields"]],
        )
    labels = {o["id"]: o["label"] for o in field["options"]}
    found = []
    for card in cards:
        value = card["values"].get(by)
        for item in value if isinstance(value, list) else [value]:
            if item not in found:
                found.append(item)
    return [
        (
            (f"{field['label']}: {labels.get(v, v)}" if v is not None else f"NO {field['label']}")
            + " ({n})",
            [
                c
                for c in cards
                if v == c["values"].get(by)
                or (isinstance(c["values"].get(by), list) and v in c["values"][by])
            ],
        )
        for v in sorted(found, key=lambda v: (v is None, str(v)))
    ]


def plan_text(view: dict, now=None, by: str = "stage") -> str:
    """A project grouped (by stage unless asked), tasks in its manual order."""
    project = view["project"]
    head = _rollup_text(view["rollup"], view["health"])
    out = [f"{project['id']}  {project['title'] or '(project)'}  [{head}]"]
    facts = view["facts"]
    known = [f"{k} {facts[k]}" for k in ("start", "end", "deadline") if facts[k]]
    if facts["lead"]:
        lead = {p["id"]: p["title"] for p in view["people"]}.get(facts["lead"], facts["lead"])
        known.append("lead " + lead)
    if facts["goal"]:
        known.append(f"goal {facts['goal']['id']}")
    if known:
        out.append("  ".join(known))
    if project.get("managed_by"):
        out.append(
            f"managed by {project['managed_by']}: read-only for applications that edit plans"
        )
    skip = {
        "stage": (),
        "milestone": ("milestone",),
        "type": ("type",),
        "parent": ("parent",),
        "assignee": ("assignee",),
    }.get(by, (by,))
    for title, cards in _groups(view, by):
        if not cards and by not in {"stage", "milestone"}:
            continue
        out.append(title.replace("{n}", str(len(cards))))
        out.extend("  " + _card(card, view, now, skip=skip) for card in cards)
    if view["proposed"]:
        out.append(f"PROPOSED, awaiting a decision ({len(view['proposed'])})")
        out.extend("  " + line(item, now, reason=False) for item in view["proposed"])
    for ref in view["external_dependencies"]:
        out.append(
            f"outside this project: {ref['id']} {ref['title']} [{ref['status']}] "
            f"blocks {', '.join(ref['dependents'])}"
        )
    for fact in view["dependency_conflicts"]:
        out.append(
            f"date conflict: {fact['item']} starts {fact['start']} before "
            f"{fact['blocker']} ends {fact['blocker_end']}"
        )
    for warning in view["warnings"]:
        out.append(f"warning: {warning['item']} {warning['code']} {warning['detail']}")
    out.append(f"cursor {view['cursor']}")
    return "\n".join(out)


def projects_text(view: dict) -> str:
    out = [
        f"{p['id']}  {p['title'] or '(project)'}  [{_rollup_text(p['rollup'], p['health'])}"
        + (f"; managed by {p['managed_by']}" if p["managed_by"] else "")
        + "]"
        for p in view["projects"]
    ]
    return "\n".join(out) if out else "No projects. Create one with `daymark project add TITLE`."


def tree_text(node: dict, depth: int = 0) -> str:
    tag = f" {node['type']}" if node.get("type") else ""
    text = (
        "  " * depth
        + f"{node['id']}  {node['title'] or '(' + node['kind'] + ')'}  [{node['status']}{tag}]"
    )
    lines = [text + (f"  (+{node['more']} below)" if node.get("more") else "")]
    lines.extend(tree_text(child, depth + 1) for child in node.get("children", []))
    return "\n".join(lines)


def _measure_text(measure: dict | None) -> str | None:
    if not measure:
        return None
    unit = f" {measure['unit']}" if measure.get("unit") else ""
    current = "?" if measure.get("current") is None else f"{measure['current']:g}"
    percent = f" ({measure['percent']}%)" if measure.get("percent") is not None else ""
    return f"{measure['label']} {current}/{measure['target']:g}{unit}{percent}"


def goal_text(view: dict) -> str:
    goal, rollup = view["goal"], view["rollup"]
    parts = [f"{rollup['done']}/{rollup['total']} done ({rollup['percent_done']}%)"]
    if rollup["at_risk"]:
        parts.append(f"{rollup['at_risk']} at risk")
    if view["target"]["end"]:
        parts.append("target " + view["target"]["end"])
    out = [f"{goal['id']}  {goal['title'] or '(goal)'}  [{'; '.join(parts)}; {view['status']}]"]
    measure = _measure_text(view["measure"])
    if measure:
        out.append("measure: " + measure)
    for project in view["projects"]:
        out.append(
            f"  project {project['id']}  {project['title']}  "
            f"[{project['done']}/{project['total']} done ({project['percent_done']}%)]"
        )
    if view["tasks"]:
        out.append("  tasks: " + ", ".join(view["tasks"]))
    if view["at_risk"]:
        out.append("  at risk: " + ", ".join(view["at_risk"]))
    return "\n".join(out)


def goals_text(view: dict) -> str:
    out = []
    for goal in view["goals"]:
        rollup = goal["rollup"]
        bits = [f"{rollup['done']}/{rollup['total']} done ({rollup['percent_done']}%)"]
        if rollup["at_risk"]:
            bits.append(f"{rollup['at_risk']} at risk")
        if goal["target"]["end"]:
            bits.append("target " + goal["target"]["end"])
        measure = _measure_text(goal["measure"])
        if measure:
            bits.append(measure)
        out.append(f"{goal['id']}  {goal['title'] or '(goal)'}  [{'; '.join(bits)}]")
    return "\n".join(out) if out else "No goals."


def presets_text(presets: dict) -> str:
    out = []
    for name, preset in presets.items():
        stages = ", ".join(s["label"] for s in preset["stages"]) or "To do, Waiting, Done"
        out.append(f"{name}: {preset['description']}")
        out.append(f"  stages: {stages}")
        if preset["types"]:
            out.append("  types: " + ", ".join(t["label"] for t in preset["types"]))
        if preset["fields"]:
            out.append(
                "  fields: " + ", ".join(f"{f['label']} ({f['type']})" for f in preset["fields"])
            )
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
  daymark ready [--scope NAME]      what can be done now, most pressing first
  daymark agenda [--days N]         overdue, today, the days ahead, waiting, proposed
  daymark show ID                   one task with blockers, notes and history
  daymark add "Title" [--due D] [--plan D] [--start D] [--every R] [-p 1-4]
              [--after ID] [--parent ID] [--project ID] [--waiting-on WHO]
              [--move person|answer|agent] [--minutes N] [--scope NAME] [--note TEXT]
  daymark propose "Title" [...]     suggest; a person runs accept/dismiss
  daymark focus ID / unfocus ID     the person's own short list; it leads `ready`
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
Projects (as plain or as structured as the work needs)
  daymark projects / plan P [--by milestone|type|parent|FIELD]   see a project
  daymark add "T" --project P [--stage S] [--assignee NAME] [--milestone M]
  daymark move ID [--stage S] [--after X|--before X|--first|--last]
      a stage sets the status; done, cancel and reopen move the card too
  daymark project add|set|stages|types|fields|config|presets   shape a project
  Add stages, types, fields or milestones only when the person asks for them.
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


def prime_text(tasks, where: str | None = None) -> str:
    """Orientation for a new session: the guide, then the live state."""
    now = tasks.now()
    counts = tasks.summary()
    out = [
        GUIDE,
        "",
        *([f"Database: {where}. Other workspaces are separate and not shown."] if where else []),
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
