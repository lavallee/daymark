"""Named profiles saved in a local workspace, separate from its active choice."""

from __future__ import annotations

import re

from ..models import DomainError
from . import SETTING, edit_profile, restore_profile, validate_profile
from .profiles import PROFILES

_LIBRARY = "profile.library"


def _read(tasks, key):
    reader = getattr(tasks.engine, "setting", None)
    return reader(*tasks._ws, key) if reader else None


def _operation(tasks, name, op, request, apply, *, reversible=False):
    writer = getattr(tasks.engine, "setting_operation", None)
    if writer is None:
        raise DomainError("invalid", "Profiles are saved on a local database")
    return writer(*tasks._ws, name, op, request, apply, reversible=reversible)


def _active_state(tasks):
    reader = getattr(tasks.engine, "setting_state", None)
    return (
        reader(*tasks._ws, SETTING) if reader else {"value": _read(tasks, SETTING), "version": None}
    )


def _library(value):
    if value is None:
        return {"revision": 0, "profiles": {}}
    if (
        not isinstance(value, dict)
        or type(value.get("revision")) is not int
        or not isinstance(value.get("profiles"), dict)
    ):
        raise DomainError("invalid", "Invalid saved profile library")
    return value


def saved_profiles(tasks):
    """Return saved names, versions and configurations without running plugins."""
    if not hasattr(tasks.engine, "setting"):
        raise DomainError("invalid", "Saved profiles can be inspected on the local database")
    library = _library(_read(tasks, _LIBRARY))
    return [entry for _, entry in sorted(library["profiles"].items())]


def named_profile(tasks, name):
    if not isinstance(name, str):
        raise DomainError("invalid", "A profile name must be text")
    if name in PROFILES:
        return PROFILES[name], {"kind": "built-in", "name": name}
    if not hasattr(tasks.engine, "setting"):
        raise DomainError("invalid", "Over HTTP choose a built-in profile", list(PROFILES))
    found = _library(_read(tasks, _LIBRARY))["profiles"].get(name)
    if found is None:
        raise DomainError(
            "not_found",
            "Unknown profile",
            ["See `daymark strategies` and `daymark profile --saved`."],
        )
    return restore_profile(found["profile"]), {
        "kind": "saved",
        "name": name,
        "version": found["version"],
    }


def select_profile(tasks, name=None, *, settings=(), drops=()):
    """Return the effective configuration and the same read's source information."""
    if name is None:
        state = _active_state(tasks)
        base = restore_profile(state["value"])
        source = {
            "kind": "workspace" if state["value"] is not None else "default",
            "name": base["name"],
            **({"version": state["version"]} if state["version"] is not None else {}),
        }
    else:
        base, source = named_profile(tasks, name)
    adjusted = bool(settings or drops)
    profile = edit_profile(base, settings, drops) if adjusted else base
    return profile, {**source, "adjusted": adjusted}


def _active_result(tasks, value, version):
    profile = restore_profile(value)
    if version is None:
        return profile
    return {**profile, "version": version, "workspace": tasks.workspace}


def active_profile(
    tasks, name=None, *, settings=(), drops=(), op=None, expected_version=None, undo=None
):
    if name is None and not settings and not drops and undo is None:
        if op is not None or expected_version is not None:
            raise DomainError("invalid", "A read has no operation ID or expected version")
        state = _active_state(tasks)
        return _active_result(tasks, state["value"], state["version"])

    operation_id = tasks._op_id(op)
    if undo is not None and (name is not None or settings or drops):
        raise DomainError("invalid", "Undo restores the previous configuration as recorded")
    request = {
        "action": "active",
        "name": name,
        "set": settings,
        "drop": drops,
        "expected_version": expected_version,
        "undo": undo,
    }

    def apply(before):
        if undo is not None:
            prior = tasks.engine.setting_before(*tasks._ws, SETTING, undo)
            if prior["version"] != before["version"]:
                raise DomainError("conflict", "The active profile changed after that operation")
            value = prior["value"]
        else:
            current = (
                restore_profile(before["value"]) if name is None else named_profile(tasks, name)[0]
            )
            current = edit_profile(current, settings, drops)
            if current.get("skipped"):
                raise DomainError(
                    "invalid",
                    "Drop unavailable strategies explicitly before saving",
                    current["skipped"],
                )
            value = validate_profile(current)
        tasks.engine.set_setting(*tasks._ws, SETTING, value, expected_version=expected_version)
        after = _active_state(tasks)
        return {
            **_active_result(tasks, after["value"], after["version"]),
            "operation_id": operation_id,
            "undo": {"operation_id": operation_id, "expected_version": after["version"]},
        }

    return _operation(tasks, SETTING, operation_id, request, apply, reversible=True)


def _change_saved(tasks, name, candidate, expected_version, previous):
    if not isinstance(name, str) or not re.fullmatch(r"[a-z][a-z0-9-]{0,47}", name):
        raise DomainError(
            "invalid",
            "A saved profile name needs 1–48 lowercase letters, digits or hyphens, starting with a letter",
        )
    if name in PROFILES:
        raise DomainError("invalid", "Choose a name outside the built-in profiles", [name])
    if type(expected_version) is not int or expected_version < 0:
        raise DomainError("invalid", "Supply a nonnegative expected profile version")
    library = _library(previous["value"])
    entries = dict(library["profiles"])
    current = entries.get(name)
    if current is None and candidate is None:
        raise DomainError("not_found", "No saved profile with that name", [name])
    version = current["version"] if current else 0
    if expected_version != version:
        raise DomainError(
            "conflict",
            "Saved profile version changed",
            [f"{name} is at version {version}. Read `daymark profile --saved` before editing."],
        )
    revision = library["revision"] + 1
    if candidate is None:
        del entries[name]
        result = {"removed": name, "version": revision}
    else:
        profile = validate_profile({**candidate, "name": name})
        if current and profile == current["profile"]:
            return current
        result = {"name": name, "version": revision, "profile": profile}
        entries[name] = result
    # Keep the revision when the last name is removed so a future use of that
    # name cannot resurrect a stale editor's version.
    tasks.engine.set_setting(
        *tasks._ws,
        _LIBRARY,
        {"revision": revision, "profiles": entries},
        expected_version=previous["version"],
    )
    return result


def save_profile(tasks, name, *, profile=None, settings=(), drops=(), expected_version=0, op=None):
    """Save a named copy. A name or explicit configuration may be the base."""
    request = {
        "action": "save",
        "name": name,
        "profile": profile,
        "set": settings,
        "drop": drops,
        "expected_version": expected_version,
    }

    def apply(before):
        if isinstance(profile, dict):
            config = profile.get("profile", profile)
            if isinstance(config, dict):
                # Outputs from inspecting an active choice also carry local metadata.
                config = {
                    k: v
                    for k, v in config.items()
                    if k not in {"workspace", "version", "operation_id", "undo"}
                }
            candidate = edit_profile(validate_profile(config), settings, drops)
        else:
            candidate, _ = select_profile(tasks, profile, settings=settings, drops=drops)
        if candidate.get("skipped"):
            raise DomainError(
                "invalid",
                "Drop unavailable strategies explicitly before saving a copy",
                candidate["skipped"],
            )
        return _change_saved(tasks, name, candidate, expected_version, before)

    return _operation(tasks, _LIBRARY, tasks._op_id(op), request, apply)


def remove_profile(tasks, name, *, expected_version, op=None):
    request = {"action": "remove", "name": name, "expected_version": expected_version}

    def apply(before):
        return _change_saved(tasks, name, None, expected_version, before)

    return _operation(tasks, _LIBRARY, tasks._op_id(op), request, apply)
