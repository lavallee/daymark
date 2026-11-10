# Daymark

**A task system that simplifies the complexities of real life.**

Real commitments are not a flat list. A passport has a hard deadline; the trip
you mean to book on Friday is only a plan. The filter is changed three months
after it was last changed, not on a calendar date. The leak cannot be fixed
until the plumber calls back. Daymark keeps those differences in the record, so
an agent or a person can ask one question, "what can be done now?", and trust
the answer.

It is built for AI agents working on a person's projects and life, and for the
applications around them: one SQLite file, no cloud, no model, no account.

```console
$ daymark init
$ daymark add "Renew passport" --due 2030-05-03 -p 1
$ daymark add "Book flights" --plan fri --id flights
$ daymark add "Book hotel" --after flights
$ daymark add "Water the plants" --every "3 days" --after-completion
$ daymark ready
dm-k3f9  Water the plants  [planned today; plan 2030-04-03; repeats]
flights  Book flights  [plan 2030-04-05]
dm-y2yj  P1  Renew passport  [due 2030-05-03]
$ daymark done flights --note "Booked, confirmation in the travel folder"
$ daymark ready            # the hotel is no longer blocked
```

## For agents

Run `daymark prime` at the start of a session. It prints the working rules, the
command list and the current state in about 2,600 characters. `daymark onboard
--write AGENTS.md` adds a short section that tells future sessions to do so.

| You want | Command |
| --- | --- |
| What can be done now, most pressing first | `daymark ready` |
| Overdue, today, the days ahead, waiting, proposed | `daymark agenda` |
| One task with blockers, notes and history | `daymark show ID` |
| Capture work | `daymark add "Title" [--due D] [--plan D] [--start D] [--every R]` |
| Suggest work for a person to accept or dismiss | `daymark propose "Title"` |
| Take a task so parallel agents do not collide | `daymark claim ID` |
| Leave context for the next session | `daymark note ID "text"` |
| Finish, or pass over one round of a repeating task | `daymark done ID`, `daymark skip ID` |
| Park on someone else, or hide until a date | `daymark wait ID --on WHO`, `daymark defer ID D` |
| Keep a durable fact | `daymark remember "fact"` |

Output is one line per task, carrying only what is set. `--json` gives the same
content as compact JSON. Failures print a stable code (`not_found`, `conflict`,
`invalid`) with the rule that was broken, and exit with a matching status.
Dates use a small fixed grammar (`2030-04-03`, `tomorrow`, `fri`, `+3d`,
`fri 09:00`); anything else is refused with the accepted forms. Daymark never
guesses a date.

`daymark mcp` serves the same verbs to an agent harness as six MCP tools over
stdio, with no extra dependency. From Python, `daymark.Tasks` is the same
surface:

```python
from daymark import Store, Tasks

tasks = Tasks(Store("daymark.sqlite3"), actor="planner")
tasks.add("Renew passport", due="2030-05-03", priority=1)
for task in tasks.ready():
    print(task["id"], task["title"])
```

Read the [agent guide](docs/agents.md) for the full surface, and
[work and life](docs/work-and-life.md) for how Daymark relates to code-centred
trackers such as Beads and what it does differently.

## What it models

- **Four kinds of time.** `deadline` is a hard cutoff. `planned` is an
  intention that may move. `earliest_start` hides work until it can begin.
  `latest_target` is a soft finish. Only a missed deadline is overdue.
- **Recurrence that matches intent.** Fixed schedules (the bins go out every
  Thursday) and after-completion intervals (the plants, three days after they
  were last watered). Skipping a round is not completing it.
- **Readiness.** A task is ready when it is open, its start has arrived, its
  dependencies are finished, and nobody else has claimed it. One rule, used by
  every surface.
- **Whose move it is.** A task can wait on someone, be proposed and await a
  decision, or be marked as the person's move, a question for them, or work an
  agent may carry out.
- **Memory.** Append-only notes on each task, durable remembered facts, and a
  full event history with undo.
- **Safety.** Every write has an operation ID and an expected version, so
  retries are harmless and stale writes are refused rather than merged.

## Install from source

The Python import is `daymark`; the distribution is `daymark-core`. Version
0.1.0 is the initial release. Package registry publication is not yet promised.
Requires Python 3.11 or newer.

```sh
git clone https://github.com/lavallee/daymark.git
cd daymark
python -m venv .venv
. .venv/bin/activate
python -m pip install .
```

