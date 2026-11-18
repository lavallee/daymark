"""Built-in strategies. Each names the practice it comes from and how well that
practice is supported; docs/research/working-methods.md has the sources."""

from __future__ import annotations

from datetime import datetime

from ..models import Temporal
from . import Advice, Note, Param, Snapshot, Strategy, register


def _age_days(stamp: str, now: datetime) -> int:
    return (now - datetime.fromisoformat(stamp)).days


def _days_until(temporal, now: datetime) -> int:
    value = Temporal.model_validate(temporal)
    return (value.local().date() - now.astimezone(value.local().tzinfo).date()).days


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
    for item in advice.picks:
        estimate = item["estimate_minutes"]
        if estimate is None:
            unknown += 1
        elif estimate > minutes:
            longer += 1
        else:
            fits.append(item)
    advice.picks = fits
    advice.held_back += longer + unknown
    if fits or longer or unknown:
        message = f"{len(fits)} individually estimated at {minutes} min or less."
        if longer:
            message += f" {longer} estimated longer."
        if unknown:
            message += f" {unknown} without an estimate."
        advice.notes.append(Note("time-fit", message))


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
    old = [x for x in view.waiting if _age_days(x["updated_at"], view.now) >= params["days"]]
    for item in old[:50]:
        whom = item["waiting_on"] or "someone"
        days = _age_days(item["updated_at"], view.now)
        advice.notes.append(
            Note("waiting-check", f"Waiting on {whom} for {days} days; ask again?", [item["id"]])
        )


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
    today = []
    for item in view.ready:
        days = [_days_until(item[key], view.now) for key in ("deadline", "planned") if item[key]]
        if item["focus"] or (days and min(days) <= 0):
            today.append(item)
    if not today:
        return
    unestimated = sum(1 for x in today if not x["estimate_minutes"])
    total = sum(x["estimate_minutes"] or params["unestimated_minutes"] for x in today)
    if total > params["capacity_minutes"]:
        guess = (
            f" ({unestimated} counted at {params['unestimated_minutes']} min)"
            if unestimated
            else ""
        )
        advice.notes.append(
            Note(
                "overbooked",
                f"Today holds about {total} min of work against {params['capacity_minutes']} "
                f"available{guess}. Something should move.",
                [x["id"] for x in today],
            )
        )


register(
    Strategy(
        name="overbooked",
        reads=(),
        kind="limit",
        summary="Warn when what is due, planned or in focus today exceeds the time available.",
        evidence="practitioner",  # EA capacity checks; "add a commitment, subtract one" (CHADD)
        apply=_overbooked,
        params={
            "capacity_minutes": Param(240, "Minutes of task work a day can hold", 15, 1440),
            "unestimated_minutes": Param(20, "Minutes assumed for a task with no estimate", 1, 480),
        },
    )
)


def _lookahead(view: Snapshot, advice: Advice, params: dict) -> None:
    coming = []
    for item in view.open:
        if not item["deadline"] or item["planned"]:
            continue
        days = _days_until(item["deadline"], view.now)
        if 0 < days <= params["days"]:
            coming.append((days, item["id"]))
    coming.sort()
    for days, id in coming[: params["limit"]]:
        advice.notes.append(
            Note("lookahead", f"Due in {days} days with no day planned for it.", [id])
        )
    if len(coming) > params["limit"]:
        rest = [id for _, id in coming[params["limit"] :]]
        advice.notes.append(
            Note("lookahead", f"{len(rest)} more due soon with no day planned.", rest[:50])
        )


register(
    Strategy(
        name="lookahead",
        reads=("open",),
        kind="anticipate",
        summary="Find deadlines coming up that have no day set aside to work on them.",
        evidence="practitioner",  # assistants plan backward from fixed dates
        apply=_lookahead,
        params={
            "days": Param(14, "How far ahead to look", 1, 365),
            "limit": Param(10, "Deadlines to mention one by one", 1, 100),
        },
    )
)


def _decisions_first(view: Snapshot, advice: Advice, params: dict) -> None:
    decisions = [x for x in advice.picks if x["next_move"] == "answer"]
    if not decisions:
        return
    rest = [x for x in advice.picks if x["next_move"] != "answer"]
    advice.picks = decisions[: params["limit"]] + rest + decisions[params["limit"] :]
    advice.notes.append(
        Note(
            "decisions-first",
            f"{len(decisions)} waiting on a decision from the person; ask them together.",
            [x["id"] for x in decisions],
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
        params={"gaps": Param(3, "How many unmoved conditions to mention", 0, 20)},
    )
)
