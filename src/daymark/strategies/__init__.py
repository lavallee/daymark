"""The strategy layer: ways of working, kept apart from the data they work on.

The kernel (``daymark.store``) stores what is true and enforces what must hold.
A *strategy* is an opinion about what to do with that: which few things to
offer, what to warn about, what to suggest. Strategies are small, named,
parameterised and switchable per workspace, and they are bundled into
*profiles* so a person can adopt a way of working in one step.

The contract that keeps the two layers apart:

- A strategy reads a ``Snapshot`` built from the kernel's public read API. It
  never opens the database and never imports the engine.
- A strategy returns ``Advice``: an ordering, notes, and suggestions. It
  writes nothing. A suggestion becomes a task only through ``propose``, which
  a person accepts or dismisses.
- A strategy is deterministic for a given snapshot and parameters. One that
  needs more than stored tasks (a calendar, a timer, a language model, a
  question to the person) says so in ``needs``; the host decides whether to
  provide it, and the kernel never does.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from ..models import DomainError

KINDS = (
    "rank",  # choose and order what to offer
    "limit",  # cap choices or load
    "hygiene",  # notice what is going stale or is missing something
    "follow-through",  # other people and waiting
    "anticipate",  # look ahead from what is coming
    "tone",  # how facts are worded
)
EVIDENCE = ("clinical", "practitioner", "folk", "convention")
SETTING = "profile"
READY_LIMIT = 1000  # the most the kernel's ready view returns in one read
SNAPSHOT_READS = ("open", "waiting", "proposed", "exploration")


@dataclass(frozen=True)
class Param:
    default: Any
    help: str
    minimum: int | None = None
    maximum: int | None = None


@dataclass(frozen=True)
class Snapshot:
    """Everything a strategy may read. Built once per run by ``advise``."""

    now: datetime
    timezone: str
    ready: tuple[dict, ...]  # the kernel's eligibility rule, in the kernel's order
    open: tuple[dict, ...]  # every open task, ready or not
    waiting: tuple[dict, ...]
    proposed: tuple[dict, ...]
    # Conditions behind the workspace's goals: see ``Tasks.explore``.
    exploration: dict = field(default_factory=dict)


@dataclass
class Note:
    strategy: str
    text: str
    ids: list[str] = field(default_factory=list)


@dataclass
class Advice:
    picks: list[dict]
    notes: list[Note] = field(default_factory=list)
    # Suggested tasks. The host may offer them through ``propose``; never applied here.
    suggestions: list[dict] = field(default_factory=list)
    held_back: int = 0  # ready tasks a limit kept out of sight
    tone: str = "plain"  # "plain" or "neutral"; read by renderers
    steps: dict[str, str] = field(default_factory=dict)  # task id -> its next small step
    profile: str = "plain"

    def to_dict(self) -> dict:
        return {
            "profile": self.profile,
            "picks": self.picks,
            "notes": [{"strategy": n.strategy, "text": n.text, "ids": n.ids} for n in self.notes],
            "suggestions": self.suggestions,
            "held_back": self.held_back,
            "tone": self.tone,
            "steps": self.steps,
        }


@dataclass(frozen=True)
class Strategy:
    name: str
    kind: str
    summary: str
    evidence: str  # how well supported the idea is; see docs/research
    apply: Callable[[Snapshot, Advice, dict], None]
    params: dict[str, Param] = field(default_factory=dict)
    needs: frozenset[str] = frozenset()  # beyond stored tasks: calendar, clock, llm, person
    reads: tuple[str, ...] = SNAPSHOT_READS  # ready and clock are always included

    def __post_init__(self):
        if self.kind not in KINDS or self.evidence not in EVIDENCE:
            raise ValueError("unknown strategy kind or evidence level")
        if set(self.reads) - set(SNAPSHOT_READS):
            raise ValueError("unknown strategy snapshot field")


_REGISTRY: dict[str, Strategy] = {}


def register(strategy: Strategy) -> Strategy:
    _REGISTRY[strategy.name] = strategy
    return strategy


def get(name: str) -> Strategy:
    """A built-in strategy, or one an installed package offers under this exact name.

    Plugins are found through the ``daymark.strategies`` entry-point group and
    are imported only when a profile names them: installing a package never
    runs its code by itself.
    """
    from . import builtin  # noqa: F401 - registers the built-ins

    if name in _REGISTRY:
        return _REGISTRY[name]
    from importlib.metadata import entry_points

    for entry in entry_points(group="daymark.strategies"):
        if entry.name == name:
            loaded = entry.load()
            if not isinstance(loaded, Strategy) or loaded.name != name:
                raise DomainError("invalid", "Plugin does not provide a strategy of that name")
            return register(loaded)
    raise DomainError("not_found", "Unknown strategy", [f"See `daymark strategies` for {name!r}."])


def available() -> list[Strategy]:
    from . import builtin  # noqa: F401

    return sorted(_REGISTRY.values(), key=lambda s: (KINDS.index(s.kind), s.name))


def resolve(strategy: Strategy, given: dict | None) -> dict:
    """Parameters with defaults filled in; unknown names and bad values are refused."""
    if given is None:
        given = {}
    if not isinstance(given, dict) or any(not isinstance(key, str) for key in given):
        raise DomainError("invalid", "Strategy parameters must be a JSON object with named fields")
    unknown = sorted(set(given) - set(strategy.params))
    if unknown:
        raise DomainError("invalid", "Unknown strategy parameter", unknown)
    out = {}
    for name, param in strategy.params.items():
        value = given.get(name, param.default)
        if type(value) is not type(param.default):
            raise DomainError("invalid", "Wrong type for strategy parameter", [name])
        if isinstance(value, int) and not isinstance(value, bool):
            low, high = param.minimum, param.maximum
            if (low is not None and value < low) or (high is not None and value > high):
                raise DomainError("invalid", "Strategy parameter out of range", [name])
        out[name] = value
    return out


def validate_profile(profile: Any) -> dict:
    """A profile is ``{"name": str, "strategies": [{"use": name, "with": {...}}, ...]}``."""
    if (
        not isinstance(profile, dict)
        or set(profile) - {"name", "strategies", "skipped"}
        or not isinstance(profile.get("name", ""), str)
        or not isinstance(profile.get("strategies"), list)
        or len(profile["strategies"]) > 30
    ):
        raise DomainError("invalid", "A profile needs a name and a list of strategies")
    stages = []
    for stage in profile["strategies"]:
        if (
            not isinstance(stage, dict)
            or set(stage) - {"use", "with"}
            or not isinstance(stage.get("use"), str)
            or not stage["use"]
        ):
            raise DomainError("invalid", "Each profile entry needs `use` and optional `with`")
        strategy = get(stage["use"])
        stages.append({"use": strategy.name, "with": resolve(strategy, stage.get("with"))})
    return {"name": profile.get("name") or "custom", "strategies": stages}


def edit_profile(profile: dict, settings: list[str], drops: list[str]) -> dict:
    """Apply ``strategy.param=value`` settings and removals to a copy of a profile."""
    import json

    if any(
        not isinstance(values, (tuple, list)) or any(not isinstance(x, str) for x in values)
        for values in (settings, drops)
    ):
        raise DomainError("invalid", "Strategy settings and removals must be lists of strings")
    stages = [{"use": s["use"], "with": dict(s.get("with") or {})} for s in profile["strategies"]]
    skipped = list(profile.get("skipped") or [])
    for name in drops:
        if name in skipped:
            skipped.remove(name)
        elif not any(stage["use"] == name for stage in stages):
            raise DomainError("not_found", "That strategy is not in the profile", [name])
        stages = [stage for stage in stages if stage["use"] != name]
    for setting in settings:
        target, equals, raw = setting.partition("=")
        name, dot, param = target.partition(".")
        if not name or bool(dot) != bool(equals) or (dot and not (param and raw)):
            raise DomainError("invalid", "Use STRATEGY or STRATEGY.PARAM=VALUE")
        stage = next((x for x in stages if x["use"] == name), None)
        if stage is None:
            stage = {"use": get(name).name, "with": {}}
            stages.append(stage)
        if param:
            try:
                stage["with"][param] = json.loads(raw)
            except ValueError:
                stage["with"][param] = raw
    edited = bool(settings or drops) and profile.get("name") not in (None, "custom")
    name = (
        f"{profile['name']} (edited)"
        if edited and "(edited)" not in profile["name"]
        else profile.get("name")
    )
    edited_profile = {"name": name, "strategies": stages}
    return {**edited_profile, "skipped": skipped} if skipped else edited_profile


def select_profile(tasks, name=None, *, settings=(), drops=()) -> dict:
    """Choose a base and apply the requested adjustments without saving it."""
    from .preferences import named_profile

    base = stored_profile(tasks) if name is None else named_profile(tasks, name)
    return edit_profile(base, settings, drops) if settings or drops else base


def snapshot(tasks, *, scope: str | None = None, reads=SNAPSHOT_READS) -> Snapshot:
    """Read ready picks and the additional views used by this profile."""
    ready = tasks.ready(limit=READY_LIMIT, scope=scope)
    opened = []
    if "open" in reads:
        opened = tasks.find(status="open", limit=None)
        if scope is not None:
            opened = [x for x in opened if (x["scope"] or "") == scope]
    agenda = tasks.agenda(days=0, scope=scope) if "waiting" in reads or "proposed" in reads else {}
    return Snapshot(
        now=tasks.now(),
        timezone=tasks.timezone,
        ready=tuple(ready),
        open=tuple(opened),
        waiting=tuple(
            x
            for bucket in ("waiting", "overdue")
            for x in agenda.get(bucket, ())
            if x["status"] == "waiting"
        ),
        proposed=tuple(agenda.get("proposed", [])),
        exploration=tasks.explore(scope=scope) if "exploration" in reads else {},
    )


def advise(tasks, profile: dict | None = None, *, scope: str | None = None) -> Advice:
    """Run a profile over the current state. Reads only; returns advice."""
    from .profiles import PROFILES

    skipped = list((profile or {}).get("skipped") or [])
    profile = validate_profile(profile or PROFILES["plain"])
    stages = [(get(stage["use"]), stage["with"]) for stage in profile["strategies"]]
    reads = {name for strategy, _ in stages for name in strategy.reads}
    view = snapshot(tasks, scope=scope, reads=reads)
    advice = Advice(picks=list(view.ready), profile=profile["name"])
    if skipped:
        advice.notes.append(
            Note("profile", "The profile names strategies that could not be used: "
                 + ", ".join(skipped) + ". Fix with `daymark profile`.", [])
        )  # fmt: skip
    if len(view.ready) == READY_LIMIT:
        total = tasks.summary(scope=scope)["ready"]
        if total > READY_LIMIT:
            advice.notes.append(
                Note("profile", f"Advice covers the first {READY_LIMIT} of {total} ready tasks.")
            )
    for strategy, params in stages:
        strategy.apply(view, advice, params)
    return advice


def stored_profile(tasks) -> dict:
    """The workspace's chosen profile, or the plain one. Local stores only.

    A saved profile can outlive what it names: a plugin is uninstalled, a
    parameter is renamed. Such entries are left out and listed under
    ``skipped`` so the workspace keeps working and the person can see why.
    """
    reader = getattr(tasks.engine, "setting", None)
    saved = reader(*tasks._ws, SETTING) if reader else None
    return restore_profile(saved)


def restore_profile(saved) -> dict:
    """Resolve a persisted profile against the strategies currently installed."""
    from .profiles import PROFILES

    if not isinstance(saved, dict) or not isinstance(saved.get("strategies"), list):
        return PROFILES["plain"]
    stages, skipped = [], []
    for stage in saved["strategies"]:
        try:
            strategy = get(stage["use"])
            known = {k: v for k, v in (stage.get("with") or {}).items() if k in strategy.params}
            stages.append({"use": strategy.name, "with": resolve(strategy, known)})
        except (DomainError, KeyError, TypeError, AttributeError):
            skipped.append(str(stage.get("use") if isinstance(stage, dict) else stage))
    profile = {"name": str(saved.get("name") or "custom"), "strategies": stages}
    return {**profile, "skipped": skipped} if skipped else profile


def store_profile(tasks, profile: dict) -> dict:
    writer = getattr(tasks.engine, "set_setting", None)
    if writer is None:
        raise DomainError("invalid", "Profiles are set on a local database")
    profile = validate_profile({k: v for k, v in profile.items() if k != "skipped"})
    writer(*tasks._ws, SETTING, profile)
    return profile
