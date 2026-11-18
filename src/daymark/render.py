"""Token-lean text and JSON renderings, and the orientation text agents read first."""

from __future__ import annotations

from pathlib import Path
from shlex import join
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from .models import DomainError, Entity, Temporal, normalize_now
from .views import claim_holder, why

_DEFAULTS = Entity(id="x", workspace="x", kind="task").model_dump(mode="json")
_ALWAYS = ("id", "kind", "title", "status", "version")
EMPTY_TAKE = "No unclaimed agent task is ready in this scope."
_BOOKKEEPING = {"workspace", "created_at", "updated_at"}


def reminder_fields(item):
    """The reminder lifecycle is independent of its task's open or completed state."""
    return {
        key: item[key]
        for key in ("task_id", "scheduled_at", "recipient_id", "channel", "reminder_state")
        if key in item
    }


def compact(item: dict) -> dict:
    """An entity without defaults, nulls or bookkeeping. Roughly a fifth of the full size."""
    out = {key: item[key] for key in _ALWAYS if key in item}
    if item.get("kind") == "reminder":
        out.update(reminder_fields(item))
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


def line(item: dict, now=None, reason=True, *, timezone="UTC") -> str:
    """One task on one line: id, priority, title, then only what is set."""
    # Historical write receipts keep the entity shape of the release that made
    # them. Apply today's display defaults without rewriting their evidence.
    item = _DEFAULTS | item
    now = normalize_now(now)
    parts = [item["id"]]
    if item["priority"]:
        parts.append(f"P{item['priority']}")
    parts.append(item["title"] or f"({item['kind']})")
    tags = ["archived"] if item["archived"] else []
    if item["kind"] == "reminder":
        scheduled = when({"at": item["scheduled_at"], "timezone": timezone})
        tags.extend([item["reminder_state"], scheduled, f"to {item['recipient_id']}", f"channel {item['channel']}"])
        if item["task_id"]:
            tags.append("task " + item["task_id"])
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
    if item["latest_target"]:
        tags.append("target " + when(item["latest_target"]))
    if item["earliest_start"]:
        tags.append("from " + when(item["earliest_start"]))
    if item["waiting_on"]:
        tags.append("waiting on " + item["waiting_on"])
    if item["recurrence"]:
        tags.append("repeats")
    if item["kind"] == "condition" and item["status"] != "cancelled":
        tags.append(item["condition"].replace("_", " "))
    if item["advances"]:
        tags.append("advances " + ",".join(item["advances"]))
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


def ready_explanation(view):
    ready, held = view["ready_count"], view["held_count"]
    if not ready and not held:
        return "No live tasks match this view."
    out = [f"{ready} ready, {held} held"]
    out.extend(f"  {item['id']}  {item['title']}  [{item['why']}]" for item in view["ready"])
    if len(view["ready"]) < ready:
        out.append(f"  Showing {len(view['ready'])} of {ready}. Use --limit for more.")
    labels = {
        "active_children": "Have unfinished child tasks", "future_start": "Start later",
        "future_round": "Waiting for the next round's planned day",
        "claimed_elsewhere": "Claimed by another worker", "dependencies": "Waiting for prerequisites",
        "proposed": "Awaiting a decision", "waiting": "Waiting on someone or something",
    }
    for code, group in sorted(view["reasons"].items()):
        out.append(f"{labels[code]} ({group['count']}): " + ", ".join(group["examples"]))
    if held:
        out.append("Read a task with daymark show ID for its details.")
    return "\n".join(out)


def search_text(page):
    lines = []
    for item in page["items"]:
        lease = f" until {item['claim_until']}" if "claim_until" in item else ""
        lines.append(f"{item['id']}  {item['title']}  [{item['status']}]{lease}")
    if page["next"] is not None:
        lines.append("More matches. Repeat this search with " + join([f"--after={page['next']}"]))
    return "\n".join(lines) or "No matches."


def listing(items, now=None, empty="Nothing here.") -> str:
    return "\n".join(line(item, now) for item in items) if items else empty


