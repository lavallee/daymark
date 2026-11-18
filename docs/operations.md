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
On POSIX, the CLI creates a new database with mode 0600 before SQLite opens
it. Its journal and shared-memory files inherit the database permissions.
Running `init` on an existing file preserves its operator-selected mode.
Backup checks the completed copy and stores it in SQLite's rollback-journal
mode so an offline, read-only reader needs no sidecar files. The live source
retains its journal mode. Opening a restored path for writing with Daymark
selects WAL again. Applications that own their SQLite connection can choose
their own journal mode after restoration.
An interrupted copy can leave a private `.daymark-backup-*` temporary file.
On filesystems without hard links, the command reserves the new destination
and commits the checked SQLite copy there. A hard interruption of this
fallback can leave an uninitialized file. Restore refuses a zero-byte source.
A file already at the destination is preserved.
Workspace JSON exports also serialize to a private temporary file before publication.
On volumes without hard links, publication copies the finished bytes into an
exclusively created destination. After a hard interruption during that fallback,
check the destination before retrying.

A path-owned database runs in SQLite WAL mode, so `-wal` and `-shm` files
appear beside it; back up with `daymark backup`, not by copying the main file.

Use `daymark where --json` to inspect the selected path without opening SQLite.
The everyday task views and local inspections (`workspaces`, `policy`, `health`,
`events`, `get`, `query`, and `export`) require an existing Daymark database.
The low-level `list` command retains its legacy create-on-open behavior when
given a new filename. Use `where` for preflight and `init` for deliberate setup.

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

A client checking whether a service understands a scope or context filter
can request `GET /v1/schema?brief=true`. The response contains route paths,
HTTP methods and query parameter names from the service's OpenAPI document.
A v0.5.0 service returns its complete schema for this request. The client
checks support before sending a read whose results depend on that filter.
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
latest cursor and `/v1/events?project=ID` keeps the events that touch one project. The project change flag and its reported cursor share one SQLite snapshot, including the current set of external dependencies.
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
has no atomic checkpoint. `GET /v1/orientation?scope=studio` returns the filter
as `scope` and computes counts and previews from matching records. Scoped
orientation omits the workspace-wide activity cursor and checkpoint.

`POST /v1/advice` reads the credential's workspace and uses its principal for
claim visibility. An empty object selects the active workspace profile.
Optional `profile`, `set`, `drop`, `scope`, `context` and `now` apply to this
request. The service evaluates built-in strategies after taking one local
snapshot. Set `brief: true` for list-sized task rows with supporting notes
and checklist steps. The MCP bridge uses this form. Its `as_of` is the UTC
instant used for claims and dates on the service. `GET /v1/profiles` lists active and saved choices. Use `view=active`,
`view=saved&name=NAME` or `view=strategy&name=STRATEGY` to inspect one.
Preference mutations require local database access.

A checked activity page returns `checkpoint` for its last delivered event.
It survives a consistent database backup or native JSON restore that preserves
the workspace name, event number and that event's metadata and contents.
A missing or changed boundary event or a different workspace produces HTTP
409. Reopen the intended workspace and read a fresh compact prime. A zero
checkpoint is bound only to the workspace name, so it can start any history
in that workspace. An integer cursor carries only a position. Clients that round large JSON integers can page by the checked string until a page contains fewer events than requested.

A checkpoint checks a single event. Backfilled or replaced earlier events,
new entities restored without events, and materialized state that differs
while the boundary matches can all escape detection. After a restore or
merge, read a fresh prime and inspect the affected entities before resuming
work. Use native export to compare the complete records, events and receipts
when that level of verification is required.

Entity history, undo and recurrence resolve event order by the workspace
cursor retained in the snapshot. The order in which sparse pieces arrive
does not change their chronology. Undo checks that the latest event's ending
state matches the materialized record before restoring the previous state.
A native merge can leave a record without that evidence. Finish restoring
the corresponding events, or explicitly edit the observed record. A
low-level undo of a hard deletion requires a matching deletion event, an
absent current entity and the deletion's expected version.

Native event numbers range from 1 through 9,223,372,036,854,775,807.
Import rejects larger numbers. A workspace restored at the maximum remains
readable, and existing operation receipts still replay. Creating another
event returns a conflict and rolls back that write.

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
A proposal’s context and brief include only the authenticated actor’s
`decision_access`. The `owner` query parameter used to evaluate readiness
cannot select a different identity for this policy check. A later decision
rechecks the policy in the write transaction.

