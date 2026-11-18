"""Check the contributor files and standalone documentation links in a built sdist.

Usage: python scripts/check_sdist.py dist/daymark_core-X.Y.Z.tar.gz
"""

import posixpath
import re
import sys
import tarfile
import tomllib
from pathlib import Path
from urllib.parse import unquote, urlsplit

REQUIRED = [
    "AGENTS.md",
    "CONTRIBUTING.md",
    "LICENSE",
    "README.md",
    "artifact.toml",
    "pyproject.toml",
    "uv.lock",
    "docs/agents.md",
    "docs/architecture.md",
    "docs/decisions.md",
    "docs/index.html",
    "skills/daymark/SKILL.md",
    "scripts/build_walkthrough.py",
    "scripts/agent_journey.py",
    "scripts/smoke_install.py",
    "scripts/smoke_http.py",
    "deploy/daymark-recorder.service",
    "scripts/smoke_client.py",
    "scripts/smoke_mcp.py",
    "scripts/check_sdist.py",
    ".github/workflows/ci.yml",
    "src/daymark/__init__.py",
    "tests/fixtures/daymark-0.4.0.sql",
]


def problems(archive: str) -> list[str]:
    with tarfile.open(archive) as tar:
        files = {
            member.name.partition("/")[2]: member
            for member in tar.getmembers() if member.isfile()
        }
        errors = [f"sdist is missing {path}" for path in REQUIRED if path not in files]
        config = tomllib.loads((Path(__file__).resolve().parents[1] / "pyproject.toml").read_text())
        selected = set(config["tool"]["hatch"]["build"]["targets"]["sdist"]["only-include"])
        for path, member in files.items():
            if path != "PKG-INFO" and not any(path == root or path.startswith(root + "/") for root in selected):
                errors.append(f"sdist contains an unexpected path: {path}")

            if not (path.startswith("docs/") and (path.endswith(".md") or path == "docs/llms.txt")):
                continue
            with tar.extractfile(member) as source:
                text = source.read().decode("utf-8")
            # Check the inline links used by the guides. Fenced examples may
            # contain illustrative paths that aren't navigation destinations.
            prose = re.sub(r"(?ms)^```[^\n]*\n.*?^```[ \t]*$", "", text)
            for href in re.findall(r"\]\(([^\s)]+)\)", prose):
                link = urlsplit(href)
                if link.scheme or link.netloc or not link.path:
                    continue
                target = posixpath.normpath(posixpath.join(posixpath.dirname(path), unquote(link.path)))
                if not target.startswith("docs/") or target not in files:
                    errors.append(f"{path}: {href} is unavailable in the standalone docs")
    return errors


if __name__ == "__main__":
    errors = problems(sys.argv[1])
    if errors:
        raise SystemExit("\n".join(errors))
    print("sdist contains the contributor files and standalone documentation link targets")
