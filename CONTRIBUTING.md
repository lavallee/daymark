# Contributing

Daymark welcomes focused bug reports, documentation improvements, and small pull requests. Discuss changes to public behavior before implementing a large feature.

## Development

Use Python 3.11 or newer and [uv](https://docs.astral.sh/uv/):

```sh
uv sync --frozen --dev
uv run ruff check src tests
uv run pytest -q
uv build
```

The library should remain useful without the optional HTTP service or any delivery provider. Keep imports of optional dependencies out of the core import path. The application owns transactions when it supplies a `sqlite3.Connection`; library changes must preserve that boundary.

For a behavior change, add a focused test that demonstrates the result and relevant failure case. Concurrency, replay, rollback, time-zone transitions, and workspace isolation deserve explicit coverage. Document changes to public signatures and behavior, and add an entry to [CHANGELOG.md](CHANGELOG.md).

Use synthetic examples and fixtures. Do not submit production databases, exports, credentials, personal records, or private deployment details. Report vulnerabilities through [SECURITY.md](SECURITY.md).

## Pull requests

Explain the problem, the resulting behavior, and the checks you ran. Keep unrelated formatting changes out of the diff. Contributions are provided under the project's [MIT license](LICENSE).

See [RELEASING.md](RELEASING.md) for maintainer release checks.
