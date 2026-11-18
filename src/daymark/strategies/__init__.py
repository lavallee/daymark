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
from contextlib import contextmanager, nullcontext
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from ..models import DomainError, normalize_now

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
    context: str | None = None
    projects: tuple[dict, ...] = ()


@dataclass
class Note:
    strategy: str
    text: str
    ids: list[str] = field(default_factory=list)
    # A discussion batch can name only picks that survive every stage.
    batch_limit: int | None = None


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
    scope: str | None = None
    profile_source: dict | None = None
    context: str | None = None
    as_of: datetime | None = None

    @classmethod
    def from_result(cls, data):
        """Reconstruct service-computed advice as data; no strategy lookup or execution."""
        return cls(
            picks=data["picks"],
            notes=[Note(**entry) for entry in data["notes"]],
            suggestions=data["suggestions"],
            held_back=data["held_back"],
            tone=data["tone"],
            steps=data["steps"],
            profile=data["profile"],
            profile_source=data["profile_source"],
            scope=data.get("scope"),
            context=data.get("context"),
            as_of=normalize_now(data["as_of"]) if data.get("as_of") else None,
        )

    def ready_query(self) -> dict:
        """A bounded plain read that reaches past the choices already displayed."""
        query = {}
        if self.scope is not None:
            query["scope"] = self.scope
        if self.context is not None:
            query["context"] = self.context
        limit = min(READY_LIMIT, len(self.picks) + min(self.held_back, 5))
        if limit > 20:  # The normal first page may contain only picks already shown.
            query["limit"] = limit
        return query

    def to_dict(self) -> dict:
        return {
            "profile": self.profile,
            **({"as_of": self.as_of.isoformat()} if self.as_of is not None else {}),
            **({"profile_source": self.profile_source} if self.profile_source else {}),
            **({"scope": self.scope} if self.scope is not None else {}),
            **({"context": self.context} if self.context is not None else {}),
            "picks": self.picks,
            "notes": [{"strategy": n.strategy, "text": n.text, "ids": n.ids} for n in self.notes],
            "suggestions": self.suggestions,
            "held_back": self.held_back,
            **({"ready_query": self.ready_query()} if self.held_back else {}),
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
    evidence_note: str = ""  # scope of the evidence, for detailed inspection

    def __post_init__(self):
        if self.kind not in KINDS or self.evidence not in EVIDENCE:
            raise ValueError("unknown strategy kind or evidence level")
        if set(self.reads) - {*SNAPSHOT_READS, "projects"}:
            raise ValueError("unknown strategy snapshot field")


_REGISTRY: dict[str, Strategy] = {}
_SERVICE_BUILTINS = ContextVar("daymark_service_builtin_strategies", default=False)


@contextmanager
def service_builtins():
    """HTTP reads may evaluate shipped methods only, including during profile resolution."""
    token = _SERVICE_BUILTINS.set(True)
    try:
        yield
    finally:
        _SERVICE_BUILTINS.reset(token)


def has_external_setting(settings):
    """Only names explicitly present in caller-supplied settings can opt in to client code."""
    from .builtin import BUILTINS

    names = {setting.partition("=")[0].partition(".")[0] for setting in settings}
    external = names - BUILTINS.keys()
    return bool(external and (external & _REGISTRY.keys() or external.intersection(installed_plugins())))



def register(strategy: Strategy) -> Strategy:
    _REGISTRY[strategy.name] = strategy
    return strategy


def get(name: str) -> Strategy:
    """A built-in strategy, or one an installed package offers under this exact name.

    Plugins are found through the ``daymark.strategies`` entry-point group and
    are imported only when a profile names them: installing a package never
    runs its code by itself.
    """
    from . import builtin

    if _SERVICE_BUILTINS.get():
        if name not in builtin.BUILTINS:
            raise DomainError("not_found", "This service evaluates only built-in strategies", [name])
        return builtin.BUILTINS[name]
    if name in _REGISTRY:
        return _REGISTRY[name]
    entries = _plugin_entries(name)
    if len(entries) > 1:
        raise DomainError("conflict", "Multiple installed packages provide this strategy", [name])
    if entries:
        try:
            loaded = entries[0].load()
        except Exception as error:
            # Importing a named extension is a package boundary. Installed
            # versions may have missing dependencies or invalid entry points.
            raise DomainError(
                "invalid",
                "Could not load the installed strategy",
                [name, "Check the plugin and its dependencies in Daymark's Python environment."],
            ) from error
        if not isinstance(loaded, Strategy) or loaded.name != name:
            raise DomainError("invalid", "Plugin does not provide a strategy of that name")
        return register(loaded)
    raise DomainError("not_found", "Unknown strategy", [f"See `daymark strategies` for {name!r}."])


def _plugin_entries(name=None):
    from importlib.metadata import entry_points

    options = {"group": "daymark.strategies"}
    if name is not None:
        options["name"] = name
    return list(entry_points(**options))


def installed_plugins() -> list[str]:
    """Entry-point names only. Listing installed packages never calls their code."""
    return [] if _SERVICE_BUILTINS.get() else sorted({entry.name for entry in _plugin_entries()})


def available() -> list[Strategy]:
    from . import builtin

    registry = builtin.BUILTINS if _SERVICE_BUILTINS.get() else _REGISTRY
    return sorted(registry.values(), key=lambda s: (KINDS.index(s.kind), s.name))


def describe(name: str) -> dict:
    """Inspect one registered strategy without importing unselected plugins."""
    found = next((strategy for strategy in available() if strategy.name == name), None)
    if found is None:
        if not _SERVICE_BUILTINS.get() and isinstance(name, str) and _plugin_entries(name):
            return {
                "name": name,
                "installed_plugin": True,
                "summary": "Installed plugin. To load its code, explicitly preview or select it.",
                "preview": {"set": [name]},
            }
        raise DomainError("not_found", "No registered or installed strategy with that name")
    return {
        "name": found.name,
        "kind": found.kind,
        "summary": found.summary,
        "evidence": found.evidence,
        **({"evidence_note": found.evidence_note} if found.evidence_note else {}),
        "parameters": {
            key: {
                "default": param.default,
                "help": param.help,
                **({"minimum": param.minimum} if param.minimum is not None else {}),
                **({"maximum": param.maximum} if param.maximum is not None else {}),
            }
            for key, param in found.params.items()
        },
    }


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


def _resolve_stage(stage, *, allow_unavailable=False, stored=False):
    """Return the portable configuration and whether this host can run it."""
    if (
        not isinstance(stage, dict)
        or set(stage) - {"use", "with", "unavailable_parameters"}
        or not isinstance(stage.get("use"), str)
        or not stage["use"]
    ):
        raise DomainError("invalid", "Each profile entry needs `use` and optional `with`")
    configured = stage.get("with")
    if configured is None:
        configured = {}
    retired = stage.get("unavailable_parameters", {})
    for values in (configured, retired):
        if not isinstance(values, dict) or any(not isinstance(key, str) for key in values):
            raise DomainError(
                "invalid", "Strategy parameters must be a JSON object with named fields"
            )
    if configured.keys() & retired.keys():
        raise DomainError("invalid", "A parameter can appear only once per strategy")
    name = stage["use"]
    try:
        strategy = get(name)
    except DomainError:
        if not allow_unavailable:
            raise
        # All values are carried exactly as data until a capable host resolves them.
        return {"use": name, "with": {**configured, **retired}}, False
    if stored:
        configured = {**configured, **retired}
        retired = {k: v for k, v in configured.items() if k not in strategy.params}
        configured = {k: v for k, v in configured.items() if k in strategy.params}
    if retired.keys() & strategy.params.keys():
        raise DomainError(
            "invalid", "A parameter available here belongs in the strategy's with object"
        )
    return {
        "use": strategy.name,
        "with": resolve(strategy, configured),
        **({"unavailable_parameters": dict(retired)} if retired else {}),
    }, True


def validate_profile(profile: Any, *, allow_unavailable=False) -> dict:
    """A profile is ``{"name": str, "strategies": [{"use": name, "with": {...}}, ...]}``."""
    if (
        not isinstance(profile, dict)
        or set(profile) - {"name", "strategies", "skipped"}
        or not isinstance(profile.get("name", ""), str)
        or not isinstance(profile.get("strategies"), list)
        or len(profile["strategies"]) > 30
    ):
        raise DomainError("invalid", "A profile needs a name and a list of strategies")
    skipped = profile.get("skipped", [])
    if not isinstance(skipped, list) or any(
        not isinstance(name, str) or not name for name in skipped
    ):
        raise DomainError("invalid", "Unavailable strategies must be listed by name")
    stages = [
        _resolve_stage(stage, allow_unavailable=allow_unavailable)[0]
        for stage in profile["strategies"]
    ]
    return {"name": profile.get("name") or "custom", "strategies": stages}


def persistent_profile(profile: dict) -> dict:
    """Keep all portable values and the order in which capable hosts run the stages."""
    if profile.get("skipped"):
        raise DomainError(
            "invalid", "Drop unavailable strategies explicitly before saving", profile["skipped"]
        )
    resolved = validate_profile(profile, allow_unavailable=True)
    for stage in resolved["strategies"]:
        stage["with"].update(stage.pop("unavailable_parameters", {}))
    return resolved


def has_adjustments(settings, drops) -> bool:
    """Validate one-call or persistent edits before deciding whether this is a read."""
    if any(
        not isinstance(values, (tuple, list)) or any(not isinstance(x, str) for x in values)
        for values in (settings, drops)
    ):
        raise DomainError("invalid", "Strategy settings and removals must be lists of strings")
    if sum(len(text) for values in (settings, drops) for text in values) > 65_536:
        raise DomainError("invalid", "Profile adjustments exceed 65,536 characters")
    return bool(settings or drops)


def edit_profile(profile: dict, settings: list[str], drops: list[str]) -> dict:
    """Apply ``strategy.param=value`` settings and removals to a copy of a profile."""
    import json

    has_adjustments(settings, drops)
    stages = [
        {
            **s,
            "with": dict(s.get("with") or {}),
            **(
                {"unavailable_parameters": dict(s["unavailable_parameters"])}
                if s.get("unavailable_parameters")
                else {}
            ),
        }
        for s in profile["strategies"]
    ]
    skipped = list(profile.get("skipped") or [])
    for name in drops:
        if name in skipped:
            skipped = [item for item in skipped if item != name]
        elif not any(stage["use"] == name for stage in stages):
            matches = [
                (s, k)
                for s in stages
                for k in s.get("unavailable_parameters", {})
                if name == f"{s['use']}.{k}"
            ]
            if not matches:
                raise DomainError(
                    "not_found",
                    "That strategy or unavailable parameter is not in the profile",
                    [name],
                )
            for stage, key in matches:
                del stage["unavailable_parameters"][key]
                if not stage["unavailable_parameters"]:
                    del stage["unavailable_parameters"]
        stages = [stage for stage in stages if stage["use"] != name]
    for setting in settings:
        target, equals, raw = setting.partition("=")
        name, dot, param = target.partition(".")
        if not name or bool(dot) != bool(equals) or (dot and not (param and raw)):
            raise DomainError("invalid", "Use STRATEGY or STRATEGY.PARAM=VALUE")
        strategy = get(name)  # Intentional edits require this host to know the schema.
        stage = next((x for x in stages if x["use"] == name), None)
        if stage is None:
            stage = {"use": strategy.name, "with": {}}
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


def _review_projects(tasks, scope):
    return [p for p in tasks.projects(scope=scope)["projects"] if p["status"] == "open"]


def snapshot(
    tasks, *, scope: str | None = None, context: str | None = None, reads=SNAPSHOT_READS
) -> Snapshot:
    """Read ready picks and the additional views used by this profile."""
    filters = {"scope": scope, **({"context": context} if context is not None else {})}
    ready = tasks.ready(limit=READY_LIMIT, **filters)
    opened = tasks.find(status="open", limit=None, **filters) if "open" in reads else []
    agenda = tasks.agenda(days=0, **filters) if "waiting" in reads or "proposed" in reads else {}
    exploration = tasks.explore(scope=scope) if "exploration" in reads else {}
    if exploration and context is not None:
        # A task can move a condition from this context. That link says nothing
        # about where someone could address a gap with no task attached.
        linked = {condition for item in ready for condition in item["advances"]}
        selected = {item["id"] for item in ready}
        exploration = {
            "goal": exploration["goal"],
            "frontier": [id for id in exploration["frontier"] if id in linked],
            "conditions": [
                {**c, "advancing": [item for item in c["advancing"] if item["id"] in selected]}
                for c in exploration["conditions"]
                if c["id"] in linked
            ],
            "gaps": [],
            "unassessed": [],
        }
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
        exploration=exploration,
        context=context,
        projects=tuple(_review_projects(tasks, scope))
        if "projects" in reads and context is None
        else (),
    )