def explore_text(view: dict) -> str:
    """Conditions in the order they were written, each with its state and what moves it."""
    out = []
    if view["goal"]:
        out.append(f"{view['goal']['id']}  {view['goal']['title']}")
    marks = {"met": "x", "partly_met": "~", "unmet": " ", "unknown": "?", "dropped": "-"}
    for item in view["conditions"]:
        tags = []
        if item["frontier"]:
            tags.append(
                "next"
                if item["advancing"] and item["id"] not in view["gaps"]
                else "next; nothing moving it"
            )
        if item["waiting_on"]:
            tags.append("needs " + ",".join(item["waiting_on"]))
        out.append(
            f"  [{marks[item['state']]}] {item['id']}  {item['title']}"
            + (f"  [{'; '.join(tags)}]" if tags else "")
        )
        for task in item["advancing"]:
            out.append(f"        {task['id']}  {task['title']}  [{task['status']}]")
        for evidence in item["evidence"][-1:]:
            out.append(f"        evidence: {evidence}")
    if not view["conditions"]:
        out.append('No conditions recorded. Add one with `daymark condition "..." --goal ID`.')
    if view["unassessed"]:
        out.append(
            f"{len(view['unassessed'])} not yet assessed: {', '.join(view['unassessed'][:8])}"
        )
    return "\n".join(out)


def _read_hint(view, options, session, mcp):
    if mcp:
        import json

        return "daymark_view " + json.dumps({"view": view, **options}, separators=(",", ":"))
    command = ["daymark"]
    if session and "database" in session and session["database"] != ":memory:":
        command += [f"--db={session['database']}", f"--workspace={session['workspace']}",
                    f"--as={session['actor']}", f"--tz={session['timezone']}"]
    command.append(view)
    for key, value in options.items():
        if key == "scope" and value == "":
            command.append("--unscoped")
        elif str(value).startswith("-"):
            command.append(f"--{key}={value}")
        else:
            command.extend([f"--{key}", str(value)])
    return join(command)


def advice_text(advice, now=None, *, session=None, mcp=False) -> str:
    """Picks, then what the profile's strategies noticed."""
    plain = advice.tone != "neutral"
    out = []
    source = advice.profile_source
    if source and (source["kind"] in {"built-in", "saved"} or source["adjusted"]):
        label = f"{source['kind']} profile {source['name']}"
        if "version" in source:
            label += f" v{source['version']}"
        changes = " with temporary adjustments" if source["adjusted"] else ""
        out.append(f"Using {label}{changes} for this call.")
    for item in advice.picks:
        # In the neutral tone the dates still show; the words "overdue" and "ago" do not.
        out.append(line(item, now, reason=plain))
        if item["id"] in advice.steps:
            out.append(f"    next: {advice.steps[item['id']]}")
    if not advice.picks:
        if advice.held_back:
            out.append("No picks for this view.")
        elif advice.context is not None:
            out.append(f"No ready tasks tagged with context {advice.context}.")
        else:
            out.append("Nothing is ready." if advice.scope is None else "Nothing is ready in this scope.")
    if advice.held_back:
        hint = _read_hint("ready", advice.ready_query(), session, mcp)
        qualifier = " with the same service and credential" if session and "endpoint" in session else ""
        out.append(f"({advice.held_back} more ready; see `{hint}`{qualifier}.)")
    for note in advice.notes:
        ids = (
            f"  [{', '.join(note.ids[:8])}{', …' if len(note.ids) > 8 else ''}]" if note.ids else ""
        )
        out.append(f"- {note.text}{ids}")
    return "\n".join(out)


def profile_text(profile: dict, *, database=None) -> str:
    out = [f"profile: {profile['name']}"]
    for stage in profile["strategies"]:
        settings = ", ".join(f"{k}={v}" for k, v in stage["with"].items())
        out.append(f"  {stage['use']}" + (f"  ({settings})" if settings else ""))
        for key, value in stage.get("unavailable_parameters", {}).items():
            field = f"{stage['use']}.{key}"
            out.append(f"    ! {field}={value!r} is saved but unavailable here. To remove it, use --drop {field}.")
    if not profile["strategies"]:
        out.append("  no strategies: `daymark ready` order, nothing held back, no notes")
    for name in profile.get("skipped") or []:
        out.append(f"  ! {name}: not available or misconfigured; left out. "
                   f"Choose a profile again or `--drop {name}`.")  # fmt: skip
    if "workspace" in profile:
        out.append(f"Workspace {profile['workspace']}, active version {profile['version']}")
    if undo := profile.get("undo"):
        command = ["daymark"]
        if database and database != ":memory:":
            command.append(f"--db={Path(database).absolute()}")
        command.extend([f"--workspace={profile['workspace']}", "profile",
                        f"--undo={undo['operation_id']}", "--if-version", str(undo["expected_version"])])
        if database == ":memory:":
            out.append("This profile lasts for the current in-memory database session.")
        else:
            out.append(f"Restore the previous settings in this database with {join(command)}")
    return "\n".join(out)


