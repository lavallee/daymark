"""Order keys: fractional indexing with an integer part, compared as plain text.

A key is an integer part (a head letter giving its length, then base-62 digits)
and an optional fraction that never ends in "0". Appending or prepending changes
the integer, so keys grow logarithmically; only repeated inserts at one spot
lengthen the fraction (about one character per six inserts). Generation is
iterative. Keys longer than ``REBALANCE_AT`` make the Store re-key the project.

This module has no dependencies so the entity model can validate keys with it.
"""

from __future__ import annotations

DIGITS = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
_SMALLEST = "A" + DIGITS[0] * 26
MAX_LENGTH = 1000  # Longest key the model accepts.
REBALANCE_AT = 128  # A placement that would need a longer key re-keys the project.


def _integer_length(head: str) -> int:
    if "a" <= head <= "z":
        return ord(head) - ord("a") + 2
    if "A" <= head <= "Z":
        return ord("Z") - ord(head) + 2
    raise ValueError("invalid order key")


def _split(key: str) -> tuple[str, str]:
    size = _integer_length(key[0])
    if size > len(key):
        raise ValueError("invalid order key")
    return key[:size], key[size:]


def valid_rank(key) -> bool:
    """Whether ``key`` is a well-formed order key."""
    if not isinstance(key, str) or not key or len(key) > MAX_LENGTH or key == _SMALLEST:
        return False
    if any(c not in DIGITS for c in key[1:]):
        return False
    try:
        _, fraction = _split(key)
    except ValueError:
        return False
    return not fraction.endswith(DIGITS[0])


def _midpoint(a: str, b: str | None) -> str:
    """A fraction strictly between fractions ``a`` and ``b`` (None is the end)."""
    prefix = ""
    while True:
        if b is not None:
            n = 0
            while n < len(b) and (a[n] if n < len(a) else DIGITS[0]) == b[n]:
                n += 1
            if n:
                prefix, a, b = prefix + b[:n], a[n:], b[n:]
        low = DIGITS.index(a[0]) if a else 0
        high = DIGITS.index(b[0]) if b else len(DIGITS)
        if high - low > 1:
            return prefix + DIGITS[(low + high + 1) // 2]
        if b is not None and len(b) > 1:
            return prefix + b[:1]
        prefix, a, b = prefix + DIGITS[low], a[1:], None


def _increment(integer: str) -> str | None:
    head, digits = integer[0], list(integer[1:])
    for i in range(len(digits) - 1, -1, -1):
        d = DIGITS.index(digits[i]) + 1
        if d < len(DIGITS):
            digits[i] = DIGITS[d]
            return head + "".join(digits)
        digits[i] = DIGITS[0]
    if head == "Z":
        return "a" + DIGITS[0]
    if head == "z":
        return None
    head = chr(ord(head) + 1)
    if head > "a":
        digits.append(DIGITS[0])
    else:
        digits.pop()
    return head + "".join(digits)


def _decrement(integer: str) -> str | None:
    head, digits = integer[0], list(integer[1:])
    for i in range(len(digits) - 1, -1, -1):
        d = DIGITS.index(digits[i]) - 1
        if d >= 0:
            digits[i] = DIGITS[d]
            return head + "".join(digits)
        digits[i] = DIGITS[-1]
    if head == "a":
        return "Z" + DIGITS[-1]
    if head == "A":
        return None
    head = chr(ord(head) - 1)
    if head < "Z":
        digits.append(DIGITS[-1])
    else:
        digits.pop()
    return head + "".join(digits)


def rank_between(low: str | None, high: str | None) -> str:
    """A key strictly after ``low`` and before ``high``; None means the start or end.

    Raises ValueError for malformed keys and for bounds that are equal or reversed:
    the caller decides how to make room.
    """
    for key in (low, high):
        if key is not None and not valid_rank(key):
            raise ValueError("invalid order key")
    if low is not None and high is not None and low >= high:
        raise ValueError("order keys are equal or reversed")
    if low is None:
        if high is None:
            return "a" + DIGITS[0]
        integer, fraction = _split(high)
        if integer == _SMALLEST:
            return integer + _midpoint("", fraction)
        if integer < high:
            return integer
        result = _decrement(integer)
        if result is None:
            raise ValueError("no room before the first key")
        return result
    integer, fraction = _split(low)
    if high is None:
        result = _increment(integer)
        return integer + _midpoint(fraction, None) if result is None else result
    high_integer, high_fraction = _split(high)
    if integer == high_integer:
        return integer + _midpoint(fraction, high_fraction)
    result = _increment(integer)
    if result is not None and result < high:
        return result
    return integer + _midpoint(fraction, None)


def spaced(count: int) -> list[str]:
    """``count`` short increasing keys, for re-keying a whole order."""
    keys, key = [], None
    for _ in range(count):
        key = rank_between(key, None)
        keys.append(key)
    return keys
