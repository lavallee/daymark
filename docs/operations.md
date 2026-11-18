# Operating Daymark with synthetic data

The engine works in-process without a service or credentials. Install optional
HTTP dependencies only when running a server or Python HTTP client:

```sh
uv sync --dev
daymark --help
daymark --db example.sqlite3 --workspace example create --help
```

This guide covers the engine commands, the service and the worker. The everyday
verbs (`add`, `ready`, `done` and the rest) are in the [agent guide](agents.md).
A path-owned database runs in SQLite WAL mode, so `-wal` and `-shm` files
appear beside it; back up with `daymark backup`, not by copying the main file.

The engine write commands require a stable `--operation-id`; updates and actions require
`--expected-version` from the last read. Creation uses expected version zero.
Reuse an operation ID only for an identical retry. Keep JSON payloads in protected
files rather than shell command arguments. Unknown entity fields are rejected.

```sh
printf '%s\n' '{"id":"task-example","kind":"task","title":"Synthetic check"}' > task.json
daymark --db example.sqlite3 --workspace example create --operation-id create-example --file task.json
daymark --db example.sqlite3 --workspace example get task-example
daymark --db example.sqlite3 --workspace example export --output snapshot.json
```

## Optional API

Use `daymark-core[service]` for a standalone installation. The default bind address
is `127.0.0.1`. Provide a protected JSON authentication file mapping high-entropy
bearer tokens to workspace and principal IDs. Generate tokens locally; do not
commit them. This example shows the format, not a usable credential:

```json
{"REPLACE_WITH_A_RANDOM_TOKEN":{"workspace":"example","principal":"operator"}}
```

```sh
chmod 600 auth.json
daymark --db example.sqlite3 serve --auth-file auth.json
```

Every route, including `/v1/schema` and `/v1/health`, requires bearer authentication.
HTTP outbox health is scoped to the token's workspace; shared scheduler health
contains only infrastructure timestamps/counts. Trusted embedded callers and
local CLI may request global operational health.
`POST /v1/operations` accepts `operation_id`, `action`, `payload`, and
`expected_version`. `POST /v1/query` accepts `filters`. Reads are
`GET /v1/entities`, `/v1/entities/{id}`, `/v1/events?after=0&limit=100`,
`/v1/export`, and the derived views `/v1/ready`, `/v1/agenda`, `/v1/summary`
and `/v1/context/{id}`. Projects read as plans: `GET /v1/projects` (each with its
roll-up), `/v1/projects/{id}/plan` (stages, tasks in manual order, roll-up and the
head cursor, in one read), and `/v1/projects/{id}/changes?after=N`
(`{"cursor", "changed"}`, cheap enough to poll). `GET /v1/events/head` gives the
latest cursor and `/v1/events?project=ID` keeps the events that touch one project.
Goals read as `GET /v1/goals` and `/v1/goals/{id}/plan`, and `GET /v1/tree/{id}?depth=3`
gives an item with its children in order.

`GET /v1/identity` returns the token’s workspace and actor. An ordinary task
capture that includes its first note can send `attribute_capture_note: true`
at the operation level to attribute that note to the authenticated actor.
Historical note authors supplied on a raw create or native import keep their
recorded provenance.

`GET /v1/orientation` returns the authenticated workspace and actor, six
summary counts, the first five ready items, and up to five remembered facts.
Task and memory titles are limited to 160 characters plus an ellipsis. A
memory’s supplied scope travels with its preview. One SQLite snapshot supplies
the results, numeric `activity_cursor` and portable `activity_checkpoint`. The field `as_of` gives the UTC instant used for
calendar and lease calculations. Continue the memory list with `next_memory`
and workspace activity with `GET /v1/activity?after=CHECKPOINT`. When connecting to an
older service the client composes its available views separately. That reply
has no atomic checkpoint.

A checked activity page returns `checkpoint` for its last delivered event.
It survives a consistent database backup or native JSON restore that preserves
the workspace name, event number and that event's metadata and contents.
A missing or changed boundary event or a different workspace produces HTTP
409. Reopen the intended workspace and read a fresh compact prime. A zero
checkpoint is bound only to the workspace name, so it can start any history
in that workspace. An integer cursor carries only a position.

A checkpoint checks a single event. Backfilled or replaced earlier events,
new entities restored without events, and materialized state that differs
while the boundary matches can all escape detection. After a restore or
merge, read a fresh prime and inspect the affected entities before resuming
work. Use native export to compare the complete records, events and receipts
when that level of verification is required.

Imported IDs can contain path punctuation. For an ID such as `crate/07`,
pass the exact ID as a query parameter, for example
`GET /v1/context?entity_id=crate%2F07`. The same form works for
`/v1/entity` and `/v1/tree` with `entity_id`, `/v1/project-plan` and
`/v1/project-changes` with `project_id`, `/v1/goal-plan` with `goal_id`,
and `/v1/receipt` with `operation_id`. Each returns the same value as its
path form. The Python client chooses the form automatically.

