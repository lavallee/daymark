"""Maintain Daymark's marked section in an existing project instruction file."""

import os
import re
import stat
import tempfile
from pathlib import Path

from .models import DomainError
from .render import ONBOARD

_MARKER = re.compile(rb"^<!-- daymark:(begin|end) -->[ \t]*\r?$", re.MULTILINE)


def updated_instructions(original: bytes) -> bytes:
    """Preserve bytes outside the marked section, including the file's line endings."""
    markers = list(_MARKER.finditer(original))
    newline = b"\r\n" if b"\r\n" in original else b"\n"
    section = ONBOARD.encode().replace(b"\n", newline)
    if not markers:
        if not original:
            return section
        separator = newline if original.endswith(b"\n") else newline * 2
        return original + separator + section
    if [m[1] for m in markers] != [b"begin", b"end"]:
        raise DomainError(
            "invalid",
            "Expected one complete Daymark section",
            ["Inspect the daymark:begin and daymark:end marker lines before retrying."],
        )
    # Leave the final line ending exactly as found.
    stop = markers[1].end()
    if original[stop - 1 : stop] == b"\r":
        stop -= 1
    return original[: markers[0].start()] + section.rstrip(b"\r\n") + original[stop:]


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
            f"Daymark instructions are {'outdated' if existed and _MARKER.search(original) else 'missing'} in {path}"
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
