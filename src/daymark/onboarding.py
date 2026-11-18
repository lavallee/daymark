"""Maintain Daymark's marked section in an existing project instruction file."""

import errno
import json
import os
import re
import site
import stat
import subprocess
import sys
import tempfile
from importlib.resources import files
from pathlib import Path
from urllib.parse import urlsplit

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


def mcp_config(host, *, database, workspace, actor, timezone, url, token_env):
    """Print-ready, read-only stdio configuration for the active installation."""
    from .tasks import local_timezone

    command = sys.executable
    # Desktop hosts can start with another checkout on PYTHONPATH.
    args = ["-E", "-P", "-m", "daymark.cli", f"--tz={local_timezone(timezone)}"]
    if url:
        try:
            endpoint = urlsplit(url)
            valid = (
                endpoint.scheme in {"http", "https"} and endpoint.hostname
                and endpoint.username is None and endpoint.password is None
                and not endpoint.query and not endpoint.fragment
                and not any(ord(char) < 32 or ord(char) == 127 for char in url)
            )
            _ = endpoint.port  # urlsplit validates the port on access.
        except ValueError:
            valid = False
        if not valid:
            raise DomainError("invalid", "Use an HTTP(S) service URL with no credentials, query or fragment")
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", token_env):
            raise DomainError("invalid", "Use the environment variable name holding the service credential")
        args.extend([f"--url={url}", f"--token-env={token_env}"])
    else:
        if not database or database == ":memory:" or not Path(database).is_file():
            raise DomainError("no_database", "A read-only MCP launch needs an existing database file",
                              ["Check daymark where or initialize a new workspace with daymark init --here."])
        args.extend([f"--db={Path(database).absolute()}", f"--workspace={workspace}",
                     f"--as={'agent' if actor is None else actor}"])
    args.extend(["mcp", "--read-only"])
    # The host may set a different Python user base. A user-site install
    # needs the same one used by the process printing this configuration.
    env = {}
    if site.ENABLE_USER_SITE and site.getuserbase():
        env["PYTHONUSERBASE"] = str(Path(site.getuserbase()).absolute())
    if url and token_env in env:
        raise DomainError("invalid", "Choose a separate environment variable for the service credential")
    # A bare PYTHONPATH checkout is available to this shell only. Verify the
    # launch interpreter can actually locate this installation without it.
    probe = (
        "import importlib.util, os, sys; spec = importlib.util.find_spec('daymark'); "
        "sys.exit(0 if spec and spec.origin and os.path.samefile(spec.origin, sys.argv[1]) else 1)"
    )
    check = subprocess.run(
        [command, "-B", "-E", "-P", "-c", probe, str(Path(__file__).with_name("__init__.py"))],
        check=False, capture_output=True, timeout=10, env={**os.environ, **env},
    )
    if check.returncode:
        raise DomainError("invalid", "This interpreter cannot locate the same Daymark installation from an editor",
                          ["Install the checkout into a virtual environment, then print the configuration using its daymark command."])
    if host == "claude":
        if any("${" in value for value in (command, *args, *env.values())):
            raise DomainError("invalid", "Claude Code expands variable references in launch arguments",
                              ["Choose a path or identity without a literal ${ sequence."])
        server = {"command": command, "args": args}
        if url:
            env[token_env] = "${" + token_env + ":-}"
        if env:
            server["env"] = env
        return json.dumps({"mcpServers": {"daymark": server}}, ensure_ascii=False, indent=2)
    # TOML also requires DEL to be escaped. Preserve non-BMP characters as UTF-8.
    fields = [("command", command), ("args", args)]
    if url:
        fields.append(("env_vars", [token_env]))
    lines = ["[mcp_servers.daymark]"]
    for key, value in fields:
        encoded = json.dumps(value, ensure_ascii=False).replace(chr(127), "\\u007f")
        lines.append(f"{key} = {encoded}")
    if env:
        lines.append("[mcp_servers.daymark.env]")
        for key, value in env.items():
            encoded = json.dumps(value, ensure_ascii=False).replace(chr(127), "\\u007f")
            lines.append(f"{key} = {encoded}")
    return "\n".join(lines)


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

    # Keep partial output out of the target, even on an interrupted write.
    changed = DomainError(
        "conflict", "The instruction file changed during the update",
        ["Review the current file and run onboard --write again."],
    )
    tmp = None
    try:
        with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as handle:
            tmp = Path(handle.name)
            handle.write(updated)
        if path.resolve() != target:
            raise changed
        if existed:
            try:
                if target.read_bytes() != original:
                    raise changed
                tmp.chmod(stat.S_IMODE(target.stat().st_mode))
            except FileNotFoundError:
                raise changed from None
            tmp.replace(target)
        else:
            # Linking the complete temp file publishes it only if the name is still free.
            try:
                os.link(tmp, target)
            except FileExistsError:
                raise changed from None
            except OSError as exc:
                if exc.errno not in (errno.EPERM, errno.ENOTSUP, errno.ENOSYS):
                    raise
                try:
                    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                except FileExistsError:
                    raise changed from None
                with os.fdopen(fd, "wb") as output:
                    output.write(updated)
    finally:
        if tmp:
            tmp.unlink(missing_ok=True)
    print(f"Wrote the Daymark section to {path}")
    return 0