`POST /v1/operations/batch` takes `operations` (up to 100, each shaped like a
single operation), `atomic` (default true: all or nothing) and optionally
`request_id` with `request`, which make the batch replay as a unit; it answers 200 with
`committed` and one result per operation, so a refusal is a result, not an HTTP
error. `POST /v1/import` accepts `snapshot`, `operation_id`, and
`expected_version` (use zero as the validated merge precondition). Import must
reject conflicting existing identities rather than rewind newer state. Schema
operation IDs remain stable within v1. A live task operation records the token’s
principal as its event actor. Claims, releases, queue pickup and appended notes
use this principal for ownership or authorship. Restored historical events keep
the actor recorded in the snapshot. If the workspace names deciders
(`daymark --db example.sqlite3 --workspace example policy --deciders operator`),
`accept` and `dismiss` from any other principal return 403.
The server resolves the workspace from its token configuration. Do not expose
this bearer-only service beyond a trusted local boundary without TLS termination
and your own access policy. CORS is disabled. Request bodies are limited to 1 MiB;
embedded apps may set a smaller limit with `create_app`.

### What the proposal policy enforces

A decider list governs the recorded decision on a proposal. Choose a person’s
authenticated service identity as a decider when an agent prepares proposals
for that person. A reviewed version or review token binds the decision to the
record the person inspected. A claim coordinates the work that follows.

| Operation | What the engine checks |
| --- | --- |
| Accept or dismiss a proposal | Current proposal state, expected revision and the configured deciding actor. Batched decisions use the same checks. |
| Edit a proposal’s terms | Revision and ordinary field rules. Direct status edits cannot change a task into or out of proposed status. Edited terms remain available for a new review. |
| Complete, skip or reopen an active proposal | Daymark requires the proposal to be decided first. |
| General task lifecycle and recovery | Cancellation, archiving, deletion and undo follow their own lifecycle and revision rules. Workspace write access includes these operations. |
| Native snapshot import | Destination workspace, entity and graph validity, and collisions with existing state. Historical actors and states belong to the supplied snapshot. The destination’s decider configuration stays local. |

A person can rely on a live, authenticated `accept` event under their
configured policy as a record of that decision. An open task alone supplies
no such evidence. For instance, a person can enter an open task directly, or
an operator can restore a workspace from trusted records. When historical
approval evidence matters, preserve the origin and custody of the database
or snapshot. Applications requiring separate reader, proposer, editor and
restore roles need to enforce those permissions at their service boundary.