The core needs no HTTP framework, credentials, notification provider, or network service. Install optional HTTP dependencies with `python -m pip install '.[service]'`.

`daymark init` creates a per-user database; `daymark init --here` creates
`.daymark/daymark.sqlite3` for one project. Commands use `--db`, then
`DAYMARK_DB`, then the nearest `.daymark/` above the working directory, then
the per-user database.

## The engine

Underneath the plain verbs is a typed engine with explicit operation IDs and
versions. Use it directly when embedding Daymark in an application. This
example uses synthetic records and an in-memory database:

```python
from daymark import Store

store = Store(":memory:")
payload = {
    "id": "draft-proposal",
    "kind": "task",
    "title": "Draft a proposal",
    "planned": {"date": "2030-04-03", "timezone": "Europe/London"},
}
task = store.execute("demo", "capture-proposal", "create", payload, expected_version=0)

# Identical retries return the original result without another write.
assert store.execute(
    "demo", "capture-proposal", "create", payload, expected_version=0
) == task

updated = store.execute(
    "demo", "rename-proposal", "update",
    {"id": task["id"], "title": "Draft the revised proposal"},
    expected_version=task["version"],
)
assert store.get("demo", task["id"])["version"] == updated["version"]
assert updated["deadline"] is None  # A planned day is not a hard deadline.
assert [item["id"] for item in store.ready("demo")] == ["draft-proposal"]
store.close()
```

Use a database path instead of `":memory:"` for persistence. A path-owned Store commits its successful operations; call `close()` when finished. Reads return dictionaries with JSON-compatible values.

Each write needs an operation ID and an expected version. Use version `0` for creation and the last read version for updates. Reuse an operation ID only for an identical retry; a changed payload or expected version conflicts. Catch `daymark.DomainError` for domain failures; its `code` is stable and its `details` name the offending fields without echoing their values.

## Embed in your transaction

Pass an existing `sqlite3.Connection` when task changes must share an application transaction:

```python
import sqlite3
from daymark import Store

connection = sqlite3.connect(":memory:")
store = Store(connection)
connection.commit()  # Caller explicitly persists initial schema setup.

with connection:  # Caller commits on success and rolls back on exception.
    store.execute(
        "demo", "capture-review", "create",
        {"id": "review", "kind": "task", "title": "Review the proposal"},
        expected_version=0,
    )

store.close()  # A caller-owned connection stays open.
connection.close()
```

Store initialization and writes participate in the supplied connection's transaction. The library uses savepoints and does not commit that connection, even when there was no active transaction. Persist schema initialization before starting a separate unit of work; rolling back initial setup also rolls back the new tables. Keep the connection's threading rules under your application's control. Only `daymark_` tables belong to the library.

## Timing and structured work

Entities have explicit kinds, lifecycle state, version, links, source references, and extensible JSON metadata. `deadline`, `planned`, `earliest_start`, and `latest_target` express different constraints. A temporal value contains either a local `date` or an offset-aware `at`, together with an IANA `timezone`; floating instants are rejected.

Recurrence models distinguish fixed schedules from completion-based intervals. Skipping a recurring occurrence advances its series without recording an actual completion: that occurrence is cancelled with no completion timestamp. Deleting a task cancels its reminders and detaches their live task references; event history retains the original links. Other inbound entity references block deletion atomically and must be resolved explicitly first. Read the [v1 decisions](https://github.com/lavallee/daymark/blob/main/docs/decisions.md) and [product comparison](https://github.com/lavallee/daymark/blob/main/docs/comparison.md) for chosen scope, deferred behavior, and design context.

## Standalone surfaces and status

The optional API, Python HTTP client, CLI, and recording worker use the same Store engine. The default recording sink sends no notifications. External sinks must durably deduplicate delivery keys; an external side effect cannot be guaranteed exactly once.

External delivery requires a committed claim or outbox record before the side effect. A worker using a caller-owned Store cannot commit that transaction; the embedding application must design and own those transaction boundaries. Supplying a custom sink alone does not qualify production delivery.

Read the [operations guide](https://github.com/lavallee/daymark/blob/main/docs/operations.md) for configuration and synthetic command examples. Check [CHANGELOG.md](CHANGELOG.md) before upgrading; stored schema and public behavior may change during 0.x.

See [CONTRIBUTING.md](CONTRIBUTING.md), [SECURITY.md](SECURITY.md), and the maintainer [release checklist](RELEASING.md).

## License

[MIT](LICENSE).
