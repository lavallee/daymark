"""Read one public Daymark view, rendering text or projecting data for MCP clients."""

from __future__ import annotations

from dataclasses import dataclass

from . import render
from .models import DomainError


def _summary(entity, now):
    return render.compact(render.list_record(entity, now))


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


def _proposal_detail(data, status, read_only):
    if status != "proposed":
        return data
    reported = data.get("decision_access")
    access = {"policy_allows": None}
    if isinstance(reported, dict) and type(reported.get("policy_allows")) is bool and isinstance(reported.get("actor"), str):
        access = {key: reported[key] for key in ("actor", "policy_allows")}
        if reported.get("rule") in ("any_writer", "named_deciders"):
            access["rule"] = reported["rule"]
    if read_only:
        access = {**access, "session_read_only": True}
    return {**data, "decision_access": access}


def read_view(tasks, args, *, read_only=False):
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
        if "project" in args:
            filters["project"] = args["project"]
        if args.get("explain"):
            data = tasks.explain_ready(**filters)
            if structured:
                return StructuredView({**data, "ready": [render.compact(row) for row in data["ready"]]})
            return render.ready_explanation(data)
        if structured:
            rows = tasks.ready_summary(**filters)
            return StructuredView({"items": [render.compact(row) for row in rows]})
        items = tasks.ready(**filters)
        return render.listing(items, now, "Nothing is ready.")

    if view == "find":
        if structured or {"kind", "status", "scope", "after", "claimed", "archived", "source"} & args.keys():
            page = tasks.search(
                args.get("text"),
                kind=args.get("kind", "task"),
                status=args.get("status"),
                scope=args.get("scope"),
                after=args.get("after"),
                limit=args.get("limit", 50),
                claimed=args.get("claimed", False),
                archived=args.get("archived", False),
                source=args.get("source"),
            )
            if structured:
                return StructuredView(page)
            return render.search_text(page, mcp=True)
        return render.listing(
            tasks.find(args.get("text"), limit=args.get("limit", 50), brief=True), now, "No matches."
        )

    if view == "show":
        if ("id" in args) == ("source" in args):
            raise DomainError("invalid", "show takes exactly one of id or source")
        id, source = args.get("id"), args.get("source")
        if "since_version" in args:
            if args.get("brief") or "note" in args or "before_note" in args:
                raise DomainError("invalid", "Choose a version comparison or a note/brief")
            data = tasks.changes_since(id, version=args["since_version"], source=source)
            return StructuredView(data) if structured else render.comparison_text(data)
        if "note" in args and (args.get("brief") or "before_note" in args):
            raise DomainError("invalid", "Choose a single note or a brief")
        if args.get("brief") or "before_note" in args:
            data = tasks.brief(id, before_note=args.get("before_note"), source=source)
            data = _proposal_detail(data, data["status"], read_only)
            return StructuredView(data) if structured else render.brief_text(data, timezone=tasks.timezone, mcp=True, source=source)
        if "note" in args:
            data = tasks.read_note(id, args["note"], source=source)
            return StructuredView(data) if structured else render.note_text(data)
        data = tasks.show(id, source=source)
        data = _proposal_detail(data, data["entity"]["status"], read_only)
        if structured:
            return StructuredView(render.detail_data(data, attributed_notes=True))
        return render.show_text(data, now)

    if view == "profiles":
        from .strategies import validate_profile
        from .strategies.profiles import PROFILES

        name = args.get("profile")
        if "strategy" in args:
            if name is not None:
                raise DomainError("invalid", "Choose either a profile or a strategy to inspect")
            data = tasks.profile_catalog(strategy=args["strategy"])
            return StructuredView(data) if structured else render.strategy_detail(data)
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
            + ("\nInstalled plugins: " + ", ".join(catalog["installed_plugins"]) if catalog.get("installed_plugins") else "")
            + ("\nClient plugins (preview on an explicit built-in base): " + ", ".join(catalog["client_plugins"]) if catalog.get("client_plugins") else "")
            + "\nAdjustments (defaults shown; use strategy for details): "
            + ", ".join(
                f"{name}.{key}={render.parameter_text(value)}" if key else name
                for name, params in catalog["adjustments"].items()
                for key, value in (params.items() or [(None, None)])
            )
        )

    if view == "advise":
        advice = tasks.advise(
            profile=args.get("profile"),
            settings=args.get("set", []),
            drops=args.get("drop", []),
            brief=True,
            context=args.get("context"),
            scope=args.get("scope"),
        )
        if structured:
            return StructuredView(
                {**advice.to_dict(), "picks": [_summary(x, advice.as_of or now) for x in advice.picks]}
            )
        return render.advice_text(advice, now, mcp=True)

    if view == "agenda":
        data = tasks.agenda(
            args.get("days", 7), scope=args.get("scope"), context=args.get("context"),
            deadlines=args.get("deadlines", False),
        )
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
        data = tasks.projects(scope=args.get("scope"))
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
        return StructuredView(data) if structured else render.activity_text(data, mcp=True, limit=args.get("limit", 20))

    if view == "explore":
        data = tasks.explore(args.get("id"), scope=args.get("scope"))
        return StructuredView(data) if structured else render.explore_text(data)

    raise DomainError("invalid", "Unknown view")