def strategy_detail(data):
    if data.get("installed_plugin"):
        return f"{data['name']}  {data['summary']} Use `advise --set {data['name']}` to preview it."
    lines = [f"{data['name']} [{data['kind']}]  {data['summary']}", f"Practice evidence: {data['evidence']}"]
    for key, param in data["parameters"].items():
        bounds = []
        if "minimum" in param:
            bounds.append(f"minimum {param['minimum']}")
        if "maximum" in param:
            bounds.append(f"maximum {param['maximum']}")
        extra = f" ({', '.join(bounds)})" if bounds else ""
        lines.append(f"  {data['name']}.{key}={param['default']}  {param['help']}{extra}")
    return "\n".join(lines)


def strategies_text(listed, profiles: dict, *, plugins=()) -> str:
    out = ["Strategies (kind, evidence for the underlying practice):"]
    for s in listed:
        settings = ", ".join(f"{k}={p.default}" for k, p in s.params.items())
        out.append(f"  {s.name}  [{s.kind}; {s.evidence}]  {s.summary}"
                   + (f"  ({settings})" if settings else ""))  # fmt: skip
    if plugins:
        out.append("Installed plugin names: " + ", ".join(plugins))
    out.append("Profiles (adopt with `daymark profile NAME`):")
    for name, profile in profiles.items():
        uses = ", ".join(x["use"] for x in profile["strategies"]) or "none"
        out.append(f"  {name}: {uses}")
    return "\n".join(out)


def saved_profiles_text(entries) -> str:
    if isinstance(entries, dict):
        return profile_text(entries["profile"]) + f"\nsaved version {entries['version']}"
    return "\n".join(
        f"{entry['name']}  version {entry['version']}" for entry in entries
    ) or "No saved profiles. Create one with `daymark profile --save-as NAME`."


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


def activity_text(view: dict) -> str:
    """One line per change; full note text remains available in JSON and show."""
    out = []
    for event in view["events"]:
        who = f" by {event['actor']}" if event["actor"] else ""
        title = " ".join(event["title"].split())
        row = f"{event['seq']}  {event['at'][:16]}  {event['action']}{who}  {event['id']}  {title}"
        if note := event.get("note"):
            text = " ".join(note["text"].split())
            row += " — " + (text if len(text) <= 240 else text[:239] + "…")
        elif event["action"] == "update" and event.get("changed"):
            row += "  [" + ", ".join(event["changed"]) + "]"
        out.append(row)
    if not out:
        out.append("No activity.")
    out.append(f"cursor {view['cursor']}")
    if "checkpoint" in view:
        out.append(f"checkpoint {view['checkpoint']}")
    if view["cursor"] < view["head"]:
        out.append(f"More changes: daymark activity --after {view.get('checkpoint', view['cursor'])}")
    return "\n".join(out)


def note_text(note: dict) -> str:
    who = f" by {note['author']}" if note.get("author") else ""
    end = "…" if note.get("truncated") else ""
    return f"note {note['id']}  {note['at']}{who}  {note['text']}{end}"


