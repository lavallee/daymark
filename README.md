# Daymark

**Daymark helps a person and their agents decide what can be done now.** It keeps
deadlines, intentions, dependencies and suggestions distinct, so a new session
can pick up a project without asking the person to reconstruct it. It runs on
one SQLite file and needs no account or model.

Already using an issue tracker? Keep the code fix there. Daymark can hold
the person's follow-up, such as booking a proof once the fix is verified.
[Working alongside an existing tracker](docs/work-and-life.md#what-daymark-learned-from-beads)

A venue deposit has a deadline. Drafting the invitation is a plan. Approving it
must wait until someone has drafted it. An idea for a poster is still a suggestion.
Here is how that looks in Daymark.

## Try it in an empty folder

Install the CLI with Python 3.11 or newer and uv:

```sh
uv tool install "daymark-core @ git+https://github.com/lavallee/daymark@v0.5.0"
```

Run these commands in a new directory with the default database settings.
`init --here` gives this example its own database.

```sh
daymark init --here
daymark add "Pay venue deposit" --id deposit --due +7d --move person
daymark add "Draft invitation" --id draft --plan tomorrow --move agent
daymark add "Approve invitation" --id approve --after draft --move person
daymark propose "Design a poster" --id poster
daymark ready --move agent
```

The agent sees `draft`. A plan for tomorrow leaves it actionable today. The
deposit belongs to the person, the approval waits for the draft, and the poster
stays in `agenda` for a decision. When the draft is actually ready for review:

```sh
daymark done draft --note "Draft is in the event folder."
daymark ready --move person
```

The person now sees both the deposit and the approval. In a later session,
`daymark show draft` explains what happened. For recurring work, a team project,
or an embedded application, see the examples below.

## For agents

Run `daymark prime` when a session starts. It identifies the workspace and
shows what is ready, what is waiting, and which facts earlier sessions kept.
`daymark onboard` previews the startup instructions. Use
`daymark onboard --write AGENTS.md` to install them when they fit the project.
To verify two agents share the intended workspace before creating a task,
see [the five-minute MCP walkthrough](docs/mcp.md#connect-two-agents-in-five-minutes).
Its enforced read-only sessions use the development checkout. From that checkout,
`daymark onboard --mcp-config claude` or `daymark onboard --mcp-config codex`
prints the launcher for the installation and workspace you selected.
For commands supported by the tagged install above, see the
[0.5.0 agent guide](https://github.com/lavallee/daymark/blob/v0.5.0/docs/agents.md).

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
| Apply the person's way of working | `daymark advise` |
| See what must be true for a goal, and what is stuck | `daymark explore GOAL` |
| Put a task on the person's own short list | `daymark focus ID` |
| See only one part of life | `daymark ready --scope household` |
| Run a project as a board, in your own order | `daymark plan P`, `daymark move ID --stage S --after X` |

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
from contextlib import closing
from daymark import Store, Tasks

with closing(Store("daymark.sqlite3")) as store:
    tasks = Tasks(store, actor="planner")
    tasks.add("Renew passport", due="2030-05-03", priority=1, op="passport-2030")
    for task in tasks.ready():
        print(task["id"], task["title"])
```

The development version also ships its agent skill inside the CLI package.
Run `daymark onboard --skill` to read the guide that matches the installed version.

For a short list during a busy afternoon or a standing preference, including
one-call adjustments in the development checkout, see
[working with a person’s preferences](docs/working-preferences.md).

Read the [agent guide](docs/agents.md) for the full surface, and
[work and life](docs/work-and-life.md) for how Daymark relates to code-centred
trackers such as Beads and what it does differently.

## Projects: as plain or as structured as the work needs

A project starts as a list of tasks on a board of To do, Waiting and Done. Add
structure only when the work asks for it: your own stages, item types such as
epics, milestones that work counts toward, a goal it serves, and fields of your
own (points, cost, risk). None of it is required, and nothing about a project
forces a method on it.

The following transcripts use the development checkout. Each starts with a
fresh `daymark init` and `DAYMARK_TZ=Europe/London`. Moving house has several
phases and one date that matters.

```console
$ daymark project add "Move house" --id move --preset event --target 2030-06-28
move  Move house  [target 2030-06-28]
$ daymark add "Moving day" --project move --id day --is-milestone --due 2030-06-28
day  Moving day  [due 2030-06-28; milestone]
$ daymark add "Book the van" --project move --id van --milestone day --stage book --set cost=180 --due 2030-06-01
van  Book the van  [due 2030-06-01; stage:book]
$ daymark add "Pack the kitchen" --project move --id pack --milestone day --stage prepare --plan 2030-06-20
pack  Pack the kitchen  [plan 2030-06-20; stage:prepare]
$ daymark plan move
move  Move house  [0/3 done (0%), 2030-06-20 to 2030-06-28, 1 milestone, cost 180]
end 2030-06-28
PLAN (1)  [plan: open]
  day  Moving day  [due 2030-06-28; milestone]  ?..2030-06-28
BOOK (1)  [book: open]
  van  Book the van  [due 2030-06-01]  ?..2030-06-01  for day  cost=180
PREPARE (1)  [prepare: open]
  pack  Pack the kitchen  [plan 2030-06-20]  2030-06-20..?  for day
ON THE DAY (0)  [day: open]
DONE (0)  [done: completed]
DROPPED (0)  [dropped: cancelled]
cursor 4
```

A software release has epics, points and a beta:

```console
$ daymark project add "Release 2.0" --id rel --preset epics
rel  Release 2.0
$ daymark add "Beta" --project rel --id beta --is-milestone --due 2030-05-01
beta  Beta  [due 2030-05-01; milestone]
$ daymark add "Accounts" --project rel --id acc --type epic
acc  Accounts
$ daymark add "Sign-in" --project rel --id signin --type story --parent acc --milestone beta --set points=3
signin  Sign-in
$ daymark add "Fix crash on login" --project rel --id crash --type bug --parent acc --milestone beta --set points=2
crash  Fix crash on login
$ daymark plan rel --by milestone
rel  Release 2.0  [0/4 done (0%), ? to 2030-05-01, 1 milestone, points 5]
MILESTONE beta Beta (2)  [2030-05-01; 0/2 done]
  signin  Sign-in  story  in acc  points=3
  crash  Fix crash on login  bug  in acc  points=2
NO MILESTONE (1)
  acc  Accounts  epic
cursor 5
```

A stage carries a lifecycle status, so moving a card to Done completes the task,
and `daymark done` moves the card. `daymark move ID --after X` sets the order.
The plan reads start as the planned day (else the earliest start) and end as the
target (else the deadline); moving a plan never moves a deadline. Roll-ups,
health (overdue, blocked, unowned, past a milestone, date conflicts) and warnings
are facts, never a score. `daymark project presets` lists the starting
configurations; `daymark project config ID` prints a project's as JSON, and
`--apply FILE` reshapes it in one step.

For an application that shows and edits a project, `daymark plan --json`,
`GET /v1/projects/{id}/plan` and `Store.plan` give the whole plan with an event
cursor in one read; `Store.changes` (`GET /v1/projects/{id}/changes`) says
cheaply whether anything changed since; `Store.batch` writes several changes in
one transaction; and `daymark.plans.schedule` turns a plan's dates back into
Daymark's fields.

## A kernel and ways of working

Daymark is built in layers. The **kernel** stores what is true and enforces
what must hold. **Strategies** sit above it: small, switchable opinions about
what to offer and what to mention, bundled into profiles (`plain`,
`assistant`, `gentle`, `dense`) that a workspace adopts in one step. A
strategy only advises; it never writes. Other packages can add their own.
Read the [architecture](docs/architecture.md) for the contract and the
[research digest](docs/research/working-methods.md) for the evidence behind
each strategy.

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
- **Separation.** A workspace is a hard boundary: nothing is listed, searched
  or referenced across it, even in one database file. A scope is a lighter
  filter inside a workspace.
- **Memory.** Append-only notes on each task, durable remembered facts, and a
  full event history with undo.
- **Safety.** Entity writes carry an operation ID and an expected version, so
  retries are harmless and stale writes are refused rather than merged.

## Install from source

The Python import is `daymark`; the distribution is `daymark-core`. The tagged release is 0.5.0. This checkout identifies itself as 0.6.0.dev0.
Use `daymark --version` to tell which CLI your agent will run. Both require
Python 3.11 or newer.

The quickstart above installs the tagged release. To run these development
examples, start in the checkout or unpacked source archive containing this
README. Install that copy and check the resulting version:

```sh
python -m venv .venv
. .venv/bin/activate
python -m pip install .
daymark --version
```

The core needs no HTTP framework, credentials, notification provider, or network service. Install optional HTTP dependencies with `python -m pip install '.[service]'`.

`daymark init` creates a per-user database; `daymark init --here` creates
`.daymark/daymark.sqlite3` for one project. `daymark where` explains how the CLI chose its database. The order is `--db`, `DAYMARK_DB`, the nearest project database, a legacy `./daymark.sqlite3`, then the per-user database.

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

A read through an application-owned connection can pin a snapshot. If another
connection commits before you write, catch `daymark.DomainError` with
`code == "conflict"`. End the application's transaction, read the current
record, and decide whether the original operation still applies.

## Use Python with an existing Daymark service

Install `daymark-core[client]` in the client environment. Use
`daymark-core[service]` on the host that runs the HTTP server. The service
credential selects the workspace and actor. This example reads one compact
session through the same plain verbs as the local store.

```python
import os
from daymark import Client, Tasks

with Client(os.environ["DAYMARK_URL"], os.environ["DAYMARK_TOKEN"]) as client:
    session = Tasks(client).orientation()
    print(session.get("identity", {}), session["summary"])
```

The client raises `daymark.ClientError` on HTTP failures.
`error.code == "conflict"` corresponds to HTTP 409. Read the current record
before revising a guarded request. If a write's response was lost, retry the
identical call with the same explicit `op` to recover its committed result.
Local argument validation can still raise `daymark.DomainError`.
[HTTP service setup and credentials](docs/operations.md)

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
