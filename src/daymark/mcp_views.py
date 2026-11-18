"""Read one public Daymark view, rendering text or projecting data for MCP clients."""

from __future__ import annotations

from dataclasses import dataclass

from . import render
from .models import DomainError
from .views import claim_holder

# A queue lists enough to choose the next record. Notes, history, descriptions
# and custom metadata belong to a request for the particular task.
_LIST_FIELDS = (
    "id",
    "kind",
    "title",
    "status",
    "version",
    "priority",
    "scope",
    "next_move",
    "planned",
    "deadline",
    "earliest_start",
    "latest_target",
    "waiting_on",
    "focus",
    "estimate_minutes",
    "project_id",
    "parent_id",
    "milestone_id",
    "milestone",
    "type",
    "stage",
    "assignee_id",
)


def _summary(entity, now):
    result = render.compact({k: entity[k] for k in _LIST_FIELDS if k in entity})
    if holder := claim_holder(entity, now):
        result.update(claimed_by=holder, claim_until=entity["claim_until"])
    return result


def _plan_data(view, by, now):
    def card(entity):
        return {**_summary(entity, now), "plan": entity["plan"]}

    data = {
        **view,
        "project": _summary(view["project"], now),
        "tasks": [card(x) for x in view["tasks"]],
        "proposed": [_summary(x, now) for x in view["proposed"]],
    }
    if by:
        data["groups"] = [
            {"label": label.replace("{n}", str(len(cards))), "items": [x["id"] for x in cards]}
            for label, cards in render.plan_groups(view, by)
        ]
    return data


@dataclass(frozen=True)
class StructuredView:
    data: dict
    text: str | None = None


def read_view(tasks, args):
    """Each branch executes the same read for either format."""
    view = args["view"]
    structured = args.get("json", False)
    now = tasks.now()

    if view == "ready":
        filters = {
            "limit": args.get("limit", 20),
            "move": args.get("move"),
            "scope": args.get("scope"),
        }
        if "context" in args:
            filters["context"] = args["context"]
        if args.get("explain"):
            data = tasks.explain_ready(**filters)
            return StructuredView(data) if structured else render.ready_explanation(data)
        if structured:
            rows = tasks.ready_summary(**filters)
            return StructuredView({"items": [render.compact(row) for row in rows]})
        items = tasks.ready(**filters)
        return render.listing(items, now, "Nothing is ready.")

    if view == "find":
        if structured or {"kind", "status", "scope", "after"} & args.keys():
            page = tasks.search(
                args.get("text"),
                kind=args.get("kind", "task"),
                status=args.get("status"),
                scope=args.get("scope"),
                after=args.get("after"),
                limit=args.get("limit", 50),
            )
            if structured:
                return StructuredView(page)
            lines = [f"{item['id']}  {item['title']}  [{item['status']}]" for item in page["items"]]
            if page["next"] is not None:
                lines.append("next: " + page["next"])
            return "\n".join(lines) or "No matches."
        return render.listing(
            tasks.find(args.get("text"), limit=args.get("limit", 50)), now, "No matches."
        )

    if view == "show":
        if ("id" in args) == ("source" in args):
            raise DomainError("invalid", "show takes exactly one of id or source")
        id, source = args.get("id"), args.get("source")
        if "note" in args and (args.get("brief") or "before_note" in args):
            raise DomainError("invalid", "Choose a single note or a brief")
        if args.get("brief") or "before_note" in args:
            data = tasks.brief(id, before_note=args.get("before_note"), source=source)
            return StructuredView(data) if structured else render.brief_text(data)
        if "note" in args:
            data = tasks.read_note(id, args["note"], source=source)
            return StructuredView(data) if structured else render.note_text(data)
        data = tasks.show(id, source=source)
        if structured:
            entity = render.compact(data["entity"])
            if data["entity"]["notes"]:
                # Full detail preserves each note ID, author and timestamp.
                entity["notes"] = data["entity"]["notes"]
            return StructuredView({**data, "entity": entity})
        return render.show_text(data, now)

    if view == "profiles":
        from .strategies import validate_profile
        from .strategies.profiles import PROFILES

        name = args.get("profile")
        if name in PROFILES:
            data = validate_profile(PROFILES[name])
            return StructuredView(data) if structured else render.profile_text(data)
        if name is not None:
            data = tasks.saved_profiles(name)
            return StructuredView(data) if structured else render.saved_profiles_text(data)
        catalog = tasks.profile_catalog()
        if structured:
            return StructuredView(catalog)
        saved = catalog["saved"]
        available = (
            "Saved profiles\n" + render.saved_profiles_text(saved)
            if saved is not None
            else "Saved profiles are available on the local database."
        )
        return (
            "Active "
            + render.profile_text(catalog["active"])
            + "\nBuilt-in profiles: "
            + ", ".join(catalog["builtins"])
            + "\n"
            + available
        )

    if view == "advise":
        advice = tasks.advise(
            profile=args.get("profile"),
            settings=args.get("set", []),
            drops=args.get("drop", []),
            scope=args.get("scope"),
        )
        if structured:
            return StructuredView(
                {**advice.to_dict(), "picks": [_summary(x, now) for x in advice.picks]}
            )
        return render.advice_text(advice, now)

    if view == "agenda":
        data = tasks.agenda(args.get("days", 7), scope=args.get("scope"))
        if structured:
            sections = {
                key: [_summary(x, now) for x in value] if isinstance(value, list) else value
                for key, value in data.items()
            }
            sections["upcoming"] = {
                day: [_summary(x, now) for x in items] for day, items in data["upcoming"].items()
            }
            return StructuredView(sections)
        return render.agenda_text(data, now)

    if view == "plan":
        data = tasks.plan(args["id"])
        if structured:
            return StructuredView(_plan_data(data, args.get("by"), now))
        return render.plan_text(data, now, by=args.get("by") or "stage")

    if view == "projects":
        data = tasks.projects()
        if structured:
            return StructuredView(
                {
                    **data,
                    "projects": [
                        {key: value for key, value in project.items() if key != "description"}
                        for project in data["projects"]
                    ],
                }
            )
        return render.projects_text(data)

    if view == "goal":
        data = tasks.goal_plan(args["id"])
        if structured:
            return StructuredView({**data, "goal": _summary(data["goal"], now)})
        return render.goal_text(data)

    if view == "goals":
        data = tasks.goals()
        return StructuredView(data) if structured else render.goals_text(data)

    if view == "activity":
        data = tasks.activity(after=args.get("after"), limit=args.get("limit", 20))
        return StructuredView(data) if structured else render.activity_text(data)

    if view == "explore":
        data = tasks.explore(args.get("id"))
        return StructuredView(data) if structured else render.explore_text(data)

    raise DomainError("invalid", "Unknown view")
