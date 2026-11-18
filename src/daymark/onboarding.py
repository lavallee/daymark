"""Maintain Daymark's marked section in an existing project instruction file."""

import os
import re
import stat
import tempfile
from importlib.resources import files
from pathlib import Path

from .models import DomainError
from .render import ONBOARD

_MARKER = re.compile(rb"<!-- daymark:(begin|end) -->[ \t]*")
_FENCE = re.compile(rb" {0,3}(`{3,}|~{3,})(.*)")


def skill_text():
    """Read the guide shipped with this version, including an editable checkout."""
    packaged = files("daymark").joinpath("SKILL.md")
    if packaged.is_file():
        return packaged.read_text(encoding="utf-8")
    return (Path(__file__).resolve().parents[2] / "skills/daymark/SKILL.md").read_text(encoding="utf-8")


def _section(original: bytes):
    """Find live markers while leaving Markdown examples alone."""
    markers = []
    fence = b""
    offset = 0
    for line in original.splitlines(keepends=True):
        content = line.rstrip(b"\r\n")
        mark = _FENCE.fullmatch(content)
        if mark:
            run, info = mark.groups()
            if fence:
                if run.startswith(fence) and not info.strip():
                    fence = b""
            elif run[:1] == b"~" or b"`" not in info:
                fence = run
        elif not fence and (marker := _MARKER.fullmatch(content)):
            markers.append((marker[1], offset, offset + len(content)))
        offset += len(line)
    if markers and [name for name, _, _ in markers] != [b"begin", b"end"]:
        raise DomainError(
            "invalid",
            "Expected one complete Daymark section",
            ["Inspect the daymark:begin and daymark:end marker lines before retrying."],
        )
    if not markers and fence:
        raise DomainError("invalid", "Close the Markdown code fence before appending instructions")
    return (markers[0][1], markers[1][2]) if markers else None


def updated_instructions(original: bytes) -> bytes:
    """Preserve bytes outside the marked section, including the file's line endings."""
    span = _section(original)
    newline = b"\r\n" if b"\r\n" in original else b"\n"
    section = ONBOARD.encode().replace(b"\n", newline)
    if span is None:
        if not original:
            return section
        separator = newline if original.endswith(b"\n") else newline * 2
        return original + separator + section
    start, stop = span
    return original[:start] + section.rstrip(b"\r\n") + original[stop:]


def maintain(path: Path, *, check: bool) -> int:
    target = path.resolve()
    existed = target.exists()
    original = target.read_bytes() if existed else b""
    updated = updated_instructions(original)
    if updated == original:
        print(f"Daymark instructions are current in {path}")
        return 0
    if check:
        print(
            f"Daymark instructions are {'outdated' if _section(original) is not None else 'missing'} in {path}"
        )
        return 1

    # Write beside the target so an interrupted refresh leaves the original intact.
    tmp = None
    try:
        with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as handle:
            tmp = Path(handle.name)
            if existed:
                os.fchmod(handle.fileno(), stat.S_IMODE(target.stat().st_mode))
            handle.write(updated)
        tmp.replace(target)
    finally:
        if tmp:
            tmp.unlink(missing_ok=True)
    print(f"Wrote the Daymark section to {path}")
    return 0