def selected_advice(tasks, name=None, *, settings=(), drops=(), scope=None, context=None):
    """Resolve installed code outside the read transaction, then bind the choice to the facts."""
    from .preferences import select_profile

    for _ in range(3):
        chosen, source = select_profile(tasks, name, settings=settings, drops=drops)
        result = _advise(tasks, chosen, scope, context, source)
        if result is not None:
            return result
    raise DomainError("conflict", "The chosen profile changed during the read. Try again.")


def advise(
    tasks, profile: dict | None = None, *, scope: str | None = None, context: str | None = None
) -> Advice:
    """Run an explicit profile over current facts without changing them."""
    return _advise(tasks, profile, scope, context, None)


def _advise(tasks, requested, scope, context, source):
    from .preferences import selection_is_current
    from .profiles import PROFILES

    requested = PROFILES["plain"] if requested is None else requested
    profile = validate_profile(requested)
    skipped = requested.get("skipped", [])
    stages = [(get(stage["use"]), stage["with"]) for stage in profile["strategies"]]
    reads = {name for strategy, _ in stages for name in strategy.reads}
    session = getattr(tasks, "read_snapshot", None)
    with (session() if session else nullcontext(tasks)) as reader:
        if source and not selection_is_current(reader, source):
            return None
        view = snapshot(reader, scope=scope, context=context, reads=reads)
        total = (
            reader.summary(scope=scope, context=context)["ready"]
            if len(view.ready) == READY_LIMIT else len(view.ready)
        )
    advice = Advice(picks=list(view.ready), profile=profile["name"], scope=scope, context=context,
                    profile_source=source, as_of=view.now)
    if skipped:
        if _SERVICE_BUILTINS.get():
            message = ("Service advice uses built-in strategies. These saved stages could not run: "
                       + ", ".join(skipped) + ". Their settings remain in the workspace.")
            advice.profile_source = {**(source or {}), "skipped": skipped}
        else:
            message = ("The profile names strategies that could not be used: "
                       + ", ".join(skipped) + ". Fix with `daymark profile`.")
        advice.notes.append(Note("profile", message))
    retired = [
        f"{s['use']}.{key}"
        for s in profile["strategies"]
        for key in s.get("unavailable_parameters", {})
    ]
    if retired:
        shown = ", ".join(retired[:8]) + (
            f" and {len(retired) - 8} more" if len(retired) > 8 else ""
        )
        advice.notes.append(
            Note(
                "profile",
                f"Saved parameters unavailable in this installation: {shown}. "
                "Advice uses the installed strategy's effective parameters.",
            )
        )
    if total > READY_LIMIT:
        advice.notes.append(
            Note("profile", f"Advice covers the first {READY_LIMIT} of {total} ready tasks.")
        )
    for strategy, params in stages:
        strategy.apply(view, advice, params)
    for note in advice.notes:
        if note.batch_limit is not None:
            eligible = set(note.ids)
            note.ids = [pick["id"] for pick in advice.picks if pick["id"] in eligible][:note.batch_limit]
            note.text += f" {len(note.ids)} offered for discussion in this batch."
    if advice.steps:
        # A later stage can narrow the picks after first-step supplied text.
        advice.steps = {
            item["id"]: advice.steps[item["id"]]
            for item in advice.picks
            if item["id"] in advice.steps
        }
    return advice


