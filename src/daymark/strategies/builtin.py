"""Built-in strategies. Each names the practice it comes from and how well that
practice is supported; docs/research/working-methods.md has the sources."""

from __future__ import annotations

from datetime import datetime

from ..models import Temporal, day_offset, start_has_arrived
from . import Advice, Note, Param, Snapshot, Strategy
from . import register as register_strategy

BUILTINS: dict[str, Strategy] = {}


def register(strategy: Strategy) -> None:
    """Keep shipped methods separately from host-registered extensions."""
    BUILTINS[strategy.name] = register_strategy(strategy)



def _age_days(stamp: str, now: datetime) -> int:
    return (now - datetime.fromisoformat(stamp)).days


def _shortlist(view: Snapshot, advice: Advice, params: dict) -> None:
    size = params["size"]
    if len(advice.picks) > size:
        advice.held_back += len(advice.picks) - size
        advice.picks = advice.picks[:size]


register(
    Strategy(
        name="shortlist",
        reads=(),
        kind="limit",
        summary="Offer only the first few ready tasks; say how many more there are.",
        evidence="practitioner",  # Ivy Lee's six; Marvin's truncated list; CBT for adult ADHD
        apply=_shortlist,
        params={"size": Param(6, "How many tasks to offer", 1, 50)},
    )
)


def _time_fit(view: Snapshot, advice: Advice, params: dict) -> None:
    minutes = params["minutes"]
    fits, longer, unknown = [], 0, 0
    due_held = []
    considered = len(advice.picks)
    for item in advice.picks:
        estimate = item["estimate_minutes"]
        if estimate is None:
            unknown += 1
        elif estimate > minutes:
            longer += 1
        else:
            fits.append(item)
            continue
        if item["deadline"] and day_offset(item["deadline"], view.now) <= 0:
            due_held.append(item["id"])
    advice.picks = fits
    advice.held_back += longer + unknown
    if fits or longer or unknown:
        count = len(fits)
        if count == 1:
            message = f"1 task has an estimate of {minutes} minutes or less."
        else:
            message = f"{count} tasks have individual estimates of {minutes} minutes or less."
        if considered < len(view.ready):
            message = (
                f"This step checked {considered} of {len(view.ready)} ready candidates. " + message
            )
        if longer:
            message += " 1 task needs more time." if longer == 1 else f" {longer} tasks need more time."
        if unknown:
            message += " 1 task has no estimate." if unknown == 1 else f" {unknown} tasks have no estimate."
        advice.notes.append(Note("time-fit", message))
    if due_held:
        count = len(due_held)
        text = (
            "One omitted task has a hard deadline"
            if count == 1
            else f"{count} omitted tasks have hard deadlines"
        )
        advice.notes.append(Note("time-fit", text + " today or earlier.", due_held[:5]))


register(
    Strategy(
        name="time-fit",
        reads=(),
        kind="limit",
        summary="Offer tasks individually estimated to fit the available time.",
        evidence="practitioner",
        apply=_time_fit,
        params={"minutes": Param(30, "Minutes available for one task", 1, 1440)},
    )
)


def _first_step(view: Snapshot, advice: Advice, params: dict) -> None:
    missing = []
    for item in advice.picks:
        step = next((c["text"] for c in item["checklist"] if not c["completed"]), None)
        if step:
            advice.steps[item["id"]] = step
        elif _age_days(item["created_at"], view.now) >= params["after_days"]:
            missing.append(item["id"])
    if missing:
        advice.notes.append(
            Note("first-step", "No first step written down; ask what the smallest one is.", missing)
        )


register(
    Strategy(
        name="first-step",
        reads=(),
        kind="hygiene",
        summary="Show each task's next unchecked step; flag older tasks that have none.",
        evidence="clinical",  # breaking tasks into steps is a taught skill in adult ADHD CBT
        evidence_note=(
            "Breaking tasks into steps is part of a therapy program evaluated in a clinical trial. "
            "Daymark's checklist display and age threshold are design choices."
        ),
        apply=_first_step,
        params={"after_days": Param(2, "Flag a task with no step once it is this old", 0, 365)},
    )
)


def _stale(view: Snapshot, advice: Advice, params: dict) -> None:
    old = [x["id"] for x in view.open if _age_days(x["updated_at"], view.now) >= params["days"]]
    if old:
        advice.notes.append(
            Note("stale", f"Untouched for {params['days']} days or more; still wanted?", old[:50])
        )


register(
    Strategy(
        name="stale",
        reads=("open",),
        kind="hygiene",
        summary="List open tasks nobody has touched for a while, for review.",
        evidence="practitioner",  # GTD weekly review; OmniFocus review; Marvin staleness warning
        apply=_stale,
        params={"days": Param(30, "Days without a change before a task counts as stale", 1, 3650)},
    )
)