The server resolves the workspace from its token configuration. Do not expose
this bearer-only service beyond a trusted local boundary without TLS termination
and your own access policy. CORS is disabled. Request bodies are limited to 1 MiB;
embedded apps may set a smaller limit with `create_app`.

### Review and change local proposal deciders

Use the same database and workspace for the read, write, and any retry.
`--deciders` replaces the whole list. Use the actors' exact names as shown
in their service identities. The list governs proposal decisions. Workspace
access and service credentials are managed separately.

```sh
daymark --db example.sqlite3 --workspace example policy --versioned
# {"deciders": ["owner-a"], "version": 1}
daymark --db example.sqlite3 --workspace example policy --deciders owner-b --if-version 1 --operation-id handover-01
# {"deciders": ["owner-b"], "version": 2}
```

An empty list allows any writer to accept or dismiss proposals. To return
to that rule, read the current version and use `policy --clear --if-version N
--operation-id ID` with a fresh ID.

Keep the write command and choose its operation ID before running it. If
the result is lost, repeat the exact command with the same ID and version.
A committed write returns its original list and version, even if a later
edit changed the list. Run `policy --versioned` to see the current state.
On a version conflict (exit 4), read the current list, reconcile the intended
change with the intervening edit, and submit the reviewed replacement with
the new version and a fresh ID.

Existing commands without these options still return only `deciders`.
Legacy writes replace the list unconditionally, including from v0.5.0.
After revision tracking is installed by a writable open, changes from
those clients advance the revision and can invalidate a guarded edit.

In a local six-process trial, all six administrators read the same policy
version before concurrently proposing different replacements. In each of 12
runs one edit succeeded and five received a conflict. After another reviewed
change, replaying the winning operation returned its original version while
a fresh policy read showed the later one.

### What the proposal policy enforces

A decider list governs the recorded decision on a proposal. Choose a person’s
authenticated service identity as a decider when an agent prepares proposals
for that person. A reviewed version checks the current revision number. A
review token also binds the decision to the observed event and its content
across deletion, recreation and native transfer. A claim coordinates the
work that follows. Unused review tokens issued by the metadata-only
development build require a fresh read. Repeating an operation that already
committed still returns its recorded receipt.

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

