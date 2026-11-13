# Releasing Daymark

This checklist covers a source release of `daymark-core` (import: `daymark`). Package registry publication is a separate step requiring an available project name and configured authentication. Never infer that a GitHub release also publishes a package.

## Local gates

1. Review the final public diff and full Git history. Include only the standalone engine, synthetic examples, tests, and public docs. Keep private adapters, databases, exports, credentials, deployment reports, and local verification receipts outside the public tree. Review both tracked and untracked files; a clean secret scan alone does not prove privacy.
2. Sync and check the frozen dependency graph:

   ```sh
   uv sync --frozen --dev
   uv run ruff check src tests
   uv run pytest -q
   uv export --all-extras --no-dev --no-emit-project --frozen --output-file /tmp/daymark-audit.txt --quiet
   uvx --from pip-audit pip-audit -r /tmp/daymark-audit.txt --strict --no-deps --disable-pip
   uvx --from zizmor zizmor .github/workflows --offline --min-severity high --min-confidence high
   gitleaks dir . --redact --no-banner
   gitleaks git . --log-opts='--all' --redact --no-banner
   ```

   Release acceptance uses ordinary package imports and the real Store through library, API, client, CLI, and worker paths. Verify caller-owned rollback with and without a preexisting transaction, stale-version/replay conflicts, workspace isolation, reminder cancellation/detachment on task deletion and atomic rejection of other inbound references, skip without actual completion, cancellation before send, and subprocess recovery after a sink side effect but before persisted success. The receiver must durably deduplicate the stable delivery key. For external sinks, verify the claim/outbox record is committed before sending; caller-owned connections require an application-owned transaction design because Store never commits them. Qualify each real delivery integration separately from the recording sink. Fake-engine or temporary package-bootstrap tests are preliminary evidence only.

   Run directory scanning on a clean source checkout, without a virtual environment or generated caches. For a new repository with no commits, history scanning is pending until the initial commit. Resolve findings rather than hiding them in broad allowlists. Review direct and transitive runtime dependency licenses and notices for the actual locked versions, including optional service dependencies.
3. Keep `pyproject.toml` and `daymark.__version__` aligned. Use semantic versions, and describe changes to stored schema and behavioral compatibility. During 0.x, flag breaking changes prominently.
4. Replace the unreleased changelog with a dated release entry. Update the landing page version and status. Build and inspect both distributions:

   ```sh
   version=$(python -c 'import tomllib; print(tomllib.load(open("pyproject.toml", "rb"))["project"]["version"])')
   uv build
   python -m zipfile -l "dist/daymark_core-$version-py3-none-any.whl"
   tar -tzf "dist/daymark_core-$version.tar.gz"
   ```

   The wheel also installs a second console script, `toucan-source-daymark`; check that it runs from the core-only install (`toucan-source-daymark list` with `DAYMARK_DB` pointing at a temporary database).

   Verify the MIT license is included. Verify no private deployment report, database, export, local path, or credential appears in either archive. Inspect archive contents as well as filenames.
5. Install the wheel into an empty environment outside this checkout and run the README quickstart. Check that importing `daymark` works without FastAPI, HTTPX, or Uvicorn. Repeat with the `service` extra for CLI and service smoke checks. Build a wheel from the sdist too: a checkout can hide missing source files.
6. Run `artoo build .` and `artoo verify .`. Inspect the rendered landing page at phone and desktop widths. Use `artoo deploy . --dry-run` to inspect the public file set. Only the manifest's `docs` site root may be served.

CI must pass across its supported Python matrix. A local pass on one interpreter does not verify the matrix. Recheck these gates after any release-blocking fix, and retain sanitized evidence outside the publishable tree.

## Source release

The release owner approves the tested candidate and commits the intended files. Confirm `git status --short` is clean and the tag points to that commit.

```sh
git tag -a "v$version" -m "v$version"
git push origin main
git push origin "v$version"
gh release create "v$version" --repo lavallee/daymark --title "Daymark $version" --notes-file /tmp/daymark-release-notes.md
```

Release notes should state verified capabilities and known limits. Use a comparison link for subsequent releases. Confirm the remote commit and tag, release page, and CI results after publishing.

## Documentation and package publication

Publish web documentation only through this repository's Artoo artifact:

```sh
artoo deploy . --dry-run
artoo deploy .
```

The configured adapter uses this repository's Git origin and the `gh-pages` branch. Confirm the origin is `lavallee/daymark` and verify the hosting setup before the first deployment. The manifest defaults to `push = false`; a deployment creates a local branch commit, and the release owner must review and push that branch to publish it. Do not enable or change hosting through a separate deployment mechanism. Review the staged public file set before publication.

Only when package registry ownership and authentication already exist, the release owner may publish the reviewed distributions:

```sh
uv publish
```

Without configured authentication, record "package registry upload not done". Do not create credentials or upload to a substitute registry. After an upload, install the exact published version into a fresh environment and rerun the quickstart.
