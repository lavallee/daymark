# Contributing

Daymark welcomes focused bug reports, documentation improvements, and small pull requests. Discuss changes to public behavior before implementing a large feature.

## Development

The git checkout and source distribution include the tests, guides and contributor scripts. [AGENTS.md](AGENTS.md) describes the layers and which guides to update when a command changes.

With Python 3.11 or newer and [uv](https://docs.astral.sh/uv/), run

```sh
uv sync --frozen --dev
uv run ruff check src tests
uv run pytest -q
uv run python scripts/build_walkthrough.py --check
uv build
uv run python scripts/check_sdist.py dist/*.tar.gz
```

The standalone site serves the contents of `docs/`. The archive check
checks local inline links in the Markdown guides and `llms.txt`. When
citing a test or script from the matching checkout, give its repository
path in code.

The test matrix covers Python 3.11–3.14 on Linux and 3.14 on macOS.
To reproduce the macOS file-descriptor budget locally, use
`(ulimit -n 256 && uv run pytest -q)` in a POSIX shell. Tests that open
path-backed stores should close them explicitly, including on failure.

With a recent pip that supports dependency groups, run `python -m pip install -e . --group dev` inside a virtual environment for an editable contributor install. The dev group includes the optional HTTP dependencies used by the service tests.

The library should remain useful without the optional HTTP service or any delivery provider. Keep imports of optional dependencies out of the core import path. The application owns transactions when it supplies a `sqlite3.Connection`; library changes must preserve that boundary.

For a behavior change, add a focused test that demonstrates the result and relevant failure case. Concurrency, replay, rollback, time-zone transitions, and workspace isolation deserve explicit coverage. Document changes to public signatures and behavior, and add an entry to [CHANGELOG.md](CHANGELOG.md).

Use synthetic examples and fixtures. Do not submit production databases, exports, credentials, personal records, or private deployment details. Report vulnerabilities through [SECURITY.md](SECURITY.md).

For a new way of working, try a Python strategy package first. The example
includes its entry point, parameter declaration and a one-call preview.
[Add a strategy](docs/strategy-plugins.md)

## Pull requests

Explain the problem, the resulting behavior, and the checks you ran. Keep unrelated formatting changes out of the diff. Contributions are provided under the project's [MIT license](LICENSE).

See [RELEASING.md](RELEASING.md) for maintainer release checks.

## Try a change as a new user or agent

The [agent journeys](docs/research/agent-journeys.md#repeating-the-journeys-with-your-own-client)
include a local driver for seven synthetic requests. Use it to compare what the
person asked for with what an agent actually stored. It works with the real
CLI and your choice of client. To test what helps a person return to a project,
compare their usual routine with two real returns and keep the notes private.
[Field trial worksheet](docs/research/trying-daymark.md)

## Check a real MCP client

From a clean checkout, run the optional SDK smoke check with uv.

```sh
uv sync --dev && uv run --script scripts/smoke_mcp.py --daymark .venv/bin/daymark
```

The script starts three fresh stdio servers against its own temporary database.
It checks capture, ownership, a handoff note, an exact restart retry and
read-only pickup. The pinned Python MCP SDK runs in the script environment.
To qualify a wheel, pass the path to its installed `daymark` executable.
