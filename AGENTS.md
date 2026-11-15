# Working on Daymark

Daymark is a task system that simplifies the complexities of real life, built
for agents handling a person's projects and life. Judge a change by whether it
makes that simpler for an agent to get right.

- `uv sync --dev`, then `uv run pytest -q` and `uv run ruff check src tests`.
- `src/daymark/models.py` and `store.py` are the typed engine. Every write
  carries an operation ID and an expected version; keep that contract.
- `views.py` holds the single readiness rule. `tasks.py` is the plain-verb
  layer, `render.py` the lean output and the `prime` text, `mcp.py` the MCP
  server. A new verb belongs in `tasks.py` first, then the CLI and MCP.
- `docs/architecture.md` sets the layers. The kernel stores facts and enforces
  invariants; anything that ranks, limits, words or advises is a strategy in
  `src/daymark/strategies/` and reads only the public views. A new field
  enters the kernel only under the admission rule there (decision S08).
- A strategy never writes, states its evidence level honestly, and has a test.
- When a verb changes, update `GUIDE` in `render.py`, `docs/agents.md` and
  `skills/daymark/SKILL.md` together. Agents read those, not the source.
- Record a changed default in `docs/decisions.md` and `CHANGELOG.md`.
- All examples, tests and documents use synthetic records. Never add real
  names, addresses, messages or task content.
- Do not add date guessing. An unrecognised date is an error that lists the
  accepted forms.