def stored_profile(tasks) -> dict:
    """The workspace's chosen profile, or the plain one. Local stores only.

    Unavailable stages appear in ``skipped``. Retired parameters appear under
    ``unavailable_parameters`` beside the current effective settings.
    """
    from .preferences import select_profile

    return select_profile(tasks)[0]


def restore_profile(saved, *, for_edit=False) -> dict:
    """Resolve stored stages; keep locally unavailable configurations in order for edits."""
    from .profiles import PROFILES

    if not isinstance(saved, dict) or not isinstance(saved.get("strategies"), list):
        return PROFILES["plain"]
    stages, skipped = [], []
    for stage in saved["strategies"]:
        try:
            resolved, available = _resolve_stage(stage, allow_unavailable=True, stored=True)
            if available or for_edit:
                stages.append(resolved)
            else:
                skipped.append(stage["use"])
        except (DomainError, KeyError, TypeError, AttributeError):
            skipped.append(str(stage.get("use") if isinstance(stage, dict) else stage))
    profile = {"name": str(saved.get("name") or "custom"), "strategies": stages}
    return {**profile, "skipped": skipped} if skipped else profile


def store_profile(tasks, profile: dict) -> dict:
    """Compatibility helper for hosts implementing only the raw setting adapter."""
    writer = getattr(tasks.engine, "set_setting", None)
    if writer is None:
        raise DomainError("invalid", "Profiles are set on a local database")
    profile = persistent_profile(validate_profile({k: v for k, v in profile.items() if k != "skipped"}))
    writer(*tasks._ws, SETTING, profile)
    return restore_profile(profile)