def comparison_text(view: dict) -> str:
    import json

    out = [f"{view['id']}  version {view['from_version']} → {view['to_version']}"]
    if not view["available"]:
        reason = "The recorded snapshots disagree." if view["reason"] == "ambiguous_history" else "The reviewed version is missing from this history."
        return "\n".join([*out, reason, "Review the current record with the person before deciding."])
    for change in view["fields"]:
        if change.get("values_hidden"):
            out.append(f"  {change['field']} changed (opaque value)")
            continue
        sides = []
        for key in ("before", "after"):
            side = json.dumps(change[key], ensure_ascii=False, separators=(",", ":"))
            if info := change.get("previews", {}).get(key):
                side = f"{side} [excerpt at {info['offset']} of {info['length']} characters]"
            sides.append(side)
        out.append(f"  {change['field']}  {sides[0]} → {sides[1]}")
    if notes := view.get("notes"):
        out.append(f"  notes: {notes['added']} added, {notes['removed']} removed, {notes['edited']} edited"
                   + (", order changed" if notes["reordered"] else ""))
        for kind in ("added", "removed", "edited"):
            for note in notes.get(kind + "_notes", []):
                out.append(f"    {kind} {note_text(note)}")
    if not view["fields"] and not view.get("notes"):
        out.append("No net field changes between these versions.")
    return "\n".join(out)


def source_text(ref: dict) -> str:
    return f"source {ref['system']}:{ref['kind']}:{ref['id']}"


def _blocker_labels(view):
    named = {row["id"]: row for row in view.get("blockers", [])}
    labels = []
    for id in view["blocked_by"]:
        row = named.get(id)
        if row and "title" in row:
            title = row["title"] + ("…" if row.get("truncated") else "")
            labels.append(f"{id}  {title} [{row['status']}]")
        else:
            labels.append(id)
    return ", ".join(labels)


def brief_text(view: dict) -> str:
    clipped = view.get("truncated", [])
    title = view["title"] + ("…" if "title" in clipped else "")
    archived = "archived, " if view.get("archived") else ""
    out = [f"{view['id']}  {title}  [{archived}{view['status']}, version {view['version']}]"]
    if view.get("review_token"):
        out.append(f"Review token: {view['review_token']}")
    if "readiness" in view:
        state = view["readiness"]
        out.append("Ready now" if state["ready"] else "Not ready: " + ", ".join(state["reasons"]))
    if "next_move" in view:
        out.append("Next move: " + view["next_move"])
    if rule := view.get("recurrence"):
        pace = "from completion" if rule["mode"] == "after_completion" else "on a fixed schedule"
        date = "deadline" if rule["advances"] == "deadline" else "planned day"
        series = f" in series {view['series_id']}" if view.get("series_id") else ""
        out.append(f"Round {rule['occurrence_index'] + 1}{series}. {rule['rule']} {pace} "
                   f"({rule['timezone']}). Generated rounds advance the {date}.")
    if "claim" in view:
        out.append(f"Claim: {view['claim']['owner']} until {view['claim']['until']}")
    if "waiting_on" in view:
        out.append("Waiting on: " + view["waiting_on"] + ("…" if "waiting_on" in clipped else ""))
    out.extend(source_text(ref) for ref in view.get("source_refs", []))
    if extra := view["omitted"].get("source_refs"):
        out.append(f"{extra} more sources in full detail")
    if "description" in view:
        out.append(view["description"] + ("…" if "description" in clipped else ""))
    for key, label in (("deadline", "Due"), ("planned", "Plan"), ("earliest_start", "Start"),
                       ("latest_target", "Target")):
        if key in view:
            out.append(f"{label}: {when(view[key])}")
    for key in ("blocked_by", "active_children"):
        if view[key]:
            extra = view["omitted"][key]
            out.append(key.replace("_", " ") + ": " + (_blocker_labels(view) if key == "blocked_by" else ", ".join(view[key]))
                       + (f" (+{extra} more)" if extra else ""))
    omitted_notes = view["omitted"]["notes"]
    if omitted_notes:
        out.append(f"{omitted_notes} earlier notes. Read before note {view['next_before_note']}.")
    if view["omitted"]["newer_notes"]:
        out.append(f"{view['omitted']['newer_notes']} newer notes outside this page")
    out.extend(note_text(note) for note in view["notes"])
    if any(note.get("truncated") for note in view["notes"]):
        out.append("Read a full note: daymark show ID --note NOTE_ID")
    return "\n".join(out)