Integration uses the same engine: `create_app(store, tokens)` accepts an existing
Store. `create_router(store)` is available for an application's own authentication
and bounded-body middleware: set `request.state.daymark_identity` to a trusted
`Identity(workspace, principal)`. Never derive that identity from request payloads.
The caller retains Store lifetime/transaction ownership. Synchronous HTTP routes
run in a thread pool: use `Store(path)` for a standalone server. When passing a
caller-owned SQLite connection, configure and serialize it for that execution
context (including SQLite's `check_same_thread` policy); do not share an active
application transaction across unrelated requests.

On a caller-owned connection a first read can open a transaction. Subsequent
reads see that snapshot until the embedding application commits or rolls back.
If a second connection commits in the meantime, SQLite can refuse a write
from the older snapshot. Daymark reports a conflict and keeps the caller's
transaction open. Finish that transaction according to your application's
rules, read current state, then retry the operation if it is still appropriate.
A Daymark write becomes visible to other connections when the caller commits.

The synchronous Python
`daymark.client.Client` accepts a base URL and bearer token, and supports context
manager cleanup. The CLI uses `--url` and a credential from `DAYMARK_TOKEN` (or
`--token-env`), avoiding credentials in command arguments.

## Recorder scheduler

```sh
daymark --db example.sqlite3 worker --once
daymark --db example.sqlite3 worker --interval 10 --lease-seconds 60
daymark --db example.sqlite3 health
```

The standalone worker uses a path-owned `Store`, which commits its claim before
calling the sink. A caller-owned embedded SQLite connection never commits inside
Daymark. Passing that embedded Store to the convenience worker does not establish
a committed delivery boundary: an application transaction may still roll back
after the sink acts. Applications with custom external sinks must own the
transaction sequence: commit the claim/outbox intent, invoke the sink with a
durable receiver deduplication key, then persist the fenced result in another
committed transaction. The convenience worker has no application-commit hook.
A custom sink alone is not production qualification; these examples prove only
recording behavior.

Each pass persists tick and heartbeat, claims due reminders under a lease, calls
the recording sink with a stable delivery key, and persists success or retry.
Each claim batch uses a fresh lease-owner token, fencing late outcomes from an
older batch even when the same worker process recovers its expired work.
Immediately before each sink call, the worker re-reads the reminder and checks
its current version, owner, delivery key, lease expiry, and associated task state.
Cancellation committed before that final check suppresses the call. That check
is the dispatch authorization point: cancellation after it may race with the sink
and cannot undo an external effect. Lease fencing still prevents a cancelled or
stale claim from being recorded as a successful delivery. The boundary tests use
barriers for both sides of this race rather than timing-dependent sleeps.

The default sink accepts only the `recording` channel and sends no notifications.
An unsupported channel records a failed attempt, never a delivery success. Its in-memory key receipts are diagnostic;
the database contains durable delivery outcomes. A crash after a sink call but
before recording success can cause retry. The crash regression terminates a
worker subprocess after a separate synthetic receiver commits a delivery-key
receipt, then starts a new process after lease expiry. It verifies two attempts
with one stable key and one receiver effect; it does not demonstrate arbitrary
provider deduplication or production delivery. Custom sinks must persistently
deduplicate delivery keys. Keep batch execution below the lease duration;
leases are not renewed while a sink blocks. A successful recording pass does
not demonstrate production notification delivery.

`deploy/daymark-recorder.service` is a generic user-systemd example. Review paths
and the service's installed executable location for your environment, copy it
into your user unit directory, then enable it using `systemctl --user`.
The service runs the recorder only and exposes no HTTP listener.

## Backup and restore drill

```sh
daymark --db example.sqlite3 backup --file example-backup.sqlite3
daymark --db restored.sqlite3 restore --file example-backup.sqlite3
daymark --db restored.sqlite3 --workspace example list
daymark --db restored.sqlite3 --workspace example export --output restored.json
```

Backup uses SQLite's online backup API, including committed WAL data, and checks
source integrity. Restore writes a new database and refuses to overwrite an
existing path. Stop workers and writers before switching the active database;
restoring delivered records can otherwise replay already performed external
side effects. Keep backups and exports private (new files use mode 0600).
Logical snapshot import also validates entity references and preserves history;
including prior operation hashes/results so identical write retries remain
replays after restore. Conflicting duplicate event/operation identities must be
rejected atomically. This is distinct from a complete SQLite backup. Compare synthetic snapshot readback
before considering a restore verified. Do not use real household data in examples.

## Upgrading between releases

A newer Daymark upgrades a database in place, in one transaction. **The upgrade
is one-way**. Stop old clients and workers before an upgrade. Some older
binaries can still open the changed file. Use the pre-upgrade backup to roll
back safely.

Two rules decide when that happens:

- **Commands that open a writable Store upgrade the file.** `daymark ready`, `daymark add`,
  `serve`, ordinary `mcp`, `worker` and `init` open the file upgrading it, because the
  command line is the operator's tool and the documented upgrade path. The first
  time it upgrades an existing database it prints one line to standard error, for
  example `daymark: upgraded /data/daymark.sqlite3 from schema 4 to 5 (Daymark
  0.4.0); use a pre-upgrade backup to return to an older schema`. A new or already current file
  prints nothing.
- **The library's reads never upgrade.** `Store.plan`, `projects`, `changes`,
  `goals`, `tree`, `export`, `get`, `list`, `query` and the ready, agenda and
  summary views refuse a schema the caller has not upgraded, all with the same
  `conflict` message. `Store(path)` upgrades when it opens a path; a caller-owned
  connection is upgraded inside the caller's transaction and the caller commits
  it. `Store(path, upgrade=False)` refuses an old schema without writing, and a
  read-only connection on an old schema is refused the same way.

So take the backup **with the release you are leaving, before installing the new
one**. A writable Store opened by the new release upgrades the file.
`daymark backup` copies the file through SQLite's backup API without opening a
Store, so it never upgrades what it copies:

```sh
daymark --db example.sqlite3 backup --file example-before-upgrade.sqlite3
```

Stop or upgrade every process that uses the file (CLI wrappers, the service, the
worker, embedding applications) together. To roll back, stop them, restore the
backup into a new path and point the older release at it; writes made after the
upgrade are not in the backup. A database written by a newer Daymark is refused
with `conflict`; Daymark never downgrades a file.

### What happened with older installations

A drill using wheels built from all five release tags created the same
two-workspace synthetic database with each release. In the selected
workspace, one task had a planned day, an independent deadline and a title
edit. Each old installation made a native backup and restored it into a new
file. Its own export before backup and after restore was identical.

We opened the original with development commit `5eaeb05` and checked the
two events, two operation receipts, original fields and second workspace.
Replaying the original capture returned its original result. Separately,
importing each release's JSON export into an empty database retained the
same fields, events and capture replay.

| Release that created the file | Schema before opening | Reading a disposable copy of the upgraded file with that old release |
| --- | --- | --- |
| 0.1.0 | Unmarked | `get` succeeded. Its success does not establish compatibility for writes. |
| 0.2.0 | 3 | `get` failed validation on newer fields. |
| 0.3.0 | 4 | `get` failed validation on newer fields. |
| 0.4.0 | 5 | Opening failed with a newer-schema conflict. |
| 0.5.0 | 6 | No schema upgrade took place. `get` succeeded. |

These checks cover the synthetic task and its history. A complete native
backup is still necessary for settings, scheduling and delivery state.

## Boundary review rationale

The [implementation review](implementation-review.md) records pinned upstream
source observations and independently derived regression designs. Daymark's own
barrier, subprocess, transaction, and restore tests establish its behavior; static
upstream inspection and upstream test assertions are not Daymark pass results.
No additional scheduler or task engine is required for the core.
