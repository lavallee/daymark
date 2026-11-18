"""Named profiles saved in a local workspace, separate from its active choice."""

from __future__ import annotations

import re

from ..models import DomainError
from . import SETTING, edit_profile, restore_profile, select_profile, validate_profile
from .profiles import PROFILES

_LIBRARY = "profile.library"


def _read(tasks, key):
    reader = getattr(tasks.engine, "setting", None)
    return reader(*tasks._ws, key) if reader else None


def _write(tasks, key, value, previous):
    writer = getattr(tasks.engine, "set_setting", None)
    if writer is None:
        raise DomainError("invalid", "Profiles are saved on a local database")
    writer(*tasks._ws, key, value, expected=previous)


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
    library = _library(_read(tasks, _LIBRARY))
    return [entry for _, entry in sorted(library["profiles"].items())]


def named_profile(tasks, name):
    if not isinstance(name, str):
        raise DomainError("invalid", "A profile name must be text")
    if name in PROFILES:
        return PROFILES[name]
    found = _library(_read(tasks, _LIBRARY))["profiles"].get(name)
    if found is None:
        raise DomainError(
            "not_found",
            "Unknown profile",
            ["See `daymark strategies` and `daymark profile --saved`."],
        )
    return restore_profile(found["profile"])


def active_profile(tasks, name=None, *, settings=(), drops=()):
    previous = _read(tasks, SETTING)
    current = restore_profile(previous) if name is None else named_profile(tasks, name)
    if not (name or settings or drops):
        return current
    current = edit_profile(current, settings, drops)
    if current.get("skipped"):
        raise DomainError(
            "invalid", "Drop unavailable strategies explicitly before saving", current["skipped"]
        )
    current = validate_profile(current)
    _write(tasks, SETTING, current, previous)
    return current


def _change_saved(tasks, name, candidate, expected_version):
    if not isinstance(name, str) or not re.fullmatch(r"[a-z][a-z0-9-]{0,47}", name):
        raise DomainError(
            "invalid",
            "A saved profile name needs 1–48 lowercase letters, digits or hyphens, starting with a letter",
        )
    if name in PROFILES:
        raise DomainError("invalid", "Choose a name outside the built-in profiles", [name])
    if type(expected_version) is not int or expected_version < 0:
        raise DomainError("invalid", "Supply a nonnegative expected profile version")
    previous = _read(tasks, _LIBRARY)
    library = _library(previous)
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
    # Keep the revision even when the last name is removed. An old edit can
    # never match a later profile created under that same name.
    _write(tasks, _LIBRARY, {"revision": revision, "profiles": entries}, previous)
    return result


def save_profile(tasks, name, *, profile=None, settings=(), drops=(), expected_version=0):
    """Save a named copy. A stored name, built-in, or explicit configuration may be the base."""
    if isinstance(profile, dict):
        candidate = edit_profile(validate_profile(profile), settings, drops)
    else:
        candidate = select_profile(tasks, profile, settings=settings, drops=drops)
    if candidate.get("skipped"):
        raise DomainError(
            "invalid",
            "Drop unavailable strategies explicitly before saving a copy",
            candidate["skipped"],
        )
    return _change_saved(tasks, name, candidate, expected_version)


def remove_profile(tasks, name, *, expected_version):
    return _change_saved(tasks, name, None, expected_version)