def show_text(ctx: dict, now=None) -> str:
    item = ctx["entity"]
    out = [line(item, now)]
    if ctx.get("review_token"):
        out.append(f"Review token: {ctx['review_token']}")
    if claim_holder(item, normalize_now(now)):
        out.append(f"claim expires: {item['claim_until']}")
    if "readiness" in ctx:
        state = ctx["readiness"]
        reasons = {
            "active_children": "finish child work " + ", ".join(state["active_children"]),
            "future_start": "start has not arrived",
            "future_round": "this round starts on its planned day",
            "claimed_elsewhere": "claimed by another actor",
            "dependencies": "waiting for " + ", ".join(ctx["blocked_by"]),
            "proposed": "awaiting a decision",
        }
        why = ", ".join(reasons.get(code, code) for code in state["reasons"])
        out.append("Ready now" if state["ready"] else "Not ready: " + why)
    out.extend(source_text(ref) for ref in item["source_refs"])
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
    if item.get("context_ids"):
        out.append("contexts: " + ", ".join(item["context_ids"]))
    for check in item["checklist"]:
        out.append(f"  [{'x' if check['completed'] else ' '}] {check['text']}")
    if ctx["blocked_by"]:
        out.append("blocked by: " + _blocker_labels(ctx))
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


def plan_groups(view: dict, by: str) -> list[tuple[str, list]]:
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
    for title, cards in plan_groups(view, by):
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
        + (f"; {p['status']}" if p["status"] != "open" else "")
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
Use Daymark to pick up a person's commitments across sessions.

Choose fields from the person's words
  intended day ("I intend to", "I'd like to")    --plan
  hard cutoff ("must be submitted by")          --due
  first possible day ("cannot start until")     --start
  hoped-for finish ("aim to finish by")         --target
  idea awaiting a decision                      propose
  existing task waiting on a reply              wait ID --on WHO_OR_WHAT
  maximum items to show this time               ready --limit N
Leave dates and priority unset unless supplied. Record completion only when it happened.
Use distinct actors for concurrent workers and claim before starting shared work.