def _waiting_check(view: Snapshot, advice: Advice, params: dict) -> None:
    old = [
        item for item in view.waiting
        if start_has_arrived(item["earliest_start"], view.now)
        and _age_days(item["updated_at"], view.now) >= params["days"]
    ]
    for item in old[:50]:
        whom = item["waiting_on"] or "someone"
        days = _age_days(item["updated_at"], view.now)
        unit = "day" if days == 1 else "days"
        text = f"Waiting on {whom}. No task update in {days} {unit}. Follow up?"
        advice.notes.append(Note("waiting-check", text, [item["id"]]))


register(
    Strategy(
        name="waiting-check",
        reads=("waiting",),
        kind="follow-through",
        summary="Point out waiting items that have been quiet long enough to chase.",
        evidence="practitioner",  # executive-assistant waiting-for lists; GTD waiting-for
        apply=_waiting_check,
        params={"days": Param(5, "Quiet days before suggesting a follow-up", 1, 365)},
    )
)


def _overbooked(view: Snapshot, advice: Advice, params: dict) -> None:
    reviewed, focus_only = [], []
    for item in view.ready:
        dated = any(
            item[key] and day_offset(item[key], view.now) <= 0 for key in ("deadline", "planned")
        )
        if dated:
            reviewed.append(item)
        elif params["include_focus"] and item["focus"]:
            reviewed.append(item)
            focus_only.append(item)
    assumed = params["unestimated_minutes"]
    total = sum(item["estimate_minutes"] or assumed for item in reviewed)
    capacity = params["capacity_minutes"]
    if total <= capacity:
        return
    unknown = sum(item["estimate_minutes"] is None for item in reviewed)
    guess = f" ({unknown} counted at {assumed} min)" if unknown else ""
    if focus_only:
        extra = sum(item["estimate_minutes"] or assumed for item in focus_only)
        noun = "task" if len(focus_only) == 1 else "tasks"
        text = (
            f"Reviewed ready work totals about {total} min against {capacity} min available{guess}. "
            f"{total - extra} min are due or planned through today. "
            f"Focus alone adds {extra} min from {len(focus_only)} {noun}."
        )
    else:
        text = (f"Ready work due or planned through today totals about {total} min "
                f"against {capacity} min available{guess}.")
    advice.notes.append(Note("overbooked", text, [item["id"] for item in reviewed]))


register(
    Strategy(
        name="overbooked",
        reads=(),
        kind="limit",
        summary="Warn when ready work due or planned through today, plus optional focus items, exceeds capacity.",
        evidence="practitioner",  # EA capacity checks; "add a commitment, subtract one" (CHADD)
        apply=_overbooked,
        params={
            "capacity_minutes": Param(240, "Minutes of task work a day can hold", 15, 1440),
            "include_focus": Param(True, "Count other focused tasks even without a current plan or deadline"),
            "unestimated_minutes": Param(20, "Minutes assumed for a task with no estimate", 1, 480),
        },
    )
)


def _lookahead(view: Snapshot, advice: Advice, params: dict) -> None:
    coming = []
    for item in view.open:
        if not item["deadline"]:
            continue
        days = day_offset(item["deadline"], view.now)
        if not 0 < days <= params["days"]:
            continue
        if item["planned"]:
            # Even the beginning of an all-day plan must be past the cutoff.
            # is_before lets an all-day deadline run through its own local day.
            plan = Temporal.model_validate(item["planned"])
            cutoff = Temporal.model_validate(item["deadline"])
            if not cutoff.is_before(plan.local()):
                continue
            finding = "The planned work falls after that hard deadline."
        else:
            finding = "No day is planned for it."
        coming.append((days, item["id"], finding))
    coming.sort()
    for days, id, finding in coming[: params["limit"]]:
        unit = "day" if days == 1 else "days"
        advice.notes.append(
            Note("lookahead", f"Due in {days} {unit}. {finding}", [id])
        )
    if len(coming) > params["limit"]:
        rest = [id for _, id, _ in coming[params["limit"] :]]
        advice.notes.append(
            Note("lookahead", f"{len(rest)} more due soon with a late or missing plan.", rest[:50])
        )


register(
    Strategy(
        name="lookahead",
        reads=("open",),
        kind="anticipate",
        summary="Find upcoming deadlines when work is unplanned or planned after the cutoff.",
        evidence="practitioner",  # assistants plan backward from fixed dates
        apply=_lookahead,
        params={
            "days": Param(14, "How far ahead to look", 1, 365),
            "limit": Param(10, "Deadlines to mention one by one", 1, 100),
        },
    )
)


def _decisions_first(view: Snapshot, advice: Advice, params: dict) -> None:
    total = sum(item["next_move"] == "answer" for item in view.ready)
    if not total:
        return
    decisions = [item for item in advice.picks if item["next_move"] == "answer"]
    rest = [item for item in advice.picks if item["next_move"] != "answer"]
    limit = params["limit"]
    advice.picks = decisions[:limit] + rest + decisions[limit:]
    noun = "task" if len(view.ready) == 1 else "tasks"
    verb = "needs" if total == 1 else "need"
    advice.notes.append(
        Note(
            "decisions-first",
            f"Of {len(view.ready)} ready {noun} reviewed, {total} {verb} the person's answer.",
            [item["id"] for item in decisions],
            batch_limit=limit,
        )
    )


