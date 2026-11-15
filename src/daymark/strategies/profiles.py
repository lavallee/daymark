"""Profiles: named bundles of strategies, adopted in one step and editable after.

Each is a starting point, not a diagnosis. ``plain`` changes nothing and is the
default, so adopting the strategy layer is always a choice.
"""

from __future__ import annotations

PROFILES = {
    "plain": {
        "name": "plain",
        "strategies": [],
    },
    # What a good assistant does unasked: look ahead, chase what is waiting,
    # batch the decisions, and say when the day will not fit.
    "assistant": {
        "name": "assistant",
        "strategies": [
            {"use": "frontier-first"},
            {"use": "decisions-first"},
            {"use": "lookahead"},
            {"use": "waiting-check"},
            {"use": "overbooked"},
            {"use": "stale"},
        ],
    },
    # Few choices, one small step each, no red ink. For anyone who finds a long
    # list harder to start than a short one.
    "gentle": {
        "name": "gentle",
        "strategies": [
            {"use": "neutral-aging"},
            {"use": "shortlist", "with": {"size": 3}},
            {"use": "first-step"},
            {"use": "overbooked"},
        ],
    },
    # Everything visible, plus the checks that keep a large system honest.
    "dense": {
        "name": "dense",
        "strategies": [
            {"use": "lookahead"},
            {"use": "waiting-check"},
            {"use": "stale"},
        ],
    },
}
