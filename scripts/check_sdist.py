"""Check a built sdist ships what the contributor docs promise.

Usage: python scripts/check_sdist.py dist/daymark_core-X.Y.Z.tar.gz
"""

import sys
import tarfile

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
    "scripts/check_sdist.py",
    ".github/workflows/ci.yml",
    "src/daymark/__init__.py",
    "tests/fixtures/daymark-0.4.0.sql",
]


def missing(archive: str) -> list[str]:
    with tarfile.open(archive) as tar:
        names = {n.split("/", 1)[1] for n in tar.getnames() if "/" in n}
    return [path for path in REQUIRED if path not in names]


if __name__ == "__main__":
    gone = missing(sys.argv[1])
    if gone:
        raise SystemExit("sdist is missing: " + ", ".join(gone))
    print("sdist contains the contributor files")
