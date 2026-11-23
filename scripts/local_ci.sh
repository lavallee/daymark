#!/usr/bin/env bash
# Run .github/workflows/ci.yml's Linux jobs locally and print one line per step.
#
#   git worktree add /tmp/daymark-ci COMMIT && cd /tmp/daymark-ci
#   scripts/local_ci.sh [PYTHON ...]        # default: 3.14
#
# Run it from a clean checkout of the commit you will merge, not a working
# tree. Each Python in the list gets the workflow's test job; 3.11 also gets
# the lowest-dependency floor and 3.12 the MCP client smoke, as in CI. The
# security job runs once. macOS jobs cannot run here: report them as not run.
# Exit status is nonzero if any step failed. Paste the summary into the PR.
set -uo pipefail

root=$(pwd)
[[ -f pyproject.toml && -f .github/workflows/ci.yml ]] || { echo "Run from the repository root." >&2; exit 2; }
if [[ -n "$(git status --porcelain --untracked-files=no 2>/dev/null)" ]]; then
  echo "The checkout has uncommitted changes; run from a clean checkout of the commit." >&2
  exit 2
fi
pythons=("$@"); [[ ${#pythons[@]} -gt 0 ]] || pythons=(3.14)
scratch=$(mktemp -d "${TMPDIR:-/tmp}/daymark-local-ci.XXXXXX")
trap 'rm -rf "$scratch"' EXIT
summary=()
failed=0

step() {  # step NAME COMMAND...
  local name=$1; shift
  local log="$scratch/$(echo "$name" | tr -c 'A-Za-z0-9.' '-').log"
  if "$@" >"$log" 2>&1; then
    summary+=("pass  $name  ($(tail -n 1 "$log" | cut -c1-100))")
  else
    summary+=("FAIL  $name  (see below)")
    failed=1
    echo "---- $name" >&2; tail -n 30 "$log" >&2
  fi
}

for py in "${pythons[@]}"; do
  export UV_PYTHON=$py UV_PROJECT_ENVIRONMENT="$scratch/venv-$py"
  rm -rf dist
  step "$py sync" uv sync --frozen --dev
  step "$py ruff" uv run ruff check src tests
  step "$py pytest" uv run pytest -q -p no:cacheprovider
  step "$py walkthrough" uv run python scripts/build_walkthrough.py --check
  step "$py build" uv build
  step "$py sdist" bash -c 'uv run python scripts/check_sdist.py dist/*.tar.gz'
  if [[ $py == 3.11 ]]; then
    step "$py floor" bash -c '
      set -e; env="$0/floor"; uv venv -q "$env" --python 3.11
      uv pip compile -q pyproject.toml --extra service --python-version 3.11 --resolution lowest-direct -o "$0/floor.txt"
      uv pip install -q --python "$env/bin/python" -r "$0/floor.txt" dist/*.whl "pytest>=8"
      "$env/bin/python" -I -m pytest -q -o pythonpath= -p no:cacheprovider' "$scratch"
  fi
  step "$py installs" bash -c '
    set -e; for artifact in "$1"/dist/*.whl "$1"/dist/*.tar.gz; do
      env="$0/smoke-${artifact##*.}-$2"; uv venv -q "$env" --python "$2"
      uv pip install -q --python "$env/bin/python" "$artifact"
      (cd "$0" && "$env/bin/python" -I -c "import importlib.util; assert all(importlib.util.find_spec(n) is None for n in (\"fastapi\", \"httpx\", \"uvicorn\"))" \
        && "$env/bin/python" -I "$1/scripts/smoke_install.py")
    done; echo ok' "$scratch" "$root" "$py"
  if [[ $py == 3.12 ]]; then
    step "$py mcp" bash -c 'cd "$0" && uv run --script "$1/scripts/smoke_mcp.py" --daymark "$0/smoke-whl-3.12/bin/daymark"' "$scratch" "$root"
  fi
  step "$py http" bash -c '
    set -e; wheels=("$1"/dist/*.whl)
    uv venv -q "$0/client-$2" --python "$2"; uv pip install -q --python "$0/client-$2/bin/python" "${wheels[0]}[client]"
    uv venv -q "$0/service-$2" --python "$2"; uv pip install -q --python "$0/service-$2/bin/python" "${wheels[0]}[service]"
    cd "$0" && env -u PYTHONPATH -u PYTHONHOME "$0/service-$2/bin/python" -I "$1/scripts/smoke_http.py" --client-python "$0/client-$2/bin/python"' "$scratch" "$root" "$py"
done

unset UV_PYTHON UV_PROJECT_ENVIRONMENT
step "security export" uv export --all-extras --no-dev --no-emit-project --frozen --output-file "$scratch/requirements-audit.txt"
step "security pip-audit" uvx --from pip-audit pip-audit -r "$scratch/requirements-audit.txt" --strict --no-deps --disable-pip
step "security zizmor" uvx --from zizmor zizmor .github/workflows --offline --min-severity high --min-confidence high

echo "Local CI for $(git rev-parse --short HEAD) on $(uname -s), Python ${pythons[*]}:"
printf '  %s\n' "${summary[@]}"
missing=(); for want in 3.11 3.12 3.13 3.14 3.15; do [[ " ${pythons[*]} " == *" $want "* ]] || missing+=("Linux $want"); done
echo "  not run: macOS 3.14 and 3.15${missing:+, ${missing[*]}}"
exit $failed