Commands (add --json to any for structured output)
  daymark where                     explain database selection without opening it
  daymark prime --brief             current session without the command guide
  daymark onboard --skill           full agent skill for the installed CLI
  daymark ready [--scope NAME] [--context ID] [--explain]   actionable work, with optional reasons work is held
  daymark agenda [--days N]         overdue, today, the days ahead, waiting, proposed
  daymark show ID [--since-version N]   current detail or changes since a reviewed version
  daymark activity [--after CURSOR]  recent changes, or changes after a saved checkpoint
  daymark add "Title" [--due D] [--plan D] [--start D] [--every R] [-p 1-4]
              [--after ID] [--parent ID] [--project ID] [--waiting-on WHO]
              [--move person|answer|agent] [--minutes N] [--scope NAME] [--note TEXT]
  daymark propose "Title" [...]     suggest; a person runs accept/dismiss ID [--if-review TOKEN]
  daymark focus ID / unfocus ID     the person's own short list; it leads `ready`
  daymark explore [GOAL]            what must be true for a goal, what is next, what is stuck
  daymark condition "..." --goal G  record such a condition; `assess ID STATE --evidence T`
  daymark advise [--context ID] [--set shortlist.size=3]   try advice for this request
  daymark strategies [NAME]   discover methods and inspect a parameter range
  daymark profile --saved           named choices to reuse with advise --profile NAME
  daymark --as WORKER take [--scope NAME] [--context ID]   claim the next unclaimed ready agent task
  daymark claim ID / release ID [--note TEXT]   take work or hand it back with context
  daymark note ID "text"            progress or context for the next session
  daymark done ID [--note TEXT]     complete (a repeating task spawns its next)
  daymark skip ID                   pass over one occurrence without completing it
  daymark wait ID --on WHO [--until D] / resume ID
  daymark remind ID WHEN --to PERSON   schedule a timed reminder; the default worker records it locally
  daymark defer ID D                hide until a date; the deadline does not move
  daymark edit ID [--if-version N] [--due D] ...   (--due "" clears)
  daymark edit ID --waiting-on WHO  park open work; resume ID when the wait ends
  daymark block ID --on OTHER / unblock ID --on OTHER
  daymark find TEXT [--kind memory] [--page]   find work or a remembered fact
  daymark --as WORKER find --claimed   recover this worker’s active claims, including held work
  daymark remember "fact" [--scope NAME] [--source SYSTEM:KIND:ID]   keep an explicit workspace fact
  daymark forget MEMORY_ID [--if-review TOKEN]   retire a remembered fact from pickup
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
- Keep each commitment in its owning tracker. Use a Daymark source reference for related work elsewhere.
<!-- daymark:end -->
"""


def endpoint_origin(url):
    """An HTTP origin suitable for display, without user info, path or query."""
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc.rsplit('@', 1)[-1]}"


def session_context(tasks, *, database=None, url=None) -> dict:
    """Describe the active session without exposing credentials or guessing HTTP identity."""
    context = {"timezone": tasks.timezone}
    if url:
        context["endpoint"] = endpoint_origin(url)
        context["identity_source"] = "server credential"
    else:
        context.update(workspace=tasks.workspace, actor=tasks.actor)
        if database:
            context["database"] = str(Path(database).absolute()) if database != ":memory:" else database
    return context


def brief_session(tasks, context):
    """Bound the amount of task history and remembered text sent at session pickup."""
    state = tasks.orientation()
    identity = state.get("identity", {})
    result = {key: value for key, value in state.items() if key != "identity"}
    result.update(context={**context, **identity}, ready=[compact(item) for item in state["ready"]])
    if getattr(tasks.engine, "legacy_search", False):
        result["summary_note"] = "The service reports its own ready count. Older servers exclude claimed work and count up to 1,000."
    return result


def prime_text(tasks, where: str | None = None, *, context=None, brief=False, mcp=False) -> str:
    """Show the live session first. The brief form omits the command reference."""
    context = context or session_context(tasks)
    state = brief_session(tasks, context)
    now = normalize_now(state["as_of"]) if state.get("as_of") else tasks.now()
    context = state["context"]
    out = []
    if where:
        out.append(f"Database: {where}")
    elif "endpoint" in context:
        if "workspace" in context and "actor" in context:
            out.append(f"Endpoint: {context['endpoint']}, workspace {context['workspace']}, actor {context['actor']} (server credential)")
        else:
            out.append(f"Endpoint: {context['endpoint']} (workspace and actor set by server credential)")
    else:
        location = f"{context['database']}, " if "database" in context else ""
        out.append(f"Daymark: {location}workspace {context['workspace']}, actor {context['actor']}")
    out.append(now.astimezone(ZoneInfo(tasks.timezone)).strftime("%Y-%m-%d %a") + ", " + tasks.timezone)
    counts = state["summary"]
    out.append("  " + ", ".join(f"{v} {k.replace('_', ' ')}" for k, v in counts.items()))
    if state.get("summary_note"):
        out.append("  " + state["summary_note"])
    if state["ready"]:
        out.append("Most pressing")
        for item in state["ready"]:
            facts = [
                f"{label} {item[key]}" for key, label in (
                    ("deadline", "due"), ("planned", "plan"), ("next_move", "move"),
                    ("claimed_by", "claimed by"),
                ) if item.get(key)
            ]
            details = "  [" + "; ".join(facts) + "]" if facts else ""
            out.append(f"  {item['id']}  {item['title']}{details}")
    if state["memories"]:
        out.append("Remembered")
        for fact in state["memories"]:
            scope = f"  [scope:{fact['scope']}]" if fact.get("scope") else ""
            out.append(f"  - {fact['title']}{scope}  ({fact['id']})")
        if state["next_memory"]:
            hint = _read_hint("find", {"kind": "memory", "after": state["next_memory"]}, context, mcp)
            out.append(f"  More facts: {hint}")
    if "activity_cursor" in state:
        cursor = state.get("activity_checkpoint", state["activity_cursor"])
        hint = _read_hint("activity", {"after": cursor}, context, mcp)
        out.append(f"Changes after this snapshot: {hint}")
    if not mcp and "endpoint" in context and (state["next_memory"] or "activity_cursor" in state):
        out.append("Use the same service URL and credential for these reads.")
    if not brief:
        out.extend(["", GUIDE])
    return "\n".join(out)