Install `daymark-core[client]` for a Python consumer of an existing service.
The synchronous `daymark.Client` accepts a base URL and bearer token and
supports context manager cleanup. The `service` extra also includes the client. The CLI uses `--url` and a credential from `DAYMARK_TOKEN` (or
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
daymark --db restored.sqlite3 --workspace example policy --versioned
daymark --db restored.sqlite3 --workspace example list
daymark --db restored.sqlite3 --workspace example export --output restored.json
```

Backup uses SQLite's online backup API, including committed WAL data, and checks
the integrity of the copied snapshot. Restore writes a new database and
refuses to overwrite an existing path. It restores all workspace decider
lists, policy revisions and successful policy receipts as of backup time.
Review the restored list before switching clients to that file. Start a
fresh review after switching to an earlier backup. Stop workers and writers before the
final backup for a switch. Restoring an earlier backup can make a reminder
pending again even if the source already delivered it. Keep backups and exports private (new files use mode 0600).
A reminder recorded as delivered, acknowledged or dismissed retains that
outcome after archiving and restoring visibility. Archiving a retryable failed
delivery cancels its future attempt. Its original result remains in the
operation receipt and its attempt count and last error remain on the record.
For a project exported by Todoist, follow the [CSV review walkthrough](todoist-csv.md).

Logical snapshot import also validates entity references and preserves history;
including prior operation hashes/results so identical write retries remain
replays after restore. Conflicting duplicate event/operation identities must be
rejected atomically. This is distinct from a complete SQLite backup. Compare synthetic snapshot readback
before considering a restore verified. Do not use real household data in examples.

### Move one workspace to a fresh file

Use a workspace snapshot for this move. A database backup includes every
workspace. With the local CLI, select the source and destination using
`--workspace NAME`; an HTTP bearer token selects its own workspace. Check
`GET /v1/identity` before exporting or importing over HTTP. The destination
must use the same workspace name. A snapshot includes the entire workspace,
including history and receipts from its other actors. It is suitable for a
person's separate workspace on a shared machine.

A logical snapshot carries entities, entity history and entity operation
receipts. The destination keeps its own decider list, policy revision and
policy receipts. Set the destination's decider policy before granting
service access. A fresh workspace allows any writer to accept or dismiss a proposal until
you configure deciders. Move profiles separately using
[named profile files](working-preferences.md#keep-more-than-one-way-of-working).

Pause source writers and reminder workers before the final export. A pending
reminder can become due and be delivered from both files if both workers run
against the same exported state. Keep destination delivery disabled until
you account for any source deliveries since export. The built-in recorder
only records locally. Task claims retain their actor and original expiry.
Coordinate release with the holder or wait for expiry before assigning the
work again.

In a two-workspace transfer over authenticated loopback HTTP, readback
matched all five entities, six events and six operation receipts for the
selected workspace. An import using the other credential failed. A competing
claim saw a conflict, and the freshly configured destination refused the
helper's proposal decision. After deliberate simultaneous activation, two
local recorders processed the same pending delivery key, one per database.

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
with `conflict`. An unrecognized marker also stops the upgrade and leaves the stored
value available for inspection. A native backup still works.

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

### Setting revisions on schema 6

The candidate installs persistent SQLite triggers on the first writable
Store open, including on a file already marked schema 6. A caller-owned
connection must commit the installation. The schema marker stays at 6, so
this installation alone does not print a CLI schema upgrade notice. An untouched v0.5.0 file still supports read-only task views and
plain setting reads. Reading a guarded preference version requires the
writable installation to commit first. Existing v0.5.0 connections can
continue across installation.
A released v0.5.0 writer stayed open during an upgrade trial. After it changed
the value five times, a version-1 write was refused and a native backup retained
revision 6. Its setting writes are unconditional, but each committed change advances
the revision checked by later candidate writes. Same-value writes and
deletes of absent settings keep the current revision.

A tagged v0.5.0 profile editor can discard settings it does not recognize.
In a mixed-version trial a current client set `overbooked.include_focus=false`
at revision 1. The older CLI added a shortlist and omitted the unfamiliar
parameter from its write. Revision 2 correctly exposed the change. Replaying
the original current-client operation returned its earlier result, and a
separately saved named copy retained the intended value. Guarded undo refused
to overwrite revision 2. Setting only the missing parameter with
`--if-version 2` preserved the new shortlist at revision 3. A portable
profile export uses `profile --saved NAME --json`.

Use settings observations obtained after installation for guarded writes,
and review any pre-installation change anew before reversing it. Installation
preserves existing revisions and receipts. On untouched files, v0.5.0 left
only the final setting value, so earlier changes and stale observations
from before installation cannot be identified reliably.

Native SQLite backup and restore carry the installed triggers, revisions,
deleted-setting revisions and local receipts. A native copy of an untouched
file still needs the writable open. Workspace JSON export and import carry
entity history and receipts. Settings and their receipts are local to the
database, and setting versions should be used with the database that issued
them.

## Boundary review rationale

The [implementation review](implementation-review.md) records pinned upstream
source observations and independently derived regression designs. Daymark's own
barrier, subprocess, transaction, and restore tests establish its behavior; static
upstream inspection and upstream test assertions are not Daymark pass results.
No additional scheduler or task engine is required for the core.

### Several agents opening a new database

When eight independent processes first opened the same new path together,
SQLite sometimes returned `SQLITE_BUSY` while selecting the journal mode,
before any task operation had started. In 50 Linux trials using a new file
each time, 200 of 400 distinct task captures succeeded. Daymark now retries
that opening step within its connection timeout. Repeating the same 50
eight-process trials committed all 400 captures. Each resulting workspace
contained exactly eight events. On Apple Silicon macOS, 319 of 400
succeeded before the change and 400 of 400 afterward. An application-provided connection keeps its
own journal settings. A separate CLI check launched eight fresh processes
at a time. Across five trials per scenario, distinct captures produced eight
events, identical submissions produced one, and the same operation submitted
to two workspaces produced one in each. [SQLite busy handlers](https://sqlite.org/c3ref/busy_handler.html) · [WAL journal mode](https://sqlite.org/wal.html#persistence_of_wal_mode)
