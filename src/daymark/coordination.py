"""Helpers for work that several actors share: a question with its answer, a
follow-up after an interval, and watching the event log.

Nothing here adds stored fields. A question lives in the task's ``metadata``
under ``daymark.question`` and its wording is also an ordinary note, so every
existing reader still sees the thread.
"""

from __future__ import annotations

import re
from datetime import timedelta

from .errors import DomainError

QUESTION = "daymark.question"
FOLLOWUP = "daymark.followup"

INTERVAL_FORMS = (
    "Accepted intervals: a whole number followed by min, h or d, such as 10min, 2h or 1d "
    "(1 minute to 365 days). A bare m is refused because +Nm means months in a date."
)
_INTERVAL = re.compile(r"([1-9][0-9]{0,5})(min|h|d)")
_UNITS = {"min": 60, "h": 3600, "d": 86400}
MAX_OPTIONS = 6


def interval(text) -> timedelta:
    """Resolve an explicit interval, or raise with the accepted forms."""
    match = _INTERVAL.fullmatch(text.strip().lower()) if isinstance(text, str) else None
    seconds = int(match[1]) * _UNITS[match[2]] if match else 0
    if not 60 <= seconds <= 365 * 86400:
        raise DomainError("invalid", "Unrecognized interval", [INTERVAL_FORMS])
    return timedelta(seconds=seconds)


def clean_question(text, to, options, recommend):
    """Validate a question before any write and return its stored form."""
    if not isinstance(text, str) or not text.strip() or len(text) > 2000:
        raise DomainError("invalid", "A question is 1 to 2,000 characters")
    if not isinstance(to, str) or not to.strip() or len(to) > 200:
        raise DomainError("invalid", "Name who should answer, in 1 to 200 characters")
    options = [o.strip() if isinstance(o, str) else o for o in options or ()]
    if len(options) > MAX_OPTIONS or any(
        not isinstance(o, str) or not o or len(o) > 200 for o in options
    ):
        raise DomainError("invalid", f"Give at most {MAX_OPTIONS} options of 1 to 200 characters")
    if len(set(options)) != len(options):
        raise DomainError("invalid", "Options must differ from one another")
    if recommend is not None and (type(recommend) is not int or not 1 <= recommend <= len(options)):
        raise DomainError("invalid", "The recommended option is its number in the list, from 1")
    return text.strip(), to.strip(), options


def question_note(question) -> str:
    lines = [f"Question for {question['to']}: {question['text']}"]
    for number, option in enumerate(question["options"], 1):
        mark = " (recommended)" if question.get("recommend") == number else ""
        lines.append(f"{number}. {option}{mark}")
    return "\n".join(lines)


def open_question(entity):
    """The unanswered question on a record, or None."""
    question = (entity.get("metadata") or {}).get(QUESTION)
    return question if isinstance(question, dict) and "answer" not in question else None


def resolve_answer(question, text, choice):
    """Validate an answer against its question and return (text, choice)."""
    options = question.get("options") or []
    if choice is not None and (type(choice) is not int or not 1 <= choice <= len(options)):
        raise DomainError(
            "invalid",
            "Choose an option by its number" if options else "This question has no options to choose from",
        )
    if text is not None and (not isinstance(text, str) or not text.strip() or len(text) > 2000):
        raise DomainError("invalid", "An answer is 1 to 2,000 characters")
    if text is None and choice is None:
        raise DomainError("invalid", "Give the answer as text, an option number, or both")
    return (text.strip() if text else options[choice - 1]), choice


def answer_note(actor, text, question, choice) -> str:
    chosen = f" (option {choice}: {question['options'][choice - 1]})" if choice else ""
    if choice and text == question["options"][choice - 1]:
        return f"Answer from {actor}: option {choice}, {text}"
    return f"Answer from {actor}: {text}{chosen}"


def matches(event, *, ids, actor, by, include_own) -> bool:
    """Whether a workspace event is one the watcher asked to be woken for."""
    if ids and event["id"] not in ids:
        return False
    if by is not None and event["actor"] != by:
        return False
    return include_own or by is not None or event["actor"] != actor