register(
    Strategy(
        name="decisions-first",
        reads=(),
        kind="rank",
        summary="Lead with what only the person can answer, batched instead of drip-fed.",
        evidence="practitioner",  # assistants batch questions for one sync
        apply=_decisions_first,
        params={"limit": Param(3, "Decisions to put at the top", 1, 20)},
    )
)


def _neutral_aging(view: Snapshot, advice: Advice, params: dict) -> None:
    advice.tone = "neutral"


register(
    Strategy(
        name="neutral-aging",
        reads=(),
        kind="tone",
        summary='Show a late task\'s date without the words "overdue" or "ago".',
        evidence="practitioner",  # qualitative studies of ADHD and planners; weak but consistent
        apply=_neutral_aging,
    )
)


def _frontier_first(view: Snapshot, advice: Advice, params: dict) -> None:
    exploration = view.exploration or {}
    frontier = set(exploration.get("frontier") or [])
    if not frontier:
        return
    moving = [x for x in advice.picks if frontier & set(x["advances"])]
    rest = [x for x in advice.picks if not frontier & set(x["advances"])]
    advice.picks = moving + rest
    titles = {c["id"]: c["title"] for c in exploration["conditions"]}
    for gap in exploration.get("gaps", [])[: params["gaps"]]:
        advice.notes.append(
            Note("frontier-first", f'Nothing is moving "{titles[gap]}", and it is next.', [gap])
        )
        advice.suggestions.append(
            {"title": f"Find a first move for: {titles[gap]}", "advances": [gap]}
        )
    if params["gaps"]:
        pending = []
        for condition in exploration["conditions"]:
            if condition["id"] not in frontier:
                continue
            statuses = {task["status"] for task in condition["advancing"]}
            if view.context is None:
                statuses.update(condition.get("outside_scope", {}).get("advancing", {}))
            if "proposed" in statuses and not statuses.intersection({"open", "waiting"}):
                pending.append(condition["id"])
        if pending:
            count = len(pending)
            subject = "One next condition has" if count == 1 else f"{count} next conditions have"
            advice.notes.append(Note(
                "frontier-first", f"{subject} only proposed work. A decision is pending.",
                pending[: params["gaps"]],
            ))
    unknown = exploration.get("unassessed") or []
    if unknown:
        advice.notes.append(
            Note(
                "frontier-first",
                f"{len(unknown)} conditions have never been assessed.",
                unknown[:20],
            )
        )


register(
    Strategy(
        name="frontier-first",
        reads=("exploration",),
        kind="rank",
        summary="Lead with work that moves a condition which is next for a goal; name the gaps.",
        evidence="practitioner",  # prerequisite trees; work backward from the outcome
        apply=_frontier_first,
        params={"gaps": Param(3, "How many conditions to name per missing-work or proposal-only finding", 0, 20)},
    )
)


def _project_review(view: Snapshot, advice: Advice, params: dict) -> None:
    if view.context is not None:
        advice.notes.append(
            Note("project-review", "Omit the task context filter to review whole projects.")
        )
        return
    missing = []
    for project in view.projects:
        counts = project["rollup"]["stages"]
        active = sum(
            counts[stage["id"]]
            for stage in project["stages"]
            if stage["status"] in {"open", "waiting"}
        )
        if not active:
            missing.append(project)
    if not missing:
        text = (
            f"Each open project has an open or waiting task ({len(view.projects)} reviewed)."
            if view.projects
            else "No open projects to review."
        )
        advice.notes.append(Note("project-review", text))
        return
    for project in missing[: params["limit"]]:
        proposed = project["rollup"]["proposed"]
        text = f"{project['title']}: no open or waiting task. "
        if proposed:
            noun = "proposal awaits" if proposed == 1 else "proposals await"
            text += f"{proposed} {noun} a decision."
        else:
            text += "What is the next action, or is the project finished?"
        advice.notes.append(Note("project-review", text, [project["id"]]))
    remaining = len(missing) - params["limit"]
    if remaining > 0:
        advice.notes.append(
            Note(
                "project-review",
                f"{remaining} more projects meet this check. Increase project-review.limit to see more.",
            )
        )


register(
    Strategy(
        name="project-review",
        reads=("projects",),
        kind="hygiene",
        summary="Review open projects with no open or waiting task. Identify outstanding proposals separately.",
        evidence="practitioner",  # GTD Weekly Review, review project and larger outcome lists
        apply=_project_review,
        params={"limit": Param(10, "How many projects to name in one review", 1, 100)},
    )
)
